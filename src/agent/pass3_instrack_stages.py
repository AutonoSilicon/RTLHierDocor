"""Pass3.3 InStrack stage helpers.

This module keeps pass3.3-specific locate/orchestrate/render logic out of
pass3_generator.py so the orchestration file stays shorter and easier to
navigate.
"""

import json
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Tuple

from .prompts import (
    PASS3_3_2_ORCHESTRATE_SYSTEM,
    PASS3_3_2_ORCHESTRATE_PROMPT,
)
from .pass3_instrack_takeover import Pass3InStrackTakeoverResolver


class Pass3InStrackStages:
    """Helper object that encapsulates pass3.3 stage operations."""

    def __init__(self, generator: Any):
        self.g = generator
        self._takeover_resolver = Pass3InStrackTakeoverResolver(generator)

    def build_pass3_3_orchestrate_input_hash(
        self,
        top_module: str,
        instruction: str,
        instruction_datasheet: str,
        search_result_json_text: str,
    ) -> str:
        payload = {
            "version": "pass3_3_instrack_orchestrate_cache_v2",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_2_ORCHESTRATE_SYSTEM,
            "prompt_template": PASS3_3_2_ORCHESTRATE_PROMPT,
            "instruction_datasheet_hash": self.g._hash_text(instruction_datasheet),
            "search_result_json_hash": self.g._hash_text(search_result_json_text),
            "tools_schema": self.g._pass3_3_2_tools(),
        }
        return self.g._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def build_pass3_3_render_input_hash(
        self,
        top_module: str,
        instruction: str,
        orchestration_json_text: str,
    ) -> str:
        payload = {
            "version": "pass3_3_instrack_render_cache_v1",
            "top_module": top_module,
            "instruction": instruction,
            "orchestration_json_hash": self.g._hash_text(orchestration_json_text),
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

    @staticmethod
    def _format_grouped_child_instances(instance_names: List[str], limit: int = 6) -> str:
        names = [str(name).strip() for name in instance_names if str(name).strip()]
        if not names:
            return ""
        if len(names) <= limit:
            return ", ".join(names)
        hidden = len(names) - limit
        return f"{', '.join(names[:limit])} ... (+{hidden} more)"

    def build_child_overview(self, current_node: Any) -> str:
        grouped: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        for child in sorted(current_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_desc = self.get_module_preview(child.module_name)
            group_key = f"{child.module_name}\0{child_desc}"
            bucket = grouped.get(group_key)
            if bucket is None:
                bucket = {
                    "module_name": child.module_name,
                    "desc": child_desc,
                    "instances": [],
                }
                grouped[group_key] = bucket
            bucket["instances"].append(child.instance_name)

        lines: List[str] = []
        for bucket in grouped.values():
            instance_label = self._format_grouped_child_instances(bucket["instances"])
            lines.append(f"- {instance_label} ({bucket['module_name']}): {bucket['desc']}")
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

    def extract_pass3_3_orchestrate_payload(self, content: str, current_node: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "stage": "orchestrate",
            "module": current_node.module_name,
            "instance": current_node.instance_name,
            "boundary_takeover": [],
            "boundary_handoffs": [],
            "lifecycle_context": "",
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

        payload["stage"] = "orchestrate"
        payload["module"] = str(parsed.get("module") or current_node.module_name).strip()
        payload["instance"] = str(parsed.get("instance") or current_node.instance_name).strip()
        payload["lifecycle_context"] = self._coerce_text_field(parsed.get("lifecycle_context"))
        conf = str(parsed.get("confidence") or "low").strip().lower()
        payload["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        payload["unknown"] = self._coerce_text_field(parsed.get("unknown"))

        boundary_handoffs = parsed.get("boundary_handoffs")
        if isinstance(boundary_handoffs, list):
            norm_handoffs: List[Dict[str, Any]] = []
            for item in boundary_handoffs:
                if not isinstance(item, dict):
                    continue
                output_port = str(item.get("output_port") or "").strip()
                if not output_port:
                    continue
                norm_handoffs.append({
                    "output_port": output_port,
                    "value_condition": self._coerce_text_field(item.get("value_condition")),
                    "behavior": self._coerce_text_field(item.get("behavior")),
                })
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

    def _normalize_public_boundary_handoff(self, raw: Any) -> Dict[str, str]:
        item = raw if isinstance(raw, dict) else {}
        return {
            "output_port": str(item.get("output_port") or "").strip(),
            "value_condition": self._coerce_text_field(item.get("value_condition")),
            "behavior": self._coerce_text_field(item.get("behavior")),
        }

    def _boundary_handoff_display_text(self, raw: Any) -> str:
        item = raw if isinstance(raw, dict) else {}
        value_condition = self._coerce_text_field(item.get("value_condition"))
        if value_condition:
            return value_condition
        return self._coerce_text_field(item.get("behavior"))

    def _normalize_public_boundary_takeover(self, raw: Any) -> Dict[str, str]:
        item = raw if isinstance(raw, dict) else {}
        return {
            "input_port": str(item.get("input_port") or "").strip(),
            "value_condition": self._coerce_text_field(item.get("value_condition")),
            "behavior": self._coerce_text_field(item.get("behavior")),
        }

    def _boundary_takeover_display_text(self, raw: Any) -> str:
        item = raw if isinstance(raw, dict) else {}
        value_condition = self._coerce_text_field(item.get("value_condition"))
        if value_condition:
            return value_condition
        return self._coerce_text_field(item.get("behavior"))

    def _get_resolved_boundary_handoffs(self, payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        resolved = payload.get("_boundary_handoff_routes")
        if isinstance(resolved, list):
            return [dict(item) for item in resolved if isinstance(item, dict)]
        handoffs = payload.get("boundary_handoffs")
        if isinstance(handoffs, list):
            return [dict(item) for item in handoffs if isinstance(item, dict)]
        return []

    def _coerce_lifecycle_history(self, lifecycle_context: Any) -> List[Dict[str, str]]:
        history: List[Dict[str, str]] = []
        if not isinstance(lifecycle_context, list):
            return history
        for raw in lifecycle_context:
            if not isinstance(raw, dict):
                continue
            accessed_submodule = str(raw.get("accessed_submodule") or "").strip()
            behavior_description = self._coerce_text_field(raw.get("behavior_description"))
            if not accessed_submodule or not behavior_description:
                continue
            history.append(
                {
                    "accessed_submodule": accessed_submodule,
                    "behavior_description": behavior_description,
                }
            )
        return history

    def _make_lifecycle_history_item(
        self,
        source_child_node: Any,
        source_payload: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, str]]:
        behavior_description = self._coerce_text_field((source_payload or {}).get("lifecycle_context"))
        if not behavior_description:
            return None
        instance_name = str(getattr(source_child_node, "instance_name", "") or "").strip()
        module_name = str(getattr(source_child_node, "module_name", "") or "").strip()
        if not instance_name or not module_name:
            return None
        return {
            "accessed_submodule": f"{instance_name}({module_name})",
            "behavior_description": behavior_description,
        }

    def _append_lifecycle_history_item(
        self,
        history: Any,
        source_child_node: Any,
        source_payload: Optional[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        out = self._coerce_lifecycle_history(history)
        item = self._make_lifecycle_history_item(source_child_node, source_payload)
        if item is not None:
            out.append(item)
        return out

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

    async def run_pass3_3_2_orchestrate(
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

        # Step 1: orchestrate leaf start module first.
        start_node = path_nodes[-1]
        results: List[Dict[str, Any]] = []
        orchestrate_cache: Dict[str, Dict[str, Any]] = {}

        self.g._append_fork_trace(
            "orchestrate_enter",
            {
                "pass": "pass3_3_2_orchestrate",
                "prompt_style": "instrack_orchestrate",
                "stage_label": "start_module",
                "instance": start_node.instance_name,
                "module": start_node.module_name,
                "level": int(getattr(start_node, "depth", 0) or 0),
                "node_path": start_node.get_path() if hasattr(start_node, "get_path") else start_node.instance_name,
            },
        )
        start_item = await self._run_orchestrate_agent_for_node(
            top_node=top_node,
            node=start_node,
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            stage_label="start_module",
            orchestrate_cache=orchestrate_cache,
        )
        self.g._append_fork_trace(
            "orchestrate_return",
            {
                "pass": "pass3_3_2_orchestrate",
                "prompt_style": "instrack_orchestrate",
                "stage_label": "start_module",
                "instance": start_node.instance_name,
                "module": start_node.module_name,
                "level": int(getattr(start_node, "depth", 0) or 0),
                "node_path": start_node.get_path() if hasattr(start_node, "get_path") else start_node.instance_name,
                "prompt_tokens": int(dict(start_item.get("token_stats") or {}).get("input_tokens", 0) or 0),
                "report_chars": len(str(start_item.get("raw_orchestration_output") or "")),
            },
        )
        results.append(start_item)
        start_path = start_node.get_path() if hasattr(start_node, "get_path") else start_node.instance_name
        recorded_paths: Set[str] = {start_path}
        orchestrate_cache[start_path] = start_item
        pending_handoffs = self._get_resolved_boundary_handoffs(start_item.get("orchestration") or {})
        pending_payload = dict(start_item.get("orchestration") or {})

        # Step 2: climb to top and orchestrate parent-context bridges level by level.
        for idx in range(len(path_nodes) - 2, -1, -1):
            parent_node = path_nodes[idx]
            source_child_node = path_nodes[idx + 1]
            parent_path = parent_node.get_path() if hasattr(parent_node, "get_path") else parent_node.instance_name
            cache_before_parent = set(orchestrate_cache.keys())
            stage_label = "top_module" if parent_node is path_nodes[0] else "parent_module"
            parent_bridge_context = self._build_bridge_context(
                parent_node=parent_node,
                source_child_node=source_child_node,
                handoffs=pending_handoffs,
                source_payload=pending_payload,
            )
            self.g._append_fork_trace(
                "orchestrate_parent_context",
                {
                    "pass": "pass3_3_2_orchestrate",
                    "prompt_style": "instrack_orchestrate",
                    "stage_label": stage_label,
                    "instance": parent_node.instance_name,
                    "module": parent_node.module_name,
                    "level": int(getattr(parent_node, "depth", 0) or 0),
                    "node_path": parent_path,
                    "source_instance": str(parent_bridge_context.get("source_instance") or "").strip(),
                    "source_module": str(parent_bridge_context.get("source_module") or "").strip(),
                    "candidate_children": list(parent_bridge_context.get("candidate_children") or []),
                    "resolved_handoffs": list(parent_bridge_context.get("resolved_handoffs") or []),
                    "exits_parent": list(parent_bridge_context.get("exits_parent") or []),
                    "unresolved": list(parent_bridge_context.get("unresolved") or []),
                },
            )
            self.g._append_fork_trace(
                "orchestrate_enter",
                {
                    "pass": "pass3_3_2_orchestrate",
                    "prompt_style": "instrack_orchestrate",
                    "stage_label": stage_label,
                    "instance": parent_node.instance_name,
                    "module": parent_node.module_name,
                    "level": int(getattr(parent_node, "depth", 0) or 0),
                    "node_path": parent_path,
                },
            )
            parent_item = await self._run_orchestrate_agent_for_node(
                top_node=top_node,
                node=parent_node,
                instruction=instruction,
                instruction_datasheet=instruction_datasheet,
                stage_label=stage_label,
                orchestrate_cache=orchestrate_cache,
                continuation_handoffs_raw=pending_handoffs,
                source_child_for_handoff=source_child_node,
                source_payload_for_handoff=pending_payload,
                bridge_context=parent_bridge_context,
                boundary_takeover=[],
                continuation_source=self._build_continuation_source(source_child_node, parent_bridge_context),
            )
            self.g._append_fork_trace(
                "orchestrate_return",
                {
                    "pass": "pass3_3_2_orchestrate",
                    "prompt_style": "instrack_orchestrate",
                    "stage_label": stage_label,
                    "instance": parent_node.instance_name,
                    "module": parent_node.module_name,
                    "level": int(getattr(parent_node, "depth", 0) or 0),
                    "node_path": parent_path,
                    "prompt_tokens": int(dict(parent_item.get("token_stats") or {}).get("input_tokens", 0) or 0),
                    "report_chars": len(str(parent_item.get("raw_orchestration_output") or "")),
                },
            )

            # Record newly generated on-demand child orchestrations triggered by drawChild requests.
            new_child_paths = sorted(path for path in orchestrate_cache.keys() if path not in cache_before_parent)
            for child_path in new_child_paths:
                if child_path in recorded_paths:
                    continue
                child_item = orchestrate_cache.get(child_path)
                if child_item:
                    results.append(child_item)
                    recorded_paths.add(child_path)

            results.append(parent_item)
            recorded_paths.add(parent_path)
            orchestrate_cache[parent_path] = parent_item
            pending_handoffs = self._get_resolved_boundary_handoffs(parent_item.get("orchestration") or {})
            pending_payload = dict(parent_item.get("orchestration") or {})

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
            output_port = str(item.get("output_port") or "").strip()
            summary_text = self._boundary_handoff_display_text(item)
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
            head = output_port or "unknown_port"
            if summary_text:
                head = f"{head}({summary_text})"
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
            output_port = str(handoff.get("output_port") or "").strip()
            norm_port = self._normalize_signal_name(output_port)
            if not output_port:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    "missing output_port",
                )
                validated.append(handoff)
                continue

            port_dir = normalized_dirs.get(norm_port, "")
            if not port_dir:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"output_port '{output_port}' is not a declared port of module '{node.module_name}'",
                )
                validated.append(handoff)
                continue

            if port_dir != "output":
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"output_port '{output_port}' is not an output port",
                )
                validated.append(handoff)
                continue

            if norm_port in seen_ports:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"duplicate boundary_handoff for output_port '{output_port}'",
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
        return self._takeover_resolver.resolve(
            parent_node=parent_node,
            source_child_node=source_child_node,
            boundary_handoffs=boundary_handoffs,
        ).resolved_handoffs

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

    def _normalize_boundary_takeover(self, boundary_takeover: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        if not isinstance(boundary_takeover, list):
            return normalized
        for raw in boundary_takeover:
            if not isinstance(raw, dict):
                continue
            item = self._normalize_public_boundary_takeover(raw)
            input_port = str(item.get("input_port") or "").strip()
            if not input_port:
                continue
            key = "|".join(
                [
                    self._normalize_signal_name(input_port),
                    str(item.get("value_condition") or "").strip(),
                    str(item.get("behavior") or "").strip(),
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def _build_continuation_source(
        self,
        source_child_node: Optional[Any],
        bridge_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        source_instance_path = ""
        if source_child_node is not None:
            source_instance_path = (
                source_child_node.get_path() if hasattr(source_child_node, "get_path") else source_child_node.instance_name
            )
        payload = {
            "source_instance": str(getattr(source_child_node, "instance_name", "") or "").strip(),
            "source_module": str(getattr(source_child_node, "module_name", "") or "").strip(),
            "source_instance_path": str(source_instance_path or "").strip(),
        }
        bridge = dict(bridge_context or {})
        for key in ("source_instance", "source_module", "source_instance_path"):
            if not payload[key]:
                payload[key] = str(bridge.get(key) or "").strip()
        return {key: value for key, value in payload.items() if value}

    @staticmethod
    def _build_continuation_source_from_bridge_context(
        bridge_context: Optional[Dict[str, Any]],
    ) -> Dict[str, str]:
        bridge = dict(bridge_context or {})
        payload = {
            "source_instance": str(bridge.get("source_instance") or "").strip(),
            "source_module": str(bridge.get("source_module") or "").strip(),
            "source_instance_path": str(bridge.get("source_instance_path") or "").strip(),
        }
        return {key: value for key, value in payload.items() if value}

    @staticmethod
    def _build_boundary_takeover_from_bundles(
        bridge_context: Optional[Dict[str, Any]],
        target_child_node: Any,
    ) -> List[Dict[str, Any]]:
        bridge = dict(bridge_context or {})
        bundles = list(bridge.get("takeover_bundles") or [])
        target_instance = str(getattr(target_child_node, "instance_name", "") or "").strip()
        target_module = str(getattr(target_child_node, "module_name", "") or "").strip()
        for bundle in bundles:
            if not isinstance(bundle, dict):
                continue
            bundle_instance = str(bundle.get("target_instance") or "").strip()
            bundle_module = str(bundle.get("target_module") or "").strip()
            if bundle_instance and bundle_instance == target_instance:
                return list(bundle.get("boundary_takeover") or [])[:8]
            if not bundle_instance and bundle_module and bundle_module == target_module:
                return list(bundle.get("boundary_takeover") or [])[:8]
            if bundle_module and bundle_module == target_module:
                return list(bundle.get("boundary_takeover") or [])[:8]
        return []

    def _group_boundary_takeover_bundles(
        self,
        *,
        handoffs: List[Dict[str, Any]],
        source_child_node: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        del source_child_node
        return self._takeover_resolver.group_boundary_takeover_bundles(handoffs)

    def _build_boundary_takeover(
        self,
        *,
        parent_node: Any,
        source_child_node: Any,
        target_child_node: Any,
        handoffs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        del parent_node
        del source_child_node
        return self._takeover_resolver.build_boundary_takeover_for_target(
            handoffs=handoffs,
            target_child_node=target_child_node,
        )

    def _build_port_ingress_bridge_context(
        self,
        *,
        parent_node: Any,
        boundary_takeover: List[Dict[str, Any]],
        continuation_source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = self._takeover_resolver.resolve_parent_ingress(
            parent_node=parent_node,
            boundary_takeover=boundary_takeover,
            target_child_node=None,
        )
        candidate_children = [
            {
                "target_instance": str(bundle.get("target_instance") or "").strip(),
                "target_module": str(bundle.get("target_module") or "").strip(),
            }
            for bundle in list(result.takeover_bundles or [])[:8]
        ]
        resolved_ingress: List[Dict[str, Any]] = []
        for item in list(result.resolved_handoffs or [])[:12]:
            if not isinstance(item, dict):
                continue
            input_port = str(item.get("input_port") or "").strip()
            value_condition = self._coerce_text_field(item.get("value_condition"))
            behavior = self._coerce_text_field(item.get("behavior"))
            status = str(item.get("status") or "").strip()
            parent_wire = str(item.get("parent_wire") or "").strip()
            targets: List[Dict[str, str]] = []
            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                if str(resolution.get("resolution_kind") or "").strip() != "sibling_child":
                    continue
                target_instance = str(resolution.get("target_instance") or "").strip()
                target_module = str(resolution.get("target_module") or "").strip()
                target_port = str(resolution.get("target_port") or "").strip()
                if target_instance or target_module or target_port:
                    targets.append(
                        {
                            "target_instance": target_instance,
                            "target_module": target_module,
                            "target_port": target_port,
                            "parent_wire": str(resolution.get("parent_wire") or parent_wire).strip(),
                        }
                    )
            if input_port or value_condition or behavior:
                entry: Dict[str, Any] = {
                    "input_port": input_port,
                    "value_condition": value_condition,
                    "behavior": behavior,
                    "status": status,
                    "parent_wire": parent_wire,
                }
                if targets:
                    entry["targets"] = targets
                resolved_ingress.append(entry)

        source_info = dict(continuation_source or {})
        return {
            "source_instance": str(source_info.get("source_instance") or "").strip(),
            "source_module": str(source_info.get("source_module") or "").strip(),
            "source_instance_path": str(source_info.get("source_instance_path") or "").strip(),
            "parent_module": str(parent_node.module_name or "").strip(),
            "entry_mode": "parent_port_ingress",
            "candidate_children": candidate_children,
            "takeover_bundles": list(result.takeover_bundles or []),
            "resolved_handoffs": resolved_ingress,
            "exits_parent": list(result.exits_parent or [])[:8],
            "unresolved": list(result.unresolved or [])[:6],
            "debug_report": dict(result.debug_report or {}),
        }

    def _prepare_child_continuation_inputs(
        self,
        *,
        parent_node: Any,
        source_child_node: Optional[Any],
        target_child_node: Any,
        handoffs: List[Dict[str, Any]],
        bridge_context: Optional[Dict[str, Any]] = None,
        override_hint: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str], str]:
        child_boundary_takeover: List[Dict[str, Any]] = []
        child_continuation_source: Dict[str, str] = {}
        warning = ""
        if source_child_node is None:
            bridge = dict(bridge_context or {})
            child_boundary_takeover = self._build_boundary_takeover_from_bundles(
                bridge,
                target_child_node,
            )
            child_continuation_source = self._build_continuation_source_from_bridge_context(bridge)
            if (
                str(bridge.get("entry_mode") or "").strip() == "parent_port_ingress"
                and not child_boundary_takeover
                and not override_hint
            ):
                warning = (
                    "No graph-resolved port-ingress boundary_takeover exists for "
                    f"{target_child_node.instance_name}({target_child_node.module_name}) in the current continuation; "
                    "proceeding with empty takeover bridge."
                )
            return child_boundary_takeover, child_continuation_source, warning

        child_boundary_takeover = self._build_boundary_takeover(
            parent_node=parent_node,
            source_child_node=source_child_node,
            target_child_node=target_child_node,
            handoffs=handoffs,
        )
        child_continuation_source = self._build_continuation_source(
            source_child_node,
            bridge_context,
        )
        if not child_boundary_takeover and not override_hint:
            warning = (
                "No graph-resolved boundary_takeover exists for "
                f"{target_child_node.instance_name}({target_child_node.module_name}) in the current continuation; "
                "proceeding with empty takeover bridge."
            )
        return child_boundary_takeover, child_continuation_source, warning

    def _build_bridge_context(
        self,
        *,
        parent_node: Any,
        source_child_node: Any,
        handoffs: List[Dict[str, Any]],
        source_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        del source_payload
        source_path = (
            source_child_node.get_path() if hasattr(source_child_node, "get_path") else source_child_node.instance_name
        )
        takeover_bundles = self._group_boundary_takeover_bundles(
            handoffs=handoffs,
            source_child_node=source_child_node,
        )
        candidate_children = [
            {
                "target_instance": str(bundle.get("target_instance") or "").strip(),
                "target_module": str(bundle.get("target_module") or "").strip(),
            }
            for bundle in takeover_bundles[:8]
        ]
        resolved_handoffs: List[Dict[str, Any]] = []
        exits_parent: List[Dict[str, str]] = []
        unresolved: List[str] = []
        seen_exit_ports: Set[str] = set()

        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            output_port = str(item.get("output_port") or "").strip()
            value_condition = self._coerce_text_field(item.get("value_condition"))
            behavior = self._coerce_text_field(item.get("behavior"))
            status = str(item.get("status") or "").strip()
            parent_wire = str(item.get("parent_wire") or "").strip()

            targets: List[Dict[str, str]] = []
            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                kind = str(resolution.get("resolution_kind") or "").strip()
                if kind == "sibling_child":
                    target_instance = str(resolution.get("target_instance") or "").strip()
                    target_module = str(resolution.get("target_module") or "").strip()
                    target_port = str(resolution.get("target_port") or "").strip()
                    if target_instance or target_module or target_port:
                        targets.append(
                            {
                                "target_instance": target_instance,
                                "target_module": target_module,
                                "target_port": target_port,
                                "parent_wire": str(resolution.get("parent_wire") or parent_wire).strip(),
                            }
                        )
                elif kind == "exit_parent":
                    parent_port = str(resolution.get("parent_port") or "").strip()
                    if parent_port and parent_port not in seen_exit_ports:
                        seen_exit_ports.add(parent_port)
                        exits_parent.append(
                            {
                                "parent_port": parent_port,
                                "parent_wire": str(resolution.get("parent_wire") or parent_wire).strip(),
                                "source_port": output_port,
                            }
                        )

            if output_port or value_condition or behavior:
                handoff_entry: Dict[str, Any] = {
                    "output_port": output_port,
                    "value_condition": value_condition,
                    "behavior": behavior,
                    "status": status,
                    "parent_wire": parent_wire,
                }
                if targets:
                    handoff_entry["targets"] = targets
                resolved_handoffs.append(handoff_entry)

            if status in {"unresolved", "invalid"}:
                label = output_port or value_condition or behavior or "unknown"
                if label and label not in unresolved:
                    unresolved.append(label)

        return {
            "source_instance": str(source_child_node.instance_name or "").strip(),
            "source_module": str(source_child_node.module_name or "").strip(),
            "source_instance_path": source_path,
            "parent_module": str(parent_node.module_name or "").strip(),
            "candidate_children": candidate_children,
            "takeover_bundles": takeover_bundles,
            "resolved_handoffs": resolved_handoffs[:12],
            "exits_parent": exits_parent[:8],
            "unresolved": unresolved[:6],
        }

    @staticmethod
    def _build_child_ref(node: Any) -> Dict[str, str]:
        path = node.get_path() if hasattr(node, "get_path") else node.instance_name
        return {
            "instance": str(getattr(node, "instance_name", "") or "").strip(),
            "module": str(getattr(node, "module_name", "") or "").strip(),
            "path": str(path or "").strip(),
        }

    @staticmethod
    def _build_child_ref_from_source_payload(source_payload: Optional[Dict[str, Any]]) -> Dict[str, str]:
        payload = dict(source_payload or {})
        return {
            "instance": str(payload.get("source_instance") or "").strip(),
            "module": str(payload.get("source_module") or "").strip(),
            "path": str(payload.get("source_instance_path") or "").strip(),
        }

    @staticmethod
    def _format_child_ref_debug(ref: Any) -> str:
        if isinstance(ref, dict):
            instance_name = str(ref.get("instance") or "").strip()
            module_name = str(ref.get("module") or "").strip()
            path = str(ref.get("path") or "").strip()
        else:
            instance_name = str(getattr(ref, "instance_name", "") or "").strip()
            module_name = str(getattr(ref, "module_name", "") or "").strip()
            if hasattr(ref, "get_path"):
                path = str(ref.get_path() or "").strip()
            else:
                path = instance_name
        head = instance_name or module_name or "unknown"
        if instance_name and module_name:
            head = f"{instance_name}({module_name})"
        if path:
            return f"{head} @ {path}"
        return head

    @staticmethod
    def _child_refs_match(left: Any, right: Any) -> bool:
        def _normalize(ref: Any) -> Dict[str, str]:
            if isinstance(ref, dict):
                return {
                    "instance": str(ref.get("instance") or "").strip(),
                    "module": str(ref.get("module") or "").strip(),
                    "path": str(ref.get("path") or "").strip(),
                }
            path = ""
            if hasattr(ref, "get_path"):
                path = str(ref.get_path() or "").strip()
            return {
                "instance": str(getattr(ref, "instance_name", "") or "").strip(),
                "module": str(getattr(ref, "module_name", "") or "").strip(),
                "path": path,
            }

        left_ref = _normalize(left)
        right_ref = _normalize(right)
        left_path = str(left_ref.get("path") or "").strip()
        right_path = str(right_ref.get("path") or "").strip()
        if left_path and right_path:
            return left_path == right_path
        left_instance = str(left_ref.get("instance") or "").strip()
        right_instance = str(right_ref.get("instance") or "").strip()
        if left_instance and right_instance:
            return left_instance == right_instance
        left_module = str(left_ref.get("module") or "").strip()
        right_module = str(right_ref.get("module") or "").strip()
        return bool(left_module and right_module and left_module == right_module)

    def _normalize_candidate_child_refs(self, candidates: Any) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for raw in list(candidates or [])[:8]:
            if not isinstance(raw, dict):
                continue
            instance_name = str(raw.get("target_instance") or raw.get("instance") or "").strip()
            module_name = str(raw.get("target_module") or raw.get("module") or "").strip()
            if not (instance_name or module_name):
                continue
            normalized.append(
                {
                    "instance": instance_name,
                    "module": module_name,
                }
            )
        return normalized

    def _resolve_candidate_child_ref(
        self,
        parent_node: Any,
        candidate: Dict[str, Any],
    ) -> Dict[str, str]:
        instance_name = str(candidate.get("target_instance") or candidate.get("instance") or "").strip()
        module_name = str(candidate.get("target_module") or candidate.get("module") or "").strip()
        if instance_name and instance_name in getattr(parent_node, "children", {}):
            return self._build_child_ref(parent_node.children[instance_name])
        if module_name:
            matches = [
                child
                for child in getattr(parent_node, "children", {}).values()
                if str(getattr(child, "module_name", "") or "").strip() == module_name
            ]
            if len(matches) == 1:
                return self._build_child_ref(matches[0])
        return {
            "instance": instance_name,
            "module": module_name,
            "path": "",
        }

    def _build_non_direct_child_advisory_tool_result(
        self,
        *,
        source_child_node: Optional[Any],
        requested_child_node: Any,
        authoritative_candidates: List[Dict[str, Any]],
        advisory: Any,
        source_child_ref: Optional[Dict[str, Any]] = None,
    ) -> str:
        source_child = (
            self._build_child_ref(source_child_node)
            if source_child_node is not None
            else self._build_child_ref_from_source_payload(source_child_ref)
        )
        payload: Dict[str, Any] = {
            "status": "advisory_non_direct_child",
            "requested_child": self._build_child_ref(requested_child_node),
            "source_child": source_child,
            "authoritative_candidates": self._normalize_candidate_child_refs(authoritative_candidates),
            "recommended_child": dict(getattr(advisory, "recommended_child", {}) or {}),
            "advisory_path": list(getattr(advisory, "advisory_path", []) or []),
            "reason": str(getattr(advisory, "reason", "") or "").strip(),
            "retry_contract": (
                "Repeat the same child once to force the override, "
                "or call the recommended child once to accept the relay suggestion."
            ),
        }
        skipped_children = list(getattr(advisory, "skipped_children", []) or [])
        if skipped_children:
            payload["skipped_children"] = skipped_children[:8]
        return f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"

    def _build_non_direct_child_override_hint(self, advisory: Dict[str, Any], mode: str) -> Dict[str, Any]:
        return {
            "mode": str(mode or "").strip(),
            "source_child": dict(advisory.get("source_child") or {}),
            "requested_child": dict(advisory.get("requested_child") or {}),
            "recommended_child": dict(advisory.get("recommended_child") or {}),
            "advisory_path": list(advisory.get("advisory_path") or []),
            "reason": str(advisory.get("reason") or "").strip(),
        }

    def _compute_active_continuation_signature(self, active_continuation: Dict[str, Any]) -> str:
        source_child_node = active_continuation.get("source_child_node")
        source_path = ""
        if source_child_node is not None:
            source_path = (
                source_child_node.get_path() if hasattr(source_child_node, "get_path") else source_child_node.instance_name
            )
        handoff_summary: List[Dict[str, Any]] = []
        for raw in list(active_continuation.get("handoffs") or [])[:16]:
            if not isinstance(raw, dict):
                continue
            item = {
                "output_port": str(raw.get("output_port") or "").strip(),
                "status": str(raw.get("status") or "").strip(),
                "parent_wire": str(raw.get("parent_wire") or "").strip(),
                "targets": [],
            }
            for resolution in list(raw.get("resolutions") or [])[:8]:
                if not isinstance(resolution, dict):
                    continue
                item["targets"].append(
                    {
                        "kind": str(resolution.get("resolution_kind") or "").strip(),
                        "target_instance": str(resolution.get("target_instance") or "").strip(),
                        "target_module": str(resolution.get("target_module") or "").strip(),
                        "target_port": str(resolution.get("target_port") or "").strip(),
                        "parent_port": str(resolution.get("parent_port") or "").strip(),
                    }
                )
            handoff_summary.append(item)
        payload = {
            "source_path": str(source_path or "").strip(),
            "boundary_takeover": self._normalize_boundary_takeover(active_continuation.get("boundary_takeover")),
            "continuation_source": dict(active_continuation.get("continuation_source") or {}),
            "bridge_context": {
                "candidate_children": self._normalize_candidate_child_refs(
                    dict(active_continuation.get("bridge_context") or {}).get("candidate_children") or []
                ),
                "resolved_handoffs": handoff_summary,
            },
        }
        return self.g._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _build_active_continuation(
        self,
        *,
        parent_node: Any,
        source_child_node: Optional[Any],
        handoffs: List[Dict[str, Any]],
        source_payload: Optional[Dict[str, Any]],
        lifecycle_context: Optional[List[Dict[str, Any]]] = None,
        bridge_context: Optional[Dict[str, Any]] = None,
        boundary_takeover: Optional[List[Dict[str, Any]]] = None,
        continuation_source: Optional[Dict[str, Any]] = None,
        override_hint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        state = {
            "source_child_node": source_child_node,
            "handoffs": list(handoffs or []),
            "payload": dict(source_payload or {}),
            "lifecycle_context": self._coerce_lifecycle_history(lifecycle_context),
            "bridge_context": dict(bridge_context or {}),
            "boundary_takeover": self._normalize_boundary_takeover(boundary_takeover),
            "continuation_source": dict(continuation_source or {}),
            "override_hint": dict(override_hint or {}),
        }
        if source_child_node is not None and not state["bridge_context"]:
            state["bridge_context"] = self._build_bridge_context(
                parent_node=parent_node,
                source_child_node=source_child_node,
                handoffs=list(handoffs or []),
                source_payload=dict(source_payload or {}),
            )
        elif (
            source_child_node is None
            and not state["bridge_context"]
            and state["boundary_takeover"]
        ):
            state["bridge_context"] = self._build_port_ingress_bridge_context(
                parent_node=parent_node,
                boundary_takeover=list(state["boundary_takeover"] or []),
                continuation_source=state.get("continuation_source"),
            )
        if not state["continuation_source"] and source_child_node is not None:
            state["continuation_source"] = self._build_continuation_source(
                source_child_node,
                state.get("bridge_context"),
            )
        elif not state["continuation_source"] and state["bridge_context"]:
            state["continuation_source"] = self._build_continuation_source_from_bridge_context(
                state.get("bridge_context"),
            )
        if source_child_node is not None:
            state["lifecycle_context"] = self._append_lifecycle_history_item(
                state.get("lifecycle_context"),
                source_child_node,
                source_payload,
            )
        return state

    def _advance_active_continuation_from_child(
        self,
        *,
        parent_node: Any,
        child_node: Any,
        child_item: Dict[str, Any],
        inherited_bridge_context: Optional[Dict[str, Any]] = None,
        inherited_lifecycle_context: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        del inherited_bridge_context
        payload = dict((child_item or {}).get("orchestration") or {})
        handoffs = self._get_resolved_boundary_handoffs(payload)
        next_bridge_context = self._build_bridge_context(
            parent_node=parent_node,
            source_child_node=child_node,
            handoffs=handoffs,
            source_payload=payload,
        )
        return self._build_active_continuation(
            parent_node=parent_node,
            source_child_node=child_node,
            handoffs=handoffs,
            source_payload=payload,
            lifecycle_context=inherited_lifecycle_context,
            bridge_context=next_bridge_context,
            boundary_takeover=[],
            continuation_source=self._build_continuation_source(child_node, next_bridge_context),
        )

    def _enrich_orchestration_payload(
        self,
        node: Any,
        payload: Dict[str, Any],
        boundary_takeover: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        out = dict(payload or {})
        out["boundary_takeover"] = self._normalize_boundary_takeover(boundary_takeover)
        boundary_handoffs = list(out.get("boundary_handoffs") or [])
        if getattr(node, "parent", None) is not None and boundary_handoffs:
            resolved_handoffs = self._resolve_boundary_handoff_destinations(
                parent_node=node.parent,
                source_child_node=node,
                boundary_handoffs=boundary_handoffs,
            )
        else:
            resolved_handoffs = self._validate_boundary_handoffs_against_module(
                node,
                boundary_handoffs,
            )
        out["boundary_handoffs"] = [
            self._normalize_public_boundary_handoff(item)
            for item in resolved_handoffs
            if str(item.get("output_port") or "").strip() and str(item.get("status") or "").strip() != "invalid"
        ]
        out["_boundary_handoff_routes"] = resolved_handoffs
        return out

    def _compact_tool_text(self, value: Any, max_chars: int = 220) -> str:
        text = self._coerce_text_field(value)
        if not text:
            return ""
        text = " ".join(text.split())
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _summarize_tool_boundary_handoffs(
        self,
        boundary_handoffs: List[Dict[str, Any]],
        *,
        max_items: int = 6,
        max_resolutions: int = 3,
    ) -> List[Dict[str, Any]]:
        summarized: List[Dict[str, Any]] = []
        for raw in list(boundary_handoffs or [])[:max_items]:
            if not isinstance(raw, dict):
                continue
            item: Dict[str, Any] = {}
            for key in ("output_port", "status"):
                value = str(raw.get(key) or "").strip()
                if value:
                    item[key] = value
            value_condition = self._compact_tool_text(raw.get("value_condition"), max_chars=160)
            if value_condition:
                item["value_condition"] = value_condition
            behavior = self._compact_tool_text(raw.get("behavior"), max_chars=180)
            if behavior:
                item["behavior"] = behavior

            resolutions_out: List[Dict[str, Any]] = []
            for resolution in list(raw.get("resolutions") or [])[:max_resolutions]:
                if not isinstance(resolution, dict):
                    continue
                resolution_item: Dict[str, Any] = {}
                kind = str(resolution.get("resolution_kind") or "").strip()
                if kind:
                    resolution_item["resolution_kind"] = kind
                for key in ("target_instance", "target_module", "target_port", "parent_port", "parent_wire"):
                    value = str(resolution.get(key) or "").strip()
                    if value:
                        resolution_item[key] = value
                if resolution_item:
                    resolutions_out.append(resolution_item)
            if resolutions_out:
                item["resolutions"] = resolutions_out
            extra_resolutions = max(0, len(list(raw.get("resolutions") or [])) - len(resolutions_out))
            if extra_resolutions:
                item["resolutions_truncated"] = extra_resolutions
            summarized.append(item)
        return summarized

    def _extract_next_children_from_handoffs(
        self,
        boundary_handoffs: List[Dict[str, Any]],
        *,
        max_items: int = 6,
    ) -> List[Dict[str, str]]:
        next_children: List[Dict[str, str]] = []
        for bundle in self._group_boundary_takeover_bundles(handoffs=boundary_handoffs):
            target_instance = str(bundle.get("target_instance") or "").strip()
            target_module = str(bundle.get("target_module") or "").strip()
            if not (target_instance or target_module):
                continue
            next_children.append(
                {
                    "target_instance": target_instance,
                    "target_module": target_module,
                }
            )
            if len(next_children) >= max_items:
                break
        return next_children

    def _summarize_tool_unknown(self, unknown: Any, *, max_items: int = 3) -> Any:
        if isinstance(unknown, list):
            lines: List[str] = []
            for raw in unknown[:max_items]:
                if isinstance(raw, dict):
                    aspect = self._compact_tool_text(raw.get("aspect"), max_chars=80)
                    reason = self._compact_tool_text(raw.get("reason"), max_chars=160)
                    text = f"{aspect}: {reason}".strip(": ")
                else:
                    text = self._compact_tool_text(raw, max_chars=180)
                if text:
                    lines.append(text)
            extra_items = max(0, len(unknown) - len(lines))
            if extra_items:
                lines.append(f"+{extra_items} more unknowns")
            return lines
        return self._compact_tool_text(unknown, max_chars=220)

    def _build_fork_subagent_tool_result(
        self,
        child_item: Dict[str, Any],
        child_task: str,
        cached: bool = False,
    ) -> str:
        payload = dict(child_item.get("orchestration") or {})
        boundary_handoffs = self._get_resolved_boundary_handoffs(payload)
        result = {
            "child": {
                "module": str(child_item.get("module") or payload.get("module") or "").strip(),
                "instance": str(child_item.get("instance") or payload.get("instance") or "").strip(),
                "path": str(child_item.get("path") or "").strip(),
            },
            "cached": bool(cached),
            "boundary_handoffs": self._summarize_tool_boundary_handoffs(boundary_handoffs),
            "next_children": self._extract_next_children_from_handoffs(boundary_handoffs),
            "confidence": str(payload.get("confidence") or "low").strip(),
            "unknown": self._summarize_tool_unknown(payload.get("unknown") or ""),
        }
        if child_task:
            result["task"] = child_task
        return f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```"

    @staticmethod
    def _rewrite_orchestrate_draw_child_tool_text(tool_text: str) -> str:
        text = str(tool_text or "")
        if not text:
            return text
        if "Error: cannot fork the current module back into itself." in text:
            return (
                "Error: cannot draw the current module into itself. "
                "Use the current-module evidence already embedded in the prompt to verify same-module facts, "
                "and use drawChild only for direct children."
            )
        return text.replace("forkSubAgent", "drawChild")

    @staticmethod
    def _sanitize_mermaid_label(text: Any, max_chars: int = 120) -> str:
        raw = " ".join(str(text or "").split())
        raw = raw.replace('"', "'").replace("<", "(").replace(">", ")")
        if len(raw) <= max_chars:
            return raw
        return raw[: max_chars - 3].rstrip() + "..."

    def render_orchestration_item_mermaid(self, item: Dict[str, Any]) -> str:
        """Render one module orchestration item into a Mermaid flowchart."""
        payload = dict(item.get("orchestration") or {})
        module_name = str(item.get("module") or payload.get("module") or "unknown").strip()
        instance_name = str(item.get("instance") or payload.get("instance") or "inst").strip()
        boundary_takeover = list(payload.get("boundary_takeover") or [])
        handoffs = list(payload.get("boundary_handoffs") or [])
        lifecycle_context = self._sanitize_mermaid_label(payload.get("lifecycle_context") or module_name)

        lines: List[str] = ["flowchart LR"]
        prev_id = None

        for idx, takeover in enumerate(boundary_takeover[:4], start=1):
            port = self._sanitize_mermaid_label(takeover.get("input_port") or f"entry_{idx}")
            semantic = self._sanitize_mermaid_label(
                takeover.get("value_condition") or takeover.get("behavior") or ""
            )
            node_id = f"E{idx}"
            label = f"ENTRY {port}"
            if semantic:
                label = f"{label} | {semantic}"
            lines.append(f'  {node_id}["{label}"]')
            if prev_id is None:
                prev_id = node_id

        if prev_id is None:
            prev_id = "S0"
            lines.append(f'  {prev_id}["{module_name}.{instance_name} | {lifecycle_context}"]')

        node_id = "N1"
        lines.append(f'  {node_id}["{lifecycle_context}"]')
        lines.append(f"  {prev_id} --> {node_id}")
        prev_id = node_id

        for idx, handoff in enumerate(handoffs[:4], start=1):
            port = self._sanitize_mermaid_label(handoff.get("output_port") or f"output_{idx}")
            semantic = self._sanitize_mermaid_label(
                handoff.get("value_condition") or handoff.get("behavior") or ""
            )
            node_id = f"H{idx}"
            label = f"EXIT {port}"
            if semantic:
                label = f"{label} | {semantic}"
            lines.append(f'  {node_id}["{label}"]')
            lines.append(f"  {prev_id} --> {node_id}")
            prev_id = node_id

        return "```mermaid\n" + "\n".join(lines) + "\n```\n"

    async def _run_orchestrate_agent_for_node(
        self,
        top_node: Any,
        node: Any,
        instruction: str,
        instruction_datasheet: str,
        stage_label: str,
        orchestrate_cache: Dict[str, Dict[str, Any]],
        continuation_handoffs_raw: Optional[List[Dict[str, Any]]] = None,
        source_child_for_handoff: Optional[Any] = None,
        source_payload_for_handoff: Optional[Dict[str, Any]] = None,
        lifecycle_context: Optional[List[Dict[str, Any]]] = None,
        bridge_context: Optional[Dict[str, Any]] = None,
        boundary_takeover: Optional[List[Dict[str, Any]]] = None,
        continuation_source: Optional[Dict[str, Any]] = None,
        override_hint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        node_path = node.get_path() if hasattr(node, "get_path") else node.instance_name

        system_prompt = PASS3_3_2_ORCHESTRATE_SYSTEM
        current_module_topology = self.g._prompts.build_instrack_current_module_topology(node.module_name)
        active_continuation = self._build_active_continuation(
            parent_node=node,
            source_child_node=source_child_for_handoff,
            handoffs=list(continuation_handoffs_raw or []),
            source_payload=dict(source_payload_for_handoff or {}),
            lifecycle_context=lifecycle_context,
            bridge_context=bridge_context,
            boundary_takeover=boundary_takeover,
            continuation_source=continuation_source,
            override_hint=override_hint,
        )
        active_bridge_context = dict(active_continuation.get("bridge_context") or {})
        if (
            source_child_for_handoff is None
            and list(active_continuation.get("boundary_takeover") or [])
            and str(active_bridge_context.get("entry_mode") or "").strip() == "parent_port_ingress"
        ):
            self.g._append_fork_trace(
                "port_ingress_bridge_context",
                {
                    "pass": "pass3_3_2_orchestrate",
                    "prompt_style": "instrack_orchestrate",
                    "parent_path": node_path,
                    "parent_level": int(getattr(node, "depth", 0) or 0),
                    "candidate_children": list(active_bridge_context.get("candidate_children") or []),
                    "resolved_handoffs": list(active_bridge_context.get("resolved_handoffs") or []),
                    "exits_parent": list(active_bridge_context.get("exits_parent") or []),
                    "unresolved": list(active_bridge_context.get("unresolved") or []),
                    "continuation_source": dict(active_continuation.get("continuation_source") or {}),
                },
            )
        prompt = PASS3_3_2_ORCHESTRATE_PROMPT.format(
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            orchestrate_state_json=self.g._build_instrack_orchestrate_state_json(
                current_module=node.module_name,
                current_instance=node.instance_name,
                boundary_takeover=list(active_continuation.get("boundary_takeover") or []),
                lifecycle_context=list(active_continuation.get("lifecycle_context") or []),
                continuation_source=dict(active_continuation.get("continuation_source") or {}),
                override_hint=dict(active_continuation.get("override_hint") or {}),
            ),
            module_preview=self.get_module_preview(node.module_name),
            current_module_topology=current_module_topology,
            child_preview_list=self.build_child_overview(node),
        )

        continuation_round_idx = 0
        pending_non_direct_advisories: Dict[str, Dict[str, Any]] = {}
        dispatch_override_info: Dict[str, Any] = {}

        def _advisory_key(continuation_signature: str, requested_path: str) -> str:
            return f"{continuation_signature}||{requested_path}"

        def _consume_pending_non_direct_advisory(
            continuation_signature: str,
            child_node: Any,
        ) -> Tuple[Optional[Dict[str, Any]], str]:
            requested_path = child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
            exact_key = _advisory_key(continuation_signature, str(requested_path or "").strip())
            exact = pending_non_direct_advisories.pop(exact_key, None)
            if exact is not None:
                return exact, "forced_non_direct_override"

            child_path = str(requested_path or "").strip()
            for key, advisory in list(pending_non_direct_advisories.items()):
                if str(advisory.get("continuation_signature") or "").strip() != continuation_signature:
                    continue
                recommended_path = str(
                    dict(advisory.get("recommended_child") or {}).get("path") or ""
                ).strip()
                if recommended_path and recommended_path == child_path:
                    pending_non_direct_advisories.pop(key, None)
                    return advisory, "guided_relay_override"
            return None, ""

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            nonlocal active_continuation
            nonlocal continuation_round_idx
            nonlocal dispatch_override_info
            if tool_name == "readSource":
                module = str(args.get("module") or "").strip()
                scoped_node, scope_error = self.g._resolve_scope_node(node, module, child_only=False)
                if scoped_node is None:
                    return (
                        "Error: instrack orchestrate readSource scope violation. "
                        "At this level you can read only current module and direct children. "
                        f"Details: {scope_error}"
                    )
                topology = self.g._build_topology_block(str(scoped_node.module_name))
                return f"[readSource] module={scoped_node.module_name}\n\n{topology}"

            if tool_name in {"drawChild", "forkSubAgent"}:
                module = str(args.get("module") or "").strip()
                child_task = str(args.get("task") or "").strip()

                async def _run_child_orchestration(child_node: Any, resolved_task: str) -> Dict[str, Any]:
                    nonlocal active_continuation
                    nonlocal continuation_round_idx
                    nonlocal dispatch_override_info

                    child_path = (
                        child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                    )
                    override_info = dict(dispatch_override_info or {})
                    dispatch_override_info = {}
                    cached_child = orchestrate_cache.get(child_path)
                    cache_hit = cached_child is not None
                    raw_handoffs = list(active_continuation.get("handoffs") or [])
                    active_source_node = active_continuation.get("source_child_node")
                    active_bridge_context = dict(active_continuation.get("bridge_context") or {})
                    child_override_hint = {}
                    if override_info:
                        child_override_hint = self._build_non_direct_child_override_hint(
                            override_info,
                            str(override_info.get("mode") or "").strip(),
                        )
                    child_boundary_takeover, child_continuation_source, takeover_warning = (
                        self._prepare_child_continuation_inputs(
                            parent_node=node,
                            source_child_node=active_source_node,
                            target_child_node=child_node,
                            handoffs=raw_handoffs,
                            bridge_context=active_bridge_context,
                            override_hint=child_override_hint,
                        )
                    )
                    if (
                        active_source_node is None
                        and str(active_bridge_context.get("entry_mode") or "").strip() == "parent_port_ingress"
                    ):
                        event_name = (
                            "port_ingress_child_dispatch"
                            if child_boundary_takeover
                            else "port_ingress_unresolved"
                        )
                        self.g._append_fork_trace(
                            event_name,
                            {
                                "pass": "pass3_3_2_orchestrate",
                                "prompt_style": "instrack_orchestrate",
                                "parent_path": node_path,
                                "parent_level": int(getattr(node, "depth", 0) or 0),
                                "child_instance": child_node.instance_name,
                                "child_module": child_node.module_name,
                                "boundary_takeover": list(child_boundary_takeover or []),
                                "continuation_source": dict(child_continuation_source or {}),
                                "warning": str(takeover_warning or "").strip(),
                            },
                        )
                    if takeover_warning:
                        print(f"[WARN] {takeover_warning}")
                    if cached_child is None:
                        cached_child = await self._run_orchestrate_agent_for_node(
                            top_node=top_node,
                            node=child_node,
                            instruction=instruction,
                            instruction_datasheet=instruction_datasheet,
                            stage_label="agent_requested_child",
                            orchestrate_cache=orchestrate_cache,
                            continuation_handoffs_raw=raw_handoffs,
                            source_child_for_handoff=None,
                            source_payload_for_handoff=dict(active_continuation.get("payload") or {}),
                            lifecycle_context=list(active_continuation.get("lifecycle_context") or []),
                            bridge_context=None,
                            boundary_takeover=child_boundary_takeover,
                            continuation_source=child_continuation_source,
                            override_hint=child_override_hint,
                        )
                        orchestrate_cache[child_path] = cached_child
                    active_continuation = self._advance_active_continuation_from_child(
                        parent_node=node,
                        child_node=child_node,
                        child_item=cached_child,
                        inherited_bridge_context=active_continuation.get("bridge_context"),
                        inherited_lifecycle_context=active_continuation.get("lifecycle_context"),
                    )
                    continuation_round_idx += 1
                    active_bridge_context = dict(active_continuation.get("bridge_context") or {})
                    continuation_state_json = self.g._build_instrack_orchestrate_state_json(
                        current_module=node.module_name,
                        current_instance=node.instance_name,
                        boundary_takeover=list(active_continuation.get("boundary_takeover") or []),
                        lifecycle_context=list(active_continuation.get("lifecycle_context") or []),
                        continuation_source=dict(active_continuation.get("continuation_source") or {}),
                        override_hint=dict(active_continuation.get("override_hint") or {}),
                    )
                    self.g._append_instrack_continuation_snapshot(
                        orchestrate_log_path,
                        round_idx=continuation_round_idx,
                        child_instance=child_node.instance_name,
                        child_module=child_node.module_name,
                        cached=cache_hit,
                        continuation_state_json=continuation_state_json,
                    )
                    self.g._append_fork_trace(
                        "orchestrate_continuation_update",
                        {
                            "pass": "pass3_3_2_orchestrate",
                            "prompt_style": "instrack_orchestrate",
                            "parent_path": node_path,
                            "parent_level": int(getattr(node, "depth", 0) or 0),
                            "child_instance": child_node.instance_name,
                            "child_module": child_node.module_name,
                            "cached": cache_hit,
                            "source_instance": str(active_bridge_context.get("source_instance") or "").strip(),
                            "source_module": str(active_bridge_context.get("source_module") or "").strip(),
                            "lifecycle_context": list(active_continuation.get("lifecycle_context") or []),
                            "candidate_children": list(active_bridge_context.get("candidate_children") or []),
                            "resolved_handoffs": list(active_bridge_context.get("resolved_handoffs") or []),
                            "exits_parent": list(active_bridge_context.get("exits_parent") or []),
                            "unresolved": list(active_bridge_context.get("unresolved") or []),
                        },
                    )
                    return {
                        "tool_result": self._build_fork_subagent_tool_result(
                            cached_child,
                            resolved_task,
                            cached=cache_hit,
                        ),
                        "report_chars": len(str(cached_child.get("raw_orchestration_output") or "")),
                        "prompt_tokens": int(
                            dict(cached_child.get("token_stats") or {}).get("input_tokens", 0) or 0
                        ),
                    }

                child_node, _ = self.g._resolve_scope_node(node, module, child_only=True)
                if child_node is not None and child_task:
                    continuation_signature = self._compute_active_continuation_signature(active_continuation)
                    matched_advisory, override_mode = _consume_pending_non_direct_advisory(
                        continuation_signature,
                        child_node,
                    )
                    if matched_advisory is not None:
                        dispatch_override_info = dict(matched_advisory)
                        dispatch_override_info["mode"] = override_mode
                        print(
                            "[DEBUG] pass3.3.2 non-direct child override: "
                            f"mode={override_mode}; "
                            f"source={self._format_child_ref_debug(matched_advisory.get('source_child') or {})}; "
                            f"requested={self._format_child_ref_debug(matched_advisory.get('requested_child') or {})}; "
                            f"recommended={self._format_child_ref_debug(matched_advisory.get('recommended_child') or {})}; "
                            f"actual={self._format_child_ref_debug(child_node)}"
                        )
                        event_name = (
                            "non_direct_child_recommended_dispatch"
                            if override_mode == "guided_relay_override"
                            else "non_direct_child_override_dispatch"
                        )
                        self.g._append_fork_trace(
                            event_name,
                            {
                                "pass": "pass3_3_2_orchestrate",
                                "prompt_style": "instrack_orchestrate",
                                "parent_path": node_path,
                                "parent_level": int(getattr(node, "depth", 0) or 0),
                                "source_child": dict(matched_advisory.get("source_child") or {}),
                                "requested_child": dict(matched_advisory.get("requested_child") or {}),
                                "recommended_child": dict(matched_advisory.get("recommended_child") or {}),
                                "actual_child": self._build_child_ref(child_node),
                                "advisory_path": list(matched_advisory.get("advisory_path") or []),
                                "reason": str(matched_advisory.get("reason") or "").strip(),
                                "continuation_signature": continuation_signature,
                            },
                        )
                    else:
                        active_source_node = active_continuation.get("source_child_node")
                        active_bridge_context = dict(active_continuation.get("bridge_context") or {})
                        port_ingress_mode = (
                            str(active_bridge_context.get("entry_mode") or "").strip() == "parent_port_ingress"
                        )
                        authoritative_candidates = list(active_bridge_context.get("candidate_children") or [])
                        normalized_candidates = self._normalize_candidate_child_refs(authoritative_candidates)
                        requested_ref = self._build_child_ref(child_node)
                        source_child_ref = (
                            self._build_child_ref(active_source_node)
                            if active_source_node is not None
                            else self._build_child_ref_from_source_payload(active_bridge_context)
                        )
                        is_candidate = any(
                            (
                                candidate.get("instance")
                                and candidate.get("instance") == requested_ref.get("instance")
                            )
                            or (
                                not candidate.get("instance")
                                and candidate.get("module")
                                and candidate.get("module") == requested_ref.get("module")
                            )
                            for candidate in normalized_candidates
                        )
                        if (active_source_node is not None or port_ingress_mode) and not is_candidate:
                            self.g._append_fork_trace(
                                "fork_request",
                                {
                                    "parent_path": node_path,
                                    "parent_instance": node.instance_name,
                                    "parent_module": node.module_name,
                                    "parent_level": node.depth,
                                    "module_selector": module,
                                    "task": child_task,
                                    "prompt_style": "instrack_orchestrate",
                                },
                            )
                            if active_source_node is not None:
                                advisory = self._takeover_resolver.resolve_non_direct_child_advisory(
                                    parent_node=node,
                                    source_child_node=active_source_node,
                                    requested_child_node=child_node,
                                )
                            else:
                                recommended_child = (
                                    self._resolve_candidate_child_ref(node, normalized_candidates[0])
                                    if normalized_candidates
                                    else {}
                                )
                                reason = (
                                    "The requested child is not supported by the current authoritative "
                                    "parent-port continuation. Try the first reachable ingress child instead."
                                )
                                if not recommended_child:
                                    reason = (
                                        "The requested child is not supported by the current authoritative "
                                        "parent-port continuation, and no reachable ingress child was identified."
                                    )
                                advisory = type(
                                    "IngressAdvisory",
                                    (),
                                    {
                                        "recommended_child": recommended_child,
                                        "advisory_path": [recommended_child] if recommended_child else [],
                                        "skipped_children": [],
                                        "reason": reason,
                                        "graph_status": str(
                                            dict(active_bridge_context.get("debug_report") or {}).get("graph_status")
                                            or "ready"
                                        ).strip(),
                                    },
                                )()
                            advisory_payload = {
                                "status": "advisory_non_direct_child",
                                "source_child": source_child_ref,
                                "requested_child": requested_ref,
                                "recommended_child": dict(advisory.recommended_child or {}),
                                "advisory_path": list(advisory.advisory_path or []),
                                "skipped_children": list(advisory.skipped_children or []),
                                "reason": str(advisory.reason or "").strip(),
                                "continuation_signature": continuation_signature,
                            }
                            advisory_path_text = " -> ".join(
                                self._format_child_ref_debug(item)
                                for item in list(advisory.advisory_path or [])[:8]
                            )
                            if self._child_refs_match(requested_ref, advisory.recommended_child or {}):
                                print(
                                    "[DEBUG] pass3.3.2 non-direct child auto-dispatch: "
                                    f"source={self._format_child_ref_debug(source_child_ref)}; "
                                    f"requested={self._format_child_ref_debug(requested_ref)}; "
                                    f"recommended={self._format_child_ref_debug(advisory.recommended_child or {})}; "
                                    f"path={advisory_path_text or 'N/A'}; "
                                    f"reason={str(advisory.reason or '').strip()}"
                                )
                                self.g._append_fork_trace(
                                    "non_direct_child_auto_dispatch",
                                    {
                                        "pass": "pass3_3_2_orchestrate",
                                        "prompt_style": "instrack_orchestrate",
                                        "parent_path": node_path,
                                        "parent_level": int(getattr(node, "depth", 0) or 0),
                                        "source_child": advisory_payload["source_child"],
                                        "requested_child": requested_ref,
                                        "authoritative_candidates": normalized_candidates,
                                        "recommended_child": dict(advisory.recommended_child or {}),
                                        "advisory_path": list(advisory.advisory_path or []),
                                        "skipped_children": list(advisory.skipped_children or []),
                                        "graph_status": str(advisory.graph_status or "").strip(),
                                        "reason": str(advisory.reason or "").strip(),
                                        "continuation_signature": continuation_signature,
                                    },
                                )
                            else:
                                pending_non_direct_advisories[
                                    _advisory_key(continuation_signature, requested_ref.get("path", ""))
                                ] = dict(advisory_payload)
                                print(
                                    "[DEBUG] pass3.3.2 non-direct child advisory: "
                                    f"source={self._format_child_ref_debug(source_child_ref)}; "
                                    f"requested={self._format_child_ref_debug(requested_ref)}; "
                                    f"recommended={self._format_child_ref_debug(advisory.recommended_child or {})}; "
                                    f"path={advisory_path_text or 'N/A'}; "
                                    f"reason={str(advisory.reason or '').strip()}"
                                )
                                self.g._append_fork_trace(
                                    "non_direct_child_advisory",
                                    {
                                        "pass": "pass3_3_2_orchestrate",
                                        "prompt_style": "instrack_orchestrate",
                                        "parent_path": node_path,
                                        "parent_level": int(getattr(node, "depth", 0) or 0),
                                        "source_child": advisory_payload["source_child"],
                                        "requested_child": requested_ref,
                                        "authoritative_candidates": normalized_candidates,
                                        "recommended_child": dict(advisory.recommended_child or {}),
                                        "advisory_path": list(advisory.advisory_path or []),
                                        "skipped_children": list(advisory.skipped_children or []),
                                        "graph_status": str(advisory.graph_status or "").strip(),
                                        "reason": str(advisory.reason or "").strip(),
                                        "continuation_signature": continuation_signature,
                                    },
                                )
                                return self._build_non_direct_child_advisory_tool_result(
                                    source_child_node=active_source_node,
                                    requested_child_node=child_node,
                                    authoritative_candidates=authoritative_candidates,
                                    advisory=advisory,
                                    source_child_ref=source_child_ref,
                                )

                tool_result = await self.g._dispatch_direct_child_fork_subagent(
                    scope_node=node,
                    module_selector=module,
                    child_task=child_task,
                    parent_path=node_path,
                    parent_instance=node.instance_name,
                    parent_module=node.module_name,
                    parent_level=node.depth,
                    prompt_style="instrack_orchestrate",
                    task_error_message=(
                        "Error: drawChild requires a non-empty 'task'. "
                        "The parent agent must define a free-form goal for the child orchestration."
                    ),
                    require_task=True,
                    runner=_run_child_orchestration,
                )
                return self._rewrite_orchestrate_draw_child_tool_text(tool_result)

            return f"Error: unknown tool '{tool_name}'"

        orchestrate_log_path = self.g._build_pass3_instrack_agent_log_path(
            instruction=instruction,
            node_path=node_path,
            level=node.depth,
            role=f"orchestrate_agent_{stage_label}",
        )

        self.g._append_agent_io_snapshot(
            orchestrate_log_path,
            stage="Input",
            system=system_prompt,
            prompt=prompt,
            prompt_redaction_policy="instrack_context",
        )

        orchestrate_content, token_stats = await self.g.owner.instrack_orchestrate_llm.generate(
            system_prompt,
            prompt,
            log_path=orchestrate_log_path,
            tools_enabled=True,
            tools=self.g._pass3_3_2_tools(),
            tool_callback=_tool_callback,
            max_tool_rounds=12,
        )

        self.g._append_agent_io_snapshot(
            orchestrate_log_path,
            stage="Output",
            system="",
            prompt="",
            output=orchestrate_content or "",
        )

        payload = self.extract_pass3_3_orchestrate_payload(orchestrate_content or "", node)
        payload = self._enrich_orchestration_payload(
            node,
            payload,
            list(active_continuation.get("boundary_takeover") or []),
        )
        return {
            "module": node.module_name,
            "instance": node.instance_name,
            "path": node_path,
            "orchestration": payload,
            "orchestrator_role": stage_label,
            "raw_orchestration_output": orchestrate_content or "",
            "token_stats": dict(token_stats or {}),
        }
