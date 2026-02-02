import os
import json
import fnmatch
from collections import defaultdict
from typing import Dict, List, Optional, Any
from pathlib import Path

from .llm_backend import LLMBackend
from .source_resolver import SourceResolver
from .progress_tracker import ProgressTracker
from .block_doc_generator import BlockDocGenerator
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
    PASS2_5_MODULE_SYSTEM,
    PASS2_5_MODULE_PROMPT,
)

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
        schematic_gen: Optional[Any] = None,
        block_doc_threshold: int = 64
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
        self.block_doc_threshold = block_doc_threshold
        self._graphs: Dict[str, SimplifiedGraph] = {}  # module_name -> SimplifiedGraph
        self._processed_pass1: set = set()
        self._processed_pass2: set = set()
        self._processed_pass2_1: set = set()  # Track Pass 2.1 completion
        self._processed_pass2_5: set = set()
        self._mermaid_summaries: Dict[str, str] = {}  # module_name -> 精简版 mermaid
        self._token_stats: Dict[str, List[Dict[str, int]]] = {}  # module_name -> list of token stats per pass

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
            
            # Update OpenAI backend context if it supports it
            if hasattr(self.llm, 'set_context'):
                self.llm.set_context(resolver=self.resolver, graphs=self._graphs)
                print("[INFO] Updated LLM backend with graphs for function calling")

        # Pass 1: Top-down Preview Generation
        print("[INFO] Running Pass 1: Top-down Preview Generation...")
        await self._run_pass1(self.hierarchy, "该模块是设计的顶层模块。")

        # Pass 1.5: Block-level Documentation (extracted from old Pass 2)
        print("[INFO] Running Pass 1.5: Block-level Documentation...")
        await self._run_pass1_5(self.hierarchy)

        # Pass 2.1 + Pass 2.5: Run in parallel (both depend only on Pass 1 + Pass 1.5)
        print("[INFO] Running Pass 2.1 + Pass 2.5 in parallel...")
        import asyncio
        await asyncio.gather(
            self._run_pass2_1(self.hierarchy),
            self._run_pass2_5(self.hierarchy)
        )

        # Pass 2: Bottom-up Synthesis Documentation (depends on Pass 2.1 + Pass 2.5)
        print("[INFO] Running Pass 2: Synthesis Documentation...")
        await self._run_pass2(self.hierarchy)

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

    async def _run_pass1_5(self, node: Any):
        """Pass 1.5: Block-level documentation generation (extracted from old Pass 2).

        Depends only on Pass 0 (SimplifiedGraph) and SourceResolver, not on Pass 1.
        """
        module_name = node.module_name

        # Skip logic
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 1.5] Skipping module {module_name} (matches skip pattern)")
            return

        # Check limit
        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return

        # Check if already done
        if self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 1.5] Module {module_name} already has block docs")
            return

        print(f"  [Pass 1.5] Generating block-level docs for {module_name}...")

        # Generate block-level docs if SimplifiedGraph is available
        block_descriptions = ""
        block_docs_dict = {}

        if module_name in self._graphs:
            graph = self._graphs[module_name]
            block_log_path = self._module_log_path(module_name, "block")
            block_gen = BlockDocGenerator(
                llm=self.llm,
                resolver=self.resolver,
                module_name=module_name,
                graph=graph,
                log_path=block_log_path,
                block_doc_threshold=self.block_doc_threshold
            )
            block_docs_dict = await block_gen.generate_all()

            # Format block docs for markdown
            if block_docs_dict:
                parts = []
                for block_id, doc_text in sorted(block_docs_dict.items()):
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

        # Save block docs (both markdown and JSON)
        self._save_module_file(module_name, "block_docs.md", block_descriptions)
        if block_docs_dict:
            import json
            module_dir = self.modules_dir / module_name
            module_dir.mkdir(parents=True, exist_ok=True)
            with open(module_dir / "block_docs.json", 'w', encoding='utf-8') as f:
                json.dump(block_docs_dict, f, indent=2, ensure_ascii=False)

        # Update tracker
        self.tracker.update_pass1_5(module_name, "done")

        # Recurse to children
        for child in node.children.values():
            await self._run_pass1_5(child)

    def _format_block_sources_topological(self, graph: SimplifiedGraph, module_name: str) -> str:
        """Format block source code in topological order.

        Args:
            graph: SimplifiedGraph object
            module_name: Module name for source resolution

        Returns:
            Formatted source code sections for all blocks
        """
        # Build adjacency list for topological sort
        adj = defaultdict(list)
        in_degree = defaultdict(int)
        all_blocks = set()

        # Add all PROC and COMB blocks
        for proc_id in graph.proc_nodes.keys():
            all_blocks.add(proc_id)
            in_degree[proc_id] = 0

        for comb_id in graph.comb_nodes.keys():
            all_blocks.add(comb_id)
            in_degree[comb_id] = 0

        # Build graph from edges (only between blocks, not I/O ports)
        for src, dst in graph.edges:
            if src in all_blocks and dst in all_blocks:
                adj[src].append(dst)
                in_degree[dst] = in_degree.get(dst, 0) + 1

        # Topological sort (Kahn's algorithm)
        queue = [node for node in all_blocks if in_degree[node] == 0]
        topo_order = []

        while queue:
            # Sort for deterministic order
            queue.sort()
            node = queue.pop(0)
            topo_order.append(node)

            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If there are remaining nodes (cycle or disconnected), add them sorted
        remaining = sorted(all_blocks - set(topo_order))
        topo_order.extend(remaining)

        # Format source code for each block
        parts = []
        for block_id in topo_order:
            if block_id in graph.proc_nodes:
                proc_info = graph.proc_nodes[block_id]
                if proc_info.source_location:
                    source = self.resolver.read_block_source([proc_info.source_location])
                    loc = proc_info.source_location
                    filename = loc.file_path.split('/')[-1] if '/' in loc.file_path else loc.file_path
                    parts.append(f"### PROC {block_id} ({filename}:{loc.start_line}-{loc.end_line}):")
                    parts.append(f"```verilog\n{source}\n```\n")
            elif block_id in graph.comb_nodes:
                comb_info = graph.comb_nodes[block_id]
                if comb_info.source_locations:
                    source = self.resolver.read_block_source(comb_info.source_locations)
                    loc_label = comb_info.location_info.get_display_label().replace("\\n", ", ")
                    type_label = comb_info.comb_type
                    assoc_info = f" (关联 {comb_info.associated_proc})" if comb_info.associated_proc else ""
                    parts.append(f"### COMB {block_id} [{type_label}{assoc_info}] ({loc_label}):")
                    parts.append(f"```verilog\n{source}\n```\n")

        return "\n".join(parts) if parts else "无逻辑块源代码"

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

        # Submodules
        if graph.submodules:
            parts.append("\n## 子模块实例:")
            for submod_id, instance_name in sorted(graph.submodules.items()):
                parts.append(f"- {instance_name}")

        # Connectivity (full edge list for topology understanding)
        if graph.edges:
            parts.append("\n## 连接关系:")
            for src, dst in graph.edges:
                src_name = self._resolve_node_name_for_desc(src, graph)
                dst_name = self._resolve_node_name_for_desc(dst, graph)
                parts.append(f"- {src_name} → {dst_name}")

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
            block_sources = self._format_block_sources_topological(self._graphs[module_name], module_name)
        else:
            # Fallback: use truncated source code
            source_code = self.resolver.read_source(module_name, max_lines=self.max_source_lines) or "Source not found."
            graph_description = f"源代码 (前 {self.max_source_lines} 行):\n```verilog\n{source_code}\n```"
            block_sources = ""  # No block sources available
            if module_name not in self._graphs and self.schematic_gen:
                print(f"  [WARN] Module {module_name}: using source fallback (no SimplifiedGraph)")

        prompt = PASS1_PROMPT.format(
            module_name=module_name,
            ancestor_context=ancestor_context,
            port_summary=port_summary,
            children_summary=children_summary,
            graph_description=graph_description,
            block_sources=block_sources
        )

        log_path = self._module_log_path(module_name, "pass1_preview")
        preview, token_stats = await self.llm.generate(PASS1_SYSTEM, prompt, log_path=log_path, disable_thinking=True)

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass1",
            **token_stats
        })

        # Save results (internally still stored as pass1_overview in tracker)
        self.tracker.update_pass1(module_name, preview)
        self._save_module_file(module_name, "preview.md", preview)
        self._save_metadata(node)

    async def _run_pass2(self, node: Any) -> str:
        """Pass 2: Bottom-up synthesis documentation (runs after Pass 2.1 and Pass 2.5)."""
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
            child_desc = await self._run_pass2(child)
            children_descriptions[child.module_name] = child_desc

        # 2. Process current module if not already done
        if module_name in self._processed_pass2:
            return self.tracker.get_state(module_name).pass2_description or ""

        if self.tracker.is_pass2_done(module_name):
            self._processed_pass2.add(module_name)
            return self.tracker.get_state(module_name).pass2_description or ""

        # 3. Check prerequisites: Pass 2.1 and Pass 2.5 must be done
        if not self.tracker.is_pass2_1_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.1 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_5_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.5 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        # 4. Generate synthesis doc
        is_leaf = len(node.children) == 0
        description = await self._generate_module_description(node, children_descriptions, is_leaf)

        self._processed_pass2.add(module_name)
        return description

    async def _generate_module_description(self, node: Any, children_descs: Dict[str, str], is_leaf: bool) -> str:
        """Generate synthesis documentation by combining all prior analysis results."""
        module_name = node.module_name
        print(f"  [Pass 2] Generating synthesis description for {module_name}...")

        state = self.tracker.get_state(module_name)
        preview = state.pass1_overview or ""
        port_summary = self.resolver.get_port_summary(module_name)

        # Read block docs from file (generated in Pass 1.5)
        block_docs_file = self.modules_dir / module_name / "block_docs.md"
        block_descriptions = block_docs_file.read_text() if block_docs_file.exists() else "无逻辑块文档"

        # Get Pass 2.1 and Pass 2.5 results from tracker
        design_highlights = state.pass2_1_highlights or "无设计亮点分析"
        flowchart = state.pass2_5_mermaid or "无流程图"

        if is_leaf:
            prompt = PASS2_LEAF_PROMPT.format(
                module_name=module_name,
                preview=preview,
                port_summary=port_summary,
                block_descriptions=block_descriptions,
                design_highlights=design_highlights,
                flowchart=flowchart
            )
            system = PASS2_LEAF_SYSTEM
        else:
            # Format children descriptions
            children_summary = "\n\n".join([f"## 子模块 {name}:\n{desc}" for name, desc in children_descs.items()])
            prompt = PASS2_NONLEAF_PROMPT.format(
                module_name=module_name,
                preview=preview,
                children_descriptions=children_summary,
                block_descriptions=block_descriptions,
                design_highlights=design_highlights,
                flowchart=flowchart
            )
            system = PASS2_NONLEAF_SYSTEM

        log_path = self._module_log_path(module_name, "pass2_description")
        description, token_stats = await self.llm.generate(system, prompt, log_path=log_path)

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2",
            **token_stats
        })

        self.tracker.update_pass2(module_name, description)
        self._save_module_file(module_name, "description.md", description)
        self._save_metadata(node)
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
            "children": [c.module_name for c in node.children.values()],
            "token_stats": self._token_stats.get(module_name, [])
        }

        with open(module_dir / "metadata.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    # ==================== Pass 2.1: Design Highlight/Trick Identification ====================

    async def _run_pass2_1(self, node: Any) -> str:
        """Pass 2.1: Bottom-up design highlight identification.

        Returns the highlights summary for this module (for parent to use).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.1] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (bottom-up)
        children_highlights = {}
        for child in node.children.values():
            highlights = await self._run_pass2_1(child)
            if highlights:
                children_highlights[child.module_name] = highlights

        # 2. Already done?
        if module_name in self._processed_pass2_1:
            state = self.tracker.get_state(module_name)
            return state.pass2_1_highlights or ""

        if self.tracker.is_pass2_1_done(module_name):
            self._processed_pass2_1.add(module_name)
            state = self.tracker.get_state(module_name)
            return state.pass2_1_highlights or ""

        # 3. Check if we have necessary context (need Pass 1.5 block docs)
        if not self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 2.1] Skipping {module_name} (Pass 1.5 not completed)")
            self._processed_pass2_1.add(module_name)
            return ""

        # 4. Generate design highlights
        print(f"  [Pass 2.1] Analyzing design highlights for {module_name}...")
        
        try:
            highlights = await self._generate_design_highlights(
                node, children_highlights
            )
            
            if highlights:
                self._save_module_file(module_name, "design_highlights.md", highlights)
                self.tracker.update_pass2_1(module_name, highlights)
            
            self._processed_pass2_1.add(module_name)
            self._save_metadata(node)
            return highlights

        except Exception as e:
            print(f"  [ERROR] [Pass 2.1] Failed to generate highlights for {module_name}: {e}")
            self._processed_pass2_1.add(module_name)
            return ""

    async def _generate_design_highlights(
        self, node: Any, children_highlights: Dict[str, str]
    ) -> str:
        """Generate design highlights/tricks documentation for a module.

        Args:
            node: HierarchyNode for the module
            children_highlights: Dict mapping child module names to their highlights

        Returns:
            Markdown document with design highlights analysis
        """
        module_name = node.module_name

        # Get Pass 1 preview (replacing old Pass 2 description dependency)
        state = self.tracker.get_state(module_name)
        preview = state.pass1_overview or "无功能预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)
        
        # Get graph description and block descriptions if available
        graph_description = ""
        block_descriptions = ""
        
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph)
            
            # We can reuse block docs if they were generated in Pass 2
            # For simplicity, we'll just get them again or from cache
            block_log_path = self._module_log_path(module_name, "block")
            if (self.modules_dir / module_name / "block_docs.json").exists():
                with open(self.modules_dir / module_name / "block_docs.json", 'r') as f:
                    block_docs = json.load(f)
                parts = []
                for block_id, doc_text in sorted(block_docs.items()):
                    parts.append(f"### {block_id}:")
                    parts.append(doc_text)
                    parts.append("")
                block_descriptions = "\n".join(parts)
        
        # Get source code (truncated if needed)
        source_code = self.resolver.read_source(module_name) or "Source not found."
        if len(source_code.split('\n')) > self.max_source_lines:
            lines = source_code.split('\n')
            source_code = '\n'.join(lines[:self.max_source_lines]) + f"\n\n// ... (truncated, {len(lines) - self.max_source_lines} more lines)"
        
        # Format children highlights
        children_highlights_str = ""
        if children_highlights:
            parts = []
            for child_name, highlights in sorted(children_highlights.items()):
                # Extract just the key points from child, not full doc
                # Look for "设计点" sections or take first few paragraphs
                lines = highlights.split('\n')
                # Take first 20 lines as summary
                summary_lines = lines[:20] if len(lines) > 20 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 20:
                    summary += "\n\n[... 更多内容见子模块文档]"
                parts.append(f"## 子模块 {child_name} 的关键设计点:\n{summary}\n")
            children_highlights_str = "\n".join(parts)
        else:
            children_highlights_str = "无子模块设计点参考"
        
        # Format prompt
        prompt = PASS2_1_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description if graph_description else "无拓扑结构信息",
            block_descriptions=block_descriptions if block_descriptions else "无逻辑块文档",
            source_code=f"```verilog\n{source_code}\n```",
            children_highlights=children_highlights_str
        )
        
        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_1_highlights")
        highlights, token_stats = await self.llm.generate(
            PASS2_1_SYSTEM, 
            prompt, 
            log_path=log_path
        )
        
        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_1",
            **token_stats
        })
        
        return highlights

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
            # Generate full module behavioral flowchart in one shot
            full_mermaid = await self._generate_module_flowchart(
                module_name, graph, children_summaries
            )
            self._save_module_file(module_name, "flowchart.mmd", full_mermaid)

            # Use full mermaid as summary — parent module decides how to summarize
            self._mermaid_summaries[module_name] = full_mermaid

            # Update tracker
            self.tracker.update_pass2_5(module_name, full_mermaid)

            self._processed_pass2_5.add(module_name)
            self._save_metadata(node)
            return full_mermaid

        except Exception as e:
            print(f"  [ERROR] [Pass 2.5] Failed to generate flowchart for {module_name}: {e}")
            self._processed_pass2_5.add(module_name)
            return ""

    async def _generate_module_flowchart(
        self, module_name: str, graph: SimplifiedGraph,
        children_summaries: Dict[str, str]
    ) -> str:
        """Generate full module behavioral flowchart in one shot.

        Uses the topological graph structure and block source code (same as PASS 1)
        to produce a data/control-centric behavioral Mermaid flowchart.
        """
        # Get module preview from PASS 1
        state = self.tracker.get_state(module_name)
        preview = state.pass1_overview or "无预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)

        # Graph description (topology with metadata, same format as PASS 1)
        graph_description = self._format_graph_description(graph)

        # Block source code in topological order (same format as PASS 1)
        block_sources = self._format_block_sources_topological(graph, module_name)

        # Block descriptions: read from Pass 1.5 saved file (guaranteed to exist)
        block_desc_file = self.modules_dir / module_name / "block_docs.md"
        if block_desc_file.exists():
            block_descriptions = block_desc_file.read_text()
        else:
            # Should not happen if Pass 1.5 ran correctly
            print(f"    [WARN] Module {module_name}: block_docs.md not found")
            block_descriptions = "无逻辑块文档"

        # Format children summaries
        child_lines = []
        for child_name, summary in sorted(children_summaries.items()):
            child_lines.append(f"## 子模块 {child_name}:\n{summary}\n")
        children_str = "\n".join(child_lines) if child_lines else "无子模块"

        # Format prompt
        prompt = PASS2_5_MODULE_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            block_sources=block_sources,
            block_descriptions=block_descriptions,
            children_summaries=children_str
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_5_module")
        full_mermaid, token_stats = await self.llm.generate(PASS2_5_MODULE_SYSTEM, prompt, log_path=log_path)

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_5",
            **token_stats
        })

        return full_mermaid

