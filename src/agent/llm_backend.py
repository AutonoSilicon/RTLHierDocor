import os
import asyncio
import datetime
from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict, Tuple

class LLMBackend(ABC):
    """Abstract base class for LLM backends."""

    def __init__(self):
        # Semaphore to limit concurrent LLM API calls (prevent rate limiting)
        self._semaphore = asyncio.Semaphore(3)  # Max 3 concurrent requests

    @staticmethod
    def _extract_mermaid_and_content(text: str) -> tuple[str, str]:
        """Extract mermaid diagram and remaining content from LLM response.
        
        Separates the mermaid code block from other content (text, thinking, etc).
        
        Args:
            text: Raw text from LLM
            
        Returns:
            Tuple of (mermaid_code, other_content)
                - mermaid_code: Pure mermaid diagram without ```mermaid tags
                - other_content: Everything else (text, thinking, etc)
        """
        if not text:
            return "", ""
        
        import re
        
        # Extract mermaid code block (with or without language tag)
        mermaid_pattern = r'```mermaid\s*(.*?)\s*```'
        mermaid_matches = re.findall(mermaid_pattern, text, flags=re.DOTALL)
        
        if mermaid_matches:
            # Use the first mermaid block if multiple exist
            mermaid_code = mermaid_matches[0].strip()
            # Remove all mermaid blocks from original text
            other_content = re.sub(mermaid_pattern, '', text, flags=re.DOTALL).strip()
        else:
            mermaid_code = ""
            other_content = text.strip()
        
        return mermaid_code, other_content

    @staticmethod
        """Extract thinking process and clean output from LLM response.
        
        Some models (DeepSeek, Claude with thinking) include thinking process
        in <think> tags. This function extracts them separately.
        
        Args:
            text: Raw text from LLM
            
        Returns:
            Tuple of (clean_output, thinking_content)
        """
        if not text:
            return text, ""
        
        import re
        
        # Extract all thinking blocks
        thinking_blocks = re.findall(r'<think>(.*?)</think>', text, flags=re.DOTALL)
        thinking_content = '\n\n---\n\n'.join(thinking_blocks) if thinking_blocks else ""
        
        # Remove thinking tags from output
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        # Also remove any orphaned closing tags
        cleaned = re.sub(r'</think>', '', cleaned)
        # Clean up excessive whitespace left by tag removal
        cleaned = re.sub(r'\n\n\n+', '\n\n', cleaned)
        
        return cleaned.strip(), thinking_content.strip()

    @staticmethod
    def _clean_thinking_tags(text: str) -> str:
        """Remove <think>...</think> tags from LLM output.
        
        Deprecated: Use _extract_thinking_content instead to preserve thinking.
        """
        if not text:
            return text
        
        import re
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        cleaned = re.sub(r'</think>', '', cleaned)
        cleaned = re.sub(r'\n\n\n+', '\n\n', cleaned)
        return cleaned.strip()

    def _append_thinking_to_log(self, thinking_content: str, log_path: str):
        """Append thinking content to debug log file.
        
        Args:
            thinking_content: Extracted thinking process text
            log_path: Path to the debug log file
        """
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

    @abstractmethod
    async def generate(self, system: str, prompt: str, log_path: str = "debug.md") -> Tuple[str, Dict[str, int]]:
        """Generate text from the LLM.

        Args:
            system: System prompt
            prompt: User prompt
            log_path: Path for debug log file
            
        Returns:
            Tuple of (generated_text, token_stats) where token_stats is a dict with:
            - input_tokens: number of input tokens
            - output_tokens: number of output tokens
            - total_tokens: total tokens used
        """
        pass

    def _log_call(self, system: str, prompt: str, model: str, log_path: str = "debug.md"):
        """Log the LLM call to a markdown file.

        Args:
            system: System prompt
            prompt: User prompt
            model: Model identifier
            log_path: Path to the log file (default: debug.md in cwd)
        """
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

