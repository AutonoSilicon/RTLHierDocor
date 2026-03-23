"""LLM Backend for OpenAI-compatible APIs.

This module provides a unified interface for LLM interactions,
supporting OpenAI-compatible APIs (including DeepSeek, vLLM, etc.).
"""

import os
import asyncio
import datetime
import json
import time
import traceback
import inspect
from typing import List, Optional, Any, Dict, Tuple

from .llm_toolcall import ToolCallback, build_tool_round_messages

try:
    import openai
except ImportError:
    openai = None
    print("[ERROR] 'openai' package not installed. Run 'pip install openai'.")


class LLMBackend:
    """LLM Backend using OpenAI's Chat Completion API."""

    def __init__(self, model: str = "gpt-4", api_key: Optional[str] = None, base_url: Optional[str] = None,
                 thinking: bool = False, enable_webui_monitoring: bool = False):
        """Initialize the LLM backend.

        Args:
            model: Model name (e.g., "gpt-4", "deepseek-chat", etc.)
            api_key: API key (defaults to OPENAI_API_KEY env var)
            base_url: Base URL for API (for non-OpenAI providers)
            thinking: Enable thinking/reasoning mode
            enable_webui_monitoring: Enable WebUI event emission for monitoring
        """
        if openai is None:
            raise ImportError("'openai' package not installed. Run 'pip install openai'.")

        self.client = openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url
        )
        self.model = model
        self.thinking = thinking
        self.base_url = base_url or "https://api.openai.com/v1"
        self.enable_webui_monitoring = enable_webui_monitoring

        # Semaphore to limit concurrent LLM API calls (prevent rate limiting)
        self._semaphore = asyncio.Semaphore(16)

    async def generate(
        self,
        system: str,
        prompt: str,
        log_path: str = "debug.md",
        disable_thinking: bool = False,
        tools_enabled: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_callback: Optional[ToolCallback] = None,
        max_tool_rounds: int = 8,
        *,
        module_name: Optional[str] = None,
        pass_name: Optional[str] = None,
    ) -> Tuple[str, Dict[str, int]]:
        """Generate text from the LLM.

        Args:
            system: System prompt
            prompt: User prompt
            log_path: Path for debug log file
            disable_thinking: Whether to disable thinking mode
            tools_enabled: Enable tool calling mode
            tools: Available tools for tool calling
            tool_callback: Callback for tool execution
            max_tool_rounds: Maximum tool calling rounds
            module_name: Module name for WebUI monitoring
            pass_name: Pass name for WebUI monitoring

        Returns:
            Tuple of (generated_text, token_stats)
        """
        start_time = time.time()
        trace_event_id = None

        # Emit WebUI trace event if monitoring enabled
        if self.enable_webui_monitoring and module_name and pass_name:
            try:
                from webui.trace_collector import get_trace_collector
                collector = get_trace_collector()
                trace_event_id = collector.emit_llm_start(
                    module_name=module_name,
                    pass_name=pass_name,
                    prompt_preview=prompt[:500] if prompt else None,
                    system_preview=system[:200] if system else None,
                    estimated_tokens=len(prompt) // 4 if prompt else 0,
                    model=self.model,
                )
            except Exception:
                pass

        async with self._semaphore:
            try:
                use_thinking = self.thinking and not disable_thinking
                messages: List[Dict[str, Any]] = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]

                tool_mode = bool(tools_enabled and tools)
                if tool_mode and tool_callback is None:
                    return "Error: tools_enabled=True but tool_callback is None.", {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }

                aggregated = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                final_text: str = ""

                # Multi-round tool calling loop.
                # - Round 0: normal call with tools enabled
                # - If tool_calls are returned, execute and append tool results, then continue.
                # - Stop when no tool_calls (final assistant content).
                rounds = max(1, int(max_tool_rounds) if tool_mode else 1)
                for _round in range(rounds):
                    kwargs: Dict[str, Any] = {
                        "model": self.model,
                        "messages": messages,
                    }
                    if use_thinking:
                        kwargs["extra_body"] = {"enable_thinking": True}
                        kwargs["stream"] = True
                        kwargs["stream_options"] = {"include_usage": True}
                    if tool_mode:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"

                    timeout_sec = float(os.environ.get("RTL_DOCOR_LLM_TIMEOUT_SEC", "600"))
                    response = await asyncio.wait_for(
                        self.client.chat.completions.create(**kwargs),
                        timeout=timeout_sec,
                    )
                    if use_thinking:
                        assistant_message, streamed_thinking = await self._consume_streaming_chat_completion(
                            response,
                            aggregated,
                        )
                    else:
                        assistant_message = response.choices[0].message
                        streamed_thinking = ""
                        if hasattr(response, "usage") and response.usage:
                            aggregated["input_tokens"] += int(getattr(response.usage, "prompt_tokens", 0) or 0)
                            aggregated["output_tokens"] += int(getattr(response.usage, "completion_tokens", 0) or 0)
                            aggregated["total_tokens"] += int(getattr(response.usage, "total_tokens", 0) or 0)

                    tool_calls = getattr(assistant_message, "tool_calls", None)
                    if tool_mode and tool_calls:
                        tool_round_log_lines: List[str] = []
                        for tc in tool_calls:
                            fn = getattr(tc, "function", None)
                            tool_name = getattr(fn, "name", "") if fn else ""
                            raw_args = getattr(fn, "arguments", "") if fn else ""
                            if len(raw_args or "") > 1200:
                                raw_args = (raw_args or "")[:1200] + "..."
                            tool_round_log_lines.append(
                                f"- call `{tool_name}` args: `{raw_args}`"
                            )

                        has_tool_calls, tool_messages = await build_tool_round_messages(
                            assistant_message,
                            tool_callback,
                        )
                        if has_tool_calls:
                            messages.extend(tool_messages)

                            # Persist tool traces incrementally so Ctrl-C still keeps useful logs.
                            self._append_tool_trace_to_log(
                                log_path=log_path,
                                round_idx=_round + 1,
                                call_lines=tool_round_log_lines,
                                tool_messages=tool_messages,
                            )
                        continue

                    # Final assistant content (no tool calls)
                    final_text = assistant_message.content or ""
                    if streamed_thinking:
                        self._append_thinking_to_log(streamed_thinking, log_path)
                    self._append_final_response_to_log(final_text, log_path)
                    break

                if final_text and use_thinking:
                    clean_result, thinking_content = self._extract_thinking_content(final_text)
                    if thinking_content:
                        self._append_thinking_to_log(thinking_content, log_path)
                    final_text = clean_result

                # Log only the initial system+prompt (keeps log sizes bounded)
                self._log_call(system, prompt, self.model, log_path)

                # Emit WebUI trace event if monitoring enabled
                if trace_event_id and self.enable_webui_monitoring:
                    try:
                        from webui.trace_collector import get_trace_collector
                        collector = get_trace_collector()
                        duration_ms = (time.time() - start_time) * 1000
                        collector.emit_llm_end(
                            event_id=trace_event_id,
                            success=True,
                            response_preview=final_text[:500] if final_text else None,
                            token_stats=aggregated,
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        pass

                return (final_text if final_text else "Error: LLM returned empty response."), aggregated

            except Exception as e:
                detail = self._format_exception_details(
                    e,
                    timeout_sec=locals().get("timeout_sec"),
                    use_thinking=locals().get("use_thinking"),
                    tool_mode=locals().get("tool_mode"),
                )
                self._append_exception_to_log(log_path, detail)

                # Emit WebUI trace event if monitoring enabled
                if trace_event_id and self.enable_webui_monitoring:
                    try:
                        from webui.trace_collector import get_trace_collector
                        collector = get_trace_collector()
                        duration_ms = (time.time() - start_time) * 1000
                        collector.emit_llm_end(
                            event_id=trace_event_id,
                            success=False,
                            error=detail['summary'],
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        pass

                return f"Error calling LLM API: {detail['summary']}", {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

    async def _consume_streaming_chat_completion(
        self,
        response_stream: Any,
        aggregated: Dict[str, int],
    ) -> Tuple[Any, str]:
        """Collect streamed reasoning/content deltas into a message-like object."""
        reasoning_parts: List[str] = []
        content_parts: List[str] = []
        final_message: Any = None

        async for chunk in self._aiter_response_stream(response_stream):
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                usage = getattr(chunk, "usage", None)
                if usage:
                    aggregated["input_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
                    aggregated["output_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
                    aggregated["total_tokens"] += int(getattr(usage, "total_tokens", 0) or 0)
                continue

            delta = getattr(choices[0], "delta", None)
            if delta is not None:
                reasoning_delta = getattr(delta, "reasoning_content", None)
                if reasoning_delta:
                    reasoning_parts.append(str(reasoning_delta))
                content_delta = getattr(delta, "content", None)
                if content_delta:
                    content_parts.append(str(content_delta))

            message = getattr(choices[0], "message", None)
            if message is not None:
                final_message = message

        if final_message is None:
            final_message = type("StreamedAssistantMessage", (), {})()
        if not getattr(final_message, "content", None):
            final_message.content = "".join(content_parts)
        if not hasattr(final_message, "tool_calls"):
            final_message.tool_calls = None

        return final_message, "".join(reasoning_parts).strip()

    async def _aiter_response_stream(self, response_stream: Any):
        """Normalize OpenAI-compatible sync/async streams into one async iterator."""
        if hasattr(response_stream, "__aiter__"):
            async for chunk in response_stream:
                yield chunk
            return

        iterator = iter(response_stream)
        while True:
            try:
                chunk = await asyncio.to_thread(next, iterator)
            except StopIteration:
                break
            if inspect.isawaitable(chunk):
                chunk = await chunk
            yield chunk

    def _format_exception_details(
        self,
        error: Exception,
        *,
        timeout_sec: Optional[float],
        use_thinking: Optional[bool],
        tool_mode: Optional[bool],
    ) -> Dict[str, Any]:
        """Build a structured exception summary for debug logs."""
        summary = f"{type(error).__name__}: {str(error)}".strip()
        request = getattr(error, "request", None)
        response = getattr(error, "response", None)
        cause_chain: List[str] = []
        current = getattr(error, "__cause__", None) or getattr(error, "__context__", None)
        hop = 0
        while current is not None and hop < 4:
            cause_chain.append(f"{type(current).__name__}: {current}")
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
            hop += 1

        traceback_text = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ).strip()

        details: Dict[str, Any] = {
            "summary": summary,
            "exception_type": f"{type(error).__module__}.{type(error).__name__}",
            "message": str(error),
            "model": self.model,
            "base_url": self.base_url,
            "timeout_sec": timeout_sec,
            "thinking": bool(use_thinking),
            "tool_mode": bool(tool_mode),
            "cause_chain": cause_chain,
            "traceback": traceback_text,
        }

        if request is not None:
            details["request"] = {
                "method": getattr(request, "method", ""),
                "url": str(getattr(request, "url", "")),
            }

        if response is not None:
            headers = getattr(response, "headers", None)
            safe_headers = {}
            if headers is not None:
                for key in ("x-request-id", "content-type", "server"):
                    value = headers.get(key)
                    if value:
                        safe_headers[key] = value
            details["response"] = {
                "status_code": getattr(response, "status_code", None),
                "headers": safe_headers,
            }

        body = getattr(error, "body", None)
        if body is not None:
            try:
                details["body"] = json.dumps(body, ensure_ascii=False, indent=2)
            except Exception:
                details["body"] = str(body)

        return details

    @staticmethod
    def _extract_mermaid_and_content(text: str) -> tuple[str, str]:
        """Extract mermaid diagram and remaining content from LLM response."""
        if not text:
            return "", ""
        
        import re
        mermaid_pattern = r'```mermaid\s*(.*?)\s*```'
        mermaid_matches = re.findall(mermaid_pattern, text, flags=re.DOTALL)
        
        if mermaid_matches:
            mermaid_code = mermaid_matches[0].strip()
            other_content = re.sub(mermaid_pattern, '', text, flags=re.DOTALL).strip()
        else:
            mermaid_code = ""
            other_content = text.strip()
        
        return mermaid_code, other_content

    @staticmethod
    def _extract_thinking_content(text: str) -> tuple[str, str]:
        """Extract thinking process and clean output from LLM response."""
        if not text:
            return text, ""
        
        import re
        thinking_blocks = re.findall(r'<think>(.*?)</think>', text, flags=re.DOTALL)
        thinking_content = '\n\n---\n\n'.join(thinking_blocks) if thinking_blocks else ""
        
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        cleaned = re.sub(r'</think>', '', cleaned)
        cleaned = re.sub(r'\n\n\n+', '\n\n', cleaned)
        
        return cleaned.strip(), thinking_content.strip()

    def _append_thinking_to_log(self, thinking_content: str, log_path: str):
        """Append thinking content to debug log file."""
        if not thinking_content:
            return
        
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write("\n\n" + "="*80 + "\n")
                f.write("## Thinking Process (推理过程)\n")
                f.write("="*80 + "\n\n")
                f.write(thinking_content)
                f.write("\n\n")
        except Exception as e:
            print(f"[WARN] Failed to append thinking to log: {e}")

    def _append_tool_trace_to_log(
        self,
        log_path: str,
        round_idx: int,
        call_lines: List[str],
        tool_messages: List[Dict[str, Any]],
    ):
        """Append one tool-calling round trace to debug log."""
        try:
            result_lines: List[str] = []
            for msg in tool_messages:
                if msg.get("role") != "tool":
                    continue
                tc_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")
                if not isinstance(content, str):
                    try:
                        content = json.dumps(content, ensure_ascii=False)
                    except Exception:
                        content = str(content)
                if len(content) > 2000:
                    content = content[:2000] + "\n\n[... truncated ...]"
                result_lines.append(f"- result `{tc_id}`:\n```text\n{content}\n```")

            with open(log_path, 'a', encoding='utf-8') as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write(f"## Tool Trace Round {round_idx}\n")
                f.write("=" * 80 + "\n\n")
                if call_lines:
                    f.write("### Calls\n")
                    f.write("\n".join(call_lines) + "\n\n")
                if result_lines:
                    f.write("### Results\n")
                    f.write("\n\n".join(result_lines) + "\n")
        except Exception as e:
            print(f"[WARN] Failed to append tool trace to log: {e}")

    def _append_final_response_to_log(self, final_text: str, log_path: str):
        """Append final assistant response summary to debug log."""
        if not final_text:
            return
        try:
            text = final_text
            if len(text) > 6000:
                text = text[:6000] + "\n\n[... truncated ...]"
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write("## Final Assistant Response\n")
                f.write("=" * 80 + "\n\n")
                f.write(text + "\n")
        except Exception as e:
            print(f"[WARN] Failed to append final response to log: {e}")

    def _append_exception_to_log(self, log_path: str, detail: Dict[str, Any]):
        """Append structured exception diagnostics to debug log."""
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write("## LLM Exception\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"- summary: {detail.get('summary', '')}\n")
                f.write(f"- exception_type: {detail.get('exception_type', '')}\n")
                f.write(f"- model: {detail.get('model', '')}\n")
                f.write(f"- base_url: {detail.get('base_url', '')}\n")
                f.write(f"- timeout_sec: {detail.get('timeout_sec', '')}\n")
                f.write(f"- thinking: {detail.get('thinking', False)}\n")
                f.write(f"- tool_mode: {detail.get('tool_mode', False)}\n")

                request = detail.get("request") or {}
                if request:
                    f.write(f"- request_method: {request.get('method', '')}\n")
                    f.write(f"- request_url: {request.get('url', '')}\n")

                response = detail.get("response") or {}
                if response:
                    f.write(f"- response_status: {response.get('status_code', '')}\n")
                    headers = response.get("headers") or {}
                    if headers:
                        f.write("- response_headers:\n")
                        for key, value in headers.items():
                            f.write(f"  - {key}: {value}\n")

                cause_chain = detail.get("cause_chain") or []
                if cause_chain:
                    f.write("\n### Cause Chain\n")
                    for item in cause_chain:
                        f.write(f"- {item}\n")

                body = detail.get("body")
                if body:
                    f.write("\n### Response Body\n")
                    f.write("```json\n" + body + "\n```\n")

                traceback_text = detail.get("traceback")
                if traceback_text:
                    f.write("\n### Traceback\n")
                    f.write("```text\n" + traceback_text + "\n```\n")
        except Exception as e:
            print(f"[WARN] Failed to append exception log: {e}")

    def _log_call(self, system: str, prompt: str, model: str, log_path: str = "debug.md"):
        """Log the LLM call to a markdown file."""
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"""
## [{timestamp}] Model: {model}

### System Prompt
{system}

### User Prompt
{prompt}

---
"""
            os.makedirs(os.path.dirname(log_path), exist_ok=True) if os.path.dirname(log_path) else None
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            print(f"[WARNING] Failed to write to {log_path}: {e}")


def get_llm_backend(config: Any) -> LLMBackend:
    """Factory to get the configured LLM backend.

    Note: Only OpenAI-compatible backends are supported.

    Args:
        config: Configuration dict with backend settings
    """
    backend_type = config.get("backend", "openai")

    if backend_type not in ("openai", "anthropic", "agent_sdk"):
        raise ValueError(f"Unknown LLM backend: {backend_type}. Only 'openai' (OpenAI-compatible) is supported.")

    # All backends now use OpenAI-compatible API
    if backend_type in ("openai", "anthropic", "agent_sdk"):
        # Note: anthropic and agent_sdk are deprecated, treated as openai
        if backend_type in ("anthropic", "agent_sdk"):
            print(f"[WARN] Backend '{backend_type}' is deprecated. Using OpenAI-compatible API instead.")

    return LLMBackend(
        model=config.get("model", "gpt-4"),
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
        thinking=config.get("thinking", False),
        enable_webui_monitoring=config.get("enable_webui_monitoring", False),
    )
