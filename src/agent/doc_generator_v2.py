"""Orchestrates the multi-pass documentation generation process."""

import os
import json
import fnmatch
from collections import defaultdict
from typing import Dict, List, Optional, Any, Callable, Awaitable
from pathlib import Path

from .llm_backend import LLMBackend
from .source_resolver import SourceResolver
from .progress_tracker import ProgressTracker
from .block_doc_generator import BlockDocGenerator
from .pass_runner import PassRunner, PassConfig
from schematic.simplifier import SimplifiedGraph
from .prompts import (
    PASS1_SYSTEM,
    PASS1_PROMPT,
    PASS2_LEAF_SYSTEM,
    PASS2_LEAF_PROMPT,
    PASS2_NONLEAF_SYSTEM,
    PASS2_NONLEAF_PROMPT,
    PASS2_1_SYSTEM,
    PASS2_1_PROMPT,
    PASS2_2_MODULE_SYSTEM,
    PASS2_2_MODULE_PROMPT,
    PASS2_3_INTERFACE_SYSTEM,
    PASS2_3_INTERFACE_PROMPT,
    PASS2_4_FUNCTIONAL_SYSTEM,
    PASS2_4_FUNCTIONAL_PROMPT,
    PASS2_5_REGISTER_SYSTEM,
    PASS2_5_REGISTER_PROMPT,
    PASS2_6_TIMING_CDC_SYSTEM,
    PASS2_6_TIMING_CDC_PROMPT,
)

# Pass configurations - centralized definition
PASS_CONFIGS = {
    "pass2_1": PassConfig(
        name="pass2_1",
        display_name="Pass 2.1",
        depends_on=["pass1_5"],
        needs_graph=True,
        recurse_first=True,
        use_children_results=True,
        output_file="design_highlights.md",
        tracker_field="pass2_1_highlights",
    ),
    "pass2_2": PassConfig(
        name="pass2_2",
        display_name="Pass 2.2",
        depends_on=["pass1_5"],
        needs_graph=True,
        recurse_first=True,
        use_children_results=True,
        output_file="flowchart.mmd",
        tracker_field="pass2_2_mermaid",
    ),
    "pass2_3": PassConfig(
        name="pass2_3",
        display_name="Pass 2.3",
        depends_on=["pass1"],
        needs_graph=False,  # Can work without graph
        recurse_first=True,
        use_children_results=True,
        output_file="interface_spec.md",
        tracker_field="pass2_3_interface",
    ),
    "pass2_4": PassConfig(
        name="pass2_4",
        display_name="Pass 2.4",
        depends_on=["pass1_5"],
        needs_graph=True,
        recurse_first=True,
        use_children_results=True,
        output_file="functional_desc.md",
        tracker_field="pass2_4_functional",
    ),
    "pass2_5": PassConfig(
        name="pass2_5",
        display_name="Pass 2.5",
        depends_on=["pass1_5"],
        needs_graph=True,
        recurse_first=True,
        use_children_results=True,
        output_file="register_desc.md",
        tracker_field="pass2_5_register",
    ),
    "pass2_6": PassConfig(
        name="pass2_6",
        display_name="Pass 2.6",
        depends_on=["pass1_5"],
        needs_graph=True,
        recurse_first=True,
        use_children_results=True,
        output_file="timing_cdc.md",
        tracker_field="pass2_6_timing_cdc",
    ),
}


