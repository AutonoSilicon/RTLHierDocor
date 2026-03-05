import os
import json
import fnmatch
import re
import time
import asyncio
from collections import defaultdict
from typing import Dict, List, Optional, Any, Tuple, Callable, Awaitable
from pathlib import Path

from .llm_backend import LLMBackend
from .source_resolver import SourceResolver
from .progress_tracker import ProgressTracker
from .project_progress_tracker import ProjectProgressTracker
from .block_doc_generator import BlockDocGenerator
from .incremental_composer import IncrementalDocComposer, SectionPatch
from .pass3_generator import Pass3Generator
from schematic.simplifier import SimplifiedGraph
from .prompts import (
    PASS1_SYSTEM,
    PASS1_PROMPT,
    PASS2_SYSTEM,
    PASS2_PROMPT,
    PASS2_A_ROOT_SYSTEM,
    PASS2_A_ROOT_PROMPT,
    PASS2_B_EXPAND_SYSTEM,
    PASS2_B_EXPAND_PROMPT,
    PASS2_B_PATCH_SYSTEM,
    PASS2_B_PATCH_PROMPT,
    PASS2_C_POLISH_SYSTEM,
    PASS2_C_POLISH_PROMPT,
    PASS2_SUBDOC_REFINE_SYSTEM,
    PASS2_SUBDOC_REFINE_PROMPT,
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
    PASS2_7_ARCHITECTURE_SYSTEM,
    PASS2_7_ARCHITECTURE_PROMPT,
)