class AnthropicBackend(LLMBackend):
    """Backend using Anthropic's Messages API."""

    def __init__(self, model: str = "claude-3-5-sonnet-20241022", api_key: Optional[str] = None, base_url: Optional[str] = None, thinking: bool = False):
        super().__init__()
        try:
            import anthropic
            self.client = anthropic.AsyncAnthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                base_url=base_url
            )
            self.model = model
            self.thinking = thinking
        except ImportError:
            print("[ERROR] 'anthropic' package not installed. Run 'pip install anthropic'.")
            self.client = None

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md", disable_thinking: bool = False) -> Tuple[str, Dict[str, int]]:
        if not self.client:
            return "Error: Anthropic client not initialized.", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        async with self._semaphore:
            try:
                # Use thinking mode unless explicitly disabled
                use_thinking = self.thinking and not disable_thinking
                
                kwargs = {
                    "model": self.model,
                    "max_tokens": 4096 if not use_thinking else 16384,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}]
                }

                if use_thinking:
                    # Support for Claude 3.7+ thinking mode
                    kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8192}
                    # When thinking is enabled, max_tokens must be > budget_tokens
                    if kwargs["max_tokens"] <= 8192:
                        kwargs["max_tokens"] = 16384

                response = await self.client.messages.create(**kwargs)
                result = response.content[0].text
                
                # Extract and save thinking process (if thinking mode was used)
                if result and use_thinking:
                    clean_result, thinking_content = self._extract_thinking_content(result)
                    if thinking_content:
                        self._append_thinking_to_log(thinking_content, log_path)
                    result = clean_result
                
                # Extract token usage
                token_stats = {
                    "input_tokens": response.usage.input_tokens if hasattr(response, 'usage') else 0,
                    "output_tokens": response.usage.output_tokens if hasattr(response, 'usage') else 0,
                    "total_tokens": 0
                }
                token_stats["total_tokens"] = token_stats["input_tokens"] + token_stats["output_tokens"]
                
                self._log_call(system, prompt, self.model, log_path)
                return result, token_stats
            except Exception as e:
                return f"Error calling Anthropic API: {str(e)}", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

