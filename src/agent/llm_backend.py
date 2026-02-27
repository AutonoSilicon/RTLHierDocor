"""LLM Backend for OpenAI-compatible APIs.

This module provides a unified interface for LLM interactions,
supporting OpenAI-compatible APIs (including DeepSeek, vLLM, etc.).
"""

import os
import asyncio
import datetime
from typing import List, Optional, Any, Dict, Tuple

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

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md",
                       disable_thinking: bool = False) -> Tuple[str, Dict[str, int]]:
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
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ]

                use_thinking = self.thinking and not disable_thinking

                kwargs = {
                    "model": self.model,
                    "messages": messages
                }

                # Common pattern for thinking/reasoning models in OpenAI-compatible APIs
                if use_thinking:
                    kwargs["extra_body"] = {"enable_thinking": True}

                response = await self.client.chat.completions.create(**kwargs)
                assistant_message = response.choices[0].message

                result = assistant_message.content

                if result and use_thinking:
                    clean_result, thinking_content = self._extract_thinking_content(result)
                    if thinking_content:
                        self._append_thinking_to_log(thinking_content, log_path)
                    result = clean_result

                token_stats = {
                    "input_tokens": response.usage.prompt_tokens if hasattr(response, 'usage') else 0,
                    "output_tokens": response.usage.completion_tokens if hasattr(response, 'usage') else 0,
                    "total_tokens": response.usage.total_tokens if hasattr(response, 'usage') else 0
                }

                self._log_call(system, prompt, self.model, log_path)
                return (result if result else "Error: LLM returned empty response."), token_stats

            except Exception as e:
                return f"Error calling LLM API: {str(e)}", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

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