class AgentDocGenerator:
    """Orchestrates the two-pass documentation generation process."""

    def __init__(
        self,
        hierarchy: Any,
        llm: LLMBackend,
        composer_llm: Optional[LLMBackend],
        resolver: SourceResolver,
        tracker: ProgressTracker,
        output_dir: str,
        max_modules: int = 0,
        skip_modules: Optional[List[str]] = None,
        schematic_gen: Optional[Any] = None,
        block_doc_threshold: int = 64,
        pass3_enabled: bool = True,
        pass3_output_subdir: str = "chip",
        pass3_key_modules_per_subsystem: int = 24,
        pass3_max_card_lines: int = 12,
        pass3_tree_max_depth_in_doc: int = 4,
        max_concurrent_modules: int = 4,
    ):
        self.hierarchy = hierarchy
        self.llm = llm
        self.resolver = resolver
        self.tracker = tracker
        self.output_dir = Path(output_dir)
        self.modules_dir = self.output_dir / "modules"
        self.debug_dir = self.output_dir / "debug"
        self.max_modules = max_modules
        self.skip_modules = skip_modules or []
        self.schematic_gen = schematic_gen
        self.block_doc_threshold = block_doc_threshold
        self.pass3_enabled = pass3_enabled
        self.pass3_output_subdir = pass3_output_subdir
        self.pass3_key_modules_per_subsystem = pass3_key_modules_per_subsystem
        self.pass3_max_card_lines = pass3_max_card_lines
        self.pass3_tree_max_depth_in_doc = pass3_tree_max_depth_in_doc
        self.chip_dir = self.output_dir / self.pass3_output_subdir
        self.chip_subsystems_dir = self.chip_dir / "subsystems"
        self.chip_debug_dir = self.chip_dir / "debug"
        self.project_tracker = ProjectProgressTracker(str(self.chip_dir))
        self.incremental_composer = IncrementalDocComposer(composer_llm or llm)
        self._graphs: Dict[str, SimplifiedGraph] = {}  # module_name -> SimplifiedGraph
        self._processed_pass1: set = set()
        self._processed_pass1_5: set = set()
        self._processed_pass2: set = set()
        self._processed_pass2_1: set = set()  # Track Pass 2.1 completion
        self._processed_pass2_2: set = set()
        self._processed_pass2_3: set = set()  # Track Pass 2.3 completion
        self._processed_pass2_4: set = set()  # Track Pass 2.4 completion
        self._processed_pass2_5: set = set()  # Track Pass 2.5 completion
        self._processed_pass2_6: set = set()  # Track Pass 2.6 completion
        self._processed_pass2_7: set = set()  # Track Pass 2.7 completion
        self._processed_pass3_subsystems: set = set()
        self._mermaid_summaries: Dict[str, str] = {}  # module_name -> 精简版 mermaid
        self._token_stats: Dict[str, List[Dict[str, int]]] = {}  # module_name -> list of token stats per pass
        self._inflight_pass_tasks: Dict[Tuple[str, str], asyncio.Task] = {}
        self.pass3_generator = Pass3Generator(self)
        # Concurrency control semaphore
        self._module_semaphore = asyncio.Semaphore(max_concurrent_modules)

    def _should_skip(self, module_name: str) -> bool:
        """Check if a module should be skipped based on skip_modules patterns."""
        for pattern in self.skip_modules:
            if fnmatch.fnmatch(module_name, pattern):
                return True
        return False

    async def _run_singleflight(
        self,
        pass_name: str,
        module_name: str,
        coro_factory: Callable[[], Awaitable[str]],
    ) -> str:
        """Ensure only one in-flight task per (pass_name, module_name)."""
        key = (pass_name, module_name)
        existing = self._inflight_pass_tasks.get(key)
        if existing is not None:
            return await existing

        task = asyncio.create_task(coro_factory())
        self._inflight_pass_tasks[key] = task
        try:
            return await task
        finally:
            if self._inflight_pass_tasks.get(key) is task:
                self._inflight_pass_tasks.pop(key, None)

    async def run(self):
        """Run the full documentation generation process."""
        print(f"[INFO] Starting Docor Agent on {self.hierarchy.module_name}...")

        # Pass 0: Precompute simplified graphs
        if self.schematic_gen:
            print("[INFO] Running Pass 0: Precomputing SimplifiedGraphs...")
            self._precompute_graphs(self.hierarchy)
            print(f"[INFO] Precomputed {len(self._graphs)} simplified graphs")

        # Pass 1: Top-down Preview Generation
        print("[INFO] Running Pass 1: Top-down Preview Generation...")
        await self._run_pass1(self.hierarchy, "该模块是设计的顶层模块。")

        # Pass 1.5: Block-level Documentation (extracted from old Pass 2)
        print("[INFO] Running Pass 1.5: Block-level Documentation...")
        await self._run_pass1_5(self.hierarchy)

        # Pass 2.1 + Pass 2.2 + Pass 2.3 + Pass 2.4 + Pass 2.5 + Pass 2.6 + Pass 2.7: Run in parallel (all depend only on Pass 1 + Pass 1.5)
        print("[INFO] Running Pass 2.1 + Pass 2.2 + Pass 2.3 + Pass 2.4 + Pass 2.5 + Pass 2.6 + Pass 2.7 in parallel...")
        import asyncio
        await asyncio.gather(
            self._run_pass2_1(self.hierarchy),
            self._run_pass2_2(self.hierarchy),
            self._run_pass2_3(self.hierarchy),
            self._run_pass2_4(self.hierarchy),
            self._run_pass2_5(self.hierarchy),
            self._run_pass2_6(self.hierarchy),
            self._run_pass2_7(self.hierarchy)
        )

        # Pass 2: Bottom-up Synthesis Documentation (depends on Pass 2.1 + Pass 2.2 + Pass 2.3 + Pass 2.4 + Pass 2.5)
        print("[INFO] Running Pass 2: Synthesis Documentation...")
        await self._run_pass2(self.hierarchy)

        # Pass 3: Chip-level top-down overview and subsystem index pages
        if self.pass3_enabled:
            print("[INFO] Running Pass 3.1: SoC-level Subsystem Partition (Agent exploration)...")
            await self._run_pass3_1(self.hierarchy)

            print("[INFO] Running Pass 3.2: Core Microarchitecture Partition (Agent Explore)...")
            await self._run_pass3_2(self.hierarchy)

            print("[INFO] Running Pass 3: Chip-level Overview & Subsystem Overview...")
            await self._run_pass3(self.hierarchy)

        print(f"[INFO] Documentation generation complete.")
        print(f"[INFO]   Modules: {self.modules_dir}")
        print(f"[INFO]   Debug logs: {self.debug_dir}")
        if self.pass3_enabled:
            print(f"[INFO]   CHIP docs: {self.chip_dir}")

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
        module_name = node.module_name
        return await self._run_singleflight(
            "pass1",
            module_name,
            lambda: self._run_pass1_impl(node, ancestor_context),
        )

    async def _run_pass1_impl(self, node: Any, ancestor_context: str):
        """Pass 1: Top-down (Overview) with concurrency control.

        Parent is processed first, then children are processed concurrently
        (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip logic
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 1] Skipping module {module_name} (matches skip pattern)")
            return

        # Check limit
        if self.max_modules > 0 and len(self._processed_pass1) >= self.max_modules:
            if node.module_name not in self._processed_pass1:
                return

        # Process current module (only if not already done in this session)
        if module_name not in self._processed_pass1:
            if not self.tracker.is_pass1_done(module_name):
                # Use semaphore to control concurrency
                async with self._module_semaphore:
                    await self._generate_module_preview(node, ancestor_context)
            else:
                print(f"  [Pass 1] Module {module_name} already has preview (skipping LLM)")
            self._processed_pass1.add(module_name)

        # Get current preview to pass down (load from file)
        state = self.tracker.get_state(module_name)
        current_preview = self.tracker.get_pass1_content(module_name) or ""

        # Process children concurrently with semaphore control
        if node.children:
            tasks = []
            for child in node.children.values():
                task = self._run_pass1(child, current_preview)
                tasks.append(task)

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_pass1_5(self, node: Any):
        module_name = node.module_name
        return await self._run_singleflight(
            "pass1_5",
            module_name,
            lambda: self._run_pass1_5_impl(node),
        )

    async def _run_pass1_5_impl(self, node: Any):
        """Pass 1.5: Block-level documentation generation (extracted from old Pass 2).

        Depends only on Pass 0 (SimplifiedGraph) and SourceResolver, not on Pass 1.
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip logic
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 1.5] Skipping module {module_name} (matches skip pattern)")
            # Still recurse to children
            if node.children:
                tasks = [self._run_pass1_5(child) for child in node.children.values()]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            return

        # Check limit
        if self.max_modules > 0 and module_name not in self._processed_pass1:
            if node.children:
                tasks = [self._run_pass1_5(child) for child in node.children.values()]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            return

        # 1. Check if already processed in this session (before processing)
        if module_name in self._processed_pass1_5:
            # Already processed, just recurse children concurrently
            if node.children:
                tasks = [self._run_pass1_5(child) for child in node.children.values()]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            return

        # 2. Check if already done in persistent storage
        if self.tracker.is_pass1_5_done(module_name):
            self._processed_pass1_5.add(module_name)
            print(f"  [Pass 1.5] Module {module_name} already has block docs (skipping LLM)")
            # Recurse children concurrently
            if node.children:
                tasks = [self._run_pass1_5(child) for child in node.children.values()]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            return

        # 3. Generate (only if not already done)
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
            # Use semaphore to control concurrency
            async with self._module_semaphore:
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
        self._processed_pass1_5.add(module_name)

        # Recurse to children concurrently
        if node.children:
            tasks = [self._run_pass1_5(child) for child in node.children.values()]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

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

    def _format_graph_description(self, graph: SimplifiedGraph, include_source: bool = True) -> str:
        """Format SimplifiedGraph as a text description for LLM.

        Blocks are ordered in topological order (data flow direction).

        Args:
            graph: SimplifiedGraph object
            include_source: Whether to include source code for each block

        Returns:
            Formatted text description with connectivity and source code embedded in each block
        """
        # Build adjacency lists for quick lookup
        predecessors: Dict[str, List[str]] = {}  # node -> list of predecessors
        successors: Dict[str, List[str]] = {}    # node -> list of successors
        
        all_nodes = set(graph.proc_nodes.keys()) | set(graph.comb_nodes.keys()) | \
                set(graph.io_ports.keys()) | set(graph.submodules.keys()) | set(graph.seq_cells.keys())
        
        for node in all_nodes:
            predecessors[node] = []
            successors[node] = []
        
        for src, dst in graph.edges:
            if src in all_nodes and dst in all_nodes:
                successors[src].append(dst)
                predecessors[dst].append(src)
        
        def format_conn_list(node_ids: List[str], graph: SimplifiedGraph) -> str:
            """Format a list of connected node IDs as readable names."""
            if not node_ids:
                return "None"
            names = []
            for node_id in node_ids:
                name = self._resolve_node_name_for_desc(node_id, graph)
                # Add type hint for clarity
                if node_id in graph.proc_nodes:
                    name = f"{name}(PROC)"
                elif node_id in graph.comb_nodes:
                    comb_type = graph.comb_nodes[node_id].comb_type
                    name = f"{name}({comb_type})"
                elif node_id in graph.submodules:
                    name = f"{name}(submodule)"
                elif node_id in graph.io_ports:
                    name = f"{name}(port)"
                elif node_id in graph.seq_cells:
                    seq_type = graph.seq_cells[node_id] or "seq"
                    name = f"{name}({seq_type})"
                names.append(name)
            return ", ".join(names)
        
        def get_source_for_block(block_id: str, graph: SimplifiedGraph) -> str:
            """Get source code for a block."""
            if block_id in graph.proc_nodes:
                proc_info = graph.proc_nodes[block_id]
                if proc_info.source_location:
                    source = self.resolver.read_block_source([proc_info.source_location])
                    return source
            elif block_id in graph.comb_nodes:
                comb_info = graph.comb_nodes[block_id]
                if comb_info.source_locations:
                    source = self.resolver.read_block_source(comb_info.source_locations)
                    return source
            return ""
        
        # Compute topological order for blocks only (PROC + COMB)
        all_blocks = set(graph.proc_nodes.keys()) | set(graph.comb_nodes.keys())
        
        # Build adjacency for topological sort (only between blocks)
        adj = defaultdict(list)
        in_degree = defaultdict(int)
        
        for block_id in all_blocks:
            in_degree[block_id] = 0
        
        for src, dst in graph.edges:
            if src in all_blocks and dst in all_blocks:
                adj[src].append(dst)
                in_degree[dst] += 1
        
        # Kahn's algorithm for topological sort
        queue = sorted([node for node in all_blocks if in_degree[node] == 0])
        topo_order = []
        
        while queue:
            node = queue.pop(0)
            topo_order.append(node)
            
            for neighbor in sorted(adj[node]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    # Insert in sorted position to maintain deterministic order
                    queue.append(neighbor)
                    queue.sort()
        
        # Add any remaining (disconnected or cycle)
        remaining = sorted(all_blocks - set(topo_order))
        topo_order.extend(remaining)
        
        parts = []

        # Output all blocks in topological order (mixed PROC and COMB)
        block_parts = []
        
        for block_id in topo_order:
            block_section = []
            
            if block_id in graph.proc_nodes:
                proc_info = graph.proc_nodes[block_id]
                
                # Basic info for PROC
                if proc_info.source_location:
                    loc = proc_info.source_location
                    filename = loc.file_path.split('/')[-1] if '/' in loc.file_path else loc.file_path
                    block_section.append(f"- **{block_id}** [PROC] ({filename}:{loc.start_line}-{loc.end_line})")
                else:
                    block_section.append(f"- **{block_id}** [PROC]")
                    
            elif block_id in graph.comb_nodes:
                comb_info = graph.comb_nodes[block_id]
                
                # Basic info for COMB
                type_label = comb_info.comb_type
                node_info = f"{comb_info.node_count} nodes"
                loc_label = comb_info.location_info.get_display_label().replace("\\n", ", ")
                
                header = f"- **{block_id}** [{type_label}, {node_info}]"
                if loc_label:
                    header += f" ({loc_label})"
                block_section.append(header)
            else:
                continue
            
            # Block-level connectivity (PROC/COMB only).
            # Boundary/direct links to ports/submodules/seq cells are listed in
            # the dedicated section below to avoid semantic ambiguity.
            block_section.append(f"  - Inputs from blocks: {format_conn_list(predecessors.get(block_id, []), graph)}")
            block_section.append(f"  - Outputs to blocks: {format_conn_list(successors.get(block_id, []), graph)}")
            
            # Source code
            if include_source:
                source = get_source_for_block(block_id, graph)
                if source:
                    block_section.append(f"  - Source:")
                    block_section.append(f"    ```verilog")
                    for line in source.strip().split('\n'):
                        block_section.append(f"    {line}")
                    block_section.append(f"    ```")
            
            block_parts.append("\n".join(block_section))
        
        # Add all blocks section
        if block_parts:
            parts.append("## Circuit Blocks (in topological order, block-to-block view):")
            parts.append("\n\n".join(block_parts))

        # Add non-block connectivity section so direct port/submodule/seq links are visible
        # (e.g., port->port direct assigns in simple modules)
        boundary_edges: List[Tuple[str, str]] = []
        for src, dst in graph.edges:
            src_is_block = src in graph.proc_nodes or src in graph.comb_nodes
            dst_is_block = dst in graph.proc_nodes or dst in graph.comb_nodes
            if not (src_is_block and dst_is_block):
                boundary_edges.append((src, dst))

        if boundary_edges:
            parts.append("\n## Boundary Connections (including direct links):")
            for src, dst in sorted(boundary_edges):
                src_name = self._resolve_node_name_for_desc(src, graph)
                dst_name = self._resolve_node_name_for_desc(dst, graph)
                src_type = ""
                dst_type = ""
                if src in graph.io_ports:
                    src_type = "(port)"
                elif src in graph.submodules:
                    src_type = "(submodule)"
                elif src in graph.seq_cells:
                    seq_type = graph.seq_cells[src] or "seq"
                    src_type = f"({seq_type})"
                elif src in graph.proc_nodes:
                    src_type = "(PROC)"
                elif src in graph.comb_nodes:
                    src_type = f"({graph.comb_nodes[src].comb_type})"

                if dst in graph.io_ports:
                    dst_type = "(port)"
                elif dst in graph.submodules:
                    dst_type = "(submodule)"
                elif dst in graph.seq_cells:
                    seq_type = graph.seq_cells[dst] or "seq"
                    dst_type = f"({seq_type})"
                elif dst in graph.proc_nodes:
                    dst_type = "(PROC)"
                elif dst in graph.comb_nodes:
                    dst_type = f"({graph.comb_nodes[dst].comb_type})"

                parts.append(f"- {src_name}{src_type} -> {dst_name}{dst_type}")

        # Submodules with connectivity (alphabetical order)
        if graph.submodules:
            parts.append("\n## Submodule Instances:")
            for submod_id, instance_name in sorted(graph.submodules.items()):
                parts.append(f"- **{instance_name}**")
                parts.append(f"  - Inputs from: {format_conn_list(predecessors.get(submod_id, []), graph)}")
                parts.append(f"  - Outputs to: {format_conn_list(successors.get(submod_id, []), graph)}")

        return "\n".join(parts) if parts else "No simplified structure information"

    def _resolve_node_name_for_desc(self, node_id: str, graph: SimplifiedGraph) -> str:
        """Resolve node ID to a short name for graph description.

        Args:
            node_id: Node ID
            graph: SimplifiedGraph

        Returns:
            Short node name (returns ID for COMB nodes instead of type)
        """
        if node_id in graph.proc_nodes:
            return node_id
        if node_id in graph.comb_nodes:
            # Return the COMB ID (e.g., "p32_in_comb_13") instead of just type
            return node_id
        if node_id in graph.io_ports:
            return graph.io_ports[node_id].strip()[:30]  # Truncate long port names
        if node_id in graph.submodules:
            return graph.submodules[node_id]
        if node_id in graph.seq_cells:
            return node_id
        return node_id

    def _format_block_summaries(self, module_name: str) -> str:
        """Format block summaries (high-level overview without full source code).

        Reads the block_docs.json file generated in Pass 1.5 and provides
        only the first 3 lines of each block's documentation as a summary.

        Args:
            module_name: Module name

        Returns:
            Formatted block summaries
        """
        block_docs_file = self.modules_dir / module_name / "block_docs.json"

        if not block_docs_file.exists():
            return "无复杂逻辑块需要单独文档化"

        try:
            import json
            with open(block_docs_file, 'r', encoding='utf-8') as f:
                block_docs = json.load(f)

            if not block_docs:
                return "无逻辑块摘要信息"

            parts = []
            for block_id, doc_text in sorted(block_docs.items()):
                # Extract first 3 lines as summary
                lines = doc_text.split('\n')
                summary_lines = [line for line in lines[:3] if line.strip()]
                if not summary_lines:
                    summary_lines = ["功能描述未生成"]

                summary = '\n'.join(summary_lines)
                parts.append(f"### {block_id}:")
                parts.append(summary)
                if len(lines) > 3:
                    parts.append("...")
                parts.append("")  # Empty line

            return "\n".join(parts)
        except Exception as e:
            print(f"[WARN] Failed to read block summaries for {module_name}: {e}")
            return "读取逻辑块摘要失败"

    async def _generate_module_preview(self, node: Any, ancestor_context: str):
        module_name = node.module_name
        print(f"  [Pass 1] Generating preview for {module_name}...")

        port_summary = self.resolver.get_port_summary(module_name)
        children_summary = ", ".join([c.module_name for c in node.children.values()]) or "无子模块"

        # Use SimplifiedGraph if available, otherwise fallback to source code
        if module_name in self._graphs:
            # graph_description now includes source code embedded in each block (topological order)
            graph_description = self._format_graph_description(self._graphs[module_name], include_source=True)
        else:
            # Fallback: read full source code (no truncation)
            source_code = self.resolver.read_source(module_name) or "Source not found."
            graph_description = f"```verilog\n{source_code}\n```"
            if module_name not in self._graphs and self.schematic_gen:
                print(f"  [WARN] Module {module_name}: using source fallback (no SimplifiedGraph)")

        prompt = PASS1_PROMPT.format(
            module_name=module_name,
            ancestor_context=ancestor_context,
            port_summary=port_summary,
            children_summary=children_summary,
            graph_description=graph_description
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
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2",
            module_name,
            lambda: self._run_pass2_impl(node),
        )

    async def _run_pass2_impl(self, node: Any) -> str:
        """Pass 2: Bottom-up synthesis documentation (runs after Pass 2.1, Pass 2.2 and Pass 2.3).

        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip logic
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2] Skipping module {module_name} (matches skip pattern)")
            return f"Skipped module {module_name} (matches skip pattern)"

        # Check limit - only process if Pass 1 processed it
        if self.max_modules > 0 and node.module_name not in self._processed_pass1:
            return "Skipped due to max_modules limit."

        # 1. Recurse to children first - concurrently
        children_descriptions = {}
        if node.children:
            async def process_child(child):
                child_desc = await self._run_pass2(child)
                return child.module_name, child_desc

            child_tasks = [process_child(child) for child in node.children.values()]
            if child_tasks:
                results = await asyncio.gather(*child_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    child_name, child_desc = result
                    children_descriptions[child_name] = child_desc

        # 2. Process current module if not already done
        if module_name in self._processed_pass2:
            print(f"  [Pass 2] Reusing in-session result for {module_name}")
            return self.tracker.get_pass2_content(module_name) or ""

        if self.tracker.is_pass2_done(module_name):
            self._processed_pass2.add(module_name)
            print(f"  [Pass 2] Reusing cached description for {module_name}")
            return self.tracker.get_pass2_content(module_name) or ""

        # 3. Check prerequisites: Pass 2.1, Pass 2.2 and Pass 2.3 must be done
        if not self.tracker.is_pass2_1_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.1 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_2_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.2 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_3_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.3 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_4_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.4 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_5_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.5 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_6_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.6 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        if not self.tracker.is_pass2_7_done(module_name):
            print(f"  [Pass 2] Skipping {module_name} (Pass 2.7 not completed)")
            self._processed_pass2.add(module_name)
            return ""

        # 4. Generate synthesis doc
        description = await self._generate_module_description(node, children_descriptions)

        self._processed_pass2.add(module_name)
        return description

    def _extract_summary(self, content: str, max_lines: int = 30, max_chars: int = 2000) -> str:
        """Extract a compact, structured summary from longer content.

        Preference order:
        1) explicit "摘要" section
        2) meaningful bullet/table lines (skip empty/template noise)
        3) fallback truncation
        """
        if not content or content.strip() in ["无设计亮点分析", "无流程图", "无接口规范", 
                                                "无功能详细描述", "无寄存器描述", 
                                                "无时序约束与CDC描述", "无架构设计描述"]:
            return content

        explicit = self._extract_named_section(content, ["摘要", "Summary", "Executive Summary"])
        if explicit:
            result = explicit
        else:
            lines = [line.rstrip() for line in content.split('\n')]
            kept: List[str] = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("```"):
                    continue
                if stripped.lower().startswith("gantt") or stripped.lower().startswith("statediagram"):
                    continue
                if stripped.startswith("|") or stripped.startswith("-") or stripped.startswith("#"):
                    kept.append(line)
                    continue
                if any(token in stripped for token in ["来源", "待确认", "时钟", "复位", "CDC", "接口", "寄存器", "数据流", "控制流"]):
                    kept.append(line)
            if kept:
                result = "\n".join(kept[:max_lines])
            else:
                lines = content.split('\n')
                if len(lines) <= max_lines:
                    result = content
                else:
                    result = '\n'.join(lines[:max_lines]) + f"\n\n[... 共{len(lines)}行，此处省略后续内容 ...]"

        # Then check char limit
        if len(result) > max_chars:
            result = result[:max_chars] + f"\n\n[... 内容过长，共{len(content)}字符，此处截断 ...]"

        return result

    def _extract_named_section(self, content: str, section_names: List[str]) -> str:
        """Extract a markdown section by heading keywords."""
        if not content:
            return ""

        lines = content.split('\n')
        start = -1
        end = len(lines)
        for idx, line in enumerate(lines):
            if not line.strip().startswith("#"):
                continue
            normalized = line.strip().lstrip("#").strip().lower()
            if any(name.lower() in normalized for name in section_names):
                start = idx
                break

        if start < 0:
            return ""

        for idx in range(start + 1, len(lines)):
            if lines[idx].strip().startswith("#"):
                end = idx
                break

        section = "\n".join(lines[start:end]).strip()
        return section

    def _parse_editable_section_titles(self, editable_sections: str) -> List[str]:
        """Parse titles from 'Editable Sections' list.

        The input is expected to be one or more lines, each like '## <title>'.
        Returned titles are without leading '#'.
        """
        titles: List[str] = []
        for line in (editable_sections or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("##"):
                titles.append(stripped.lstrip("#").strip())
        return titles

    def _parse_section_patches(self, llm_output: str, editable_sections: str) -> List[SectionPatch]:
        """Parse patch-mode output into section patches.

        Expected format: only a list of sections, each beginning with '## <title>'.
        The section title must match one of the Editable Sections.
        Unexpected sections (e.g., child module descriptions) are silently ignored.
        """
        allowed_titles = set(self._parse_editable_section_titles(editable_sections))
        text = (llm_output or "").strip()
        if not text:
            raise ValueError("empty patch output")

        if "无须更新" in text or "无更新" in text:
            return []

        matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
        if not matches:
            raise ValueError("no '##' section headers found in patch output")

        patches: List[SectionPatch] = []
        for i, m in enumerate(matches):
            title = m.group(1).strip()
            if title not in allowed_titles:
                # Skip unexpected sections (e.g., child module descriptions)
                continue
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            patches.append(SectionPatch(section_title=title, replacement_markdown=body))

        return patches

    async def _refine_pass2_subdoc(
        self,
        module_name: str,
        subdoc_name: str,
        raw_content: str,
        coverage_focus: str,
        fallback_lines: int = 25,
        fallback_chars: int = 2500,
    ) -> str:
        """Refine one Pass2 sub-document into concise high-coverage bullets.

        Falls back to rule-based summary when refinement fails or returns empty/error text.
        """
        if not raw_content:
            return ""
        if raw_content.strip().startswith("无"):
            return raw_content

        refine_prompt = PASS2_SUBDOC_REFINE_PROMPT.format(
            module_name=module_name,
            subdoc_name=subdoc_name,
            coverage_focus=coverage_focus,
            raw_subdoc=raw_content,
        )
        refine_result = await self.incremental_composer.refine_subdoc(
            PASS2_SUBDOC_REFINE_SYSTEM,
            refine_prompt,
            log_path=self._module_log_path(module_name, f"pass2_refine_{subdoc_name}"),
            module_name=module_name,
            subdoc_name=subdoc_name,
        )

        refined = (refine_result.content or "").strip()
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": f"pass2_refine_{subdoc_name}",
            **refine_result.token_stats
        })

        if (
            not refined
            or refined.startswith("Error calling LLM API")
            or refined.startswith("Error:")
        ):
            fallback = self._extract_summary(raw_content, max_lines=fallback_lines, max_chars=fallback_chars)
            print(f"    [WARN] [Pass2][{module_name}] refine {subdoc_name} failed, fallback to rule summary")
            self._save_pass2_debug_file(module_name, f"description_refined_{subdoc_name}.md", fallback)
            return fallback

        self._save_pass2_debug_file(module_name, f"description_refined_{subdoc_name}.md", refined)
        return refined

    async def _generate_module_description(self, node: Any, children_descs: Dict[str, str]) -> str:
        """Generate executive summary documentation (Pass 2 - Synthesis).

        Uses a staged merge workflow:
        1) root draft from preview + architecture
        2) incremental expansions with each target sub-doc
        3) final global polish and conflict consolidation
        """
        module_name = node.module_name
        pass2_start = time.perf_counter()
        print(f"  [Pass 2] Generating executive summary for {module_name}...")

        preview = self.tracker.get_pass1_content(module_name) or ""
        port_summary = self.resolver.get_port_summary(module_name)
        block_descriptions = self._format_block_summaries(module_name)

        # Get graph description WITHOUT source code (topology only, to save context)
        graph_description = ""
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph, include_source=False)
            num_blocks = len(graph.proc_nodes) + len(graph.comb_nodes)
            print(f"    [Context] Topology: {len(graph.proc_nodes)} PROC + {len(graph.comb_nodes)} COMB blocks (no source)")
        else:
            # Fallback: minimal info
            graph_description = "无拓扑结构信息（模块未解析）"
            print(f"    [Context] No topology available")

        # Get Pass 2.1/2.3/2.4/2.5/2.6/2.7 raw content and refine them with composer model.
        # Note: Pass 2.2 flowchart is intentionally excluded from textual synthesis.
        raw_design_highlights = self.tracker.get_pass2_1_content(module_name) or "无设计亮点分析"
        raw_interface_spec = self.tracker.get_pass2_3_content(module_name) or "无接口规范"
        raw_functional_desc = self.tracker.get_pass2_4_content(module_name) or "无功能详细描述"
        raw_register_desc = self.tracker.get_pass2_5_content(module_name) or "无寄存器描述"
        raw_timing_cdc_desc = self.tracker.get_pass2_6_content(module_name) or "无时序约束与CDC描述"
        raw_architecture_desc = self.tracker.get_pass2_7_content(module_name) or "无架构设计描述"

        print(f"    [Pass2][{module_name}] Refining Pass2 subdocs before composition (excluding flowchart)")
        (
            design_highlights,
            interface_spec,
            functional_desc,
            register_desc,
            timing_cdc_desc,
            architecture_desc,
        ) = await asyncio.gather(
            self._refine_pass2_subdoc(
                module_name,
                "design_highlights",
                raw_design_highlights,
                coverage_focus="架构亮点、关键取舍、与子模块协同要点",
                fallback_lines=20,
                fallback_chars=2200,
            ),
            self._refine_pass2_subdoc(
                module_name,
                "interface_spec",
                raw_interface_spec,
                coverage_focus="端口分组、握手/时序语义、输入输出约束",
                fallback_lines=25,
                fallback_chars=2600,
            ),
            self._refine_pass2_subdoc(
                module_name,
                "functional_desc",
                raw_functional_desc,
                coverage_focus="主功能路径、关键条件分支、异常/边界行为",
                fallback_lines=25,
                fallback_chars=2600,
            ),
            self._refine_pass2_subdoc(
                module_name,
                "register_desc",
                raw_register_desc,
                coverage_focus="寄存器字段含义、可配参数、复位默认值与作用",
                fallback_lines=20,
                fallback_chars=2200,
            ),
            self._refine_pass2_subdoc(
                module_name,
                "timing_cdc",
                raw_timing_cdc_desc,
                coverage_focus="时钟复位关系、CDC风险点、约束假设与限制",
                fallback_lines=20,
                fallback_chars=2200,
            ),
            self._refine_pass2_subdoc(
                module_name,
                "architecture",
                raw_architecture_desc,
                coverage_focus="模块内功能分层、数据/控制通路、设计边界",
                fallback_lines=25,
                fallback_chars=2600,
            ),
        )

        # Handle children_descriptions: use default for leaf modules
        if children_descs:
            children_summary_parts = []
            for name, desc in children_descs.items():
                child_summary = self._extract_summary(desc, max_lines=15, max_chars=1000)
                children_summary_parts.append(f"## 子模块 {name}:\n{child_summary}")
            children_summary = "\n\n".join(children_summary_parts)
        else:
            children_summary = "当前为叶模块，无子模块"

        print(
            f"    [Pass2][{module_name}] Context prepared "
            f"(preview={len(preview)} chars, ports={len(port_summary)} chars, "
            f"children={len(children_descs)}, graph={len(graph_description)} chars)"
        )

        # Stage A: Root doc (preview + architecture as stable backbone)
        root_prompt = PASS2_A_ROOT_PROMPT.format(
            module_name=module_name,
            preview=preview,
            architecture_desc=architecture_desc,
            port_summary=port_summary,
            block_descriptions=self._extract_summary(block_descriptions, max_lines=40, max_chars=4000),
        )
        root_result = await self.incremental_composer.compose_root(
            PASS2_A_ROOT_SYSTEM,
            root_prompt,
            log_path=self._module_log_path(module_name, "pass2_a_root"),
            module_name=module_name,
        )
        current_doc = root_result.content
        self.tracker.update_pass2_a_root(module_name, current_doc)
        self._save_pass2_debug_file(module_name, "description_root.md", current_doc)
        print(f"    [Pass2][{module_name}] Root draft saved: debug/{module_name}/description_root.md ({len(current_doc)} chars)")

        # Stage B: Simple concatenation of refined sub-docs
        # Append each refined sub-document as additional sections
        subdoc_sections = []

        if design_highlights and not design_highlights.startswith("无"):
            subdoc_sections.append(f"\n\n## 设计亮点 (Design Highlights)\n\n{design_highlights}")

        if interface_spec and not interface_spec.startswith("无"):
            subdoc_sections.append(f"\n\n## 接口规范 (Interface Specification)\n\n{interface_spec}")

        if timing_cdc_desc and not timing_cdc_desc.startswith("无"):
            subdoc_sections.append(f"\n\n## 时钟复位与时序 (Clocking, Reset & Timing)\n\n{timing_cdc_desc}")

        if register_desc and not register_desc.startswith("无"):
            subdoc_sections.append(f"\n\n## 寄存器描述 (Register Description)\n\n{register_desc}")

        if functional_desc and not functional_desc.startswith("无"):
            subdoc_sections.append(f"\n\n## 功能描述 (Functional Description)\n\n{functional_desc}")

        if children_descs:
            children_summary_parts = []
            for name, desc in children_descs.items():
                child_summary = self._extract_summary(desc, max_lines=15, max_chars=1000)
                children_summary_parts.append(f"### 子模块 {name}\n{child_summary}")
            subdoc_sections.append(f"\n\n## 子模块综述 (Sub-modules Overview)\n\n" + "\n\n".join(children_summary_parts))

        # Concatenate all sections
        if subdoc_sections:
            current_doc = current_doc + "".join(subdoc_sections)
            print(f"    [Pass2][{module_name}] Sub-docs appended: {len(subdoc_sections)} sections, total {len(current_doc)} chars")

        # Stage B cache checkpoint (expand/working draft)
        self.tracker.update_pass2_b_expand(module_name, current_doc)
        self._save_pass2_debug_file(module_name, "description_working.md", current_doc)

        # Stage C: Final polish and conflict appendix
        checklist = "\n".join([
            "- 保持章节结构固定，不新增主章节",
            "- 删除重复结论，保留最有证据的一条",
            "- 每条关键结论必须有来源标签",
            "- 对冲突结论写入附录并标记待确认",
        ])
        polish_prompt = PASS2_C_POLISH_PROMPT.format(
            module_name=module_name,
            draft_doc=current_doc,
            consistency_checklist=checklist,
            cross_refs=children_summary,
        )
        polish_result = await self.incremental_composer.polish(
            PASS2_C_POLISH_SYSTEM,
            polish_prompt,
            log_path=self._module_log_path(module_name, "pass2_c_polish"),
            module_name=module_name,
        )
        description = polish_result.content
        self.tracker.update_pass2_c_polish(module_name, description)
        self._save_pass2_debug_file(module_name, "description_polished.md", description)
        print(f"    [Pass2][{module_name}] Polished draft saved: debug/{module_name}/description_polished.md ({len(description)} chars)")

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({"pass": "pass2_a_root", **root_result.token_stats})
        self._token_stats[module_name].append({"pass": "pass2_c_polish", **polish_result.token_stats})

        self.tracker.update_pass2(module_name, description)
        self._save_module_file(module_name, "description.md", description)
        self._cleanup_pass2_intermediate_files(module_name)
        self._save_metadata(node)
        print(f"  [Pass 2] Finished {module_name} in {time.perf_counter() - pass2_start:.2f}s")
        return description

    def _module_log_path(self, module_name: str, pass_name: str) -> str:
        """Get the debug log path for a specific module and pass.

        Args:
            module_name: Module name
            pass_name: Pass identifier (e.g., "pass1", "pass2", "block")

        Returns:
            Absolute path to the log file
        """
        # Save debug files to debug subdirectory: debug/<module_name>/debug_<pass_name>.md
        module_debug_dir = self.debug_dir / module_name
        module_debug_dir.mkdir(parents=True, exist_ok=True)
        return str(module_debug_dir / f"debug_{pass_name}.md")

    def _save_module_file(self, module_name: str, filename: str, content: str):
        module_dir = self.modules_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)
        with open(module_dir / filename, 'w', encoding='utf-8') as f:
            f.write(content)

    def _save_pass2_debug_file(self, module_name: str, filename: str, content: str):
        """Save Pass2 intermediate artifacts under debug tree."""
        module_debug_dir = self.debug_dir / module_name
        module_debug_dir.mkdir(parents=True, exist_ok=True)
        with open(module_debug_dir / filename, 'w', encoding='utf-8') as f:
            f.write(content)

    def _cleanup_pass2_intermediate_files(self, module_name: str):
        """Ensure modules output keeps only final description for Pass2."""
        module_dir = self.modules_dir / module_name
        for filename in ["description_root.md", "description_working.md", "description_polished.md"]:
            file_path = module_dir / filename
            if file_path.exists():
                try:
                    file_path.unlink()
                    print(f"    [Pass2][{module_name}] Removed intermediate module artifact: {filename}")
                except Exception as e:
                    print(f"    [WARN] [Pass2][{module_name}] Failed to remove {filename}: {e}")

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
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_1",
            module_name,
            lambda: self._run_pass2_1_impl(node),
        )

    async def _run_pass2_1_impl(self, node: Any) -> str:
        """Pass 2.1: Bottom-up design highlight identification.

        Returns the highlights summary for this module (for parent to use).
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.1] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        if node.children:
            child_tasks = [self._run_pass2_1(child) for child in node.children.values()]
            if child_tasks:
                await asyncio.gather(*child_tasks, return_exceptions=True)

        # 2. Collect children previews (Pass 1 results) for context
        children_previews = {}
        for child in node.children.values():
            child_preview = self.tracker.get_pass1_content(child.module_name)
            if child_preview:
                children_previews[child.module_name] = child_preview

        # 2. Check if already processed in this session
        if module_name in self._processed_pass2_1:
            return self.tracker.get_pass2_1_content(module_name) or ""

        # 3. Check if already done in persistent storage
        if self.tracker.is_pass2_1_done(module_name):
            self._processed_pass2_1.add(module_name)
            print(f"  [Pass 2.1] Module {module_name} already has design highlights (skipping LLM)")
            return self.tracker.get_pass2_1_content(module_name) or ""

        # 4. Check if we have necessary context (need Pass 1.5 block docs)
        if not self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 2.1] Skipping {module_name} (Pass 1.5 not completed)")
            self._processed_pass2_1.add(module_name)
            return ""

        # 5. Generate design highlights
        print(f"  [Pass 2.1] Analyzing design highlights for {module_name}...")

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                highlights = await self._generate_design_highlights(
                    node, children_previews
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
        self, node: Any, children_previews: Dict[str, str]
    ) -> str:
        """Generate design highlights/tricks documentation for a module.

        Args:
            node: HierarchyNode for the module
            children_previews: Dict mapping child module names to their Pass 1 previews

        Returns:
            Markdown document with design highlights analysis
        """
        module_name = node.module_name

        # Get Pass 1 preview (replacing old Pass 2 description dependency)
        preview = self.tracker.get_pass1_content(module_name) or "无功能预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)
        
        # Get structured graph description (with embedded source code, same as Pass 1)
        graph_description = ""
        block_descriptions = ""
        
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            # Use include_source=True to embed source code in topological order (Pass 1 style)
            graph_description = self._format_graph_description(graph, include_source=True)
            
            # Read block docs from Pass 1.5
            if (self.modules_dir / module_name / "block_docs.json").exists():
                with open(self.modules_dir / module_name / "block_docs.json", 'r') as f:
                    block_docs = json.load(f)
                parts = []
                for block_id, doc_text in sorted(block_docs.items()):
                    parts.append(f"### {block_id}:")
                    parts.append(doc_text)
                    parts.append("")
                block_descriptions = "\n".join(parts)
        else:
            # Fallback: read full source code (no truncation)
            source_code = self.resolver.read_source(module_name) or "Source not found."
            graph_description = f"```verilog\n{source_code}\n```"

        # Format children previews (Pass 1 results)
        children_previews_str = ""
        if children_previews:
            parts = []
            for child_name, preview in sorted(children_previews.items()):
                # Extract just the first few lines from preview
                lines = preview.split('\n')
                # Take first 10 lines as summary
                summary_lines = lines[:10] if len(lines) > 10 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 10:
                    summary += "\n\n[... 更多内容见子模块预览文档]"
                parts.append(f"## 子模块 {child_name}:\n{summary}\n")
            children_previews_str = "\n".join(parts)
        else:
            children_previews_str = "无子模块预览信息"
        
        # Format prompt (aligned with Pass 1 structure)
        prompt = PASS2_1_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description if graph_description else "无拓扑结构信息",
            block_descriptions=block_descriptions if block_descriptions else "无逻辑块文档",
            children_previews=children_previews_str
        )
        
        # Call LLM (disable tools if no logical blocks exist)
        log_path = self._module_log_path(module_name, "pass2_1_highlights")
        
        # Generate design highlights (complete info in prompt, disable tools)
        highlights, token_stats = await self.llm.generate(
            PASS2_1_SYSTEM, 
            prompt, 
            log_path=log_path,
        )
        
        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_1",
            **token_stats
        })
        
        return highlights

    # ==================== Pass 2.2: Mermaid Flowchart Generation ====================

    async def _run_pass2_2(self, node: Any) -> str:
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_2",
            module_name,
            lambda: self._run_pass2_2_impl(node),
        )

    async def _run_pass2_2_impl(self, node: Any) -> str:
        """Pass 2.2: Bottom-up Mermaid flowchart generation.

        Returns the summary mermaid for this module (for parent to use).
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.2] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        children_summaries = {}
        if node.children:
            async def process_child(child):
                summary = await self._run_pass2_2(child)
                return child.module_name, summary

            child_tasks = [process_child(child) for child in node.children.values()]
            if child_tasks:
                results = await asyncio.gather(*child_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    child_name, summary = result
                    if summary:
                        children_summaries[child_name] = summary

        # 2. Check if already processed in this session
        if module_name in self._processed_pass2_2:
            return self._mermaid_summaries.get(module_name, "")

        # 3. Check if already done in persistent storage
        if self.tracker.is_pass2_2_done(module_name):
            self._processed_pass2_2.add(module_name)
            mermaid_content = self.tracker.get_pass2_2_content(module_name)
            if mermaid_content:
                self._mermaid_summaries[module_name] = mermaid_content
                print(f"  [Pass 2.2] Module {module_name} already has flowchart (skipping LLM)")
            return mermaid_content or ""

        # 4. Generate (only if not already done)
        if module_name not in self._graphs:
            print(f"  [Pass 2.2] Skipping {module_name} (no SimplifiedGraph)")
            self._processed_pass2_2.add(module_name)
            return ""

        print(f"  [Pass 2.2] Generating flowchart for {module_name}...")
        graph = self._graphs[module_name]

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                # Generate full module behavioral flowchart in one shot
                # (returns only the mermaid code, other content goes to debug log)
                mermaid_code = await self._generate_module_flowchart(
                    module_name, graph, children_summaries
                )

            # Store mermaid for summary (parent module uses it)
            self._mermaid_summaries[module_name] = mermaid_code

            # Update tracker with mermaid code
            self.tracker.update_pass2_2(module_name, mermaid_code)

            self._processed_pass2_2.add(module_name)
            self._save_metadata(node)
            return mermaid_code

        except Exception as e:
            print(f"  [ERROR] [Pass 2.2] Failed to generate flowchart for {module_name}: {e}")
            self._processed_pass2_2.add(module_name)
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
        preview = self.tracker.get_pass1_content(module_name) or "无预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)

        # Graph description (topology with embedded source, same format as PASS 1)
        graph_description = self._format_graph_description(graph, include_source=True)

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

        # Format prompt (aligned with Pass 1 structure - no block_sources)
        prompt = PASS2_2_MODULE_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            block_descriptions=block_descriptions,
            children_summaries=children_str
        )

        # Call LLM (complete info in prompt, disable tools)
        log_path = self._module_log_path(module_name, "pass2_2_module")
        full_output, token_stats = await self.llm.generate(
            PASS2_2_MODULE_SYSTEM, 
            prompt, 
            log_path=log_path,
        )

        # Extract mermaid diagram and other content
        from agent.llm_backend import LLMBackend
        mermaid_code, other_content = LLMBackend._extract_mermaid_and_content(full_output)
        
        # Save mermaid diagram to flowchart.mmd
        if mermaid_code:
            self._save_module_file(module_name, "flowchart.mmd", mermaid_code)
        
        # Append other content (text, thinking) to debug log
        if other_content:
            try:
                # Use debug subdirectory for debug files
                module_debug_dir = self.debug_dir / module_name
                module_debug_dir.mkdir(parents=True, exist_ok=True)
                debug_file_path = str(module_debug_dir / "debug_pass2_2_module.md")
                with open(debug_file_path, 'a', encoding='utf-8') as f:
                    f.write("\n\n" + "="*80 + "\n")
                    f.write("## Pass 2.2 Additional Content (文本说明与思考过程)\n")
                    f.write("="*80 + "\n\n")
                    f.write(other_content)
                    f.write("\n\n")
            except Exception as e:
                print(f"  [WARN] Failed to append content to debug log: {e}")

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_2",
            **token_stats
        })

        return mermaid_code


    # ==================== Pass 2.3: Interface Specification Generation ====================

    async def _run_pass2_3(self, node: Any) -> str:
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_3",
            module_name,
            lambda: self._run_pass2_3_impl(node),
        )

    async def _run_pass2_3_impl(self, node: Any) -> str:
        """Pass 2.3: Bottom-up interface specification generation.

        Returns the interface specification for this module.
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.3] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        if node.children:
            child_tasks = [self._run_pass2_3(child) for child in node.children.values()]
            if child_tasks:
                await asyncio.gather(*child_tasks, return_exceptions=True)

        # 2. Collect children interface specs for context
        children_interfaces = {}
        for child in node.children.values():
            child_interface = self.tracker.get_pass2_3_content(child.module_name)
            if child_interface:
                children_interfaces[child.module_name] = child_interface

        # 3. Check if already processed in this session
        if module_name in self._processed_pass2_3:
            return self.tracker.get_pass2_3_content(module_name) or ""

        # 4. Check if already done in persistent storage
        if self.tracker.is_pass2_3_done(module_name):
            self._processed_pass2_3.add(module_name)
            print(f"  [Pass 2.3] Module {module_name} already has interface spec (skipping LLM)")
            return self.tracker.get_pass2_3_content(module_name) or ""

        # 5. Check if we have necessary context (need Pass 1)
        if not self.tracker.is_pass1_done(module_name):
            print(f"  [Pass 2.3] Skipping {module_name} (Pass 1 not completed)")
            self._processed_pass2_3.add(module_name)
            return ""

        # 6. Generate interface specification
        print(f"  [Pass 2.3] Generating interface spec for {module_name}...")

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                interface_doc = await self._generate_interface_spec(
                    node, children_interfaces
                )

            if interface_doc:
                self._save_module_file(module_name, "interface_spec.md", interface_doc)
                self.tracker.update_pass2_3(module_name, interface_doc)

            self._processed_pass2_3.add(module_name)
            self._save_metadata(node)
            return interface_doc

        except Exception as e:
            print(f"  [ERROR] [Pass 2.3] Failed to generate interface spec for {module_name}: {e}")
            self._processed_pass2_3.add(module_name)
            return ""

    async def _generate_interface_spec(
        self, node: Any, children_interfaces: Dict[str, str]
    ) -> str:
        """Generate interface specification documentation for a module.

        Args:
            node: HierarchyNode for the module
            children_interfaces: Dict mapping child module names to their interface specs

        Returns:
            Markdown document with interface specification
        """
        module_name = node.module_name

        # Get Pass 1 preview
        preview = self.tracker.get_pass1_content(module_name) or "无功能预览"

        # Port summary (detailed)
        port_summary = self.resolver.get_port_summary(module_name)

        # Get structured graph description
        graph_description = ""
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph, include_source=False)
        else:
            graph_description = "无拓扑结构信息"

        # Format children interface summaries
        children_interfaces_str = ""
        if children_interfaces:
            parts = []
            for child_name, interface_doc in sorted(children_interfaces.items()):
                # Extract just the interface summary section
                lines = interface_doc.split('\n')
                # Take first 15 lines as summary
                summary_lines = lines[:15] if len(lines) > 15 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 15:
                    summary += "\n\n[... 更多内容见子模块接口规范文档]"
                parts.append(f"## 子模块 {child_name}:\n{summary}\n")
            children_interfaces_str = "\n".join(parts)
        else:
            children_interfaces_str = "无子模块接口信息"

        # Format prompt
        prompt = PASS2_3_INTERFACE_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            children_interfaces=children_interfaces_str
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_3_interface")

        interface_doc, token_stats = await self.llm.generate(
            PASS2_3_INTERFACE_SYSTEM,
            prompt,
            log_path=log_path,
        )

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_3",
            **token_stats
        })

        return interface_doc


    # ==================== Pass 2.4: Functional Detailed Description ====================

    async def _run_pass2_4(self, node: Any) -> str:
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_4",
            module_name,
            lambda: self._run_pass2_4_impl(node),
        )

    async def _run_pass2_4_impl(self, node: Any) -> str:
        """Pass 2.4: Bottom-up functional detailed description generation.

        Returns the functional description for this module.
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.4] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        children_functional = {}
        if node.children:
            async def process_child(child):
                child_desc = await self._run_pass2_4(child)
                return child.module_name, child_desc

            child_tasks = [process_child(child) for child in node.children.values()]
            if child_tasks:
                results = await asyncio.gather(*child_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    child_name, child_desc = result
                    if child_desc:
                        children_functional[child_name] = child_desc

        # 2. Check if already processed in this session
        if module_name in self._processed_pass2_4:
            return self.tracker.get_pass2_4_content(module_name) or ""

        # 3. Check if already done in persistent storage
        if self.tracker.is_pass2_4_done(module_name):
            self._processed_pass2_4.add(module_name)
            print(f"  [Pass 2.4] Module {module_name} already has functional description (skipping LLM)")
            return self.tracker.get_pass2_4_content(module_name) or ""

        # 4. Check if we have necessary context (need Pass 1.5)
        if not self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 2.4] Skipping {module_name} (Pass 1.5 not completed)")
            self._processed_pass2_4.add(module_name)
            return ""

        # 5. Generate functional description
        print(f"  [Pass 2.4] Generating functional description for {module_name}...")

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                functional_doc = await self._generate_functional_description(
                    node, children_functional
                )

            if functional_doc:
                self._save_module_file(module_name, "functional_desc.md", functional_doc)
                self.tracker.update_pass2_4(module_name, functional_doc)

            self._processed_pass2_4.add(module_name)
            self._save_metadata(node)
            return functional_doc

        except Exception as e:
            print(f"  [ERROR] [Pass 2.4] Failed to generate functional description for {module_name}: {e}")
            self.tracker.mark_failed(module_name, f"[Pass 2.4] {e}")
            self._processed_pass2_4.add(module_name)
            return ""

    async def _generate_functional_description(
        self, node: Any, children_functional: Dict[str, str]
    ) -> str:
        """Generate functional detailed description for a module.

        Args:
            node: HierarchyNode for the module
            children_functional: Dict mapping child module names to their functional descriptions

        Returns:
            Markdown document with functional detailed description
        """
        module_name = node.module_name

        # Get Pass 1 preview
        preview = self.tracker.get_pass1_content(module_name) or "无功能预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)

        # Get structured graph description with full source code
        graph_description = ""
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph, include_source=True)
        else:
            # Fallback: read full source code (no truncation)
            source_code = self.resolver.read_source(module_name) or "Source not found."
            graph_description = f"```verilog\n{source_code}\n```"

        # Get block descriptions from Pass 1.5
        block_descriptions = self._format_block_summaries(module_name)

        # Format children functional descriptions
        children_functional_str = ""
        if children_functional:
            parts = []
            for child_name, func_desc in sorted(children_functional.items()):
                # Extract first 20 lines as summary
                lines = func_desc.split('\n')
                summary_lines = lines[:20] if len(lines) > 20 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 20:
                    summary += "\n\n[... 更多内容见子模块功能详细描述文档]"
                parts.append(f"## 子模块 {child_name}:\n{summary}\n")
            children_functional_str = "\n".join(parts)
        else:
            children_functional_str = "无子模块功能详细描述"

        # Format prompt
        prompt = PASS2_4_FUNCTIONAL_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            block_descriptions=block_descriptions,
            children_functional=children_functional_str
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_4_functional")

        functional_doc, token_stats = await self.llm.generate(
            PASS2_4_FUNCTIONAL_SYSTEM,
            prompt,
            log_path=log_path,
        )

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_4",
            **token_stats
        })

        return functional_doc


    # ==================== Pass 2.5: Register Description Generation ====================

    async def _run_pass2_5(self, node: Any) -> str:
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_5",
            module_name,
            lambda: self._run_pass2_5_impl(node),
        )

    async def _run_pass2_5_impl(self, node: Any) -> str:
        """Pass 2.5: Bottom-up register description generation.

        Returns the register description for this module.
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.5] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        children_register = {}
        if node.children:
            async def process_child(child):
                child_desc = await self._run_pass2_5(child)
                return child.module_name, child_desc

            child_tasks = [process_child(child) for child in node.children.values()]
            if child_tasks:
                results = await asyncio.gather(*child_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    child_name, child_desc = result
                    if child_desc:
                        children_register[child_name] = child_desc

        # 2. Check if already processed in this session
        if module_name in self._processed_pass2_5:
            return self.tracker.get_pass2_5_content(module_name) or ""

        # 3. Check if already done in persistent storage
        if self.tracker.is_pass2_5_done(module_name):
            self._processed_pass2_5.add(module_name)
            print(f"  [Pass 2.5] Module {module_name} already has register description (skipping LLM)")
            return self.tracker.get_pass2_5_content(module_name) or ""

        # 4. Check if we have necessary context (need Pass 1.5)
        if not self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 2.5] Skipping {module_name} (Pass 1.5 not completed)")
            self._processed_pass2_5.add(module_name)
            return ""

        # 5. Generate register description
        print(f"  [Pass 2.5] Generating register description for {module_name}...")

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                register_doc = await self._generate_register_description(
                    node, children_register
                )

            if register_doc:
                self._save_module_file(module_name, "register_desc.md", register_doc)
                self.tracker.update_pass2_5(module_name, register_doc)

            self._processed_pass2_5.add(module_name)
            self._save_metadata(node)
            return register_doc

        except Exception as e:
            print(f"  [ERROR] [Pass 2.5] Failed to generate register description for {module_name}: {e}")
            self.tracker.mark_failed(module_name, f"[Pass 2.5] {e}")
            self._processed_pass2_5.add(module_name)
            return ""

    async def _generate_register_description(
        self, node: Any, children_register: Dict[str, str]
    ) -> str:
        """Generate register description for a module.

        Args:
            node: HierarchyNode for the module
            children_register: Dict mapping child module names to their register descriptions

        Returns:
            Markdown document with register description
        """
        module_name = node.module_name

        # Get Pass 1 preview
        preview = self.tracker.get_pass1_content(module_name) or "无功能预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)

        # Get structured graph description with full source code
        graph_description = ""
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph, include_source=True)
        else:
            # Fallback: read full source code (no truncation)
            source_code = self.resolver.read_source(module_name) or "Source not found."
            graph_description = f"```verilog\n{source_code}\n```"

        # Format children register descriptions
        children_register_str = ""
        if children_register:
            parts = []
            for child_name, reg_desc in sorted(children_register.items()):
                # Extract first 20 lines as summary
                lines = reg_desc.split('\n')
                summary_lines = lines[:20] if len(lines) > 20 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 20:
                    summary += "\n\n[... 更多内容见子模块寄存器描述文档]"
                parts.append(f"## 子模块 {child_name}:\n{summary}\n")
            children_register_str = "\n".join(parts)
        else:
            children_register_str = "无子模块寄存器描述"

        # Format prompt
        prompt = PASS2_5_REGISTER_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            children_register=children_register_str
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_5_register")

        register_doc, token_stats = await self.llm.generate(
            PASS2_5_REGISTER_SYSTEM,
            prompt,
            log_path=log_path,
        )

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_5",
            **token_stats
        })

        return register_doc


    # ==================== Pass 2.6: Timing Constraints and CDC Generation ====================

    async def _run_pass2_6(self, node: Any) -> str:
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_6",
            module_name,
            lambda: self._run_pass2_6_impl(node),
        )

    async def _run_pass2_6_impl(self, node: Any) -> str:
        """Pass 2.6: Bottom-up timing constraints and CDC documentation generation.

        Returns the timing constraints and CDC documentation for this module.
        Children are processed concurrently (up to max_concurrent_modules at a time).
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.6] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        children_timing = {}
        if node.children:
            async def process_child(child):
                child_desc = await self._run_pass2_6(child)
                return child.module_name, child_desc

            child_tasks = [process_child(child) for child in node.children.values()]
            if child_tasks:
                results = await asyncio.gather(*child_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    child_name, child_desc = result
                    if child_desc:
                        children_timing[child_name] = child_desc

        # 2. Check if already processed in this session
        if module_name in self._processed_pass2_6:
            return self.tracker.get_pass2_6_content(module_name) or ""

        # 3. Check if already done in persistent storage
        if self.tracker.is_pass2_6_done(module_name):
            self._processed_pass2_6.add(module_name)
            print(f"  [Pass 2.6] Module {module_name} already has timing/CDC doc (skipping LLM)")
            return self.tracker.get_pass2_6_content(module_name) or ""

        # 4. Check if we have necessary context (need Pass 1.5)
        if not self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 2.6] Skipping {module_name} (Pass 1.5 not completed)")
            self._processed_pass2_6.add(module_name)
            return ""

        # 5. Generate timing constraints and CDC documentation
        print(f"  [Pass 2.6] Generating timing/CDC doc for {module_name}...")

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                timing_cdc_doc = await self._generate_timing_cdc_doc(
                    node, children_timing
                )

            if timing_cdc_doc:
                self._save_module_file(module_name, "timing_cdc.md", timing_cdc_doc)
                self.tracker.update_pass2_6(module_name, timing_cdc_doc)

            self._processed_pass2_6.add(module_name)
            self._save_metadata(node)
            return timing_cdc_doc

        except Exception as e:
            print(f"  [ERROR] [Pass 2.6] Failed to generate timing/CDC doc for {module_name}: {e}")
            self.tracker.mark_failed(module_name, f"[Pass 2.6] {e}")
            self._processed_pass2_6.add(module_name)
            return ""

    async def _generate_timing_cdc_doc(
        self, node: Any, children_timing: Dict[str, str]
    ) -> str:
        """Generate timing constraints and CDC documentation for a module.

        Args:
            node: HierarchyNode for the module
            children_timing: Dict mapping child module names to their timing/CDC docs

        Returns:
            Markdown document with timing constraints and CDC documentation
        """
        module_name = node.module_name

        # Get Pass 1 preview
        preview = self.tracker.get_pass1_content(module_name) or "无功能预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)

        # Get structured graph description with full source code
        graph_description = ""
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph, include_source=True)
        else:
            # Fallback: read full source code (no truncation)
            source_code = self.resolver.read_source(module_name) or "Source not found."
            graph_description = f"```verilog\n{source_code}\n```"

        # Format children timing/CDC descriptions
        children_timing_str = ""
        if children_timing:
            parts = []
            for child_name, timing_doc in sorted(children_timing.items()):
                # Extract first 20 lines as summary
                lines = timing_doc.split('\n')
                summary_lines = lines[:20] if len(lines) > 20 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 20:
                    summary += "\n\n[... 更多内容见子模块时序约束文档]"
                parts.append(f"## 子模块 {child_name}:\n{summary}\n")
            children_timing_str = "\n".join(parts)
        else:
            children_timing_str = "无子模块时序约束描述"

        # Format prompt
        prompt = PASS2_6_TIMING_CDC_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            children_timing=children_timing_str
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_6_timing")

        timing_cdc_doc, token_stats = await self.llm.generate(
            PASS2_6_TIMING_CDC_SYSTEM,
            prompt,
            log_path=log_path,
        )

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_6",
            **token_stats
        })

        return timing_cdc_doc


    # ==================== Pass 2.7: Architecture Design Generation ====================

    async def _run_pass2_7(self, node: Any) -> str:
        module_name = node.module_name
        return await self._run_singleflight(
            "pass2_7",
            module_name,
            lambda: self._run_pass2_7_impl(node),
        )

    async def _run_pass2_7_impl(self, node: Any) -> str:
        """Pass 2.7: Bottom-up architecture design documentation generation.

        Returns the architecture design documentation for this module.
        """
        module_name = node.module_name

        # Skip/limit checks (same as Pass 2)
        if self._should_skip(module_name):
            print(f"[INFO] [Pass 2.7] Skipping module {module_name} (matches skip pattern)")
            return ""

        if self.max_modules > 0 and module_name not in self._processed_pass1:
            return ""

        # 1. Recurse children first (ALWAYS, even if this module is already done) - concurrently
        children_architecture = {}
        if node.children:
            async def process_child(child):
                child_desc = await self._run_pass2_7(child)
                return child.module_name, child_desc

            child_tasks = [process_child(child) for child in node.children.values()]
            if child_tasks:
                results = await asyncio.gather(*child_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    child_name, child_desc = result
                    if child_desc:
                        children_architecture[child_name] = child_desc

        # 2. Check if already processed in this session
        if module_name in self._processed_pass2_7:
            return self.tracker.get_pass2_7_content(module_name) or ""

        # 3. Check if already done in persistent storage
        if self.tracker.is_pass2_7_done(module_name):
            self._processed_pass2_7.add(module_name)
            print(f"  [Pass 2.7] Module {module_name} already has architecture doc (skipping LLM)")
            return self.tracker.get_pass2_7_content(module_name) or ""

        # 4. Check if we have necessary context (need Pass 1.5)
        if not self.tracker.is_pass1_5_done(module_name):
            print(f"  [Pass 2.7] Skipping {module_name} (Pass 1.5 not completed)")
            self._processed_pass2_7.add(module_name)
            return ""

        # 5. Generate architecture design documentation
        print(f"  [Pass 2.7] Generating architecture doc for {module_name}...")

        try:
            # Use semaphore to control concurrency
            async with self._module_semaphore:
                architecture_doc = await self._generate_architecture_doc(
                    node, children_architecture
                )

            if architecture_doc:
                self._save_module_file(module_name, "architecture.md", architecture_doc)
                self.tracker.update_pass2_7(module_name, architecture_doc)

            self._processed_pass2_7.add(module_name)
            self._save_metadata(node)
            return architecture_doc

        except Exception as e:
            print(f"  [ERROR] [Pass 2.7] Failed to generate architecture doc for {module_name}: {e}")
            self.tracker.mark_failed(module_name, f"[Pass 2.7] {e}")
            self._processed_pass2_7.add(module_name)
            return ""

    async def _generate_architecture_doc(
        self, node: Any, children_architecture: Dict[str, str]
    ) -> str:
        """Generate architecture design documentation for a module.

        Args:
            node: HierarchyNode for the module
            children_architecture: Dict mapping child module names to their architecture docs

        Returns:
            Markdown document with architecture design documentation
        """
        module_name = node.module_name

        # Get Pass 1 preview
        preview = self.tracker.get_pass1_content(module_name) or "无功能预览"

        # Port summary
        port_summary = self.resolver.get_port_summary(module_name)

        # Get structured graph description with full source code
        graph_description = ""
        if module_name in self._graphs:
            graph = self._graphs[module_name]
            graph_description = self._format_graph_description(graph, include_source=True)
        else:
            # Fallback: read full source code (no truncation)
            source_code = self.resolver.read_source(module_name) or "Source not found."
            graph_description = f"```verilog\n{source_code}\n```"

        # Get block descriptions from Pass 1.5
        block_descriptions = self._format_block_summaries(module_name)

        # Format children architecture descriptions
        children_architecture_str = ""
        if children_architecture:
            parts = []
            for child_name, arch_doc in sorted(children_architecture.items()):
                # Extract first 20 lines as summary
                lines = arch_doc.split('\n')
                summary_lines = lines[:20] if len(lines) > 20 else lines
                summary = '\n'.join(summary_lines)
                if len(lines) > 20:
                    summary += "\n\n[... 更多内容见子模块架构设计文档]"
                parts.append(f"## 子模块 {child_name}:\n{summary}\n")
            children_architecture_str = "\n".join(parts)
        else:
            children_architecture_str = "无子模块架构设计描述"

        # Format prompt
        prompt = PASS2_7_ARCHITECTURE_PROMPT.format(
            module_name=module_name,
            preview=preview,
            port_summary=port_summary,
            graph_description=graph_description,
            block_descriptions=block_descriptions,
            children_architecture=children_architecture_str
        )

        # Call LLM
        log_path = self._module_log_path(module_name, "pass2_7_architecture")

        architecture_doc, token_stats = await self.llm.generate(
            PASS2_7_ARCHITECTURE_SYSTEM,
            prompt,
            log_path=log_path,
        )

        # Record token stats
        if module_name not in self._token_stats:
            self._token_stats[module_name] = []
        self._token_stats[module_name].append({
            "pass": "pass2_7",
            **token_stats
        })

        return architecture_doc

    # ==================== Pass 3: Chip-level Overview ====================

    def clear_pass3_progress(self):
        """Clear pass3 project-level progress state."""
        self.pass3_generator.clear_progress()

    async def _run_pass3_1(self, top_node: Any) -> Optional[str]:
        """Pass 3.1: Generate SoC-level subsystem partition markdown using Agent exploration."""
        return await self.pass3_generator.run_pass3_1(top_node)

    async def _run_pass3_2(self, top_node: Any) -> Optional[str]:
        """Pass 3.2: Generate core microarchitecture partition markdown."""
        return await self.pass3_generator.run_pass3_2(top_node)

    async def _run_pass3(self, root: Any):
        """Run chip-level pass3 generation (overview + subsystem pages)."""
        await self.pass3_generator.run_pass3(root)