class AgentDocGenerator:
    """Orchestrates the multi-pass documentation generation process."""

    def __init__(
        self,
        hierarchy: Any,
        llm: LLMBackend,
        resolver: SourceResolver,
        tracker: ProgressTracker,
        output_dir: str,
        max_source_lines: int = 2000,
        max_modules: int = 0,
        skip_modules: Optional[List[str]] = None,
        schematic_gen: Optional[Any] = None,
        block_doc_threshold: int = 64
    ):
        self.hierarchy = hierarchy
        self.llm = llm
        self.resolver = resolver
        self.tracker = tracker
        self.output_dir = Path(output_dir)
        self.modules_dir = self.output_dir / "modules"
        self.debug_dir = self.output_dir / "debug"  # Debug output subdirectory
        self.max_source_lines = max_source_lines
        self.max_modules = max_modules
        self.skip_modules = skip_modules or []
        self.schematic_gen = schematic_gen
        self.block_doc_threshold = block_doc_threshold
        
        # Data caches
        self._graphs: Dict[str, SimplifiedGraph] = {}
        self._mermaid_summaries: Dict[str, str] = {}
        self._token_stats: Dict[str, List[Dict[str, int]]] = defaultdict(list)
        
        # Unified processed tracking: pass_name -> set of module_names
        self._processed: Dict[str, set] = {
            "pass1": set(),
            "pass1_5": set(),
            "pass2": set(),
        }
        # Pass 2.x processed sets managed by PassRunners
        
        # Initialize pass runners
        self._pass_runners: Dict[str, PassRunner] = {}
        self._init_pass_runners()

    def _init_pass_runners(self):
        """Initialize PassRunner instances for each pass."""
        self._pass_runners = {
            "pass2_1": PassRunner(PASS_CONFIGS["pass2_1"], self._gen_design_highlights, self),
            "pass2_2": PassRunner(PASS_CONFIGS["pass2_2"], self._gen_flowchart, self),
            "pass2_3": PassRunner(PASS_CONFIGS["pass2_3"], self._gen_interface_spec, self),
            "pass2_4": PassRunner(PASS_CONFIGS["pass2_4"], self._gen_functional_desc, self),
            "pass2_5": PassRunner(PASS_CONFIGS["pass2_5"], self._gen_register_desc, self),
            "pass2_6": PassRunner(PASS_CONFIGS["pass2_6"], self._gen_timing_cdc, self),
        }

    def _should_skip(self, module_name: str) -> bool:
        """Check if a module should be skipped based on skip_modules patterns."""
        return any(fnmatch.fnmatch(module_name, p) for p in self.skip_modules)

    def _is_pass_done(self, module_name: str, pass_name: str) -> bool:
        """Generic pass completion check using tracker."""
        if pass_name == "pass1":
            return self.tracker.is_pass1_done(module_name)
        elif pass_name == "pass1_5":
            return self.tracker.is_pass1_5_done(module_name)
        elif pass_name == "pass2":
            return self.tracker.is_pass2_done(module_name)
        elif pass_name in PASS_CONFIGS:
            return self._pass_runners[pass_name].is_done(module_name)
        return False

    def _update_tracker(self, module_name: str, pass_name: str, result: str):
        """Generic tracker update using pass name."""
        updater_map = {
            "pass2_1": self.tracker.update_pass2_1,
            "pass2_2": self.tracker.update_pass2_2,
            "pass2_3": self.tracker.update_pass2_3,
            "pass2_4": self.tracker.update_pass2_4,
            "pass2_5": self.tracker.update_pass2_5,
            "pass2_6": self.tracker.update_pass2_6,
        }
        if pass_name in updater_map:
            updater_map[pass_name](module_name, result)

    # ==================== Main Entry Point ====================

    async def run(self):
        """Run the full documentation generation process."""
        print(f"[INFO] Starting Docor Agent on {self.hierarchy.module_name}...")

        # Pass 0: Precompute simplified graphs
        if self.schematic_gen:
            print("[INFO] Running Pass 0: Precomputing SimplifiedGraphs...")
            self._precompute_graphs(self.hierarchy)
            print(f"[INFO] Precomputed {len(self._graphs)} simplified graphs")
            
            if hasattr(self.llm, 'set_context'):
                self.llm.set_context(resolver=self.resolver, graphs=self._graphs)

        # Pass 1: Top-down Preview Generation
        print("[INFO] Running Pass 1: Top-down Preview Generation...")
        await self._run_pass1(self.hierarchy, "该模块是设计的顶层模块。")

        # Pass 1.5: Block-level Documentation
        print("[INFO] Running Pass 1.5: Block-level Documentation...")
        await self._run_pass1_5(self.hierarchy)

        # Pass 2.1-2.6: Run in parallel
        parallel_passes = ["pass2_1", "pass2_2", "pass2_3", "pass2_4", "pass2_5", "pass2_6"]
        print(f"[INFO] Running {' + '.join(parallel_passes)} in parallel...")
        import asyncio
        await asyncio.gather(*[
            self._pass_runners[p].run(self.hierarchy) for p in parallel_passes
        ])

        # Pass 2: Bottom-up Synthesis Documentation
        print("[INFO] Running Pass 2: Synthesis Documentation...")
        await self._run_pass2(self.hierarchy)

        print(f"[INFO] Documentation generation complete. Results in {self.modules_dir}")

    # ==================== Pass 0: Precompute Graphs ====================

    def _precompute_graphs(self, node: Any):
        """Recursively precompute SimplifiedGraphs for all modules."""
        module_name = node.module_name
        
        if module_name in self._graphs or self._should_skip(module_name):
            return
            
        if self.schematic_gen:
            try:
                graph = self.schematic_gen.generate_simplified_graph(module_name)
                if graph:
                    self._graphs[module_name] = graph
            except Exception as e:
                print(f"[WARN] Module {module_name}: SimplifiedGraph generation failed: {e}")

        for child in node.children.values():
            self._precompute_graphs(child)

    # ==================== Pass 1: Top-down Preview ====================

    async def _run_pass1(self, node: Any, ancestor_context: str):
        """Pass 1: Top-down preview generation."""
        module_name = node.module_name
        
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 1] Skipping {module_name}")
            return
            
        if self.max_modules > 0 and len(self._processed["pass1"]) >= self.max_modules:
            if module_name not in self._processed["pass1"]:
                return

        if module_name not in self._processed["pass1"]:
            if not self.tracker.is_pass1_done(module_name):
                await self._generate_module_preview(node, ancestor_context)
            self._processed["pass1"].add(module_name)

        # Recurse with current preview as context
        state = self.tracker.get_state(module_name)
        for child in node.children.values():
            await self._run_pass1(child, state.pass1_overview or "")

    async def _generate_module_preview(self, node: Any, ancestor_context: str):
        """Generate Pass 1 preview for a module."""
        module_name = node.module_name
        print(f"  [Pass 1] Generating preview for {module_name}...")

        port_summary = self.resolver.get_port_summary(module_name)
        children_summary = ", ".join(c.module_name for c in node.children.values()) or "无子模块"
        
        graph_description = self._get_graph_description(module_name, include_source=True)

        prompt = PASS1_PROMPT.format(
            module_name=module_name,
            ancestor_context=ancestor_context,
            port_summary=port_summary,
            children_summary=children_summary,
            graph_description=graph_description
        )

        preview, token_stats = await self.llm.generate(
            PASS1_SYSTEM, prompt, 
            log_path=self._log_path(module_name, "pass1_preview"),
            disable_thinking=True
        )

        self._token_stats[module_name].append({"pass": "pass1", **token_stats})
        self.tracker.update_pass1(module_name, preview)
        self._save_module_file(module_name, "preview.md", preview)
        self._save_metadata(node)

    # ==================== Pass 1.5: Block Documentation ====================

    async def _run_pass1_5(self, node: Any):
        """Pass 1.5: Block-level documentation generation."""
        module_name = node.module_name

        if self._should_skip(module_name):
            return
        if self.max_modules > 0 and module_name not in self._processed["pass1"]:
            return
        if module_name in self._processed["pass1_5"]:
            for child in node.children.values():
                await self._run_pass1_5(child)
            return
        if self.tracker.is_pass1_5_done(module_name):
            self._processed["pass1_5"].add(module_name)
            for child in node.children.values():
                await self._run_pass1_5(child)
            return

        print(f"  [Pass 1.5] Generating block docs for {module_name}...")
        
        block_descriptions = "// 无逻辑块文档"
        block_docs_dict = {}

        if module_name in self._graphs:
            block_gen = BlockDocGenerator(
                llm=self.llm,
                resolver=self.resolver,
                module_name=module_name,
                graph=self._graphs[module_name],
                log_path=self._log_path(module_name, "block"),
                block_doc_threshold=self.block_doc_threshold
            )
            block_docs_dict = await block_gen.generate_all()
            
            if block_docs_dict:
                parts = [f"### {bid}:\n{doc}\n" for bid, doc in sorted(block_docs_dict.items())]
                block_descriptions = "\n".join(parts)
        else:
            source = self.resolver.read_source(module_name) or "Source not found."
            block_descriptions = f"```verilog\n{source}\n```"

        self._save_module_file(module_name, "block_docs.md", block_descriptions)
        if block_docs_dict:
            self._save_json(module_name, "block_docs.json", block_docs_dict)

        self.tracker.update_pass1_5(module_name, "done")
        self._processed["pass1_5"].add(module_name)
        
        for child in node.children.values():
            await self._run_pass1_5(child)

    # ==================== Pass 2: Synthesis Documentation ====================

    async def _run_pass2(self, node: Any) -> str:
        """Pass 2: Bottom-up synthesis documentation."""
        module_name = node.module_name

        if self._should_skip(module_name):
            return f"Skipped {module_name}"
        if self.max_modules > 0 and module_name not in self._processed["pass1"]:
            return "Skipped due to limit"

        # Recurse children first
        children_descs = {}
        for child in node.children.values():
            children_descs[child.module_name] = await self._run_pass2(child)

        if module_name in self._processed["pass2"]:
            return self.tracker.get_state(module_name).pass2_description or ""
        if self.tracker.is_pass2_done(module_name):
            self._processed["pass2"].add(module_name)
            return self.tracker.get_state(module_name).pass2_description or ""

        # Check all dependencies
        for p in ["pass2_1", "pass2_2", "pass2_3", "pass2_4", "pass2_5", "pass2_6"]:
            if not self._is_pass_done(module_name, p):
                print(f"  [Pass 2] Skipping {module_name} ({p} not completed)")
                self._processed["pass2"].add(module_name)
                return ""

        is_leaf = len(node.children) == 0
        description = await self._generate_synthesis_doc(node, children_descs, is_leaf)
        
        self._processed["pass2"].add(module_name)
        return description

    async def _generate_synthesis_doc(
        self, node: Any, children_descs: Dict[str, str], is_leaf: bool
    ) -> str:
        """Generate Pass 2 synthesis documentation."""
        module_name = node.module_name
        print(f"  [Pass 2] Generating synthesis doc for {module_name}...")

        state = self.tracker.get_state(module_name)
        preview = state.pass1_overview or ""
        port_summary = self.resolver.get_port_summary(module_name)
        graph_description = self._get_graph_description(module_name, include_source=True)

        # Collect all pass results
        data = {
            "design_highlights": state.pass2_1_highlights or "无设计亮点",
            "flowchart": state.pass2_2_mermaid or "无流程图",
            "interface_spec": state.pass2_3_interface or "无接口规范",
            "functional_desc": state.pass2_4_functional or "无功能详细描述",
            "register_desc": state.pass2_5_register or "无寄存器描述",
            "timing_cdc_desc": state.pass2_6_timing_cdc or "无时序约束",
        }

        if is_leaf:
            prompt = PASS2_LEAF_PROMPT.format(
                module_name=module_name,
                preview=preview,
                port_summary=port_summary,
                graph_description=graph_description,
                **data
            )
            system = PASS2_LEAF_SYSTEM
        else:
            children_summary = "\n\n".join(
                f"## 子模块 {name}:\n{desc}" for name, desc in children_descs.items()
            )
            prompt = PASS2_NONLEAF_PROMPT.format(
                module_name=module_name,
                preview=preview,
                children_descriptions=children_summary,
                graph_description=graph_description,
                **data
            )
            system = PASS2_NONLEAF_SYSTEM

        description, token_stats = await self.llm.generate(
            system, prompt,
            log_path=self._log_path(module_name, "pass2_description"),
            tools_enabled=False
        )

        self._token_stats[module_name].append({"pass": "pass2", **token_stats})
        self.tracker.update_pass2(module_name, description)
        self._save_module_file(module_name, "description.md", description)
        self._save_metadata(node)
        return description

    # ==================== Pass 2.x Generators (used by PassRunner) ====================

    async def _gen_design_highlights(self, node: Any, children_previews: Dict[str, str]) -> str:
        """Generate Pass 2.1 design highlights."""
        module_name = node.module_name
        state = self.tracker.get_state(module_name)
        
        preview = state.pass1_overview or "无功能预览"
        port_summary = self.resolver.get_port_summary(module_name)
        graph_description = self._get_graph_description(module_name, include_source=True)
        block_descriptions = self._get_block_summaries(module_name)
        
        children_str = self._format_children_summaries(children_previews, 10)

        prompt = PASS2_1_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            block_descriptions=block_descriptions,
            children_previews=children_str
        )

        highlights, token_stats = await self.llm.generate(
            PASS2_1_SYSTEM, prompt,
            log_path=self._log_path(module_name, "pass2_1_highlights"),
            tools_enabled=False
        )

        self._token_stats[module_name].append({"pass": "pass2_1", **token_stats})
        return highlights

    async def _gen_flowchart(self, node: Any, children_summaries: Dict[str, str]) -> str:
        """Generate Pass 2.2 Mermaid flowchart."""
        module_name = node.module_name
        state = self.tracker.get_state(module_name)
        graph = self._graphs[module_name]
        
        preview = state.pass1_overview or "无预览"
        port_summary = self.resolver.get_port_summary(module_name)
        graph_description = self._format_graph_description(graph, include_source=True)
        
        block_file = self.modules_dir / module_name / "block_docs.md"
        block_descriptions = block_file.read_text() if block_file.exists() else "无逻辑块文档"
        
        children_str = self._format_children_summaries(children_summaries, lines=0)  # Full content for mermaid

        prompt = PASS2_2_MODULE_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            block_descriptions=block_descriptions,
            children_summaries=children_str
        )

        output, token_stats = await self.llm.generate(
            PASS2_2_MODULE_SYSTEM, prompt,
            log_path=self._log_path(module_name, "pass2_2_module"),
            tools_enabled=False
        )

        # Extract mermaid code
        mermaid_code, other = self.llm._extract_mermaid_and_content(output)
        if mermaid_code:
            self._save_module_file(module_name, "flowchart.mmd", mermaid_code)
        if other:
            self._append_to_log(module_name, "pass2_2_module", f"\n## Additional Content\n{other}")

        self._token_stats[module_name].append({"pass": "pass2_2", **token_stats})
        self._mermaid_summaries[module_name] = mermaid_code
        return mermaid_code

    async def _gen_interface_spec(self, node: Any, children_interfaces: Dict[str, str]) -> str:
        """Generate Pass 2.3 interface specification."""
        module_name = node.module_name
        state = self.tracker.get_state(module_name)
        
        prompt = PASS2_3_INTERFACE_PROMPT.format(
            module_name=module_name,
            preview=state.pass1_overview or "无功能预览",
            port_summary=self.resolver.get_port_summary(module_name),
            graph_description=self._get_graph_description(module_name, include_source=False),
            children_interfaces=self._format_children_summaries(children_interfaces, 15)
        )

        interface_doc, token_stats = await self.llm.generate(
            PASS2_3_INTERFACE_SYSTEM, prompt,
            log_path=self._log_path(module_name, "pass2_3_interface"),
            tools_enabled=False
        )

        self._token_stats[module_name].append({"pass": "pass2_3", **token_stats})
        return interface_doc

    async def _gen_functional_desc(self, node: Any, children_functional: Dict[str, str]) -> str:
        """Generate Pass 2.4 functional description."""
        module_name = node.module_name
        state = self.tracker.get_state(module_name)
        
        prompt = PASS2_4_FUNCTIONAL_PROMPT.format(
            module_name=module_name,
            preview=state.pass1_overview or "无功能预览",
            port_summary=self.resolver.get_port_summary(module_name),
            graph_description=self._get_graph_description(module_name, include_source=True),
            block_descriptions=self._get_block_summaries(module_name),
            children_functional=self._format_children_summaries(children_functional, 20)
        )

        func_doc, token_stats = await self.llm.generate(
            PASS2_4_FUNCTIONAL_SYSTEM, prompt,
            log_path=self._log_path(module_name, "pass2_4_functional"),
            tools_enabled=False
        )

        self._token_stats[module_name].append({"pass": "pass2_4", **token_stats})
        return func_doc

    async def _gen_register_desc(self, node: Any, children_register: Dict[str, str]) -> str:
        """Generate Pass 2.5 register description."""
        module_name = node.module_name
        state = self.tracker.get_state(module_name)
        
        prompt = PASS2_5_REGISTER_PROMPT.format(
            module_name=module_name,
            preview=state.pass1_overview or "无功能预览",
            port_summary=self.resolver.get_port_summary(module_name),
            graph_description=self._get_graph_description(module_name, include_source=True),
            children_register=self._format_children_summaries(children_register, 20)
        )

        reg_doc, token_stats = await self.llm.generate(
            PASS2_5_REGISTER_SYSTEM, prompt,
            log_path=self._log_path(module_name, "pass2_5_register"),
            tools_enabled=False
        )

        self._token_stats[module_name].append({"pass": "pass2_5", **token_stats})
        return reg_doc

    async def _gen_timing_cdc(self, node: Any, children_timing: Dict[str, str]) -> str:
        """Generate Pass 2.6 timing and CDC documentation."""
        module_name = node.module_name
        state = self.tracker.get_state(module_name)
        
        prompt = PASS2_6_TIMING_CDC_PROMPT.format(
            module_name=module_name,
            preview=state.pass1_overview or "无功能预览",
            port_summary=self.resolver.get_port_summary(module_name),
            graph_description=self._get_graph_description(module_name, include_source=True),
            children_timing=self._format_children_summaries(children_timing, 20)
        )

        timing_doc, token_stats = await self.llm.generate(
            PASS2_6_TIMING_CDC_SYSTEM, prompt,
            log_path=self._log_path(module_name, "pass2_6_timing"),
            tools_enabled=False
        )

        self._token_stats[module_name].append({"pass": "pass2_6", **token_stats})
        return timing_doc

    # ==================== Helper Methods ====================

    def _get_graph_description(self, module_name: str, include_source: bool = True) -> str:
        """Get graph description for a module, with fallback to source."""
        if module_name in self._graphs:
            return self._format_graph_description(self._graphs[module_name], include_source)
        source = self.resolver.read_source(module_name, self.max_source_lines) or "Source not found."
        return f"```verilog\n{source}\n```"

    def _get_block_summaries(self, module_name: str) -> str:
        """Get block summaries from Pass 1.5 output."""
        block_file = self.modules_dir / module_name / "block_docs.json"
        if not block_file.exists():
            return "无逻辑块摘要"
        
        try:
            with open(block_file, 'r', encoding='utf-8') as f:
                block_docs = json.load(f)
            
            parts = []
            for bid, doc in sorted(block_docs.items()):
                lines = doc.split('\n')[:3]
                summary = '\n'.join(lines) if lines else "功能描述未生成"
                parts.append(f"### {bid}:\n{summary}{'...' if len(doc.split(chr(10))) > 3 else ''}\n")
            
            parts.append(f"\n**说明**: 可使用 `read_block_source` 工具查看具体实现。")
            return "\n".join(parts)
        except Exception as e:
            print(f"[WARN] Failed to read block summaries: {e}")
            return "读取逻辑块摘要失败"

    def _format_children_summaries(self, children: Dict[str, str], lines: int = 10) -> str:
        """Format children summaries, truncating to N lines each."""
        if not children:
            return "无子模块"
        
        parts = []
        for name, content in sorted(children.items()):
            if lines > 0:
                content_lines = content.split('\n')
                summary = '\n'.join(content_lines[:lines])
                if len(content_lines) > lines:
                    summary += "\n\n[... 更多内容见子模块文档]"
            else:
                summary = content
            parts.append(f"## 子模块 {name}:\n{summary}\n")
        
        return "\n".join(parts)

    def _format_graph_description(self, graph: SimplifiedGraph, include_source: bool) -> str:
        """Format SimplifiedGraph as text description."""
        # Build adjacency
        predecessors: Dict[str, List[str]] = {n: [] for n in 
            set(graph.proc_nodes) | set(graph.comb_nodes) | set(graph.io_ports) | set(graph.submodules)}
        successors: Dict[str, List[str]] = {n: [] for n in predecessors}
        
        for src, dst in graph.edges:
            if src in predecessors and dst in predecessors:
                successors[src].append(dst)
                predecessors[dst].append(src)

        def fmt_conn(nodes: List[str]) -> str:
            if not nodes:
                return "None"
            names = []
            for n in nodes:
                if n in graph.proc_nodes:
                    names.append(f"{n}(PROC)")
                elif n in graph.comb_nodes:
                    names.append(f"{n}({graph.comb_nodes[n].comb_type})")
                elif n in graph.submodules:
                    names.append(f"{graph.submodules[n]}(submodule)")
                elif n in graph.io_ports:
                    names.append(f"{graph.io_ports[n][:30]}(port)")
                else:
                    names.append(n)
            return ", ".join(names)

        def get_source(block_id: str) -> str:
            if block_id in graph.proc_nodes:
                loc = graph.proc_nodes[block_id].source_location
                if loc:
                    return self.resolver.read_block_source([loc])
            elif block_id in graph.comb_nodes:
                locs = graph.comb_nodes[block_id].source_locations
                if locs:
                    return self.resolver.read_block_source(locs)
            return ""

        # Topological sort
        all_blocks = set(graph.proc_nodes) | set(graph.comb_nodes)
        adj = defaultdict(list)
        in_degree = {b: 0 for b in all_blocks}
        
        for src, dst in graph.edges:
            if src in all_blocks and dst in all_blocks:
                adj[src].append(dst)
                in_degree[dst] += 1

        queue = sorted([b for b in all_blocks if in_degree[b] == 0])
        topo = []
        while queue:
            node = queue.pop(0)
            topo.append(node)
            for nb in sorted(adj[node]):
                in_degree[nb] -= 1
                if in_degree[nb] == 0:
                    queue.append(nb)
                    queue.sort()
        topo.extend(sorted(all_blocks - set(topo)))

        # Format blocks
        block_parts = []
        for bid in topo:
            if bid in graph.proc_nodes:
                proc = graph.proc_nodes[bid]
                if proc.source_location:
                    fn = proc.source_location.file_path.split('/')[-1]
                    header = f"- **{bid}** [PROC] ({fn}:{proc.source_location.start_line}-{proc.source_location.end_line})"
                else:
                    header = f"- **{bid}** [PROC]"
            elif bid in graph.comb_nodes:
                comb = graph.comb_nodes[bid]
                loc = comb.location_info.get_display_label().replace("\\n", ", ")
                header = f"- **{bid}** [{comb.comb_type}, {comb.node_count} nodes] ({loc})"
            else:
                continue

            lines = [header]
            lines.append(f"  - Inputs from: {fmt_conn(predecessors.get(bid, []))}")
            lines.append(f"  - Outputs to: {fmt_conn(successors.get(bid, []))}")
            
            if include_source:
                src = get_source(bid)
                if src:
                    lines.append("  - Source:")
                    lines.append("    ```verilog")
                    lines.extend(f"    {ln}" for ln in src.strip().split('\n'))
                    lines.append("    ```")
            
            block_parts.append("\n".join(lines))

        parts = []
        if block_parts:
            parts.append("## Circuit Blocks (topological order):\n\n" + "\n\n".join(block_parts))

        if graph.submodules:
            parts.append("\n## Submodule Instances:")
            for sid, name in sorted(graph.submodules.items()):
                parts.append(f"- **{name}")
                parts.append(f"  - Inputs from: {fmt_conn(predecessors.get(sid, []))}")
                parts.append(f"  - Outputs to: {fmt_conn(successors.get(sid, []))}")

        return "\n".join(parts) if parts else "No simplified structure information"

    # ==================== File I/O Utilities ====================

    def _log_path(self, module_name: str, pass_name: str) -> str:
        """Get debug log path for a module/pass."""
        # Save debug files to debug subdirectory: debug/<module_name>/debug_<pass_name>.md
        module_debug_dir = self.debug_dir / module_name
        module_debug_dir.mkdir(parents=True, exist_ok=True)
        return str(module_debug_dir / f"debug_{pass_name}.md")

    def _save_module_file(self, module_name: str, filename: str, content: str):
        """Save content to module directory."""
        module_dir = self.modules_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)
        (module_dir / filename).write_text(content, encoding='utf-8')

    def _save_json(self, module_name: str, filename: str, data: Any):
        """Save JSON data to module directory."""
        module_dir = self.modules_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)
        with open(module_dir / filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _append_to_log(self, module_name: str, pass_name: str, content: str):
        """Append content to debug log file."""
        # Use debug subdirectory for debug files
        log_file = self.debug_dir / module_name / f"debug_{pass_name}.md"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"[WARN] Failed to append to log: {e}")

    def _save_metadata(self, node: Any):
        """Save module metadata to JSON."""
        module_name = node.module_name
        metadata = {
            "module_name": module_name,
            "instances": [node.instance_name],
            "ports": self.resolver.get_port_summary(module_name).split('\n'),
            "source_path": self.resolver.resolve_path(module_name),
            "children": [c.module_name for c in node.children.values()],
            "token_stats": self._token_stats.get(module_name, [])
        }
        self._save_json(module_name, "metadata.json", metadata)
