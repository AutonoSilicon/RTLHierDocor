"""Block-level documentation generator for COMB and PROC blocks."""

import asyncio
from typing import Dict, Tuple, List, Any, Optional

from schematic.simplifier import SimplifiedGraph, ProcNodeInfo, CombNodeInfo
from .prompts import BLOCK_SYSTEM, BLOCK_PROMPT


class BlockDocGenerator:
    """Generates documentation for individual COMB and PROC blocks."""

    def __init__(self, llm: Any, resolver: Any, module_name: str,
                 graph: SimplifiedGraph, log_path: Optional[str] = None,
                 block_doc_threshold: int = 64):
        """Initialize block documentation generator.

        Args:
            llm: LLMBackend instance
            resolver: SourceResolver instance
            module_name: Name of the module
            graph: SimplifiedGraph for the module
            log_path: Path to write debug logs (per-module)
            block_doc_threshold: Min lines to invoke LLM for block doc
        """
        self.llm = llm
        self.resolver = resolver
        self.module_name = module_name
        self.graph = graph
        self.log_path = log_path or "debug.md"
        self.block_doc_threshold = block_doc_threshold

    async def generate_all(self) -> Dict[str, str]:
        """Generate documentation for all COMB and PROC blocks concurrently.

        Returns:
            Dict mapping block_id to documentation text
        """
        tasks = []

        # Generate docs for all PROC blocks
        for proc_id, proc_info in self.graph.proc_nodes.items():
            tasks.append(self._generate_proc_doc(proc_id, proc_info))

        # Generate docs for all COMB blocks
        for comb_id, comb_info in self.graph.comb_nodes.items():
            tasks.append(self._generate_comb_doc(comb_id, comb_info))

        # Run all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and build result dict
        block_docs = {}
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                block_id, doc_text = result
                block_docs[block_id] = doc_text
            elif isinstance(result, Exception):
                print(f"[WARN] Block doc generation failed: {result}")

        return block_docs

    async def _generate_proc_doc(self, proc_id: str, proc_info: ProcNodeInfo) -> Tuple[str, str]:
        """Generate documentation for a PROC block."""
        # Read source code snippet
        if proc_info.source_location:
            source_snippet = self.resolver.read_block_source([proc_info.source_location])
        else:
            source_snippet = "// Source location not available"

        # Check line count threshold
        line_count = source_snippet.count('\n') + 1
        if line_count < self.block_doc_threshold:
            # Skip LLM call for small blocks
            return (proc_id, f"(< {self.block_doc_threshold} lines, skipped)")

        # Get connectivity info
        upstream, downstream = self._get_connectivity(proc_id)

        # Format prompt
        prompt = BLOCK_PROMPT.format(
            module_name=self.module_name,
            block_id=proc_id,
            block_type="PROC (时序逻辑块)",
            upstream=upstream,
            downstream=downstream,
            source_snippet=source_snippet
        )

        # Call LLM
        doc, token_stats = await self.llm.generate(BLOCK_SYSTEM, prompt, log_path=self.log_path)

        return (proc_id, doc)

    async def _generate_comb_doc(self, comb_id: str, comb_info: CombNodeInfo) -> Tuple[str, str]:
        """Generate documentation for a COMB block."""
        # Read source code snippets
        if comb_info.source_locations:
            source_snippet = self.resolver.read_block_source(comb_info.source_locations)
        else:
            source_snippet = "// Source locations not available"

        # Check line count threshold
        line_count = source_snippet.count('\n') + 1
        if line_count < self.block_doc_threshold:
            # Skip LLM call for small blocks
            return (comb_id, f"(< {self.block_doc_threshold} lines, skipped)")

        # Get connectivity info
        upstream, downstream = self._get_connectivity(comb_id)

        # Format block type with Chinese translation
        block_type_map = {
            "IN_COMB": "IN_COMB (输入组合逻辑块)",
            "OUT_COMB": "OUT_COMB (输出组合逻辑块)",
            "COMB": "COMB (组合逻辑块)"
        }
        block_type = block_type_map.get(comb_info.comb_type, comb_info.comb_type)

        # Format prompt
        prompt = BLOCK_PROMPT.format(
            module_name=self.module_name,
            block_id=comb_id,
            block_type=block_type,
            upstream=upstream,
            downstream=downstream,
            source_snippet=source_snippet
        )

        # Call LLM
        doc, token_stats = await self.llm.generate(BLOCK_SYSTEM, prompt, log_path=self.log_path)

        return (comb_id, doc)

    def _get_connectivity(self, block_id: str) -> Tuple[str, str]:
        """Extract upstream and downstream connectivity for a block."""
        upstream_ids = [src for src, dst in self.graph.edges if dst == block_id]
        downstream_ids = [dst for src, dst in self.graph.edges if src == block_id]

        upstream_names = [self._resolve_node_name(node_id) for node_id in upstream_ids]
        downstream_names = [self._resolve_node_name(node_id) for node_id in downstream_ids]

        upstream_str = ", ".join(upstream_names) if upstream_names else "无"
        downstream_str = ", ".join(downstream_names) if downstream_names else "无"

        return upstream_str, downstream_str

    def _resolve_node_name(self, node_id: str) -> str:
        """Resolve node ID to a human-readable name."""
        if node_id in self.graph.proc_nodes:
            return f"PROC({node_id})"
        if node_id in self.graph.comb_nodes:
            comb_info = self.graph.comb_nodes[node_id]
            return f"{comb_info.comb_type}({node_id})"
        if node_id in self.graph.io_ports:
            port_label = self.graph.io_ports[node_id]
            return f"端口: {port_label}"
        if node_id in self.graph.submodules:
            instance_name = self.graph.submodules[node_id]
            return f"子模块: {instance_name}"
        return node_id
