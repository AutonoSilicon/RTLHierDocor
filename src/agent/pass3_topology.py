"""Pass3 topology and discovery helpers.

This module encapsulates core candidate discovery, topology block building,
and instruction route exploration for pass3.
"""

import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .pass3_utils import contains_any


class Pass3Topology:
    """Helper object encapsulating topology and discovery operations."""

    def __init__(self, generator: Any):
        self.g = generator

    def build_instance_path_index(self, top_node: Any) -> Dict[str, Any]:
        """Build a path-to-node index for the entire hierarchy."""
        index: Dict[str, Any] = {}

        def walk(node: Any, path: str):
            index[path] = node
            for child in node.children.values():
                child_path = f"{path}/{child.instance_name}" if path else child.instance_name
                walk(child, child_path)

        walk(top_node, "")
        return index

    def resolve_root_token_to_paths(self, token: str, path_index: Dict[str, Any]) -> List[str]:
        """Resolve a root token to one or more instance paths."""
        cleaned = (token or "").strip().strip("/")
        if not cleaned:
            return []

        if cleaned in ("<top>", "top", "TOP"):
            return [""]

        if cleaned in path_index:
            return [cleaned]

        by_instance = [
            p for p, n in path_index.items()
            if p and getattr(n, "instance_name", "") == cleaned
        ]
        if by_instance:
            by_instance.sort(key=lambda p: (p.count('/'), p))
            return by_instance[:1]

        by_module = [
            p for p, n in path_index.items()
            if p and getattr(n, "module_name", "") == cleaned
        ]
        if by_module:
            by_module.sort(key=lambda p: (p.count('/'), p))
            return by_module[:2]

        return []

    def discover_core_candidates(self, top_node: Any) -> List[Dict[str, Any]]:
        """Discover and rank core candidate modules in the hierarchy.

        Uses keyword matching on module/instance names to find likely CPU cores.
        """
        primary_kw = ["core", "cpu", "ct_top"]
        micro_kw = ["ifu", "idu", "iu", "lsu", "rtu", "biu", "cp0", "had", "fpu", "vpu"]

        candidates: List[Dict[str, Any]] = []
        queue: List[Any] = [top_node]

        while queue:
            node = queue.pop(0)
            for child in node.children.values():
                queue.append(child)

            if node is top_node:
                continue

            path = node.get_path() if hasattr(node, "get_path") else node.instance_name
            name_blob = f"{node.instance_name} {node.module_name}".lower()

            score = 0
            primary_hits = contains_any(name_blob, primary_kw)
            score += len(primary_hits) * 60

            child_blob = " ".join(
                f"{c.instance_name} {c.module_name}".lower()
                for c in node.children.values()
            )
            micro_hits = contains_any(child_blob, micro_kw)
            score += len(set(micro_hits)) * 10

            if node.depth <= 2:
                score += 10
            if len(node.children) >= 6:
                score += 8
            if "ct_top" in name_blob:
                score += 25

            if score <= 0:
                continue

            key_children = sorted({c.module_name for c in node.children.values()})[:10]
            candidates.append({
                "path": path,
                "instance": node.instance_name,
                "module": node.module_name,
                "depth": node.depth,
                "children": len(node.children),
                "score": score,
                "primary_hits": sorted(set(primary_hits)),
                "micro_hits": sorted(set(micro_hits)),
                "key_children": key_children,
            })

        candidates.sort(key=lambda c: (-c["score"], c["depth"], c["path"]))
        return candidates

    def tool_explore_core(self, top_node: Any, max_candidates: int = 10) -> str:
        """Generate core exploration report for agent tool."""
        try:
            limit = max(1, min(int(max_candidates), 20))
        except Exception:
            limit = 10

        cands = self.discover_core_candidates(top_node)
        if not cands:
            return "[exploreCore]\n\nNo core-like candidates found."

        chosen = [c for c in cands if c["score"] >= 90][:4]
        if not chosen:
            chosen = cands[: min(2, len(cands))]

        lines = [
            "[exploreCore]",
            "",
            "## Recommended Core Roots",
        ]

        for c in chosen:
            lines.append(
                f"- {c['path']} ({c['instance']} / {c['module']}), score={c['score']}, children={c['children']}"
            )

        lines.extend([
            "",
            "## Candidate Ranking",
            "",
            "| Rank | Instance Path | Instance(Module) | Depth | Children | Score | Name Hits | Micro Hits |",
            "|---:|---|---|---:|---:|---:|---|---|",
        ])

        for idx, c in enumerate(cands[:limit], start=1):
            name_hits = ",".join(c["primary_hits"]) or "-"
            micro_hits = ",".join(c["micro_hits"]) or "-"
            lines.append(
                f"| {idx} | {c['path']} | {c['instance']}({c['module']}) | {c['depth']} | {c['children']} | {c['score']} | {name_hits} | {micro_hits} |"
            )

        lines.append("\n## Top Candidate Summaries")
        for c in chosen:
            summary_src = (
                self.g.owner.tracker.get_pass2_7_content(c["module"]) or
                self.g.owner.tracker.get_pass2_content(c["module"]) or
                self.g.owner.tracker.get_pass1_content(c["module"]) or
                "无可用文档摘要"
            )
            summary = summary_src
            lines.append(f"\n### {c['path']} ({c['module']})\n{summary}")

        return "\n".join(lines)

    def build_topology_block(self, module_name: str) -> str:
        """Build a topology block for a module.

        Prefer core topology generation from SimplifiedGraph. Fall back to
        legacy pass2 artifacts only when core topology is unavailable.
        """
        core_topology = self._build_core_topology_block(module_name)
        if core_topology:
            return core_topology.strip()

        topology_prompt = self._load_pass2_4_topology_prompt(module_name)
        if topology_prompt:
            return topology_prompt.strip()

        tracker = self.g.owner.tracker
        structured_src = (
            tracker.get_pass2_4_content(module_name)
            or tracker.get_pass2_content(module_name)
            or tracker.get_pass2_7_content(module_name)
            or ""
        )
        if structured_src:
            return structured_src.strip()

        resolver = self.g.owner.resolver
        topology_src = resolver.get_topology_content(module_name)
        if not topology_src:
            return f"# Topology for {module_name}\n\n无可用拓扑数据"
        return topology_src

    def build_pass2_style_topology_block(self, module_name: str) -> str:
        """Backward-compatible alias for legacy call sites."""
        return self.build_topology_block(module_name)

    def _build_core_topology_block(self, module_name: str) -> str:
        """Build topology text directly from SimplifiedGraph core capability."""
        graph = self._get_simplified_graph(module_name)
        if graph is None:
            return ""
        return self._format_graph_description(graph, include_source=True)

    def _get_simplified_graph(self, module_name: str) -> Any:
        owner = getattr(self.g, "owner", None)
        if owner is None:
            return None

        graphs = getattr(owner, "_graphs", None)
        if isinstance(graphs, dict) and module_name in graphs:
            return graphs[module_name]

        schematic_gen = getattr(owner, "schematic_gen", None)
        if schematic_gen is None:
            return None
        try:
            graph = schematic_gen.generate_simplified_graph(module_name)
        except Exception:
            return None
        if graph is not None and isinstance(graphs, dict):
            graphs[module_name] = graph
        return graph

    def _format_graph_description(self, graph: Any, include_source: bool = True) -> str:
        """Format SimplifiedGraph as a topology block for agent consumption."""
        predecessors: Dict[str, List[str]] = {}
        successors: Dict[str, List[str]] = {}

        all_nodes = (
            set(graph.proc_nodes.keys())
            | set(graph.comb_nodes.keys())
            | set(graph.io_ports.keys())
            | set(graph.submodules.keys())
            | set(graph.seq_cells.keys())
        )
        for node in all_nodes:
            predecessors[node] = []
            successors[node] = []

        unique_edges: List[Tuple[str, str]] = []
        seen_edges = set()
        for src, dst in graph.edges:
            if src not in all_nodes or dst not in all_nodes:
                continue
            key = (src, dst)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            unique_edges.append(key)
            successors[src].append(dst)
            predecessors[dst].append(src)

        def format_conn_list(node_ids: List[str]) -> str:
            if not node_ids:
                return "None"
            names: List[str] = []
            seen_names = set()
            for node_id in node_ids:
                name = self._resolve_node_name_for_desc(node_id, graph)
                if node_id in graph.proc_nodes:
                    name = f"{name}(PROC)"
                elif node_id in graph.comb_nodes:
                    name = f"{name}({graph.comb_nodes[node_id].comb_type})"
                elif node_id in graph.submodules:
                    name = f"{name}(submodule)"
                elif node_id in graph.io_ports:
                    name = f"{name}(port)"
                elif node_id in graph.seq_cells:
                    seq_type = graph.seq_cells[node_id] or "seq"
                    name = f"{name}({seq_type})"
                if name in seen_names:
                    continue
                seen_names.add(name)
                names.append(name)
            return ", ".join(names)

        def get_source_for_block(block_id: str) -> str:
            resolver = getattr(self.g.owner, "resolver", None)
            if resolver is None:
                return ""
            if block_id in graph.proc_nodes:
                proc_info = graph.proc_nodes[block_id]
                if getattr(proc_info, "source_location", None):
                    return resolver.read_block_source([proc_info.source_location])
            if block_id in graph.comb_nodes:
                comb_info = graph.comb_nodes[block_id]
                if getattr(comb_info, "source_locations", None):
                    return resolver.read_block_source(comb_info.source_locations)
            return ""

        all_blocks = set(graph.proc_nodes.keys()) | set(graph.comb_nodes.keys())
        adj: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = defaultdict(int)
        for block_id in all_blocks:
            in_degree[block_id] = 0
        for src, dst in unique_edges:
            if src in all_blocks and dst in all_blocks:
                adj[src].append(dst)
                in_degree[dst] += 1

        queue = sorted([node for node in all_blocks if in_degree[node] == 0])
        topo_order: List[str] = []
        while queue:
            node = queue.pop(0)
            topo_order.append(node)
            for neighbor in sorted(adj[node]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    queue.sort()
        topo_order.extend(sorted(all_blocks - set(topo_order)))

        parts: List[str] = [f"# Topology for {graph.module_name}"]
        block_parts: List[str] = []
        for block_id in topo_order:
            block_section: List[str] = []
            if block_id in graph.proc_nodes:
                proc_info = graph.proc_nodes[block_id]
                loc = getattr(proc_info, "source_location", None)
                if loc:
                    filename = loc.file_path.split('/')[-1] if '/' in loc.file_path else loc.file_path
                    end_line = loc.end_line or loc.start_line
                    block_section.append(f"- **{block_id}** [PROC] ({filename}:{loc.start_line}-{end_line})")
                else:
                    block_section.append(f"- **{block_id}** [PROC]")
            elif block_id in graph.comb_nodes:
                comb_info = graph.comb_nodes[block_id]
                header = f"- **{block_id}** [{comb_info.comb_type}, {comb_info.node_count} nodes]"
                loc_label = comb_info.location_info.get_display_label().replace("\\n", ", ")
                if loc_label:
                    header += f" ({loc_label})"
                block_section.append(header)
            else:
                continue

            block_section.append(f"  - Inputs from blocks: {format_conn_list(predecessors.get(block_id, []))}")
            block_section.append(f"  - Outputs to blocks: {format_conn_list(successors.get(block_id, []))}")
            if include_source:
                source = get_source_for_block(block_id)
                if source:
                    block_section.append("  - Source:")
                    block_section.append("    ```verilog")
                    for line in source.strip().split("\n"):
                        block_section.append(f"    {line}")
                    block_section.append("    ```")
            block_parts.append("\n".join(block_section))

        if block_parts:
            parts.append("")
            parts.append("## Circuit Blocks (in topological order, block-to-block view):")
            parts.append("\n\n".join(block_parts))

        boundary_edges: List[Tuple[str, str]] = []
        for src, dst in unique_edges:
            src_is_block = src in graph.proc_nodes or src in graph.comb_nodes
            dst_is_block = dst in graph.proc_nodes or dst in graph.comb_nodes
            if not (src_is_block and dst_is_block):
                boundary_edges.append((src, dst))

        if boundary_edges:
            parts.append("\n## Boundary Connections (including direct links):")
            for src, dst in sorted(boundary_edges):
                parts.append(
                    f"- {self._resolve_node_name_for_desc(src, graph)}{self._node_type_suffix(src, graph)}"
                    f" -> {self._resolve_node_name_for_desc(dst, graph)}{self._node_type_suffix(dst, graph)}"
                )

        if graph.submodules:
            parts.append("\n## Submodule Instances:")
            for submod_id, instance_name in sorted(graph.submodules.items()):
                parts.append(f"- **{instance_name}**")
                parts.append(f"  - Inputs from: {format_conn_list(predecessors.get(submod_id, []))}")
                parts.append(f"  - Outputs to: {format_conn_list(successors.get(submod_id, []))}")

        return "\n".join(parts).strip()

    @staticmethod
    def _resolve_node_name_for_desc(node_id: str, graph: Any) -> str:
        if node_id in graph.proc_nodes:
            return node_id
        if node_id in graph.comb_nodes:
            return node_id
        if node_id in graph.io_ports:
            return graph.io_ports[node_id].strip()[:30]
        if node_id in graph.submodules:
            return graph.submodules[node_id]
        if node_id in graph.seq_cells:
            return node_id
        return node_id

    @staticmethod
    def _node_type_suffix(node_id: str, graph: Any) -> str:
        if node_id in graph.io_ports:
            return "(port)"
        if node_id in graph.submodules:
            return "(submodule)"
        if node_id in graph.seq_cells:
            seq_type = graph.seq_cells[node_id] or "seq"
            return f"({seq_type})"
        if node_id in graph.proc_nodes:
            return "(PROC)"
        if node_id in graph.comb_nodes:
            return f"({graph.comb_nodes[node_id].comb_type})"
        return ""

    def _load_pass2_4_topology_prompt(self, module_name: str) -> str:
        """Load topologyized source block from pass2.4 debug prompt file.

        Source file format: output_dir/debug/<module>/debug_pass2_4_functional.md
        We extract exactly the original {graph_description} payload.
        """
        tracker = self.g.owner.tracker
        output_dir = str(getattr(tracker, "output_dir", "") or "").strip()
        if not output_dir:
            return ""

        debug_path = os.path.join(output_dir, "debug", module_name, "debug_pass2_4_functional.md")
        if not os.path.exists(debug_path):
            return ""

        try:
            with open(debug_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            return ""

        start_marker = "# Circuit Topology (PROC/COMB Logic Block Connection Diagram, Topologically Sorted, with Source Code):"
        end_marker = "# Child Module Functional Descriptions:"

        start = text.find(start_marker)
        if start >= 0:
            graph_start = start + len(start_marker)
            end = text.find(end_marker, graph_start)
            if end < 0:
                end = len(text)
            payload = text[graph_start:end].strip()
            if payload:
                return payload

        # Fallback: legacy debug format without explicit child heading.
        marker = "# Circuit Topology"
        idx = text.find(marker)
        if idx < 0:
            return ""
        return text[idx:].strip()

    def tool_explore_inst_route(
        self,
        top_node: Any,
        instruction: str,
        max_domains: int = 12,
    ) -> str:
        """Generate instruction route exploration report for agent tool."""
        try:
            limit = max(1, min(int(max_domains), 20))
        except Exception:
            limit = 12

        # Get core candidates
        cores = self.discover_core_candidates(top_node)

        # Build domain list from candidates
        domains: List[Dict[str, Any]] = []
        for c in cores[:limit]:
            domains.append({
                "path": c["path"],
                "instance": c["instance"],
                "module": c["module"],
                "depth": c["depth"],
                "score": c["score"],
            })

        if not domains:
            return f"[exploreInstRoute]\n\nNo domains found for instruction '{instruction}'."

        lines = [
            f"[exploreInstRoute]",
            f"",
            f"## Instruction: {instruction}",
            f"",
            f"## Candidate Domains (top {len(domains)})",
            "",
            "| Rank | Instance Path | Module | Depth | Score |",
            "|---:|---|---|---:|---:|",
        ]

        for idx, d in enumerate(domains, start=1):
            lines.append(
                f"| {idx} | {d['path']} | {d['module']} | {d['depth']} | {d['score']} |"
            )

        lines.extend(["", "## Recommendations"])

        # Recommend the highest scoring domain
        if domains:
            top = domains[0]
            lines.append(
                f"\n建议从 `{top['path']}` ({top['module']}) 开始追踪 "
                f"`{instruction}` 指令，因其包含关键子系统且层级较浅。"
            )

        return "\n".join(lines)
