import os
from abc import ABC, abstractmethod
from typing import List, Optional, Any

class LLMBackend(ABC):
    """Abstract base class for LLM backends."""
    
    @abstractmethod
    async def generate(self, system: str, prompt: str) -> str:
        """Generate text from the LLM."""
        pass

class AnthropicBackend(LLMBackend):
    """Backend using Anthropic's Messages API."""
    
    def __init__(self, model: str = "claude-3-5-sonnet-20241022", api_key: Optional[str] = None, base_url: Optional[str] = None, thinking: bool = False):
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

    async def generate(self, system: str, prompt: str) -> str:
        if not self.client:
            return "Error: Anthropic client not initialized."
        
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
            return response.content[0].text
        except Exception as e:
            return f"Error calling Anthropic API: {str(e)}"

class OpenAIBackend(LLMBackend):
    """Backend using OpenAI's Chat Completion API."""
    
    def __init__(self, model: str = "gpt-4", api_key: Optional[str] = None, base_url: Optional[str] = None, thinking: bool = False):
        try:
            import openai
            self.client = openai.AsyncOpenAI(
                api_key=api_key or os.environ.get("OPENAI_API_KEY"),
                base_url=base_url
            )
            self.model = model
            self.thinking = thinking
        except ImportError:
            print("[ERROR] 'openai' package not installed. Run 'pip install openai'.")
            self.client = None

    async def generate(self, system: str, prompt: str) -> str:
        if not self.client:
            return "Error: OpenAI client not initialized."
        
        try:
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ]
            }
            
            # Common pattern for thinking/reasoning models in OpenAI-compatible APIs (e.g. DeepSeek)
            if self.thinking:
                # Some providers use specific parameters in extra_body
                kwargs["extra_body"] = {"enable_thinking": True}
                # For o1-style models, they might not support 'system' role in some versions or require 'developer'
                # But here we keep it simple as most compatible APIs handle 'system'

            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            return f"Error calling OpenAI API: {str(e)}"

class AgentSDKBackend(LLMBackend):
    """Backend using Claude Agent SDK."""
    
    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        self.model = model
        # Note: Actual implementation depends on specific SDK version and setup
        # This is a conceptual implementation based on common Agent SDK patterns
        try:
            # Placeholder for agent sdk import
            # import claude_agent_sdk as sdk
            pass
        except ImportError:
            pass

    async def generate(self, system: str, prompt: str) -> str:
        # In a real Agent SDK scenario, we might provide tools to the agent
        # For this docor task, we are currently passing source code directly in prompt (Pass 1/2)
        # But the Agent SDK could be used to let the agent explore the codebase
        
        # Conceptual implementation:
        # agent = sdk.Agent(model=self.model, system=system)
        # result = await agent.query(prompt)
        # return result.text
        
        return "Agent SDK Backend implementation pending SDK availability."

def get_llm_backend(config: Any) -> LLMBackend:
    """Factory to get the configured LLM backend."""
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
            thinking=config.get("thinking", False)
        )
    elif backend_type == "agent_sdk":
        return AgentSDKBackend(model=model)
    else:
        raise ValueError(f"Unknown LLM backend: {backend_type}")
