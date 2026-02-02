import os
import asyncio
import datetime
from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict

class LLMBackend(ABC):
    """Abstract base class for LLM backends."""

    def __init__(self):
        # Semaphore to limit concurrent LLM API calls (prevent rate limiting)
        self._semaphore = asyncio.Semaphore(3)  # Max 3 concurrent requests

    @abstractmethod
    async def generate(self, system: str, prompt: str, log_path: str = "debug.md") -> str:
        """Generate text from the LLM.

        Args:
            system: System prompt
            prompt: User prompt
            log_path: Path for debug log file
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

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md") -> str:
        if not self.client:
            return "Error: Anthropic client not initialized."

        async with self._semaphore:
            try:
                kwargs = {
                    "model": self.model,
                    "max_tokens": 4096 if not self.thinking else 16384,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}]
                }

                if self.thinking:
                    # Support for Claude 3.7+ thinking mode
                    kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8192}
                    # When thinking is enabled, max_tokens must be > budget_tokens
                    if kwargs["max_tokens"] <= 8192:
                        kwargs["max_tokens"] = 16384

                response = await self.client.messages.create(**kwargs)
                result = response.content[0].text
                self._log_call(system, prompt, self.model, log_path)
                return result
            except Exception as e:
                return f"Error calling Anthropic API: {str(e)}"

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
        if tool_name == "read_block_source":
            return self._read_block_source(
                tool_args.get("module_name", ""),
                tool_args.get("block_id", "")
            )
        else:
            return f"Unknown tool: {tool_name}"

    def _read_block_source(self, module_name: str, block_id: str) -> str:
        """Read source code for a specific PROC or COMB block.
        
        Args:
            module_name: Name of the module
            block_id: ID of the block (e.g., 'PROC_0', 'COMB_1')
            
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
        
        # Find the block in PROC or COMB nodes
        if block_id in graph.proc_nodes:
            proc_info = graph.proc_nodes[block_id]
            if proc_info.source_location:
                source = self.resolver.read_block_source([proc_info.source_location])
                return f"# PROC Block: {block_id}\n{source}"
            else:
                return f"Error: No source location available for PROC block '{block_id}'"
        
        elif block_id in graph.comb_nodes:
            comb_info = graph.comb_nodes[block_id]
            if comb_info.source_locations:
                source = self.resolver.read_block_source(comb_info.source_locations)
                return f"# COMB Block: {block_id} ({comb_info.comb_type})\n{source}"
            else:
                return f"Error: No source locations available for COMB block '{block_id}'"
        
        else:
            available_blocks = list(graph.proc_nodes.keys()) + list(graph.comb_nodes.keys())
            return f"Error: Block '{block_id}' not found in module '{module_name}'. Available blocks: {', '.join(available_blocks[:10])}"

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md") -> str:
        if not self.client:
            return "Error: OpenAI client not initialized."

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
                    if self.thinking:
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
                        
                        # Continue loop to get next response from LLM
                        continue
                    else:
                        # No more tool calls, we have the final response
                        result = assistant_message.content
                        self._log_call(system, prompt, self.model, log_path)
                        return result if result else "Error: OpenAI returned empty response."
                
                # If we hit max iterations
                return "Error: Maximum function calling iterations reached."
                
            except Exception as e:
                return f"Error calling OpenAI API: {str(e)}"

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

    async def generate(self, system: str, prompt: str, log_path: str = "debug.md") -> str:
        if not self._sdk_available:
            return "Error: claude-code-sdk not installed."

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
                return result if result else "Error: Agent SDK returned empty response."
            except Exception as e:
                return f"Error calling Claude Agent SDK: {str(e)}"

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
