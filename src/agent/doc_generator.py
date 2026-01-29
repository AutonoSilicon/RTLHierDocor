import os
import asyncio
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

from .llm_backend import LLMBackend
from .source_resolver import SourceResolver
from .progress_tracker import ProgressTracker
from . import prompt_templates as prompts

class AgentDocGenerator:
    """Orchestrates the two-pass documentation generation process."""

    def __init__(
        self,
        hierarchy: Any,
        llm: LLMBackend,
        resolver: SourceResolver,
        tracker: ProgressTracker,
        output_dir: str,
        max_source_lines: int = 2000,
        max_modules: int = 0
    ):
        self.hierarchy = hierarchy
        self.llm = llm
        self.resolver = resolver
        self.tracker = tracker
        self.output_dir = Path(output_dir)
        self.modules_dir = self.output_dir / "modules"
        self.max_source_lines = max_source_lines
        self.max_modules = max_modules
        self._processed_pass1: set = set()
        self._processed_pass2: set = set()

    async def run(self):
        """Run the full documentation generation process."""
        print(f"[INFO] Starting Docor Agent on {self.hierarchy.module_name}...")
        
        # Pass 1: Top-down Overview Generation
        print("[INFO] Running Pass 1: Top-down Overview Generation...")
        await self._run_pass1(self.hierarchy, "该模块是设计的顶层模块。")
        
        # Pass 2: Bottom-up Detailed Documentation
        print("[INFO] Running Pass 2: Bottom-up Detailed Documentation...")
        await self._run_pass2(self.hierarchy, "该模块是设计的顶层模块。")
        
        print(f"[INFO] Documentation generation complete. Results in {self.modules_dir}")

    async def _run_pass1(self, node: Any, ancestor_context: str):
        """Pass 1: Top-down (Overview)"""
        # Check limit
        if self.max_modules > 0 and len(self._processed_pass1) >= self.max_modules:
            if node.module_name not in self._processed_pass1:
                return

        module_name = node.module_name
        
        if module_name not in self._processed_pass1:
            if not self.tracker.is_pass1_done(module_name):
                await self._generate_module_overview(node, ancestor_context)
            self._processed_pass1.add(module_name)
        
        # Get current overview to pass down
        state = self.tracker.get_state(module_name)
        current_overview = state.pass1_overview or ""
        
        # For children, the context is a summarized version of (ancestor_context + current_overview)
        # The user wants "将其所有向上父层级的描述总结成1个段落预算的背景描述"
        # We can call LLM to condense if it's too long, but for now we'll just use the current overview 
        # as the immediate context, which is already 1 paragraph.
        
        for child in node.children.values():
            await self._run_pass1(child, current_overview)

    async def _generate_module_overview(self, node: Any, ancestor_context: str):
        module_name = node.module_name
        print(f"  [Pass 1] Generating overview for {module_name}...")
        
        source_code = self.resolver.read_source(module_name, max_lines=self.max_source_lines) or "Source not found."
        port_summary = self.resolver.get_port_summary(module_name)
        children_summary = ", ".join([c.module_name for c in node.children.values()]) or "无子模块"
        
        prompt = prompts.PASS1_PROMPT.format(
            module_name=module_name,
            ancestor_context=ancestor_context,
            port_summary=port_summary,
            children_summary=children_summary,
            source_code=source_code,
            max_lines=self.max_source_lines
        )
        
        overview = await self.llm.generate(prompts.PASS1_SYSTEM, prompt)
        
        # Save results
        self.tracker.update_pass1(module_name, overview)
        self._save_module_file(module_name, "overview.md", overview)
        self._save_metadata(node)

    async def _run_pass2(self, node: Any, ancestor_context: str) -> str:
        """Pass 2: Bottom-up (Detailed Doc)"""
        # Check limit - only process if Pass 1 processed it
        if self.max_modules > 0 and node.module_name not in self._processed_pass1:
            return "Skipped due to max_modules limit."

        module_name = node.module_name
        
        # 1. Recurse to children first
        children_descriptions = {}
        for child in node.children.values():
            # Note: We need the overview of the current node to pass as context to children in Pass 2?
            # User says: "向上回溯，每个模块会结合所有子模块的功能描述，和本模块的数据流..."
            child_desc = await self._run_pass2(child, self.tracker.get_state(module_name).pass1_overview or "")
            children_descriptions[child.module_name] = child_desc
        
        # 2. Process current module if not already done
        if module_name in self._processed_pass2:
            return self.tracker.get_state(module_name).pass2_description or ""

        if self.tracker.is_pass2_done(module_name):
            self._processed_pass2.add(module_name)
            return self.tracker.get_state(module_name).pass2_description or ""

        # 3. Generate detailed doc
        is_leaf = len(node.children) == 0
        description = await self._generate_module_description(node, ancestor_context, children_descriptions, is_leaf)
        
        self._processed_pass2.add(module_name)
        return description

    async def _generate_module_description(self, node: Any, ancestor_context: str, children_descs: Dict[str, str], is_leaf: bool) -> str:
        module_name = node.module_name
        print(f"  [Pass 2] Generating description for {module_name}...")
        
        state = self.tracker.get_state(module_name)
        overview = state.pass1_overview or ""
        source_code = self.resolver.read_source(module_name) or "Source not found."
        port_summary = self.resolver.get_port_summary(module_name)

        if is_leaf:
            prompt = prompts.PASS2_LEAF_PROMPT.format(
                module_name=module_name,
                ancestor_context=ancestor_context,
                overview=overview,
                port_summary=port_summary,
                source_code=source_code
            )
            system = prompts.PASS2_LEAF_SYSTEM
        else:
            # Format children descriptions
            children_summary = "\n\n".join([f"## 子模块 {name}:\n{desc}" for name, desc in children_descs.items()])
            prompt = prompts.PASS2_NONLEAF_PROMPT.format(
                module_name=module_name,
                ancestor_context=ancestor_context,
                overview=overview,
                children_descriptions=children_summary,
                source_code=source_code
            )
            system = prompts.PASS2_NONLEAF_SYSTEM
        
        description = await self.llm.generate(system, prompt)
        
        self.tracker.update_pass2(module_name, description)
        self._save_module_file(module_name, "description.md", description)
        return description

    def _save_module_file(self, module_name: str, filename: str, content: str):
        module_dir = self.modules_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)
        with open(module_dir / filename, 'w', encoding='utf-8') as f:
            f.write(content)

    def _save_metadata(self, node: Any):
        module_name = node.module_name
        module_dir = self.modules_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)
        
        metadata = {
            "module_name": module_name,
            "instances": [node.instance_name], # In Pass 1, we might only see one instance initially
            "ports": self.resolver.get_port_summary(module_name).split('\n'),
            "source_path": self.resolver.resolve_path(module_name),
            "children": [c.module_name for c in node.children.values()]
        }
        
        with open(module_dir / "metadata.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
