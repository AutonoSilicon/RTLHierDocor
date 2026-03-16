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
            "version": "pass3_3_instrack_draw_cache_v10",
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

    def get_module_preview(self, module_name: str) -> str:
        text = self.g.owner.tracker.get_pass1_content(module_name) or ""
        if not text:
            text = "无可用 preview 文档"
        return text

    def build_child_overview(self, current_node: Any) -> str:
        lines: List[str] = []
        for child in sorted(current_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_desc = self.get_module_preview(child.module_name)
            lines.append(
                f"- {child.instance_name} ({child.module_name}): "
                f"{child_desc}"
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
            "entry_ports": [],
            "boundary_handoffs": [],
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
        payload["lifecycle_context"] = self._coerce_text_field(parsed.get("lifecycle_context"))
        payload["instruction_state"] = self._coerce_text_field(parsed.get("instruction_state"))
        conf = str(parsed.get("confidence") or "low").strip().lower()
        payload["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        payload["unknown"] = self._coerce_text_field(parsed.get("unknown"))

        entry_ports = parsed.get("entry_ports")
        if isinstance(entry_ports, list):
            norm_entry_ports: List[Dict[str, Any]] = []
            for item in entry_ports:
                if not isinstance(item, dict):
                    continue
                port = str(item.get("port") or item.get("target_port") or "").strip()
                if not port:
                    continue
                direction = str(item.get("direction") or "input").strip().lower()
                if direction not in {"input", "output", "inout"}:
                    direction = "input"
                norm_item: Dict[str, Any] = {
                    "port": port,
                    "direction": direction,
                    "value_kind": str(item.get("value_kind") or "other").strip() or "other",
                    "semantic": self._coerce_text_field(item.get("semantic")),
                }
                matched_from = item.get("matched_from")
                if isinstance(matched_from, dict):
                    norm_item["matched_from"] = {
                        "source_instance": str(matched_from.get("source_instance") or "").strip(),
                        "source_module": str(matched_from.get("source_module") or "").strip(),
                        "source_port": str(matched_from.get("source_port") or "").strip(),
                        "parent_wire": str(matched_from.get("parent_wire") or matched_from.get("wire") or "").strip(),
                    }
                norm_entry_ports.append(norm_item)
            payload["entry_ports"] = norm_entry_ports

        boundary_handoffs = parsed.get("boundary_handoffs")
        if isinstance(boundary_handoffs, list):
            norm_handoffs: List[Dict[str, Any]] = []
            for item in boundary_handoffs:
                if not isinstance(item, dict):
                    continue
                egress_port = str(item.get("egress_port") or "").strip()
                if not egress_port:
                    continue
                norm_handoffs.append(
                    {
                        "source_block": str(item.get("source_block") or "").strip(),
                        "source_state": self._coerce_text_field(item.get("source_state")),
                        "egress_port": egress_port,
                        "value_kind": str(item.get("value_kind") or "other").strip() or "other",
                        "semantic": self._coerce_text_field(item.get("semantic")),
                        "behavior": self._coerce_text_field(item.get("behavior")),
                        "resolutions": [],
                        "status": str(item.get("status") or "").strip(),
                        "confidence": str(item.get("confidence") or payload["confidence"]).strip().lower() or payload["confidence"],
                        "unknown": self._coerce_text_field(item.get("unknown")),
                    }
                )
            payload["boundary_handoffs"] = norm_handoffs

        return payload

    @staticmethod
    def _coerce_text_field(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(value).strip()

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
        pending_handoffs = list((start_item.get("draw_payload") or {}).get("boundary_handoffs") or [])
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
            pending_handoffs = list((parent_item.get("draw_payload") or {}).get("boundary_handoffs") or [])
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
            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                if str(resolution.get("resolution_kind") or "").strip() != "sibling_child":
                    continue
                target_instance = str(resolution.get("target_instance") or "").strip()
                target_module = str(resolution.get("target_module") or "").strip()
                if target_instance and target_instance == child_node.instance_name:
                    out.append(item)
                    break
                if target_module and target_module == child_node.module_name:
                    out.append(item)
                    break
        return out

    def _summarize_handoffs_for_state(self, handoffs: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = []
        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            egress_port = str(item.get("egress_port") or "").strip()
            semantic = self._coerce_text_field(item.get("semantic"))
            resolutions = list(item.get("resolutions") or [])
            targets: List[str] = []
            for resolution in resolutions:
                if not isinstance(resolution, dict):
                    continue
                kind = str(resolution.get("resolution_kind") or "").strip()
                if kind == "sibling_child":
                    target_instance = str(resolution.get("target_instance") or "").strip()
                    target_module = str(resolution.get("target_module") or "").strip()
                    target_port = str(resolution.get("target_port") or "").strip()
                    head = target_instance or target_module or "unknown"
                    if target_instance and target_module:
                        head = f"{target_instance}({target_module})"
                    if target_port:
                        head = f"{head}:{target_port}"
                    targets.append(head)
                elif kind == "exit_parent":
                    parent_port = str(resolution.get("parent_port") or "").strip()
                    targets.append(f"parent:{parent_port}" if parent_port else "parent_exit")
            preview = "; ".join(targets[:3])
            if len(targets) > 3:
                preview += f"; +{len(targets) - 3}"
            head = egress_port or "unknown_port"
            if semantic:
                head = f"{head}({semantic})"
            lines.append(f"{head} -> {preview}" if preview else head)
        return lines[:6]

    @staticmethod
    def _normalize_signal_name(name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        text = text.lstrip("\\")
        text = re.sub(r"\[[^\]]+\]$", "", text)
        return text.strip()

    def _get_module_port_directions(self, module_name: str) -> Dict[str, str]:
        resolver = getattr(self.g.owner, "resolver", None)
        backend = getattr(resolver, "backend", None)
        if backend is None:
            return {}
        try:
            module = backend.get_module(module_name)
        except Exception:
            module = None
        if module is None:
            return {}

        directions: Dict[str, str] = {}
        try:
            for wire_id in module.wires_:
                wire = module.wire(wire_id)
                if not (getattr(wire, "port_input", False) or getattr(wire, "port_output", False)):
                    continue
                name = wire.name.str().lstrip("\\")
                if getattr(wire, "port_input", False) and getattr(wire, "port_output", False):
                    directions[name] = "inout"
                elif getattr(wire, "port_output", False):
                    directions[name] = "output"
                else:
                    directions[name] = "input"
        except Exception:
            return {}
        return directions

    def _find_port_direction(self, module_name: str, port_name: str) -> str:
        norm_port = self._normalize_signal_name(port_name)
        if not norm_port:
            return ""
        for name, direction in self._get_module_port_directions(module_name).items():
            if self._normalize_signal_name(name) == norm_port:
                return direction
        return ""

    def _lookup_port_connection(self, node: Any, port_name: str) -> str:
        port_name = str(port_name or "").strip()
        if not port_name:
            return ""
        raw = dict(getattr(node, "port_connections", {}) or {})
        if port_name in raw:
            return str(raw.get(port_name) or "").strip()
        norm_port = self._normalize_signal_name(port_name)
        for candidate, wire in raw.items():
            if self._normalize_signal_name(candidate) == norm_port:
                return str(wire or "").strip()
        return ""

    def _validate_boundary_handoffs_against_module(
        self,
        node: Any,
        boundary_handoffs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        port_directions = self._get_module_port_directions(node.module_name)
        normalized_dirs = {
            self._normalize_signal_name(name): direction
            for name, direction in port_directions.items()
            if self._normalize_signal_name(name)
        }

        validated: List[Dict[str, Any]] = []
        seen_ports: Set[str] = set()
        for raw_item in boundary_handoffs or []:
            if not isinstance(raw_item, dict):
                continue
            handoff = dict(raw_item)
            egress_port = str(handoff.get("egress_port") or "").strip()
            norm_port = self._normalize_signal_name(egress_port)
            if not egress_port:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    "missing egress_port",
                )
                validated.append(handoff)
                continue

            port_dir = normalized_dirs.get(norm_port, "")
            if not port_dir:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"egress_port '{egress_port}' is not a declared port of module '{node.module_name}'",
                )
                validated.append(handoff)
                continue

            if port_dir != "output":
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"egress_port '{egress_port}' is not an output port",
                )
                validated.append(handoff)
                continue

            if norm_port in seen_ports:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"duplicate boundary_handoff for egress_port '{egress_port}'",
                )
                validated.append(handoff)
                continue

            seen_ports.add(norm_port)
            validated.append(handoff)

        return validated

    def _resolve_boundary_handoff_destinations(
        self,
        parent_node: Any,
        source_child_node: Any,
        boundary_handoffs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        source_path = source_child_node.get_path() if hasattr(source_child_node, "get_path") else source_child_node.instance_name
        source_port_dirs = self._get_module_port_directions(source_child_node.module_name)
        graphs = getattr(self.g.owner, "_graphs", None)
        graph = graphs.get(parent_node.module_name) if isinstance(graphs, dict) else None
        io_norm_to_label: Dict[str, str] = {}
        if graph is not None:
            for _, io_label in dict(getattr(graph, "io_ports", {}) or {}).items():
                label = str(io_label or "").strip().lstrip("\\")
                norm = self._normalize_signal_name(label)
                if norm:
                    io_norm_to_label.setdefault(norm, label)

        enriched: List[Dict[str, Any]] = []
        for item in boundary_handoffs or []:
            if not isinstance(item, dict):
                continue
            handoff = dict(item)
            egress_port = str(handoff.get("egress_port") or "").strip()
            handoff["source_instance_path"] = source_path
            handoff["source_module"] = source_child_node.module_name
            handoff["source_instance"] = source_child_node.instance_name
            handoff["handoff_id"] = f"{source_path}::{egress_port}" if egress_port else f"{source_path}::unknown"
            handoff["source_direction"] = ""
            handoff["parent_wire"] = ""
            handoff["resolutions"] = []

            if not egress_port:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(str(handoff.get("unknown") or ""), "missing egress_port")
                enriched.append(handoff)
                continue

            if str(handoff.get("status") or "").strip() == "invalid":
                enriched.append(handoff)
                continue

            port_dir = ""
            for name, direction in source_port_dirs.items():
                if self._normalize_signal_name(name) == self._normalize_signal_name(egress_port):
                    port_dir = direction
                    break
            handoff["source_direction"] = port_dir
            if port_dir and port_dir != "output":
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"egress_port '{egress_port}' is not an output port",
                )
                enriched.append(handoff)
                continue

            raw_wire = self._lookup_port_connection(source_child_node, egress_port)
            norm_wire = self._normalize_signal_name(raw_wire)
            if not raw_wire or raw_wire.startswith("[") or not norm_wire:
                handoff["status"] = "unresolved"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"no resolvable parent wire for egress_port '{egress_port}'",
                )
                enriched.append(handoff)
                continue

            handoff["parent_wire"] = norm_wire
            resolutions: List[Dict[str, str]] = []
            seen_resolution: Set[str] = set()
            for sibling in parent_node.children.values():
                sibling_path = sibling.get_path() if hasattr(sibling, "get_path") else sibling.instance_name
                if sibling_path == source_path:
                    continue
                for target_port, target_wire in dict(getattr(sibling, "port_connections", {}) or {}).items():
                    target_norm_wire = self._normalize_signal_name(str(target_wire or ""))
                    if target_norm_wire != norm_wire:
                        continue
                    target_dir = self._find_port_direction(sibling.module_name, str(target_port or "").strip())
                    if target_dir and target_dir != "input":
                        continue
                    key = f"sibling|{sibling.instance_name}|{target_port}|{norm_wire}"
                    if key in seen_resolution:
                        continue
                    seen_resolution.add(key)
                    resolutions.append(
                        {
                            "resolution_kind": "sibling_child",
                            "parent_wire": norm_wire,
                            "target_instance": sibling.instance_name,
                            "target_module": sibling.module_name,
                            "target_port": str(target_port or "").strip(),
                            "match_policy": "exact_port",
                        }
                    )

            if norm_wire in io_norm_to_label:
                parent_port = io_norm_to_label[norm_wire]
                key = f"parent|{parent_port}|{norm_wire}"
                if key not in seen_resolution:
                    resolutions.append(
                        {
                            "resolution_kind": "exit_parent",
                            "parent_wire": norm_wire,
                            "parent_port": parent_port,
                            "match_policy": "exact_port",
                        }
                    )

            handoff["resolutions"] = resolutions
            if resolutions:
                kinds = {str(r.get("resolution_kind") or "") for r in resolutions}
                if kinds == {"exit_parent"}:
                    handoff["status"] = "exit_parent"
                else:
                    handoff["status"] = "resolved"
            else:
                handoff["status"] = "unresolved"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"no strict boundary match for egress_port '{egress_port}' on wire '{norm_wire}'",
                )
            enriched.append(handoff)
        return enriched

    @staticmethod
    def _append_reason(base: str, extra: str) -> str:
        base = str(base or "").strip()
        extra = str(extra or "").strip()
        if not extra:
            return base
        if not base:
            return extra
        if extra in base:
            return base
        return f"{base}; {extra}"

    def _build_upstream_context(
        self,
        *,
        parent_node: Any,
        source_child_node: Any,
        target_child_node: Any,
        handoffs: List[Dict[str, Any]],
        source_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        entry_ports: List[Dict[str, str]] = []
        exits_parent: List[Dict[str, str]] = []
        unresolved: List[str] = []
        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            egress_port = str(item.get("egress_port") or "").strip()
            parent_wire = str(item.get("parent_wire") or "").strip()
            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                kind = str(resolution.get("resolution_kind") or "").strip()
                if kind == "sibling_child":
                    if str(resolution.get("target_instance") or "").strip() != str(target_child_node.instance_name or "").strip():
                        continue
                    entry_ports.append(
                        {
                            "target_port": str(resolution.get("target_port") or "").strip(),
                            "wire": str(resolution.get("parent_wire") or parent_wire).strip(),
                            "source_port": egress_port,
                            "source_instance": str(item.get("source_instance") or source_child_node.instance_name).strip(),
                            "source_module": str(item.get("source_module") or source_child_node.module_name).strip(),
                            "semantic": self._coerce_text_field(item.get("semantic")),
                            "value_kind": str(item.get("value_kind") or "other").strip() or "other",
                        }
                    )
                elif kind == "exit_parent":
                    exits_parent.append(
                        {
                            "parent_wire": str(resolution.get("parent_wire") or parent_wire).strip(),
                            "parent_port": str(resolution.get("parent_port") or "").strip(),
                            "source_port": egress_port,
                        }
                    )
            if str(item.get("status") or "").strip() == "unresolved":
                unresolved.append(egress_port or str(item.get("semantic") or "").strip())

        unique_entries: List[Dict[str, str]] = []
        seen_entries: Set[str] = set()
        for entry in entry_ports:
            key = f"{entry.get('target_port')}|{entry.get('wire')}|{entry.get('source_port')}|{entry.get('source_instance')}"
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
            "exits_parent": exits_parent[:4],
            "unresolved": unresolved[:6],
            "lifecycle_context": lifecycle_context,
            "instruction_state": instruction_state,
        }

    def _build_existing_child_draws(self, parent_node: Any, draw_cache: Dict[str, Dict[str, Any]]) -> str:
        lines: List[str] = []
        for child in sorted(parent_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_path = child.get_path() if hasattr(child, "get_path") else child.instance_name
            child_item = draw_cache.get(child_path)
            if not child_item:
                continue
            summary = self._summarize_child_draw_for_prompt(child_item)
            if not summary:
                continue
            lines.append(summary)
        return "\n\n".join(lines) if lines else "- 无已完成子模块 draw 结果"

    @staticmethod
    def _truncate_inline_text(text: str, max_chars: int = 220) -> str:
        raw = " ".join(str(text or "").strip().split())
        if len(raw) <= max_chars:
            return raw
        return raw[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _extract_mermaid_outline(mermaid_text: str) -> List[str]:
        outlines: List[str] = []
        seen: Set[str] = set()
        for line in str(mermaid_text or "").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("flowchart"):
                continue
            if "-->" not in raw and "-.->" not in raw and "[" not in raw:
                continue
            cleaned = re.sub(r"^[A-Za-z0-9_]+\s*\[\s*", "", raw)
            cleaned = re.sub(r"\]\s*$", "", cleaned)
            cleaned = cleaned.strip("`\"' ")
            cleaned = cleaned.replace("<br/>", " | ").replace("<br>", " | ")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not cleaned:
                continue
            compact = Pass3InStrackStages._truncate_inline_text(cleaned, max_chars=160)
            if compact in seen:
                continue
            seen.add(compact)
            outlines.append(compact)
            if len(outlines) >= 3:
                break
        return outlines

    def _summarize_child_draw_for_prompt(self, child_item: Dict[str, Any]) -> str:
        payload = dict(child_item.get("draw_payload") or {})
        child_instance = str(child_item.get("instance") or payload.get("instance") or "").strip()
        child_module = str(child_item.get("module") or payload.get("module") or "").strip()
        if not child_instance and not child_module:
            return ""

        lines: List[str] = [f"### {child_instance} ({child_module})".strip()]
        lifecycle_context = self._truncate_inline_text(payload.get("lifecycle_context") or "", max_chars=240)
        instruction_state = self._truncate_inline_text(payload.get("instruction_state") or "", max_chars=240)
        confidence = str(payload.get("confidence") or "low").strip()
        unknown = self._truncate_inline_text(payload.get("unknown") or "", max_chars=200)

        entry_ports = []
        for item in list(payload.get("entry_ports") or []):
            if not isinstance(item, dict):
                continue
            port = str(item.get("port") or item.get("target_port") or "").strip()
            if not port:
                continue
            matched_from = dict(item.get("matched_from") or {})
            source_port = str(matched_from.get("source_port") or item.get("source_port") or "").strip()
            source_instance = str(matched_from.get("source_instance") or item.get("source_instance") or "").strip()
            preview = port
            if source_instance or source_port:
                preview = f"{preview} <= {source_instance}:{source_port}".strip(":")
            entry_ports.append(preview)

        handoffs = []
        for item in list(payload.get("boundary_handoffs") or []):
            if not isinstance(item, dict):
                continue
            egress_port = str(item.get("egress_port") or "").strip()
            semantic = self._truncate_inline_text(item.get("semantic") or "", max_chars=80)
            targets: List[str] = []
            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                kind = str(resolution.get("resolution_kind") or "").strip()
                if kind == "sibling_child":
                    target_instance = str(resolution.get("target_instance") or "").strip()
                    target_port = str(resolution.get("target_port") or "").strip()
                    targets.append(f"{target_instance}:{target_port}".strip(":"))
                elif kind == "exit_parent":
                    targets.append(f"parent:{str(resolution.get('parent_port') or '').strip()}".rstrip(":"))
            preview = "; ".join(targets[:3])
            if len(targets) > 3:
                preview += f"; +{len(targets) - 3}"
            head = egress_port or "unknown_port"
            if semantic:
                head = f"{head}({semantic})"
            handoffs.append(f"{head} -> {preview}" if preview else head)

        if lifecycle_context:
            lines.append(f"- lifecycle_context: {lifecycle_context}")
        if instruction_state:
            lines.append(f"- instruction_state: {instruction_state}")
        if entry_ports:
            lines.append(f"- entry_ports: {'; '.join(entry_ports[:3])}")
        if handoffs:
            lines.append(f"- boundary_handoffs: {'; '.join(handoffs[:3])}")
        lines.append(f"- confidence: {confidence}")
        if unknown:
            lines.append(f"- unknown: {unknown}")
        return "\n".join(lines)

    def _enrich_entry_ports(
        self,
        entry_ports: List[Dict[str, Any]],
        upstream_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for item in entry_ports or []:
            if not isinstance(item, dict):
                continue
            enriched.append(dict(item))

        upstream_entries = list((upstream_context or {}).get("entry_ports") or [])
        if not upstream_entries:
            return enriched

        by_port: Dict[str, Dict[str, Any]] = {}
        for entry in enriched:
            port = self._normalize_signal_name(entry.get("port") or entry.get("target_port") or "")
            if port and port not in by_port:
                by_port[port] = entry

        for raw in upstream_entries:
            if not isinstance(raw, dict):
                continue
            target_port = str(raw.get("target_port") or raw.get("port") or "").strip()
            if not target_port:
                continue
            norm_port = self._normalize_signal_name(target_port)
            current = by_port.get(norm_port)
            matched_from = {
                "source_instance": str(raw.get("source_instance") or "").strip(),
                "source_module": str(raw.get("source_module") or "").strip(),
                "source_port": str(raw.get("source_port") or "").strip(),
                "parent_wire": str(raw.get("wire") or raw.get("parent_wire") or "").strip(),
            }
            if current is None:
                current = {
                    "port": target_port,
                    "direction": "input",
                    "value_kind": str(raw.get("value_kind") or "other").strip() or "other",
                    "semantic": self._coerce_text_field(raw.get("semantic")),
                    "matched_from": matched_from,
                }
                enriched.append(current)
                by_port[norm_port] = current
                continue
            current["matched_from"] = matched_from
            if not str(current.get("semantic") or "").strip():
                current["semantic"] = self._coerce_text_field(raw.get("semantic"))
            if not str(current.get("value_kind") or "").strip():
                current["value_kind"] = str(raw.get("value_kind") or "other").strip() or "other"
        return enriched

    def _enrich_draw_payload(
        self,
        node: Any,
        payload: Dict[str, Any],
        upstream_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        out = dict(payload or {})
        out["entry_ports"] = self._enrich_entry_ports(list(out.get("entry_ports") or []), upstream_context)
        boundary_handoffs = self._validate_boundary_handoffs_against_module(
            node,
            list(out.get("boundary_handoffs") or []),
        )
        if getattr(node, "parent", None) is not None and boundary_handoffs:
            out["boundary_handoffs"] = self._resolve_boundary_handoff_destinations(
                parent_node=node.parent,
                source_child_node=node,
                boundary_handoffs=boundary_handoffs,
            )
        else:
            out["boundary_handoffs"] = boundary_handoffs
        return out

    def _build_draw_child_tool_result(self, child_item: Dict[str, Any], child_task: str) -> str:
        payload = dict(child_item.get("draw_payload") or {})
        result = {
            "child": {
                "module": str(child_item.get("module") or payload.get("module") or "").strip(),
                "instance": str(child_item.get("instance") or payload.get("instance") or "").strip(),
                "path": str(child_item.get("path") or "").strip(),
            },
            "entry_ports": list(payload.get("entry_ports") or []),
            "boundary_handoffs": list(payload.get("boundary_handoffs") or []),
            "instruction_state": payload.get("instruction_state") or "",
            "lifecycle_context": payload.get("lifecycle_context") or "",
            "confidence": str(payload.get("confidence") or "low").strip(),
            "unknown": payload.get("unknown") or "",
        }
        if child_task:
            result["task"] = child_task
        return f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```"

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
            module_preview=self.get_module_preview(node.module_name),
            child_preview_list=self.build_child_overview(node),
            existing_child_draws=existing_child_draws,
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
                topology = self.g._build_topology_block(str(scoped_node.module_name))
                return f"[readSource] module={scoped_node.module_name}\n\n{topology}"

            if tool_name == "drawChild":
                module = str(args.get("module") or "").strip()
                child_task = str(args.get("task") or "").strip()
                child_node, error = self.g._resolve_scope_node(node, module, child_only=True)
                if child_node is None:
                    return error
                child_path = child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                if child_path in draw_cache:
                    return (
                        "[drawChild][reject] duplicate child draw request. "
                        f"child={child_node.instance_name}({child_node.module_name}) has already been drawn; "
                        "reuse the cached boundary summary instead of invoking drawChild again."
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
                            "prompt_tokens": int(
                                dict(cached_child.get("token_stats") or {}).get("input_tokens", 0) or 0
                            ),
                            "prompt_style": "instrack_draw",
                        },
                    )
                return self._build_draw_child_tool_result(cached_child, child_task)

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

        draw_content, token_stats = await self.g.owner.instrack_draw_llm.generate(
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
        payload = self._enrich_draw_payload(node, payload, upstream_context)
        return {
            "module": node.module_name,
            "instance": node.instance_name,
            "path": node_path,
            "mermaid": f"```mermaid\n{mermaid}\n```\n" if mermaid else "",
            "draw_payload": payload,
            "orchestrator_role": stage_label,
            "raw_draw_output": draw_content or "",
            "token_stats": dict(token_stats or {}),
        }
