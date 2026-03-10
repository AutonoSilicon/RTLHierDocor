"""Pass3 topology and discovery helpers.

This module encapsulates core candidate discovery, topology block building,
and instruction route exploration for pass3.
"""

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
            summary = self.g.owner._extract_summary(summary_src, max_lines=5, max_chars=500)
            lines.append(f"\n### {c['path']} ({c['module']})\n{summary}")

        return "\n".join(lines)

    def build_pass2_style_topology_block(self, module_name: str) -> str:
        """Build a pass2-style topology block for a module.

        Returns PROC/COMB topology with source annotations if available.
        """
        resolver = self.g.owner.resolver
        topology_src = resolver.get_topology_content(module_name)

        if not topology_src:
            return f"# Topology for {module_name}\n\n无可用拓扑数据"

        lines = [
            f"# Topology: {module_name}",
            "",
            "## PROC Blocks",
        ]

        # Extract PROC blocks from topology
        proc_blocks = resolver.get_proc_blocks(module_name)
        if proc_blocks:
            for proc in proc_blocks:
                proc_id = proc.get("id", "unknown")
                line_range = proc.get("line_range", {})
                start = line_range.get("start", 0)
                end = line_range.get("end", 0)
                lines.append(f"- {proc_id}: lines {start}-{end}")
        else:
            lines.append("- 无PROC块")

        lines.extend(["", "## COMB Blocks"])

        # Extract COMB blocks from topology
        comb_blocks = resolver.get_comb_blocks(module_name)
        if comb_blocks:
            for comb in comb_blocks[:20]:  # Limit to avoid huge output
                comb_id = comb.get("id", "unknown")
                line_range = comb.get("line_range", {})
                start = line_range.get("start", 0)
                end = line_range.get("end", 0)
                lines.append(f"- {comb_id}: lines {start}-{end}")
            if len(comb_blocks) > 20:
                lines.append(f"- ... and {len(comb_blocks) - 20} more")
        else:
            lines.append("- 无COMB块")

        return "\n".join(lines)

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
