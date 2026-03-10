"""Pass3.3 InStrack stage helpers.

This module keeps pass3.3-specific locate/draw logic out of pass3_generator.py
so the orchestration file stays shorter and easier to navigate.
"""

import json
import re
from typing import Any, Dict, List, Optional, Set

from .prompts import (
    PASS3_3_2_DRAW_SYSTEM,
    PASS3_3_2_DRAW_PROMPT,
)


class Pass3InStrackStages:
    """Helper object that encapsulates pass3.3 locate/draw stage operations."""

    def __init__(self, generator: Any):
        self.g = generator

    def build_pass3_3_locate_input_hash(
        self,
        top_module: str,
        instruction: str,
        instruction_datasheet: str,
        search_result_json_text: str,
        target_path_text: str,
    ) -> str:
        payload = {
            "version": "pass3_3_instrack_locate_cache_v3",
            "top_module": top_module,
            "instruction": instruction,
            "instruction_datasheet_hash": self.g._hash_text(instruction_datasheet),
            "search_result_json_hash": self.g._hash_text(search_result_json_text),
            "target_path_hash": self.g._hash_text(target_path_text),
            "tools_schema": self.g._pass3_3_2_tools(),
        }
        return self.g._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def build_pass3_3_draw_input_hash(
        self,
        top_module: str,
        instruction: str,
        instruction_datasheet: str,
        search_result_json_text: str,
    ) -> str:
        payload = {
            "version": "pass3_3_instrack_draw_cache_v9",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_2_DRAW_SYSTEM,
            "prompt_template": PASS3_3_2_DRAW_PROMPT,
            "instruction_datasheet_hash": self.g._hash_text(instruction_datasheet),
            "search_result_json_hash": self.g._hash_text(search_result_json_text),
            "tools_schema": self.g._pass3_3_2_tools(),
        }
        return self.g._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def collect_hierarchy_nodes_depth_first(self, top_node: Any) -> List[Any]:
        nodes: List[Any] = []

        def walk(node: Any):
            nodes.append(node)
            for child in node.children.values():
                walk(child)

        walk(top_node)
        return nodes

    def find_hierarchy_path(self, top_node: Any, target_module: str, target_instance: str = "") -> List[Any]:
        module_selector = str(target_module or "").strip()
        inst_selector = str(target_instance or "").strip()
        if not module_selector and not inst_selector:
            return []

        all_nodes = self.collect_hierarchy_nodes_depth_first(top_node)
        candidates: List[Any] = []
        for node in all_nodes:
            if inst_selector and node.instance_name != inst_selector:
                continue
            if module_selector and node.module_name != module_selector:
                continue
            candidates.append(node)

        if not candidates and module_selector:
            for node in all_nodes:
                if node.module_name == module_selector:
                    candidates.append(node)

        if not candidates:
            return []

        candidates.sort(key=lambda n: (n.depth, n.get_path() if hasattr(n, "get_path") else n.instance_name))
        target = candidates[0]
        path_nodes: List[Any] = []
        cur = target
        while cur is not None:
            path_nodes.append(cur)
            cur = getattr(cur, "parent", None)
        path_nodes.reverse()
        return path_nodes

    def resolve_start_node(self, top_node: Any, search_result: Dict[str, Any]) -> Optional[Any]:
        start_module = str(search_result.get("start_module") or "").strip()
        start_instance = str(search_result.get("start_instance") or "").strip()
        path_nodes = self.find_hierarchy_path(top_node, start_module, start_instance)
        if path_nodes:
            return path_nodes[-1]
        return None

    def get_module_description(self, module_name: str) -> str:
        text = self.g.owner.tracker.get_pass2_content(module_name) or ""
        if not text:
            text = self.g.owner.tracker.get_pass2_7_content(module_name) or ""
        if not text:
            text = self.g.owner.tracker.get_pass1_content(module_name) or "无可用 description 文档"
        return text

    def _get_functional_description(self, module_name: str) -> str:
        text = self.g.owner.tracker.get_pass2_4_content(module_name) or ""
        if not text:
            text = self.g.owner.tracker.get_pass2_1_content(module_name) or ""
        if not text:
            text = "无可用功能描述文档"
        return text

    def build_child_overview(self, current_node: Any) -> str:
        lines: List[str] = []
        for child in sorted(current_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_desc = self.get_module_description(child.module_name)
            lines.append(
                f"- {child.instance_name} ({child.module_name}): "
                f"{self.g.owner._extract_summary(child_desc, max_lines=3, max_chars=320)}"
            )
        return "\n".join(lines) if lines else "- 无子模块"

    @staticmethod
    def extract_json_code_blocks(markdown: str) -> List[str]:
        text = markdown or ""
        blocks = re.findall(r"```json\s*(.*?)```", text, flags=re.S | re.I)
        out: List[str] = []
        for block in blocks:
            body = (block or "").strip()
            if body:
                out.append(body)
        return out

    def extract_pass3_3_locate_json(
        self,
        content: str,
        instruction: str,
        current_node: Any,
        level: int,
        expected_next_instance: str,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "stage": "locate",
            "instruction": instruction,
            "current_module": current_node.module_name,
            "current_instance": current_node.instance_name,
            "current_level": int(level),
            "role_summary": "",
            "relevant_signals": [],
            "port_mapping": [],
            "next_target_instance": expected_next_instance,
            "fork_target": "",
            "confidence": "low",
            "unknown": "",
        }

        json_body = self.g._extract_json_code(content or "")
        if not json_body:
            return out
        try:
            parsed = json.loads(json_body)
        except Exception:
            return out
        if not isinstance(parsed, dict):
            return out

        out["stage"] = "locate"
        out["instruction"] = str(parsed.get("instruction") or instruction)
        out["current_module"] = str(parsed.get("current_module") or current_node.module_name)
        out["current_instance"] = str(parsed.get("current_instance") or current_node.instance_name)
        out["current_level"] = int(parsed.get("current_level") or level)
        out["role_summary"] = str(parsed.get("role_summary") or "").strip()
        out["next_target_instance"] = str(parsed.get("next_target_instance") or expected_next_instance).strip()
        out["fork_target"] = str(parsed.get("fork_target") or "").strip()
        conf = str(parsed.get("confidence") or "low").strip().lower()
        out["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        out["unknown"] = str(parsed.get("unknown") or "").strip()

        signals = parsed.get("relevant_signals")
        if isinstance(signals, list):
            out["relevant_signals"] = [str(s).strip() for s in signals if str(s).strip()]

        mappings = parsed.get("port_mapping")
        if isinstance(mappings, list):
            norm: List[Dict[str, str]] = []
            for item in mappings:
                if not isinstance(item, dict):
                    continue
                norm.append({
                    "from": str(item.get("from") or "").strip(),
                    "to": str(item.get("to") or "").strip(),
                    "signal": str(item.get("signal") or "").strip(),
                })
            out["port_mapping"] = [m for m in norm if m["from"] or m["to"] or m["signal"]]

        return out

    def extract_pass3_3_draw_payload(self, content: str, current_node: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "stage": "draw",
            "module": current_node.module_name,
            "instance": current_node.instance_name,
            "handoff_signals": [],
            "lifecycle_context": "",
            "instruction_state": "",
            "confidence": "low",
            "unknown": "",
        }

        json_blocks = self.extract_json_code_blocks(content or "")
        if not json_blocks:
            return payload
        json_body = json_blocks[-1]
        try:
            parsed = json.loads(json_body)
        except Exception:
            return payload
        if not isinstance(parsed, dict):
            return payload

        payload["stage"] = "draw"
        payload["module"] = str(parsed.get("module") or current_node.module_name).strip()
        payload["instance"] = str(parsed.get("instance") or current_node.instance_name).strip()
        payload["lifecycle_context"] = str(parsed.get("lifecycle_context") or "").strip()
        payload["instruction_state"] = str(parsed.get("instruction_state") or "").strip()
        conf = str(parsed.get("confidence") or "low").strip().lower()
        payload["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        payload["unknown"] = str(parsed.get("unknown") or "").strip()

        handoff = parsed.get("handoff_signals")
        if isinstance(handoff, list):
            norm_handoff: List[Dict[str, Any]] = []
            for item in handoff:
                if not isinstance(item, dict):
                    continue
                signals = item.get("signals")
                if not isinstance(signals, list):
                    signals = [str(signals)] if str(signals or "").strip() else []
                norm_handoff.append({
                    "target_module": str(item.get("target_module") or "").strip(),
                    "target_instance": str(item.get("target_instance") or "").strip(),
                    "signals": [str(s).strip() for s in signals if str(s).strip()],
                })
            payload["handoff_signals"] = norm_handoff

        return payload

    @staticmethod
    def salvage_mermaid_text(content: str) -> str:
        """Best-effort salvage when fenced mermaid block is missing.

        Some model outputs contain plain `flowchart LR` lines without a
        ```mermaid fenced block. In that case we try to slice the diagram body
        before the trailing JSON block.
        """
        text = content or ""
        idx = text.find("flowchart ")
        if idx < 0:
            idx = text.find("flowchart\n")
        if idx < 0:
            return ""

        tail = text[idx:]
        json_fence = tail.find("```json")
        if json_fence >= 0:
            tail = tail[:json_fence]
        end_fence = tail.rfind("```")
        if end_fence > 0:
            tail = tail[:end_fence]
        return tail.strip()

    async def run_pass3_3_2_locate(
        self,
        top_node: Any,
        instruction: str,
        instruction_datasheet: str,
        search_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Precreate deterministic locate path context.

        Locate stage is intentionally lightweight: it only materializes the
        hierarchy path from top to search start-point and does not invoke LLM
        generation.
        """
        start_module = str(search_result.get("start_module") or "").strip()
        start_instance = str(search_result.get("start_instance") or "").strip()
        path_nodes = self.find_hierarchy_path(top_node, start_module, start_instance)
        if not path_nodes:
            return []

        path_context: List[Dict[str, Any]] = []
        path_names = [f"{n.instance_name}({n.module_name})" for n in path_nodes]
        total = len(path_nodes)

        for idx, node in enumerate(path_nodes):
            expected_next = ""
            if idx + 1 < len(path_nodes):
                expected_next = path_nodes[idx + 1].instance_name

            # Keep a compact locate record for path precreation only.
            path_context.append(
                {
                    "stage": "locate",
                    "instruction": instruction,
                    "path_nodes": path_names,
                    "path_index": idx,
                    "path_total": total,
                    "current_module": node.module_name,
                    "current_instance": node.instance_name,
                    "current_path": node.get_path() if hasattr(node, "get_path") else node.instance_name,
                    "current_level": int(node.depth),
                    "next_target_instance": expected_next,
                    "precreate_only": True,
                }
            )

        return path_context

    async def run_pass3_3_2_draw(
        self,
        top_node: Any,
        instruction: str,
        instruction_datasheet: str,
        search_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        start_module = str(search_result.get("start_module") or "").strip()
        start_instance = str(search_result.get("start_instance") or "").strip()
        path_nodes = self.find_hierarchy_path(top_node, start_module, start_instance)
        if not path_nodes:
            return []

        # Step 1: draw leaf start module first.
        start_node = path_nodes[-1]
        results: List[Dict[str, Any]] = []
        draw_cache: Dict[str, Dict[str, Any]] = {}

        start_item = await self._run_draw_agent_for_node(
            top_node=top_node,
            node=start_node,
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            upstream_signals=[],
            stage_label="start_module",
            existing_child_draws="- 无已知子模块 draw 结果",
            draw_cache=draw_cache,
            required_children=[],
            upstream_context=None,
        )
        results.append(start_item)
        start_path = start_node.get_path() if hasattr(start_node, "get_path") else start_node.instance_name
        recorded_paths: Set[str] = {start_path}
        draw_cache[start_path] = start_item
        pending_handoffs = list((start_item.get("draw_payload") or {}).get("handoff_signals") or [])
        pending_payload = dict(start_item.get("draw_payload") or {})

        # Step 2: climb to top and draw parent-context bridges level by level.
        for idx in range(len(path_nodes) - 2, -1, -1):
            parent_node = path_nodes[idx]
            source_child_node = path_nodes[idx + 1]
            parent_path = parent_node.get_path() if hasattr(parent_node, "get_path") else parent_node.instance_name
            cache_before_parent = set(draw_cache.keys())
            existing_child_draws = self._build_existing_child_draws(parent_node, draw_cache)
            parent_item = await self._run_draw_agent_for_node(
                top_node=top_node,
                node=parent_node,
                instruction=instruction,
                instruction_datasheet=instruction_datasheet,
                upstream_signals=self._summarize_handoffs_for_state(pending_handoffs),
                stage_label="top_module" if parent_node is path_nodes[0] else "parent_module",
                existing_child_draws=existing_child_draws,
                draw_cache=draw_cache,
                required_children=[],
                upstream_handoffs_raw=pending_handoffs,
                source_child_for_handoff=source_child_node,
                source_payload_for_handoff=pending_payload,
                upstream_context=None,
            )

            # Record newly generated on-demand child draws triggered by drawChild.
            new_child_paths = sorted(path for path in draw_cache.keys() if path not in cache_before_parent)
            for child_path in new_child_paths:
                if child_path in recorded_paths:
                    continue
                child_item = draw_cache.get(child_path)
                if child_item:
                    results.append(child_item)
                    recorded_paths.add(child_path)

            results.append(parent_item)
            recorded_paths.add(parent_path)
            draw_cache[parent_path] = parent_item
            pending_handoffs = list((parent_item.get("draw_payload") or {}).get("handoff_signals") or [])
            pending_payload = dict(parent_item.get("draw_payload") or {})

        return results

    @staticmethod
    def _format_child_selector(child_node: Any) -> str:
        return f"{child_node.instance_name}({child_node.module_name})"

    def _resolve_direct_handoff_children(self, parent_node: Any, handoffs: List[Dict[str, Any]]) -> List[Any]:
        selected: List[Any] = []
        seen: set = set()
        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            target_instance = str(item.get("target_instance") or "").strip()
            target_module = str(item.get("target_module") or "").strip()
            child_node = None
            if target_instance and target_instance in parent_node.children:
                child_node = parent_node.children[target_instance]
            elif target_module:
                for candidate in parent_node.children.values():
                    if candidate.module_name == target_module:
                        child_node = candidate
                        break
            if child_node is None:
                continue
            child_path = child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
            if child_path in seen:
                continue
            seen.add(child_path)
            selected.append(child_node)
        return selected

    def _resolve_required_children_from_destinations(self, parent_node: Any, destinations: Dict[str, Any]) -> List[Any]:
        selected: List[Any] = []
        seen: Set[str] = set()
        for item in list((destinations or {}).get("sibling_connections") or []):
            if not isinstance(item, dict):
                continue
            target_instance = str(item.get("target_instance") or "").strip()
            if not target_instance:
                continue
            child_node = parent_node.children.get(target_instance)
            if child_node is None:
                continue
            child_path = child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
            if child_path in seen:
                continue
            seen.add(child_path)
            selected.append(child_node)
        return selected

    def _filter_handoffs_for_child(self, handoffs: List[Dict[str, Any]], child_node: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            target_instance = str(item.get("target_instance") or "").strip()
            target_module = str(item.get("target_module") or "").strip()
            if target_instance and target_instance == child_node.instance_name:
                out.append(item)
                continue
            if target_module and target_module == child_node.module_name:
                out.append(item)
        return out

    def _summarize_handoffs_for_state(self, handoffs: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = []
        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            target_instance = str(item.get("target_instance") or "").strip()
            target_module = str(item.get("target_module") or "").strip()
            signals = item.get("signals") or []
            if not isinstance(signals, list):
                signals = [signals]
            sigs = [str(sig).strip() for sig in signals if str(sig).strip()]
            preview = ", ".join(sigs[:3])
            if len(sigs) > 3:
                preview += f", +{len(sigs) - 3}"
            head = target_instance or target_module or "unknown"
            if target_instance and target_module:
                head = f"{target_instance}({target_module})"
            lines.append(f"{head}: {preview}" if preview else head)
        return lines[:6]

    @staticmethod
    def _normalize_signal_name(name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        text = text.lstrip("\\")
        text = re.sub(r"\[[^\]]+\]$", "", text)
        return text.strip()

    def _resolve_handoff_destinations(
        self,
        parent_node: Any,
        source_child_node: Any,
        handoffs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Resolve handoff destinations using hierarchy port maps + simplified graph.

        Returns three destination categories:
        - sibling_connections: exact child-port to child-port via parent wire
        - exits_parent: parent IO ports that carry unresolved handoff wires upward
        - parent_logic: parent-level PROC/COMB/seq nodes connected to source child in topology
        """
        source_conn = dict(getattr(source_child_node, "port_connections", {}) or {})
        wire_to_source_ports: Dict[str, Set[str]] = {}
        for src_port, raw_wire in source_conn.items():
            wire = str(raw_wire or "").strip()
            if not wire or wire.startswith("["):
                continue
            norm_wire = self._normalize_signal_name(wire)
            if not norm_wire:
                continue
            wire_to_source_ports.setdefault(norm_wire, set()).add(str(src_port or "").strip())

        sibling_connections: List[Dict[str, str]] = []
        exits_parent: List[Dict[str, str]] = []
        parent_logic: List[Dict[str, str]] = []
        unresolved: List[str] = []
        seen_sibling: Set[str] = set()
        seen_exit: Set[str] = set()
        seen_logic: Set[str] = set()

        def _handoff_signals() -> List[str]:
            out: List[str] = []
            for item in handoffs or []:
                if not isinstance(item, dict):
                    continue
                sigs = item.get("signals") or []
                if not isinstance(sigs, list):
                    sigs = [sigs]
                for sig in sigs:
                    sig_text = str(sig or "").strip()
                    if sig_text:
                        out.append(sig_text)
            return out

        signals = _handoff_signals()
        matched_wires: Set[str] = set()

        for signal in signals:
            norm_signal = self._normalize_signal_name(signal)
            if not norm_signal:
                continue
            resolved_wires: Set[str] = set()

            for src_port, raw_wire in source_conn.items():
                port_name = str(src_port or "").strip()
                norm_port = self._normalize_signal_name(port_name)
                wire_name = str(raw_wire or "").strip()
                norm_wire = self._normalize_signal_name(wire_name)
                if not norm_wire or wire_name.startswith("["):
                    continue
                if norm_signal == norm_port or norm_signal == norm_wire:
                    resolved_wires.add(norm_wire)

            if not resolved_wires:
                unresolved.append(signal)
                continue

            for norm_wire in sorted(resolved_wires):
                matched_wires.add(norm_wire)
                src_ports = sorted(wire_to_source_ports.get(norm_wire, set()))
                src_port = src_ports[0] if src_ports else ""
                for sibling in parent_node.children.values():
                    sibling_path = sibling.get_path() if hasattr(sibling, "get_path") else sibling.instance_name
                    source_path = (
                        source_child_node.get_path()
                        if hasattr(source_child_node, "get_path")
                        else source_child_node.instance_name
                    )
                    if sibling_path == source_path:
                        continue
                    sibling_conn = dict(getattr(sibling, "port_connections", {}) or {})
                    for target_port, target_wire in sibling_conn.items():
                        target_norm_wire = self._normalize_signal_name(str(target_wire or ""))
                        if target_norm_wire != norm_wire:
                            continue
                        key = f"{sibling.instance_name}|{target_port}|{norm_wire}|{signal}"
                        if key in seen_sibling:
                            continue
                        seen_sibling.add(key)
                        sibling_connections.append(
                            {
                                "source_signal": signal,
                                "source_port": src_port,
                                "parent_wire": norm_wire,
                                "target_instance": sibling.instance_name,
                                "target_module": sibling.module_name,
                                "target_port": str(target_port or "").strip(),
                            }
                        )

        graphs = getattr(self.g.owner, "_graphs", None)
        graph = graphs.get(parent_node.module_name) if isinstance(graphs, dict) else None

        if graph is not None:
            io_norm_to_label: Dict[str, str] = {}
            for _, io_label in dict(getattr(graph, "io_ports", {}) or {}).items():
                label = str(io_label or "").strip().lstrip("\\")
                norm = self._normalize_signal_name(label)
                if norm:
                    io_norm_to_label.setdefault(norm, label)

            for norm_wire in sorted(matched_wires):
                if norm_wire not in io_norm_to_label:
                    continue
                key = f"{norm_wire}|{io_norm_to_label[norm_wire]}"
                if key in seen_exit:
                    continue
                seen_exit.add(key)
                exits_parent.append(
                    {
                        "source_signal": norm_wire,
                        "parent_wire": norm_wire,
                        "port_name": io_norm_to_label[norm_wire],
                    }
                )

            source_node_ids = [
                str(node_id)
                for node_id, inst_name in dict(getattr(graph, "submodules", {}) or {}).items()
                if str(inst_name or "").strip() == str(source_child_node.instance_name or "").strip()
            ]
            proc_nodes = dict(getattr(graph, "proc_nodes", {}) or {})
            comb_nodes = dict(getattr(graph, "comb_nodes", {}) or {})
            seq_nodes = dict(getattr(graph, "seq_cells", {}) or {})
            for src_id, dst_id in list(getattr(graph, "edges", []) or []):
                relation = ""
                neighbor = ""
                if src_id in source_node_ids:
                    relation = "from_child"
                    neighbor = str(dst_id)
                elif dst_id in source_node_ids:
                    relation = "to_child"
                    neighbor = str(src_id)
                if not neighbor:
                    continue
                if neighbor in proc_nodes:
                    proc = proc_nodes[neighbor]
                    src_loc = getattr(proc, "source_location", None)
                    summary = f"PROC {neighbor}"
                    if src_loc is not None:
                        summary = f"PROC {neighbor} @ {src_loc.short_display()}"
                    key = f"proc|{neighbor}|{relation}"
                    if key not in seen_logic:
                        seen_logic.add(key)
                        parent_logic.append(
                            {
                                "dest_type": "proc",
                                "node_id": neighbor,
                                "direction": relation,
                                "summary": summary,
                            }
                        )
                elif neighbor in comb_nodes:
                    comb = comb_nodes[neighbor]
                    comb_type = str(getattr(comb, "comb_type", "COMB"))
                    src_locs = list(getattr(comb, "source_locations", []) or [])
                    summary = f"{comb_type} {neighbor}"
                    if src_locs:
                        summary = f"{comb_type} {neighbor} @ {src_locs[0].short_display()}"
                    key = f"comb|{neighbor}|{relation}"
                    if key not in seen_logic:
                        seen_logic.add(key)
                        parent_logic.append(
                            {
                                "dest_type": "comb",
                                "node_id": neighbor,
                                "direction": relation,
                                "summary": summary,
                            }
                        )
                elif neighbor in seq_nodes:
                    cell_type = str(seq_nodes[neighbor] or "SEQ")
                    key = f"seq|{neighbor}|{relation}"
                    if key not in seen_logic:
                        seen_logic.add(key)
                        parent_logic.append(
                            {
                                "dest_type": "seq_cell",
                                "node_id": neighbor,
                                "direction": relation,
                                "summary": f"SEQ {neighbor} ({cell_type})",
                            }
                        )

        return {
            "sibling_connections": sibling_connections,
            "exits_parent": exits_parent,
            "parent_logic": parent_logic,
            "unresolved": unresolved,
        }

    def _build_upstream_context(
        self,
        *,
        parent_node: Any,
        source_child_node: Any,
        target_child_node: Any,
        handoffs: List[Dict[str, Any]],
        source_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        resolved = self._resolve_handoff_destinations(parent_node, source_child_node, handoffs)
        sibling_connections = list(resolved.get("sibling_connections") or [])
        parent_logic = list(resolved.get("parent_logic") or [])
        exits_parent = list(resolved.get("exits_parent") or [])
        unresolved = list(resolved.get("unresolved") or [])

        entry_ports: List[Dict[str, str]] = []
        for item in sibling_connections:
            if str(item.get("target_instance") or "").strip() != str(target_child_node.instance_name or "").strip():
                continue
            entry_ports.append(
                {
                    "target_port": str(item.get("target_port") or "").strip(),
                    "wire": str(item.get("parent_wire") or "").strip(),
                    "source_port": str(item.get("source_port") or "").strip(),
                }
            )

        unique_entries: List[Dict[str, str]] = []
        seen_entries: Set[str] = set()
        for entry in entry_ports:
            key = f"{entry.get('target_port')}|{entry.get('wire')}|{entry.get('source_port')}"
            if key in seen_entries:
                continue
            seen_entries.add(key)
            unique_entries.append(entry)

        lifecycle_context = str((source_payload or {}).get("lifecycle_context") or "").strip()
        instruction_state = str((source_payload or {}).get("instruction_state") or "").strip()

        return {
            "source_instance": str(source_child_node.instance_name or "").strip(),
            "source_module": str(source_child_node.module_name or "").strip(),
            "entry_ports": unique_entries[:8],
            "parent_logic_on_path": [str(item.get("summary") or "").strip() for item in parent_logic[:4] if str(item.get("summary") or "").strip()],
            "exits_parent": exits_parent[:4],
            "unresolved": unresolved[:6],
            "lifecycle_context": lifecycle_context,
            "instruction_state": instruction_state,
        }

    @staticmethod
    def _build_upstream_context_section(upstream_context: Optional[Dict[str, Any]]) -> str:
        if not isinstance(upstream_context, dict) or not upstream_context:
            return ""
        compact = json.dumps(upstream_context, ensure_ascii=False, separators=(",", ":"))
        return (
            "\n## Upstream Continuation Context（来自前序模块的精确续画上下文）\n"
            "你当前处于按需子模块续画阶段。入口端口列表由 Python 层根据 RTL 连接精确计算，请直接据此续画，不要猜测入口信号。\n"
            f"{compact}\n"
        )

    def _build_existing_child_draws(self, parent_node: Any, draw_cache: Dict[str, Dict[str, Any]]) -> str:
        lines: List[str] = []
        for child in sorted(parent_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_path = child.get_path() if hasattr(child, "get_path") else child.instance_name
            child_item = draw_cache.get(child_path)
            if not child_item:
                continue
            mermaid = str(child_item.get("mermaid") or "").strip()
            if not mermaid:
                continue
            lines.append(f"### {child.instance_name} ({child.module_name})")
            lines.append(mermaid)
        return "\n\n".join(lines) if lines else "- 无已完成子模块 draw 结果"

    async def _run_draw_agent_for_node(
        self,
        top_node: Any,
        node: Any,
        instruction: str,
        instruction_datasheet: str,
        upstream_signals: List[str],
        stage_label: str,
        existing_child_draws: str,
        draw_cache: Dict[str, Dict[str, Any]],
        required_children: List[str],
        upstream_handoffs_raw: Optional[List[Dict[str, Any]]] = None,
        source_child_for_handoff: Optional[Any] = None,
        source_payload_for_handoff: Optional[Dict[str, Any]] = None,
        upstream_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        node_path = node.get_path() if hasattr(node, "get_path") else node.instance_name
        draw_state = {
            "current_module": node.module_name,
            "current_instance": node.instance_name,
            "current_path": node_path,
            "current_level": node.depth,
            "upstream_handoff": upstream_signals,
            "draw_phase": stage_label,
            "is_top_module": node.parent is None,
            "has_children": bool(node.children),
            "upstream_context": upstream_context or {},
        }

        system_prompt = PASS3_3_2_DRAW_SYSTEM
        prompt = PASS3_3_2_DRAW_PROMPT.format(
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            draw_state_json=self.g._build_instrack_draw_state_json(
                current_module=str(draw_state.get("current_module") or ""),
                current_instance=str(draw_state.get("current_instance") or ""),
                current_path=str(draw_state.get("current_path") or ""),
                current_level=int(draw_state.get("current_level") or 0),
                upstream_handoff=list(draw_state.get("upstream_handoff") or []),
                required_children=required_children,
                draw_phase=str(draw_state.get("draw_phase") or "draw"),
                is_top_module=bool(draw_state.get("is_top_module")),
                has_children=bool(draw_state.get("has_children")),
                upstream_context=dict(draw_state.get("upstream_context") or {}),
            ),
            module_description=self.g.owner._extract_summary(
                self.get_module_description(node.module_name),
                max_lines=20,
                max_chars=2400,
            ),
            child_overview=self.build_child_overview(node),
            existing_child_draws=existing_child_draws,
            upstream_context_section=self._build_upstream_context_section(upstream_context),
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "readSource":
                module = str(args.get("module") or "").strip()
                scoped_node, scope_error = self.g._resolve_scope_node(node, module, child_only=False)
                if scoped_node is None:
                    return (
                        "Error: instrack draw readSource scope violation. "
                        "At this level you can read only current module and direct children. "
                        f"Details: {scope_error}"
                    )
                topology = self.g._build_pass2_style_topology_block(str(scoped_node.module_name))
                return f"[readSource] module={scoped_node.module_name}\n\n{topology}"

            if tool_name == "drawChild":
                module = str(args.get("module") or "").strip()
                child_task = str(args.get("task") or "").strip()
                child_node, error = self.g._resolve_scope_node(node, module, child_only=True)
                if child_node is None:
                    return error
                child_path = child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                # De-duplicate by module: once any instance of a module is drawn in current draw session,
                # reject repeated drawChild calls for the same module.
                already_drawn_module = None
                for _, cached in draw_cache.items():
                    cached_module = str((cached or {}).get("module") or "").strip()
                    if cached_module and cached_module == child_node.module_name:
                        already_drawn_module = cached_module
                        break
                if already_drawn_module is not None:
                    return (
                        "[drawChild][reject] duplicate module draw request. "
                        f"module={child_node.module_name} has already been drawn; "
                        "reuse existing child draw summary instead of invoking drawChild again."
                    )
                cached_child = draw_cache.get(child_path)
                if cached_child is None:
                    raw_handoffs = list(upstream_handoffs_raw or [])
                    filtered_handoffs = self._filter_handoffs_for_child(raw_handoffs, child_node)
                    child_upstream_context: Optional[Dict[str, Any]] = None
                    if source_child_for_handoff is not None and filtered_handoffs:
                        child_upstream_context = self._build_upstream_context(
                            parent_node=node,
                            source_child_node=source_child_for_handoff,
                            target_child_node=child_node,
                            handoffs=filtered_handoffs,
                            source_payload=source_payload_for_handoff,
                        )
                    self.g._append_fork_trace(
                        "fork_dispatch",
                        {
                            "parent_path": node_path,
                            "parent_level": node.depth,
                            "child_instance": child_node.instance_name,
                            "child_module": child_node.module_name,
                            "child_level": node.depth + 1,
                            "task": child_task,
                            "prompt_style": "instrack_draw",
                        },
                    )
                    child_existing_draws = self._build_existing_child_draws(child_node, draw_cache)
                    cached_child = await self._run_draw_agent_for_node(
                        top_node=top_node,
                        node=child_node,
                        instruction=instruction,
                        instruction_datasheet=instruction_datasheet,
                        upstream_signals=self._summarize_handoffs_for_state(filtered_handoffs),
                        stage_label="agent_requested_child",
                        existing_child_draws=child_existing_draws,
                        draw_cache=draw_cache,
                        required_children=[],
                        upstream_handoffs_raw=filtered_handoffs,
                        source_child_for_handoff=None,
                        source_payload_for_handoff=source_payload_for_handoff,
                        upstream_context=child_upstream_context,
                    )
                    draw_cache[child_path] = cached_child
                    self.g._append_fork_trace(
                        "fork_return",
                        {
                            "parent_path": node_path,
                            "parent_level": node.depth,
                            "child_instance": child_node.instance_name,
                            "child_module": child_node.module_name,
                            "child_level": node.depth + 1,
                            "report_chars": len(str(cached_child.get("raw_draw_output") or "")),
                            "prompt_style": "instrack_draw",
                        },
                    )
                mermaid = str(cached_child.get("mermaid") or "").strip()
                if not mermaid:
                    return f"[drawChild] child={child_node.instance_name}({child_node.module_name}) no mermaid output"
                if child_task:
                    return (
                        f"[drawChild] task={child_task}\n"
                        f"[drawChild] child={child_node.instance_name}({child_node.module_name})\n\n{mermaid}"
                    )
                return f"[drawChild] child={child_node.instance_name}({child_node.module_name})\n\n{mermaid}"

            return f"Error: unknown tool '{tool_name}'"

        draw_log_path = self.g._build_pass3_instrack_agent_log_path(
            instruction=instruction,
            node_path=node_path,
            level=node.depth,
            role=f"draw_agent_{stage_label}",
        )

        self.g._append_agent_io_snapshot(
            draw_log_path,
            stage="Input",
            system=system_prompt,
            prompt=prompt,
        )

        draw_content, _ = await self.g.owner.llm.generate(
            system_prompt,
            prompt,
            log_path=draw_log_path,
            tools_enabled=True,
            tools=self.g._pass3_3_2_tools(),
            tool_callback=_tool_callback,
            max_tool_rounds=12,
        )

        self.g._append_agent_io_snapshot(
            draw_log_path,
            stage="Output",
            system="",
            prompt="",
            output=draw_content or "",
        )

        mermaid = self.g._extract_mermaid_code(draw_content or "")
        if not mermaid:
            mermaid = self.salvage_mermaid_text(draw_content or "")
        payload = self.extract_pass3_3_draw_payload(draw_content or "", node)
        return {
            "module": node.module_name,
            "instance": node.instance_name,
            "path": node_path,
            "mermaid": f"```mermaid\n{mermaid}\n```\n" if mermaid else "",
            "draw_payload": payload,
            "orchestrator_role": stage_label,
            "raw_draw_output": draw_content or "",
        }
