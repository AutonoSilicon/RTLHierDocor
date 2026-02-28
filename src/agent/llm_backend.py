"""LLM Backend for OpenAI-compatible APIs.

This module provides a unified interface for LLM interactions,
supporting OpenAI-compatible APIs (including DeepSeek, vLLM, etc.).
"""

import os
import asyncio
import datetime
import json
from typing import List, Optional, Any, Dict, Tuple, Callable

try:
    import openai
except ImportError:
    openai = None
    print("[ERROR] 'openai' package not installed. Run 'pip install openai'.")


class LLMBackend:
    """LLM Backend using OpenAI's Chat Completion API."""

    def __init__(self, model: str = "gpt-4", api_key: Optional[str] = None, base_url: Optional[str] = None,
                 thinking: bool = False):
        """Initialize the LLM backend.

        Args:
            model: Model name (e.g., "gpt-4", "deepseek-chat", etc.)
            api_key: API key (defaults to OPENAI_API_KEY env var)
            base_url: Base URL for API (for non-OpenAI providers)
            thinking: Enable thinking/reasoning mode
        """
        if openai is None:
            raise ImportError("'openai' package not installed. Run 'pip install openai'.")

        self.client = openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url
        )
        self.model = model
        self.thinking = thinking

        # Semaphore to limit concurrent LLM API calls (prevent rate limiting)
        self._semaphore = asyncio.Semaphore(3)

    async def generate(
        self,
        system: str,
        prompt: str,
        log_path: str = "debug.md",
        disable_thinking: bool = False,
        tools_enabled: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_callback: Optional[Callable[[str, Dict[str, Any]], str]] = None,
        max_tool_rounds: int = 8,
    ) -> Tuple[str, Dict[str, int]]:
        """Generate text from the LLM.

        Args:
            system: System prompt
            prompt: User prompt
            log_path: Path for debug log file
            disable_thinking: Whether to disable thinking mode

        Returns:
            Tuple of (generated_text, token_stats)
        """
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
                    if tool_mode:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"

                    response = await self.client.chat.completions.create(**kwargs)
                    assistant_message = response.choices[0].message

                    if hasattr(response, "usage") and response.usage:
                        aggregated["input_tokens"] += int(getattr(response.usage, "prompt_tokens", 0) or 0)
                        aggregated["output_tokens"] += int(getattr(response.usage, "completion_tokens", 0) or 0)
                        aggregated["total_tokens"] += int(getattr(response.usage, "total_tokens", 0) or 0)

                    tool_calls = getattr(assistant_message, "tool_calls", None)
                    if tool_mode and tool_calls:
                        # Append assistant tool call message
                        try:
                            tool_calls_payload = [tc.model_dump() for tc in tool_calls]
                        except Exception:
                            tool_calls_payload = []
                            for tc in tool_calls:
                                tool_calls_payload.append({
                                    "id": getattr(tc, "id", ""),
                                    "type": getattr(tc, "type", "function"),
                                    "function": {
                                        "name": getattr(getattr(tc, "function", None), "name", ""),
                                        "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                                    },
                                })

                        messages.append({
                            "role": "assistant",
                            "content": assistant_message.content or "",
                            "tool_calls": tool_calls_payload,
                        })

                        # Execute tool calls
                        for tc in tool_calls:
                            tc_id = getattr(tc, "id", "")
                            fn = getattr(tc, "function", None)
                            tool_name = getattr(fn, "name", "") if fn else ""
                            raw_args = getattr(fn, "arguments", "") if fn else ""

                            try:
                                args_dict = json.loads(raw_args) if raw_args else {}
                                if not isinstance(args_dict, dict):
                                    args_dict = {"_args": args_dict}
                            except Exception:
                                args_dict = {"_raw": raw_args}

                            try:
                                tool_result = tool_callback(tool_name, args_dict) if tool_callback else ""
                            except Exception as e:
                                tool_result = f"Error: tool '{tool_name}' failed: {e}"

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": tool_result or "",
                            })
                        continue

                    # Final assistant content (no tool calls)
                    final_text = assistant_message.content or ""
                    break

                if final_text and use_thinking:
                    clean_result, thinking_content = self._extract_thinking_content(final_text)
                    if thinking_content:
                        self._append_thinking_to_log(thinking_content, log_path)
                    final_text = clean_result

                # Log only the initial system+prompt (keeps log sizes bounded)
                self._log_call(system, prompt, self.model, log_path)
                return (final_text if final_text else "Error: LLM returned empty response."), aggregated

            except Exception as e:
                return f"Error calling LLM API: {str(e)}", {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

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
        thinking=config.get("thinking", False)
    )