class OpenAIBackend(LLMBackend):
    """Backend using OpenAI's Chat Completion API with function calling support."""

    def __init__(self, model: str = "gpt-4", api_key: Optional[str] = None, base_url: Optional[str] = None, 
                 thinking: bool = False, resolver: Optional[Any] = None, graphs: Optional[Dict[str, Any]] = None):
        super().__init__()
        try:
            import openai
            self.client = openai.AsyncOpenAI(
                api_key=api_key or os.environ.get("OPENAI_API_KEY"),
                base_url=base_url
            )
            self.model = model
            self.thinking = thinking
            self.resolver = resolver  # SourceResolver for reading source code
            self.graphs = graphs or {}  # Dict mapping module_name -> SimplifiedGraph
        except ImportError:
            print("[ERROR] 'openai' package not installed. Run 'pip install openai'.")
            self.client = None

    def set_context(self, resolver: Optional[Any] = None, graphs: Optional[Dict[str, Any]] = None):
        """Update the resolver and graphs for function calling.
        
        Args:
            resolver: SourceResolver for reading source code
            graphs: Dict mapping module_name -> SimplifiedGraph
        """
        if resolver is not None:
            self.resolver = resolver
        if graphs is not None:
            self.graphs = graphs

    def _get_tools_definition(self):
        """Define the tools available for function calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_block_source",
                    "description": "Read the source code of a specific BLOCK (PROC or COMB) within a module. "
                                   "Use this to inspect the detailed RTL implementation of a logical block.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module_name": {
                                "type": "string",
                                "description": "The name of the module containing the block"
                            },
                            "block_id": {
                                "type": "string",
                                "description": "The ID of the block to read (e.g., 'PROC_0', 'COMB_1', 'IN_COMB_0', 'OUT_COMB_0')"
                            }
                        },
                        "required": ["module_name", "block_id"]
                    }
                }
            }
        ]

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Execute a tool call and return the result."""
        print(f"    [TOOL CALL] {tool_name}({', '.join(f'{k}={repr(v)}' for k, v in tool_args.items())})")

        if tool_name == "read_block_source":
            result = self._read_block_source(
                tool_args.get("module_name", ""),
                tool_args.get("block_id", "")
            )
            # Log result summary (first line only to avoid spam)
            first_line = result.split('\n')[0] if result else ""
            if result.startswith("Error:"):
                print(f"    [TOOL RESULT] ❌ {first_line}")
            else:
                source_lines = result.count('\n')
                print(f"    [TOOL RESULT] ✓ {first_line} ({source_lines} lines)")
            return result
        else:
            print(f"    [TOOL RESULT] ❌ Unknown tool: {tool_name}")
            return f"Unknown tool: {tool_name}"

    def _read_block_source(self, module_name: str, block_id: str) -> str:
        """Read source code for a specific PROC or COMB block.

        Args:
            module_name: Name of the module
            block_id: ID of the block (e.g., 'PROC_0', 'COMB_1', 'p0', 'c1')

        Returns:
            Source code string or error message
        """
        if not self.resolver:
            return "Error: SourceResolver not available"

        if not module_name:
            return "Error: module_name is required"

        if not block_id:
            return "Error: block_id is required"

        # Get the SimplifiedGraph for this module
        graph = self.graphs.get(module_name)
        if not graph:
            return f"Error: No graph found for module '{module_name}'"

        # Normalize block_id: support both 'PROC_11' and 'p11' formats
        normalized_id = block_id
        if block_id.startswith("PROC_"):
            normalized_id = "p" + block_id[5:]  # PROC_11 -> p11
        elif block_id.startswith("COMB_"):
            normalized_id = "c" + block_id[5:]  # COMB_11 -> c11
        elif block_id.startswith("IN_COMB_"):
            normalized_id = "c" + block_id[8:]  # IN_COMB_11 -> c11
        elif block_id.startswith("OUT_COMB_"):
            normalized_id = "c" + block_id[9:]  # OUT_COMB_11 -> c11

        # Find the block in PROC or COMB nodes
        if normalized_id in graph.proc_nodes:
            proc_info = graph.proc_nodes[normalized_id]
            if proc_info.source_location:
                source = self.resolver.read_block_source([proc_info.source_location])
                return f"# PROC Block: {block_id}\n{source}"
            else:
                return f"Error: No source location available for PROC block '{block_id}'"
        
        elif normalized_id in graph.comb_nodes:
            comb_info = graph.comb_nodes[normalized_id]
            if comb_info.source_locations:
                source = self.resolver.read_block_source(comb_info.source_locations)
                return f"# COMB Block: {block_id} ({comb_info.comb_type})\n{source}"
            else:
                return f"Error: No source locations available for COMB block '{block_id}'"
        
        else:
            available_blocks = list(graph.proc_nodes.keys()) + list(graph.comb_nodes.keys())
            return f"Error: Block '{block_id}' not found in module '{module_name}'. Available blocks: {', '.join(available_blocks[:10])}"

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md", disable_thinking: bool = False) -> Tuple[str, Dict[str, int]]:
        if not self.client:
            return "Error: OpenAI client not initialized.", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        async with self._semaphore:
            try:
                import json
                
                # Initialize conversation history
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ]

                # Add tools if resolver is available
                tools = self._get_tools_definition() if self.resolver else None
                
                # Maximum number of function call iterations to prevent infinite loops
                max_iterations = 10
                iteration = 0
                
                # Use thinking mode unless explicitly disabled
                use_thinking = self.thinking and not disable_thinking
                
                while iteration < max_iterations:
                    iteration += 1
                    
                    kwargs = {
                        "model": self.model,
                        "messages": messages
                    }

                    # Add tools to request if available
                    if tools:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"

                    # Common pattern for thinking/reasoning models in OpenAI-compatible APIs (e.g. DeepSeek)
                    if use_thinking:
                        kwargs["extra_body"] = {"enable_thinking": True}

                    response = await self.client.chat.completions.create(**kwargs)
                    assistant_message = response.choices[0].message
                    
                    # Add assistant's message to history
                    msg_dict = {
                        "role": "assistant",
                        "content": assistant_message.content
                    }
                    if hasattr(assistant_message, 'tool_calls') and assistant_message.tool_calls:
                        msg_dict["tool_calls"] = assistant_message.tool_calls
                    messages.append(msg_dict)
                    
                    # Check if there are tool calls to process
                    if hasattr(assistant_message, 'tool_calls') and assistant_message.tool_calls:
                        num_calls = len(assistant_message.tool_calls)
                        print(f"    [LLM] Requesting {num_calls} tool call(s)...")

                        # Process each tool call
                        for tool_call in assistant_message.tool_calls:
                            function_name = tool_call.function.name
                            function_args = json.loads(tool_call.function.arguments)

                            # Execute the tool
                            tool_result = self._execute_tool(function_name, function_args)

                            # Add tool response to messages
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": function_name,
                                "content": tool_result
                            })

                        print(f"    [LLM] Continuing generation with tool results...")
                        # Continue loop to get next response from LLM
                        continue
                    else:
                        # No more tool calls, we have the final response
                        result = assistant_message.content
                        
                        # Extract and save thinking process (for DeepSeek and other thinking models)
                        if result and use_thinking:
                            clean_result, thinking_content = self._extract_thinking_content(result)
                            if thinking_content:
                                self._append_thinking_to_log(thinking_content, log_path)
                            result = clean_result
                        
                        # Extract token usage from final response
                        token_stats = {
                            "input_tokens": response.usage.prompt_tokens if hasattr(response, 'usage') and hasattr(response.usage, 'prompt_tokens') else 0,
                            "output_tokens": response.usage.completion_tokens if hasattr(response, 'usage') and hasattr(response.usage, 'completion_tokens') else 0,
                            "total_tokens": response.usage.total_tokens if hasattr(response, 'usage') and hasattr(response.usage, 'total_tokens') else 0
                        }
                        
                        self._log_call(system, prompt, self.model, log_path)
                        return (result if result else "Error: OpenAI returned empty response."), token_stats
                
                # If we hit max iterations
                return "Error: Maximum function calling iterations reached.", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                
            except Exception as e:
                return f"Error calling OpenAI API: {str(e)}", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

