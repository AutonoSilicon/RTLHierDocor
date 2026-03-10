"""Pass3 partition and subsystem rendering helpers.

This module extracts pass3.1/pass3.2 table parsing helpers from
pass3_generator.py to keep orchestration code shorter.
"""

from typing import Any, Dict, List, Optional


class Pass3PartitionHelpers:
    """Helper object encapsulating pass3 partition parsing/rendering helpers."""

    def __init__(self, generator: Any):
        self.g = generator

    def extract_pass3_1_partition_json(self, markdown: str, top_node: Any) -> Optional[Dict[str, Any]]:
        content = (markdown or "").strip()
        if not content:
            return None

        table_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]
        if len(table_lines) < 3:
            return None

        header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]
        header_map = {cell: idx for idx, cell in enumerate(header_cells)}

        idx_name = header_map.get("子系统", 0)
        idx_roots = header_map.get("Roots(root modules)", 1)
        idx_intent = header_map.get("职责/边界(intent)", 2)
        idx_sw_visible = header_map.get("软件可见面(sw visible)", 3)
        idx_evidence = header_map.get("证据(readDoc 摘要)", 4)
        idx_unknown = header_map.get("未知/待确认", 5)

        path_index = self.g._build_instance_path_index(top_node)
        subsystems: List[Dict[str, Any]] = []

        for row_idx, line in enumerate(table_lines[2:], start=1):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 2:
                continue

            name = cells[idx_name] if idx_name < len(cells) else ""
            roots_cell = cells[idx_roots] if idx_roots < len(cells) else ""
            if not name or not roots_cell:
                continue

            tokens = self.g._parse_roots_cell_tokens(roots_cell)
            root_paths: List[str] = []
            for token in tokens:
                root_paths.extend(self.g._resolve_root_token_to_paths(token, path_index))

            dedup_paths: List[str] = []
            seen_paths = set()
            for p in root_paths:
                if p in seen_paths:
                    continue
                seen_paths.add(p)
                dedup_paths.append(p)

            if not dedup_paths:
                continue

            roots = [{"instance_path": p if p else "<top>"} for p in dedup_paths]
            intent = cells[idx_intent] if idx_intent < len(cells) else ""
            sw_visible = cells[idx_sw_visible] if idx_sw_visible < len(cells) else ""
            evidence = cells[idx_evidence] if idx_evidence < len(cells) else ""
            unknown = cells[idx_unknown] if idx_unknown < len(cells) else ""

            subsystems.append({
                "id": f"SS{row_idx}",
                "name": name,
                "roots": roots,
                "intent": intent,
                "sw_visible": sw_visible,
                "evidence": evidence,
                "unknown": unknown,
            })

        if not subsystems:
            return None

        return {
            "top_module": top_node.module_name,
            "subsystems": subsystems,
        }

    def extract_pass3_2_partition_json(self, markdown: str, top_node: Any) -> Optional[Dict[str, Any]]:
        content = (markdown or "").strip()
        if not content:
            return None

        table_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]
        if len(table_lines) < 3:
            return None

        header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]
        header_map = {cell: idx for idx, cell in enumerate(header_cells)}

        idx_domain = header_map.get("微架构域", 0)
        idx_roots = header_map.get("Roots(root modules)", 1)
        idx_intent = header_map.get("职责/边界(intent)", 2)
        idx_iface = header_map.get("关键接口/状态(key interface/state)", 3)
        idx_evidence = header_map.get("证据(readDoc 摘要)", 4)
        idx_unknown = header_map.get("未知/待确认", 5)

        path_index = self.g._build_instance_path_index(top_node)
        micro_domains: List[Dict[str, Any]] = []

        for row_idx, line in enumerate(table_lines[2:], start=1):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 2:
                continue

            name = cells[idx_domain] if idx_domain < len(cells) else ""
            roots_cell = cells[idx_roots] if idx_roots < len(cells) else ""
            if not name or not roots_cell:
                continue

            tokens = self.g._parse_roots_cell_tokens(roots_cell)
            root_paths: List[str] = []
            for token in tokens:
                root_paths.extend(self.g._resolve_root_token_to_paths(token, path_index))

            dedup_paths: List[str] = []
            seen_paths = set()
            for p in root_paths:
                if p in seen_paths:
                    continue
                seen_paths.add(p)
                dedup_paths.append(p)

            if not dedup_paths:
                continue

            roots = [{"instance_path": p if p else "<top>"} for p in dedup_paths]
            intent = cells[idx_intent] if idx_intent < len(cells) else ""
            key_iface = cells[idx_iface] if idx_iface < len(cells) else ""
            evidence = cells[idx_evidence] if idx_evidence < len(cells) else ""
            unknown = cells[idx_unknown] if idx_unknown < len(cells) else ""

            micro_domains.append({
                "id": f"CORE{row_idx}",
                "name": name,
                "roots": roots,
                "intent": intent,
                "key_interface_state": key_iface,
                "evidence": evidence,
                "unknown": unknown,
            })

        if not micro_domains:
            return None

        return {
            "top_module": top_node.module_name,
            "micro_domains": micro_domains,
        }
