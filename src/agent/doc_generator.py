import os
import asyncio
import json
import fnmatch
from typing import Dict, List, Optional, Any
from pathlib import Path

from .llm_backend import LLMBackend
from .source_resolver import SourceResolver
from .progress_tracker import ProgressTracker
from .block_doc_generator import BlockDocGenerator
from schematic.simplifier import SimplifiedGraph
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
        max_modules: int = 0,
        skip_modules: Optional[List[str]] = None,
        schematic_gen: Optional[Any] = None
    ):
        self.hierarchy = hierarchy
        self.llm = llm
        self.resolver = resolver
        self.tracker = tracker
        self.output_dir = Path(output_dir)
        self.modules_dir = self.output_dir / "modules"
        self.max_source_lines = max_source_lines
        self.max_modules = max_modules
        self.skip_modules = skip_modules or []
        self.schematic_gen = schematic_gen
        self._graphs: Dict[str, SimplifiedGraph] = {}  # module_name -> SimplifiedGraph
        self._processed_pass1: set = set()
        self._processed_pass2: set = set()
        self._processed_pass2_5: set = set()
        self._mermaid_summaries: Dict[str, str] = {}  # module_name -> 精简版 mermaid

    def _should_skip(self, module_name: str) -> bool:
        """Check if a module should be skipped based on skip_modules patterns."""
        for pattern in self.skip_modules:
            if fnmatch.fnmatch(module_name, pattern):
                return True
        return False

    async def run(self):
        """Run the full documentation generation process."""
        print(f"[INFO] Starting Docor Agent on {self.hierarchy.module_name}...")

        # Pass 0: Precompute simplified graphs
        if self.schematic_gen:
            print("[INFO] Running Pass 0: Precomputing SimplifiedGraphs...")
            self._precompute_graphs(self.hierarchy)
            print(f"[INFO] Precomputed {len(self._graphs)} simplified graphs")

        # Pass 1: Top-down Preview Generation (renamed from Overview)
        print("[INFO] Running Pass 1: Top-down Preview Generation...")
        await self._run_pass1(self.hierarchy, "该模块是设计的顶层模块。")

        # Pass 2: Bottom-up Detailed Documentation (with block-level docs)
        print("[INFO] Running Pass 2: Bottom-up Detailed Documentation...")
        await self._run_pass2(self.hierarchy, "该模块是设计的顶层模块。")

        # Pass 2.5: Mermaid Flowchart Generation
        print("[INFO] Running Pass 2.5: Mermaid Flowchart Generation...")
        await self._run_pass2_5(self.hierarchy)

        print(f"[INFO] Documentation generation complete. Results in {self.modules_dir}")

    def _precompute_graphs(self, node: Any):
        """Recursively precompute SimplifiedGraphs for all modules.

        Args:
            node: HierarchyNode to traverse
        """
        module_name = node.module_name

        # Skip if already computed or should be skipped
        if module_name in self._graphs or self._should_skip(module_name):
            return

        # Try to generate simplified graph
        if self.schematic_gen:
            try:
                graph = self.schematic_gen.generate_simplified_graph(module_name)
                if graph:
                    self._graphs[module_name] = graph
                else:
                    if self.max_modules > 0 and len(self._graphs) < 5:  # Only log for first few
                        print(f"[INFO] Module {module_name}: SimplifiedGraph unavailable (fallback to source)")
            except Exception as e:
                print(f"[WARN] Module {module_name}: SimplifiedGraph generation failed: {e}")

        # Recurse to children
        for child in node.children.values():
            self._precompute_graphs(child)

    async def _run_pass1(self, node: Any, ancestor_context: str):
        """Pass 1: Top-down (Overview)"""
        module_name = node.module_name
        
        # Skip logic
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 1] Skipping module {module_name} (matches skip pattern)")
            return

        # Check limit
        if self.max_modules > 0 and len(self._processed_pass1) >= self.max_modules:
            if node.module_name not in self._processed_pass1:
                return

        module_name = node.module_name

        if module_name not in self._processed_pass1:
            if not self.tracker.is_pass1_done(module_name):
                await self._generate_module_preview(node, ancestor_context)
            self._processed_pass1.add(module_name)

        # Get current preview to pass down (stored as pass1_overview internally)
        state = self.tracker.get_state(module_name)
        current_preview = state.pass1_overview or ""
        
        # For children, pass current preview as context
        for child in node.children.values():
            await self._run_pass1(child, current_preview)

    def _format_graph_description(self, graph: SimplifiedGraph) -> str:
        """Format SimplifiedGraph as a text description for LLM.

        Args:
            graph: SimplifiedGraph object

        Returns:
            Formatted text description
        """
        parts = []

        # PROC blocks
        if graph.proc_nodes:
            parts.append("## 时序逻辑块 (PROC):")
            for proc_id, proc_info in sorted(graph.proc_nodes.items()):
                if proc_info.source_location:
                    loc = proc_info.source_location
                    filename = loc.file_path.split('/')[-1] if '/' in loc.file_path else loc.file_path
                    parts.append(f"- {proc_id}: {filename}:{loc.start_line}-{loc.end_line}")
                else:
                    parts.append(f"- {proc_id}")

        # COMB blocks
        if graph.comb_nodes:
            parts.append("\n## 组合逻辑块 (COMB):")
            for comb_id, comb_info in sorted(graph.comb_nodes.items()):
                type_label = comb_info.comb_type
                assoc_info = f" (关联 {comb_info.associated_proc})" if comb_info.associated_proc else ""
                node_info = f" ({comb_info.node_count}个节点)"
                loc_label = comb_info.location_info.get_display_label().replace("\\n", ", ")
                if loc_label:
                    parts.append(f"- {comb_id} [{type_label}{assoc_info}{node_info}]: {loc_label}")
                else:
                    parts.append(f"- {comb_id} [{type_label}{assoc_info}{node_info}]")

        # I/O ports
        if graph.io_ports:
            parts.append("\n## I/O 端口:")
            port_items = [f"{label.strip()}" for port_id, label in sorted(graph.io_ports.items())]
            parts.append("- " + ", ".join(port_items))

        # Submodules
        if graph.submodules:
            parts.append("\n## 子模块实例:")
            for submod_id, instance_name in sorted(graph.submodules.items()):
                parts.append(f"- {instance_name}")

        # Connectivity overview
        if graph.edges:
            parts.append("\n## 连接关系:")
            # Sample a few representative edges (to avoid overwhelming the LLM)
            sample_edges = graph.edges[:10]
            for src, dst in sample_edges:
                src_name = self._resolve_node_name_for_desc(src, graph)
                dst_name = self._resolve_node_name_for_desc(dst, graph)
                parts.append(f"- {src_name} → {dst_name}")
            if len(graph.edges) > 10:
                parts.append(f"- ... (共 {len(graph.edges)} 条连接)")

        return "\n".join(parts) if parts else "无简化结构信息"

    def _resolve_node_name_for_desc(self, node_id: str, graph: SimplifiedGraph) -> str:
        """Resolve node ID to a short name for graph description.

        Args:
            node_id: Node ID
            graph: SimplifiedGraph

        Returns:
            Short node name
        """
        if node_id in graph.proc_nodes:
            return node_id
        if node_id in graph.comb_nodes:
            return graph.comb_nodes[node_id].comb_type
        if node_id in graph.io_ports:
            return graph.io_ports[node_id].strip()[:20]  # Truncate long port names
        if node_id in graph.submodules:
            return graph.submodules[node_id]
        return node_id

    async def _generate_module_preview(self, node: Any, ancestor_context: str):
        module_name = node.module_name
        print(f"  [Pass 1] Generating preview for {module_name}...")

        port_summary = self.resolver.get_port_summary(module_name)
        children_summary = ", ".join([c.module_name for c in node.children.values()]) or "无子模块"

        # Use SimplifiedGraph if available, otherwise fallback to source code
        if module_name in self._graphs:
            graph_description = self._format_graph_description(self._graphs[module_name])
        else:
            # Fallback: use truncated source code
            source_code = self.resolver.read_source(module_name, max_lines=self.max_source_lines) or "Source not found."
            graph_description = f"源代码 (前 {self.max_source_lines} 行):\n```verilog\n{source_code}\n```"
            if module_name not in self._graphs and self.schematic_gen:
                print(f"  [WARN] Module {module_name}: using source fallback (no SimplifiedGraph)")

        prompt = prompts.PASS1_PROMPT.format(
            module_name=module_name,
            ancestor_context=ancestor_context,
            port_summary=port_summary,
            children_summary=children_summary,
            graph_description=graph_description
        )

        log_path = self._module_log_path(module_name, "pass1_preview")
        preview = await self.llm.generate(prompts.PASS1_SYSTEM, prompt, log_path=log_path)

        # Save results (internally still stored as pass1_overview in tracker)
        self.tracker.update_pass1(module_name, preview)
        self._save_module_file(module_name, "preview.md", preview)
        self._save_metadata(node)

    async def _run_pass2(self, node: Any, ancestor_context: str) -> str:
        """Pass 2: Bottom-up (Detailed Doc)"""
        module_name = node.module_name

        # Skip logic
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2] Skipping module {module_name} (matches skip pattern)")
            return f"Skipped module {module_name} (matches skip pattern)"

        # Check limit - only process if Pass 1 processed it
        if self.max_modules > 0 and node.module_name not in self._processed_pass1:
            return "Skipped due to max_modules limit."
        
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
        preview = state.pass1_overview or ""  # Internally stored as pass1_overview
        port_summary = self.resolver.get_port_summary(module_name)

        # Generate block-level docs if SimplifiedGraph is available
        block_descriptions = ""
        if module_name in self._graphs:
            print(f"    [Pass 1.5] Generating block-level docs for {module_name}...")
            graph = self._graphs[module_name]
            block_log_path = self._module_log_path(module_name, "block")
            block_gen = BlockDocGenerator(
                llm=self.llm,
                resolver=self.resolver,
                module_name=module_name,
                graph=graph,
                log_path=block_log_path
            )
            block_docs = await block_gen.generate_all()

            # Format block docs
            if block_docs:
                parts = []
                for block_id, doc_text in sorted(block_docs.items()):
                    parts.append(f"### {block_id}:")
                    parts.append(doc_text)
                    parts.append("")  # Empty line
                block_descriptions = "\n".join(parts)
            else:
                block_descriptions = "// 无逻辑块文档"
        else:
            # Fallback: use source code
            source_code = self.resolver.read_source(module_name) or "Source not found."
            block_descriptions = f"```verilog\n{source_code}\n```"
            print(f"    [WARN] Module {module_name}: using source fallback (no SimplifiedGraph)")

        if is_leaf:
            prompt = prompts.PASS2_LEAF_PROMPT.format(
                module_name=module_name,
                ancestor_context=ancestor_context,
                preview=preview,
                port_summary=port_summary,
                block_descriptions=block_descriptions
            )
            system = prompts.PASS2_LEAF_SYSTEM
        else:
            # Format children descriptions
            children_summary = "\n\n".join([f"## 子模块 {name}:\n{desc}" for name, desc in children_descs.items()])
            prompt = prompts.PASS2_NONLEAF_PROMPT.format(
                module_name=module_name,
                ancestor_context=ancestor_context,
                preview=preview,
                children_descriptions=children_summary,
                block_descriptions=block_descriptions
            )
            system = prompts.PASS2_NONLEAF_SYSTEM

        log_path = self._module_log_path(module_name, "pass2_description")
        description = await self.llm.generate(system, prompt, log_path=log_path)

        self.tracker.update_pass2(module_name, description)
        self._save_module_file(module_name, "description.md", description)
        return description

    def _module_log_path(self, module_name: str, pass_name: str) -> str:
        """Get the debug log path for a specific module and pass.

        Args:
            module_name: Module name
            pass_name: Pass identifier (e.g., "pass1", "pass2", "block")

        Returns:
            Absolute path to the log file
        """
        module_dir = self.modules_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)
        return str(module_dir / f"debug_{pass_name}.md")

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

    # ==================== Pass 2.5: Mermaid Flowchart Generation ====================

    async def _run_pass2_5(self, node: Any) -> str:
        """Pass 2.5: Bottom-up Mermaid flowchart generation.

        Returns the summary mermaid for this module (for parent to use).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.5] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first
        children_summaries = {}
        for child in node.children.values():
            summary = await self._run_pass2_5(child)
            if summary:
                children_summaries[child.module_name] = summary

        # 2. Already done?
        if module_name in self._processed_pass2_5:
            return self._mermaid_summaries.get(module_name, "")

        if self.tracker.is_pass2_5_done(module_name):
            self._processed_pass2_5.add(module_name)
            state = self.tracker.get_state(module_name)
            if state.pass2_5_mermaid:
                self._mermaid_summaries[module_name] = state.pass2_5_mermaid
            return state.pass2_5_mermaid or ""

        # 3. Generate
        if module_name not in self._graphs:
            print(f"  [Pass 2.5] Skipping {module_name} (no SimplifiedGraph)")
            self._processed_pass2_5.add(module_name)
            return ""

        print(f"  [Pass 2.5] Generating flowchart for {module_name}...")
        graph = self._graphs[module_name]

        try:
            # Step A: Block-level flowcharts (concurrent)
            block_fragments = await self._generate_all_block_flowcharts(module_name, graph)

            # Step B: Integrate into full module flowchart
            full_mermaid = await self._generate_module_flowchart(
                module_name, graph, block_fragments, children_summaries
            )
            self._save_module_file(module_name, "flowchart.mmd", full_mermaid)

            # Step C: Generate summary for parent
            summary = await self._generate_summary_flowchart(module_name, full_mermaid)
            self._mermaid_summaries[module_name] = summary

            # Update tracker
            self.tracker.update_pass2_5(module_name, summary)

            self._processed_pass2_5.add(module_name)
            return summary

        except Exception as e:
            print(f"  [ERROR] [Pass 2.5] Failed to generate flowchart for {module_name}: {e}")
            self._processed_pass2_5.add(module_name)
            return ""

    async def _generate_all_block_flowcharts(
        self, module_name: str, graph: SimplifiedGraph
    ) -> Dict[str, str]:
        """Generate flowchart fragments for all PROC and COMB blocks concurrently."""
        tasks = []

        # Generate for all PROC blocks
        for proc_id, proc_info in graph.proc_nodes.items():
            tasks.append(self._generate_block_flowchart(
                module_name, graph, proc_id, "PROC", proc_info
            ))

        # Generate for all COMB blocks
        for comb_id, comb_info in graph.comb_nodes.items():
            tasks.append(self._generate_block_flowchart(
                module_name, graph, comb_id, comb_info.comb_type, comb_info
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter exceptions and build result dict
        block_fragments = {}
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                block_id, fragment = result
                block_fragments[block_id] = fragment
            elif isinstance(result, Exception):
                print(f"  [WARN] Block flowchart generation failed: {result}")

        return block_fragments

    async def _generate_block_flowchart(
        self, module_name: str, graph: SimplifiedGraph,
        block_id: str, block_type: str, block_info: Any
    ) -> tuple:
        """Generate Mermaid flowchart fragment for a single block."""
        # Read source code snippet
        if hasattr(block_info, 'source_location') and block_info.source_location:
            # PROC block
            source_snippet = self.resolver.read_block_source([block_info.source_location])
        elif hasattr(block_info, 'source_locations') and block_info.source_locations:
            # COMB block
            source_snippet = self.resolver.read_block_source(block_info.source_locations)
        else:
            source_snippet = "// Source location not available"

        # Get connectivity info
        upstream, downstream = self._get_connectivity_with_signals(block_id, graph)

        # Format prompt
        prompt = prompts.PASS2_5_BLOCK_PROMPT.format(
            module_name=module_name,
            block_id=block_id,
            block_type=block_type,
            upstream=upstream,
            downstream=downstream,
            source_snippet=source_snippet
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_5_blocks")
        fragment = await self.llm.generate(prompts.PASS2_5_BLOCK_SYSTEM, prompt, log_path=log_path)

        return (block_id, fragment)

    def _get_connectivity_with_signals(self, block_id: str, graph: SimplifiedGraph) -> tuple:
        """Extract upstream and downstream connectivity with signal names."""
        upstream_ids = [src for src, dst in graph.edges if dst == block_id]
        downstream_ids = [dst for src, dst in graph.edges if src == block_id]

        upstream_names = [self._resolve_node_name_with_signal(node_id, graph) for node_id in upstream_ids]
        downstream_names = [self._resolve_node_name_with_signal(node_id, graph) for node_id in downstream_ids]

        upstream_str = ", ".join(upstream_names) if upstream_names else "无"
        downstream_str = ", ".join(downstream_names) if downstream_names else "无"

        return upstream_str, downstream_str

    def _resolve_node_name_with_signal(self, node_id: str, graph: SimplifiedGraph) -> str:
        """Resolve node ID to a name with signal info (for connectivity description)."""
        if node_id in graph.proc_nodes:
            return f"PROC({node_id})"
        if node_id in graph.comb_nodes:
            comb_info = graph.comb_nodes[node_id]
            return f"{comb_info.comb_type}({node_id})"
        if node_id in graph.io_ports:
            port_label = graph.io_ports[node_id]
            return f"端口: {port_label}"
        if node_id in graph.submodules:
            instance_name = graph.submodules[node_id]
            return f"子模块: {instance_name}"
        return node_id

    async def _generate_module_flowchart(
        self, module_name: str, graph: SimplifiedGraph,
        block_fragments: Dict[str, str], children_summaries: Dict[str, str]
    ) -> str:
        """Integrate block fragments and child summaries into full module flowchart."""
        # Format port list
        port_lines = []
        for port_id, label in sorted(graph.io_ports.items()):
            port_lines.append(f"  {label}")
        port_list = "\n".join(port_lines) if port_lines else "无端口"

        # Format block fragments
        fragment_lines = []
        for block_id, fragment in sorted(block_fragments.items()):
            fragment_lines.append(f"## Block {block_id}:\n{fragment}\n")
        block_fragments_str = "\n".join(fragment_lines) if fragment_lines else "无 block 片段"

        # Format children summaries
        child_lines = []
        for child_name, summary in sorted(children_summaries.items()):
            child_lines.append(f"## 子模块 {child_name}:\n{summary}\n")
        children_str = "\n".join(child_lines) if child_lines else "无子模块"

        # Format edges summary
        edge_lines = []
        for src, dst in graph.edges[:20]:  # Sample first 20 edges
            src_name = self._resolve_node_name_for_desc(src, graph)
            dst_name = self._resolve_node_name_for_desc(dst, graph)
            edge_lines.append(f"  {src_name} → {dst_name}")
        if len(graph.edges) > 20:
            edge_lines.append(f"  ... (共 {len(graph.edges)} 条连接)")
        edges_summary = "\n".join(edge_lines) if edge_lines else "无连接"

        # Format prompt
        prompt = prompts.PASS2_5_MODULE_PROMPT.format(
            module_name=module_name,
            port_list=port_list,
            block_fragments=block_fragments_str,
            children_summaries=children_str,
            edges_summary=edges_summary
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_5_module")
        full_mermaid = await self.llm.generate(prompts.PASS2_5_MODULE_SYSTEM, prompt, log_path=log_path)

        return full_mermaid

    async def _generate_summary_flowchart(self, module_name: str, full_mermaid: str) -> str:
        """Generate simplified flowchart summary for parent module."""
        port_summary = self.resolver.get_port_summary(module_name)

        prompt = prompts.PASS2_5_SUMMARY_PROMPT.format(
            module_name=module_name,
            full_mermaid=full_mermaid,
            port_summary=port_summary
        )

        log_path = self._module_log_path(module_name, "pass2_5_summary")
        summary = await self.llm.generate(prompts.PASS2_5_SUMMARY_SYSTEM, prompt, log_path=log_path)

        return summary