class AgentSDKBackend(LLMBackend):
    """Backend using Claude Agent SDK (claude-code-sdk).

    Spawns Claude Code sub-processes that can autonomously use tools
    (Read, Grep, Glob) to explore source files. Unlike the Anthropic/OpenAI
    backends where we pass source code in the prompt, the Agent SDK lets
    Claude decide what to read and how deeply to explore.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        cwd: Optional[str] = None,
        max_turns: int = 10,
        allowed_tools: Optional[List[str]] = None
    ):
        super().__init__()
        self.model = model
        self.cwd = cwd or os.getcwd()
        self.max_turns = max_turns
        self.allowed_tools = allowed_tools or ["Read", "Grep", "Glob"]
        self._sdk_available = False

        try:
            from claude_code_sdk import query as _query, ClaudeCodeOptions as _opts
            self._sdk_available = True
        except ImportError:
            print("[ERROR] 'claude-code-sdk' package not installed. "
                  "Run 'pip install claude-code-sdk'.")

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md", disable_thinking: bool = False) -> Tuple[str, Dict[str, int]]:
        if not self._sdk_available:
            return "Error: claude-code-sdk not installed.", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        from claude_code_sdk import query, ClaudeCodeOptions

        async with self._semaphore:
            try:
                options = ClaudeCodeOptions(
                    system_prompt=system,
                    allowed_tools=self.allowed_tools,
                    max_turns=self.max_turns,
                    cwd=self.cwd,
                    model=self.model,
                    permission_mode="acceptEdits",
                )

                text_parts = []
                async for message in query(
                    prompt=prompt,
                    options=options
                ):
                    # message types: AssistantMessage, ResultMessage, etc.
                    if hasattr(message, 'content') and isinstance(message.content, list):
                        for block in message.content:
                            if hasattr(block, 'text'):
                                text_parts.append(block.text)

                result = "\n".join(text_parts)
                self._log_call(system, prompt, f"agent_sdk/{self.model}", log_path)
                
                # Agent SDK doesn't provide token stats, return zeros
                token_stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                
                return (result if result else "Error: Agent SDK returned empty response."), token_stats
            except Exception as e:
                return f"Error calling Claude Agent SDK: {str(e)}", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

def get_llm_backend(config: Any, resolver: Optional[Any] = None, graphs: Optional[Dict[str, Any]] = None) -> LLMBackend:
    """Factory to get the configured LLM backend.
    
    Args:
        config: Configuration dict with backend settings
        resolver: Optional SourceResolver for function calling (OpenAI backend)
        graphs: Optional dict mapping module_name -> SimplifiedGraph (OpenAI backend)
    """
    backend_type = config.get("backend", "anthropic")
    model = config.get("model", "claude-3-5-sonnet-20241022")
    
    if backend_type == "anthropic":
        return AnthropicBackend(
            model=model, 
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            thinking=config.get("thinking", False)
        )
    elif backend_type == "openai":
        return OpenAIBackend(
            model=model,
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            thinking=config.get("thinking", False),
            resolver=resolver,
            graphs=graphs or {}
        )
    elif backend_type == "agent_sdk":
        return AgentSDKBackend(
            model=model,
            cwd=config.get("code_base_path"),
            max_turns=config.get("max_turns", 10),
            allowed_tools=config.get("allowed_tools", ["Read", "Grep", "Glob"])
        )
    else:
        raise ValueError(f"Unknown LLM backend: {backend_type}")
