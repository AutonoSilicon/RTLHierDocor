"""Pass3 InStrack parsing logic.

This module encapsulates instruction tracking result parsing,
route extraction, and JSON coercion for pass3.3.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .pass3_utils import (
    extract_mermaid_code,
    extract_json_code,
    split_chain,
    normalize_instruction,
    normalize_str_list,
    coerce_optional_list,
    norm_header,
    is_separator_row,
)


class Pass3InStrackParsing:
    """Helper object encapsulating InStrack parsing operations."""

    def __init__(self, generator: Any):
        self.g = generator

    def extract_pass3_3_instrack_json(
        self,
        markdown: str,
        top_node: Any,
        instruction: str,
    ) -> Dict[str, Any]:
        """Extract InStrack result from markdown content.

        Parses either a Mermaid diagram or a markdown table to extract
        route information for an instruction.
        """
        content = (markdown or "").strip()
        out: Dict[str, Any] = {
            "schema_version": "pass3_3_instrack_v2",
            "top_module": top_node.module_name,
            "instruction": instruction,
            "route_blocks": [],
            "route_bridges": [],
            "route_modules_approx": [],
            "evidence": "",
            "unknown": "",
            "verification_mode": "topology_plus_doc_evidence",
        }
        if not content:
            return out

        mermaid_code = extract_mermaid_code(content)
        if mermaid_code:
            route_blocks, route_bridges, modules_approx = self.extract_routes_from_mermaid(mermaid_code)
            out["route_blocks"] = route_blocks
            out["route_bridges"] = route_bridges
            out["route_modules_approx"] = modules_approx
            out["raw_summary"] = self.g.owner._extract_summary(content, max_lines=24, max_chars=3000)
            out["mermaid"] = mermaid_code
            return out

        lines = content.splitlines()

        # Prefer parsing the table under the explicit final-route section.
        section_markers = ["## 最终路线表格", "### 最终路线表格"]
        start_idx = 0
        for i, line in enumerate(lines):
            if line.strip() in section_markers:
                start_idx = i + 1
                break

        scoped_lines = lines[start_idx:] if start_idx < len(lines) else lines
        table_lines = [line.strip() for line in scoped_lines if line.strip().startswith("|")]
        if len(table_lines) < 3:
            out["raw_summary"] = self.g.owner._extract_summary(content, max_lines=24, max_chars=3000)
            return out

        header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]

        header_map = {cell: idx for idx, cell in enumerate(header_cells)}
        header_map_norm = {norm_header(cell): idx for idx, cell in enumerate(header_cells)}

        idx_inst = header_map.get("指令", header_map_norm.get(norm_header("指令"), -1))
        idx_route_blocks = header_map.get("路线(Block路径链)")
        if idx_route_blocks is None:
            idx_route_blocks = header_map_norm.get(norm_header("路线(Block路径链)"))
        if idx_route_blocks is None:
            idx_route_blocks = header_map.get("Block路径链")
        if idx_route_blocks is None:
            idx_route_blocks = header_map_norm.get(norm_header("Block路径链"))
        if idx_route_blocks is None:
            idx_route_blocks = header_map.get("路线(路径链)")
        if idx_route_blocks is None:
            idx_route_blocks = header_map_norm.get(norm_header("路线(路径链)"))

        idx_route_bridge = header_map.get("桥接(Bridge链)")
        if idx_route_bridge is None:
            idx_route_bridge = header_map_norm.get(norm_header("桥接(Bridge链)"))
        if idx_route_bridge is None:
            idx_route_bridge = header_map.get("Bridge链")
        if idx_route_bridge is None:
            idx_route_bridge = header_map_norm.get(norm_header("Bridge链"))
        if idx_route_bridge is None:
            idx_route_bridge = header_map.get("桥接")
        if idx_route_bridge is None:
            idx_route_bridge = header_map_norm.get(norm_header("桥接"))

        idx_evidence = header_map.get("证据(readDoc 摘要)")
        if idx_evidence is None:
            idx_evidence = header_map_norm.get(norm_header("证据(readDoc 摘要)"))
        if idx_evidence is None:
            idx_evidence = header_map.get("证据")
        if idx_evidence is None:
            idx_evidence = header_map_norm.get(norm_header("证据"))
        if idx_evidence is None:
            idx_evidence = 3

        idx_unknown = header_map.get("未知/待确认")
        if idx_unknown is None:
            idx_unknown = header_map_norm.get(norm_header("未知/待确认"))
        if idx_unknown is None:
            idx_unknown = 4

        if idx_route_blocks is None:
            out["raw_summary"] = self.g.owner._extract_summary(content, max_lines=24, max_chars=3000)
            return out

        target_row: Optional[List[str]] = None
        normalized_target = normalize_instruction(instruction)

        candidate_rows: List[List[str]] = []
        for line in table_lines[2:]:
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 3 or is_separator_row(cells):
                continue

            candidate_rows.append(cells)

            if idx_inst >= 0:
                inst = cells[idx_inst] if idx_inst < len(cells) else ""
                if normalize_instruction(inst) == normalized_target:
                    target_row = cells
                    break

        if target_row is None and candidate_rows:
            def _chain_score(cells: List[str]) -> int:
                route_block = cells[idx_route_blocks] if idx_route_blocks < len(cells) else ""
                route_bridge = cells[idx_route_bridge] if (idx_route_bridge is not None and idx_route_bridge < len(cells)) else ""

                # Reject obvious placeholders/header echoes.
                reject_tokens = {"路径链", "route", "path", "chain", "bridge", "block"}
                low_b = route_block.lower()
                low_g = route_bridge.lower()
                if any(tok in low_b for tok in reject_tokens) and "->" not in route_block and "→" not in route_block:
                    return -1
                if route_bridge and any(tok in low_g for tok in reject_tokens) and "->" not in route_bridge and "→" not in route_bridge:
                    return -1

                return max(
                    len(split_chain(route_block)),
                    len(split_chain(route_bridge)),
                )

            target_row = max(candidate_rows, key=_chain_score)

        if not target_row:
            out["raw_summary"] = self.g.owner._extract_summary(content, max_lines=24, max_chars=3000)
            return out

        route_block_text = target_row[idx_route_blocks] if idx_route_blocks < len(target_row) else ""
        route_bridge_text = target_row[idx_route_bridge] if (idx_route_bridge is not None and idx_route_bridge < len(target_row)) else ""
        evidence_text = target_row[idx_evidence] if idx_evidence < len(target_row) else ""
        unknown_text = target_row[idx_unknown] if idx_unknown < len(target_row) else ""

        route_blocks = split_chain(route_block_text)
        route_bridges = split_chain(route_bridge_text)

        # Approximate module chain from module:block tokens.
        modules_approx: List[str] = []
        seen_modules = set()
        for item in route_blocks:
            if ":" not in item:
                continue
            module_name = item.split(":", 1)[0].strip()
            if not module_name or module_name in seen_modules:
                continue
            seen_modules.add(module_name)
            modules_approx.append(module_name)

        out["route_blocks"] = route_blocks
        out["route_bridges"] = route_bridges
        out["route_modules_approx"] = modules_approx
        out["evidence"] = evidence_text
        out["unknown"] = unknown_text
        out["raw_summary"] = self.g.owner._extract_summary(content, max_lines=24, max_chars=3000)
        return out

    def extract_routes_from_mermaid(self, mermaid_code: str) -> Tuple[List[str], List[str], List[str]]:
        """Extract route information from a Mermaid diagram.

        Returns:
            Tuple of (route_blocks, route_bridges, modules_approx)
        """
        code = mermaid_code or ""
        if not code:
            return [], [], []

        block_matches = re.findall(
            r"([A-Za-z0-9_.$\\]+):((?:PROC|COMB|IN_COMB|OUT_COMB)_\d+)",
            code,
            flags=re.I,
        )
        bridge_matches = re.findall(r"BRIDGE:([A-Za-z0-9_./$\\-]+)", code)

        route_blocks: List[str] = []
        seen_blocks = set()
        for module_name, block_id in block_matches:
            item = f"{module_name}:{block_id.upper()}"
            if item in seen_blocks:
                continue
            seen_blocks.add(item)
            route_blocks.append(item)

        route_bridges: List[str] = []
        seen_bridges = set()
        for bridge in bridge_matches:
            item = f"BRIDGE:{bridge}"
            if item in seen_bridges:
                continue
            seen_bridges.add(item)
            route_bridges.append(item)

        modules_approx: List[str] = []
        seen_modules = set()
        for item in route_blocks:
            if ":" not in item:
                continue
            module_name = item.split(":", 1)[0]
            if module_name in seen_modules:
                continue
            seen_modules.add(module_name)
            modules_approx.append(module_name)

        return route_blocks, route_bridges, modules_approx

    def coerce_instrack_mermaid_only(self, content: str) -> str:
        """Extract only the Mermaid diagram from content."""
        mermaid = extract_mermaid_code(content or "")
        if not mermaid:
            return (content or "").strip()
        return f"```mermaid\n{mermaid}\n```\n"

    def extract_pass3_3_search_json(self, content: str, top_node: Any, instruction: str) -> Dict[str, Any]:
        """Extract search result JSON from content."""
        out = {
            "schema_version": "pass3_3_1_startpoint_v2",
            "top_module": top_node.module_name,
            "instruction": instruction,
            "start_module": "",
            "start_instance": "",
            "start_block": "",
            "key_register": "",
            "key_register_line_range": {
                "start_line": 0,
                "end_line": 0,
            },
            "key_register_reason": "",
            "start_reason": "",
            "confidence": "low",
            "candidate_domains": [],
            "unknown": "",
        }
        json_body = extract_json_code(content or "")
        if not json_body:
            return out
        try:
            parsed = json.loads(json_body)
        except Exception:
            return out
        if not isinstance(parsed, dict):
            return out

        out["start_module"] = str(parsed.get("start_module") or "").strip()
        out["start_instance"] = str(parsed.get("start_instance") or "").strip()
        out["start_block"] = str(parsed.get("start_block") or "").strip()
        out["key_register"] = str(parsed.get("key_register") or "").strip()
        out["key_register_line_range"] = self.normalize_line_range(
            parsed.get("key_register_line_range")
        )
        out["key_register_reason"] = str(parsed.get("key_register_reason") or "").strip()
        out["start_reason"] = str(parsed.get("start_reason") or "").strip()
        conf = str(parsed.get("confidence") or "low").strip().lower()
        out["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        out["unknown"] = str(parsed.get("unknown") or "").strip()

        domains = parsed.get("candidate_domains")
        if isinstance(domains, list):
            normalized: List[str] = []
            for item in domains:
                text = str(item or "").strip()
                if text:
                    normalized.append(text)
            out["candidate_domains"] = normalized

        return out

    @staticmethod
    def normalize_line_range(value: Any) -> Dict[str, int]:
        """Normalize model output into a stable line-range object.

        Accepted inputs:
        - {"start_line": 10, "end_line": 20}
        - {"start": 10, "end": 20}
        - "10-20" / "10:20" / "10,20"
        - [10, 20]
        Unknown or invalid input returns {"start_line": 0, "end_line": 0}.
        """

        def _to_int(v: Any) -> int:
            try:
                n = int(str(v).strip())
                return n if n > 0 else 0
            except Exception:
                return 0

        start_line = 0
        end_line = 0

        if isinstance(value, dict):
            start_line = _to_int(value.get("start_line", value.get("start")))
            end_line = _to_int(value.get("end_line", value.get("end")))
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            start_line = _to_int(value[0])
            end_line = _to_int(value[1])
        elif isinstance(value, str) and value.strip():
            nums = re.findall(r"\d+", value)
            if len(nums) >= 2:
                start_line = _to_int(nums[0])
                end_line = _to_int(nums[1])
            elif len(nums) == 1:
                start_line = _to_int(nums[0])
                end_line = start_line

        if start_line > 0 and end_line == 0:
            end_line = start_line
        if end_line > 0 and start_line == 0:
            start_line = end_line
        if start_line > 0 and end_line > 0 and end_line < start_line:
            start_line, end_line = end_line, start_line

        return {
            "start_line": start_line,
            "end_line": end_line,
        }

    def coerce_instrack_search_json_only(self, content: str, top_node: Any, instruction: str) -> str:
        """Extract search result as a JSON code block."""
        parsed = self.extract_pass3_3_search_json(content, top_node, instruction)
        return "```json\n" + json.dumps(parsed, ensure_ascii=False, indent=2) + "\n```\n"

    def record_path_entry(
        self,
        *,
        instruction: str,
        current_node: Any,
        level: int,
        relation: str,
        start_block: str,
        key_path: str,
        evidence: str,
        confidence: str,
        handoff_to: str,
        register_reads: Optional[List[str]] = None,
        register_writes: Optional[List[str]] = None,
        key_conditions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a path entry record for InStrack tracking."""
        conf = (confidence or "medium").strip().lower()
        if conf not in {"high", "medium", "low"}:
            conf = "medium"
        return {
            "instruction": instruction,
            "module": getattr(current_node, "module_name", ""),
            "instance": getattr(current_node, "instance_name", ""),
            "level": int(level),
            "relation": (relation or "related").strip(),
            "start_block": (start_block or "").strip(),
            "key_path": (key_path or "").strip(),
            "evidence": (evidence or "").strip(),
            "confidence": conf,
            "handoff_to": (handoff_to or "").strip(),
            "register_reads": normalize_str_list(register_reads),
            "register_writes": normalize_str_list(register_writes),
            "key_conditions": normalize_str_list(key_conditions),
        }

    def is_register_level_record(self, item: Dict[str, Any]) -> bool:
        """Check if a path record has register-level detail."""
        reads = normalize_str_list(item.get("register_reads") or [])
        writes = normalize_str_list(item.get("register_writes") or [])
        conds = normalize_str_list(item.get("key_conditions") or [])
        return bool(reads and writes and conds)

    def enforce_register_level_records(
        self, search_json: Dict[str, Any], records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Filter to only register-level records, or mark confidence as low."""
        strong = [r for r in records if self.is_register_level_record(r)]
        if strong:
            return strong

        unknown = str(search_json.get("unknown") or "").strip()
        extra = "证据不足，当前记录未达到寄存器级粒度（缺少register_reads/register_writes/key_conditions）。"
        search_json["unknown"] = f"{unknown} {extra}".strip()
        search_json["confidence"] = "low"
        return []

    @staticmethod
    def dedupe_path_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate path records based on key fields."""
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in records or []:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("instruction", ""),
                item.get("instance", ""),
                item.get("start_block", ""),
                item.get("key_path", ""),
                item.get("handoff_to", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def render_instrack_search_markdown(
        self, search_json: Dict[str, Any], records: List[Dict[str, Any]]
    ) -> str:
        """Render InStrack search results as markdown."""
        lines: List[str] = [
            "# Pass3.3 Search Report",
            "",
            f"- instruction: {search_json.get('instruction', '')}",
            f"- start_module: {search_json.get('start_module', '')}",
            f"- start_instance: {search_json.get('start_instance', '')}",
        ]

        for rec in records:
            lines.append("")
            lines.append(f"## Level {rec.get('level', '?')} - {rec.get('instance', '')}")
            lines.append(f"- module: {rec.get('module', '')}")
            lines.append(f"- relation: {rec.get('relation', '')}")
            lines.append(f"- confidence: {rec.get('confidence', '')}")
            lines.append(f"- start_block: {rec.get('start_block', '')}")
            lines.append(f"- key_path: {rec.get('key_path', '')}")
            if rec.get("register_reads"):
                lines.append(f"- register_reads: {', '.join(rec['register_reads'])}")
            if rec.get("register_writes"):
                lines.append(f"- register_writes: {', '.join(rec['register_writes'])}")
            if rec.get("key_conditions"):
                lines.append(f"- key_conditions: {', '.join(rec['key_conditions'])}")
            if rec.get("evidence"):
                lines.append(f"- evidence: {rec['evidence'][:200]}...")
            if rec.get("handoff_to"):
                lines.append(f"- handoff_to: {rec['handoff_to']}")

        if search_json.get("unknown"):
            lines.extend(["", f"## Unknown\n{search_json['unknown']}"])

        return "\n".join(lines)
