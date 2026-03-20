"""Graph-driven takeover resolution for pass3.3 instruction tracking."""

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from schematic.simplifier import CircuitGraph, DotEdge, DotParser


@dataclass
class TakeoverResolutionResult:
    """Structured graph-resolution output for one parent/source continuation."""

    resolved_handoffs: List[Dict[str, Any]] = field(default_factory=list)
    takeover_bundles: List[Dict[str, Any]] = field(default_factory=list)
    boundary_takeover_for_target: List[Dict[str, Any]] = field(default_factory=list)
    exits_parent: List[Dict[str, str]] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)
    debug_report: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NonDirectChildAdvisoryResult:
    """Heuristic relay-path guidance for unsupported direct-child requests."""

    requested_child: Dict[str, str] = field(default_factory=dict)
    recommended_child: Dict[str, str] = field(default_factory=dict)
    advisory_path: List[Dict[str, str]] = field(default_factory=list)
    skipped_children: List[Dict[str, str]] = field(default_factory=list)
    reachable_children: List[Dict[str, str]] = field(default_factory=list)
    graph_status: str = "unknown"
    reason: str = ""


@dataclass
class _GraphNodeInfo:
    node_id: str
    node_type: str
    label: str
    instance_name: str = ""
    module_name: str = ""
    port_name_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class _ParentGraphBundle:
    module_name: str
    parser: DotParser
    circuit: CircuitGraph
    node_info: Dict[str, _GraphNodeInfo] = field(default_factory=dict)
    submodules_by_instance: Dict[str, List[_GraphNodeInfo]] = field(default_factory=dict)
    submodules_by_module: Dict[str, List[_GraphNodeInfo]] = field(default_factory=dict)


class Pass3InStrackTakeoverResolver:
    """Resolve boundary handoffs by walking the parent raw DOT graph."""

    def __init__(self, generator: Any):
        self.g = generator
        self._parent_graph_cache: Dict[str, Optional[_ParentGraphBundle]] = {}
        self._module_port_dir_cache: Dict[str, Dict[str, str]] = {}

    def resolve(
        self,
        *,
        parent_node: Any,
        source_child_node: Any,
        boundary_handoffs: List[Dict[str, Any]],
        target_child_node: Optional[Any] = None,
    ) -> TakeoverResolutionResult:
        source_path = (
            source_child_node.get_path() if hasattr(source_child_node, "get_path") else source_child_node.instance_name
        )
        parent_module = str(getattr(parent_node, "module_name", "") or "").strip()
        target_instance = str(getattr(target_child_node, "instance_name", "") or "").strip()
        target_module = str(getattr(target_child_node, "module_name", "") or "").strip()

        debug_report: Dict[str, Any] = {
            "parent_module": parent_module,
            "source_instance": str(getattr(source_child_node, "instance_name", "") or "").strip(),
            "source_module": str(getattr(source_child_node, "module_name", "") or "").strip(),
            "source_instance_path": str(source_path or "").strip(),
            "target_instance": target_instance,
            "target_module": target_module,
            "graph_status": "unknown",
            "handoffs": [],
        }

        graph_bundle = self._get_parent_graph(parent_module)
        if graph_bundle is None:
            debug_report["graph_status"] = "graph_unavailable"
        else:
            debug_report["graph_status"] = "ready"

        source_port_dirs = self._get_module_port_directions(source_child_node.module_name)
        normalized_dirs = {
            self._normalize_signal_name(name): direction
            for name, direction in source_port_dirs.items()
            if self._normalize_signal_name(name)
        }

        enriched: List[Dict[str, Any]] = []
        seen_ports: Set[str] = set()
        for raw_item in boundary_handoffs or []:
            if not isinstance(raw_item, dict):
                continue

            handoff = dict(raw_item)
            output_port = str(handoff.get("output_port") or "").strip()
            norm_port = self._normalize_signal_name(output_port)
            parent_wire = self._normalize_signal_name(self._lookup_port_connection(source_child_node, output_port))
            debug_entry: Dict[str, Any] = {
                "handoff_id": f"{source_path}::{output_port}" if output_port else f"{source_path}::unknown",
                "output_port": output_port,
                "seed": None,
                "visited_count": 0,
                "target_hits": [],
                "sibling_hits": [],
                "exit_parent_hits": [],
                "stop_reasons": [],
            }
            debug_report["handoffs"].append(debug_entry)

            handoff["source_instance_path"] = source_path
            handoff["source_module"] = source_child_node.module_name
            handoff["source_instance"] = source_child_node.instance_name
            handoff["handoff_id"] = debug_entry["handoff_id"]
            handoff["source_direction"] = normalized_dirs.get(norm_port, "")
            handoff["parent_wire"] = parent_wire
            handoff["resolutions"] = []

            if not output_port:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(str(handoff.get("unknown") or ""), "missing output_port")
                enriched.append(handoff)
                continue

            if str(handoff.get("status") or "").strip() == "invalid":
                enriched.append(handoff)
                continue

            if not handoff["source_direction"]:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"output_port '{output_port}' is not a declared port of module '{source_child_node.module_name}'",
                )
                debug_entry["stop_reasons"].append("invalid_output_port")
                enriched.append(handoff)
                continue

            if handoff["source_direction"] != "output":
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"output_port '{output_port}' is not an output port",
                )
                debug_entry["stop_reasons"].append("non_output_port")
                enriched.append(handoff)
                continue

            if norm_port in seen_ports:
                handoff["status"] = "invalid"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"duplicate boundary_handoff for output_port '{output_port}'",
                )
                debug_entry["stop_reasons"].append("duplicate_output_port")
                enriched.append(handoff)
                continue
            seen_ports.add(norm_port)

            if graph_bundle is None:
                handoff["status"] = "unresolved"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    f"graph_unavailable: unable to build raw DOT graph for parent module '{parent_module}'",
                )
                debug_entry["stop_reasons"].append("graph_unavailable")
                enriched.append(handoff)
                continue

            source_graph_node = self._find_submodule_node(
                graph_bundle,
                instance_name=str(getattr(source_child_node, "instance_name", "") or "").strip(),
                module_name=str(getattr(source_child_node, "module_name", "") or "").strip(),
            )
            if source_graph_node is None:
                handoff["status"] = "unresolved"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    (
                        "seed_unresolved: source child "
                        f"'{source_child_node.instance_name}({source_child_node.module_name})' is absent from "
                        f"parent DOT graph '{parent_module}'"
                    ),
                )
                debug_entry["stop_reasons"].append("source_child_missing")
                enriched.append(handoff)
                continue

            seed_port_id, seed_edges = self._build_seed_edges(graph_bundle, source_graph_node, output_port)
            if not seed_port_id or not seed_edges:
                handoff["status"] = "unresolved"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    (
                        "seed_unresolved: unable to locate a forward edge from "
                        f"{source_child_node.instance_name}({source_child_node.module_name}).{output_port}"
                    ),
                )
                debug_entry["stop_reasons"].append("seed_unresolved")
                if seed_port_id:
                    debug_entry["seed"] = {
                        "node_id": source_graph_node.node_id,
                        "port_id": seed_port_id,
                        "port_name": graph_bundle.node_info[source_graph_node.node_id].port_name_map.get(seed_port_id, ""),
                    }
                enriched.append(handoff)
                continue

            debug_entry["seed"] = {
                "node_id": source_graph_node.node_id,
                "port_id": seed_port_id,
                "port_name": graph_bundle.node_info[source_graph_node.node_id].port_name_map.get(seed_port_id, ""),
            }
            resolutions, walk_debug = self._walk_forward(
                graph_bundle=graph_bundle,
                parent_node=parent_node,
                source_child_node=source_child_node,
                source_parent_wire=parent_wire,
                seed_edges=seed_edges,
                target_child_node=target_child_node,
            )
            handoff["resolutions"] = resolutions
            debug_entry["visited_count"] = int(walk_debug.get("visited_count", 0) or 0)
            debug_entry["target_hits"] = list(walk_debug.get("target_hits") or [])
            debug_entry["sibling_hits"] = list(walk_debug.get("sibling_hits") or [])
            debug_entry["exit_parent_hits"] = list(walk_debug.get("exit_parent_hits") or [])
            debug_entry["stop_reasons"] = list(walk_debug.get("stop_reasons") or [])

            if resolutions:
                kinds = {str(item.get("resolution_kind") or "") for item in resolutions}
                if kinds == {"exit_parent"}:
                    handoff["status"] = "exit_parent"
                else:
                    handoff["status"] = "resolved"
            else:
                handoff["status"] = "unresolved"
                handoff["unknown"] = self._append_reason(
                    str(handoff.get("unknown") or ""),
                    (
                        "graph_forward search did not reach any direct child input or parent output for "
                        f"output_port '{output_port}'"
                    ),
                )
                if "no_boundary_hit" not in debug_entry["stop_reasons"]:
                    debug_entry["stop_reasons"].append("no_boundary_hit")

            enriched.append(handoff)

        takeover_bundles = self.group_boundary_takeover_bundles(enriched)
        boundary_takeover_for_target = self.build_boundary_takeover_for_target(
            handoffs=enriched,
            target_child_node=target_child_node,
        )
        exits_parent: List[Dict[str, str]] = []
        unresolved: List[str] = []
        seen_exit_ports: Set[str] = set()
        for item in enriched:
            if not isinstance(item, dict):
                continue
            output_port = str(item.get("output_port") or "").strip()
            if str(item.get("status") or "").strip() in {"invalid", "unresolved"}:
                label = output_port or self._boundary_handoff_display_text(item) or "unknown"
                if label and label not in unresolved:
                    unresolved.append(label)
            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                if str(resolution.get("resolution_kind") or "").strip() != "exit_parent":
                    continue
                parent_port = str(resolution.get("parent_port") or "").strip()
                if not parent_port or parent_port in seen_exit_ports:
                    continue
                seen_exit_ports.add(parent_port)
                exits_parent.append(
                    {
                        "parent_port": parent_port,
                        "parent_wire": str(resolution.get("parent_wire") or item.get("parent_wire") or "").strip(),
                        "source_port": output_port,
                    }
                )

        return TakeoverResolutionResult(
            resolved_handoffs=enriched,
            takeover_bundles=takeover_bundles,
            boundary_takeover_for_target=boundary_takeover_for_target,
            exits_parent=exits_parent[:8],
            unresolved=unresolved[:6],
            debug_report=debug_report,
        )

    def resolve_non_direct_child_advisory(
        self,
        *,
        parent_node: Any,
        source_child_node: Any,
        requested_child_node: Any,
    ) -> NonDirectChildAdvisoryResult:
        result = NonDirectChildAdvisoryResult(
            requested_child=self._build_child_ref(requested_child_node),
        )
        parent_module = str(getattr(parent_node, "module_name", "") or "").strip()
        graph_bundle = self._get_parent_graph(parent_module)
        if graph_bundle is None:
            result.graph_status = "graph_unavailable"
            result.reason = (
                "The requested child is not supported by the current authoritative continuation, "
                "and heuristic relay search is unavailable because the parent raw DOT graph could not be built."
            )
            return result

        result.graph_status = "ready"
        relay_cache: Dict[str, List[Dict[str, str]]] = {}
        start_ref = self._build_child_ref(source_child_node)
        queue: Deque[Tuple[Any, List[Dict[str, str]]]] = deque([(source_child_node, [])])
        seen_child_paths: Set[str] = {start_ref.get("path", "")}
        first_hops = self._collect_relay_children(
            graph_bundle=graph_bundle,
            parent_node=parent_node,
            child_node=source_child_node,
            relay_cache=relay_cache,
        )
        result.reachable_children = first_hops[:8]

        while queue:
            current_child, path = queue.popleft()
            neighbors = self._collect_relay_children(
                graph_bundle=graph_bundle,
                parent_node=parent_node,
                child_node=current_child,
                relay_cache=relay_cache,
            )
            for child_ref in neighbors:
                target_path = str(child_ref.get("path") or "").strip()
                if not target_path:
                    continue
                next_path = path + [child_ref]
                if target_path == str(result.requested_child.get("path") or "").strip():
                    result.advisory_path = next_path[:8]
                    result.recommended_child = dict(next_path[0]) if next_path else {}
                    result.skipped_children = next_path[:-1][:8]
                    path_text = " -> ".join(
                        f"{item.get('instance')}({item.get('module')})"
                        for item in result.advisory_path
                        if item.get("instance") or item.get("module")
                    )
                    result.reason = (
                        "The requested child is not supported by the current authoritative continuation, "
                        f"but heuristic same-parent relay search suggests: {path_text}."
                    )
                    return result
                if target_path in seen_child_paths:
                    continue
                next_node = parent_node.children.get(str(child_ref.get("instance") or "").strip())
                if next_node is None:
                    continue
                seen_child_paths.add(target_path)
                queue.append((next_node, next_path))

        if result.reachable_children:
            result.recommended_child = dict(result.reachable_children[0])
            result.reason = (
                "The requested child is not supported by the current authoritative continuation, "
                "and heuristic relay search did not reach it from the current source child. "
                "Try the first reachable relay child instead."
            )
        else:
            result.reason = (
                "The requested child is not supported by the current authoritative continuation, "
                "and heuristic relay search found no reachable relay child from the current source child."
            )
        return result

    def group_boundary_takeover_bundles(self, handoffs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        bundles: Dict[str, Dict[str, Any]] = {}

        for item in handoffs or []:
            if not isinstance(item, dict):
                continue
            output_port = str(item.get("output_port") or "").strip()
            if not output_port:
                continue
            value_condition = self._boundary_handoff_display_text(item)
            behavior = self._coerce_text_field(item.get("behavior"))

            for resolution in list(item.get("resolutions") or []):
                if not isinstance(resolution, dict):
                    continue
                if str(resolution.get("resolution_kind") or "").strip() != "sibling_child":
                    continue
                target_instance = str(resolution.get("target_instance") or "").strip()
                target_module = str(resolution.get("target_module") or "").strip()
                ingress_port = str(resolution.get("target_port") or "").strip()
                if not ingress_port or not (target_instance or target_module):
                    continue
                bundle_key = f"{target_instance}|{target_module}"
                bundle = bundles.get(bundle_key)
                if bundle is None:
                    bundle = {
                        "target_instance": target_instance,
                        "target_module": target_module,
                        "boundary_takeover": [],
                    }
                    bundles[bundle_key] = bundle
                takeover_item = {
                    "input_port": ingress_port,
                    "value_condition": value_condition,
                    "behavior": behavior,
                }
                dedupe_key = "|".join(
                    [
                        self._normalize_signal_name(ingress_port),
                        str(takeover_item.get("value_condition") or "").strip(),
                        str(takeover_item.get("behavior") or "").strip(),
                    ]
                )
                seen_takeovers = bundle.setdefault("_seen_takeovers", set())
                if dedupe_key in seen_takeovers:
                    continue
                seen_takeovers.add(dedupe_key)
                bundle["boundary_takeover"].append(takeover_item)

        grouped: List[Dict[str, Any]] = []
        for bundle in bundles.values():
            bundle.pop("_seen_takeovers", None)
            bundle["boundary_takeover"] = self._normalize_boundary_takeover(bundle.get("boundary_takeover") or [])
            if bundle["boundary_takeover"]:
                grouped.append(bundle)
        return grouped

    def build_boundary_takeover_for_target(
        self,
        *,
        handoffs: List[Dict[str, Any]],
        target_child_node: Optional[Any],
    ) -> List[Dict[str, Any]]:
        if target_child_node is None:
            return []
        for bundle in self.group_boundary_takeover_bundles(handoffs):
            target_instance = str(bundle.get("target_instance") or "").strip()
            target_module = str(bundle.get("target_module") or "").strip()
            if target_instance and target_instance == str(target_child_node.instance_name or "").strip():
                return list(bundle.get("boundary_takeover") or [])[:8]
            if not target_instance and target_module and target_module == str(target_child_node.module_name or "").strip():
                return list(bundle.get("boundary_takeover") or [])[:8]
            if target_module and target_module == str(target_child_node.module_name or "").strip():
                return list(bundle.get("boundary_takeover") or [])[:8]
        return []

    def _get_parent_graph(self, module_name: str) -> Optional[_ParentGraphBundle]:
        if module_name in self._parent_graph_cache:
            return self._parent_graph_cache[module_name]

        backend = self._get_backend()
        bundle: Optional[_ParentGraphBundle] = None
        if backend is not None and hasattr(backend, "generate_dot"):
            try:
                dot_content = backend.generate_dot(module_name)
            except Exception:
                dot_content = None
            if dot_content:
                try:
                    parser = DotParser()
                    parser.parse(dot_content)
                    circuit = CircuitGraph(parser)
                    bundle = _ParentGraphBundle(
                        module_name=module_name,
                        parser=parser,
                        circuit=circuit,
                    )
                    for node_id, node in parser.nodes.items():
                        info = _GraphNodeInfo(
                            node_id=node_id,
                            node_type=node.node_type,
                            label=node.label,
                            port_name_map=self._extract_port_name_map(node.label),
                        )
                        if node.node_type == "submodule":
                            info.instance_name, info.module_name = self._extract_submodule_identity(node.label, node_id)
                            if info.instance_name:
                                key = self._normalize_signal_name(info.instance_name)
                                bundle.submodules_by_instance.setdefault(key, []).append(info)
                            if info.module_name:
                                key = self._normalize_signal_name(info.module_name)
                                bundle.submodules_by_module.setdefault(key, []).append(info)
                        bundle.node_info[node_id] = info
                except Exception:
                    bundle = None

        self._parent_graph_cache[module_name] = bundle
        return bundle

    def _build_child_ref(self, node: Any) -> Dict[str, str]:
        path = node.get_path() if hasattr(node, "get_path") else node.instance_name
        return {
            "instance": str(getattr(node, "instance_name", "") or "").strip(),
            "module": str(getattr(node, "module_name", "") or "").strip(),
            "path": str(path or "").strip(),
        }

    def _find_submodule_node(
        self,
        bundle: _ParentGraphBundle,
        *,
        instance_name: str,
        module_name: str,
    ) -> Optional[_GraphNodeInfo]:
        normalized_instance = self._normalize_signal_name(instance_name)
        normalized_module = self._normalize_signal_name(module_name)
        if normalized_instance:
            for candidate in bundle.submodules_by_instance.get(normalized_instance, []):
                if not normalized_module or self._normalize_signal_name(candidate.module_name) == normalized_module:
                    return candidate
        if normalized_module:
            candidates = [
                candidate
                for candidate in bundle.submodules_by_module.get(normalized_module, [])
                if not normalized_instance or self._normalize_signal_name(candidate.instance_name) == normalized_instance
            ]
            if len(candidates) == 1:
                return candidates[0]
        return None

    def _build_seed_edges(
        self,
        bundle: _ParentGraphBundle,
        source_node: _GraphNodeInfo,
        output_port: str,
    ) -> Tuple[str, List[DotEdge]]:
        port_id = ""
        normalized_output = self._normalize_signal_name(output_port)
        for candidate_port_id, candidate_name in source_node.port_name_map.items():
            if self._normalize_signal_name(candidate_name) == normalized_output:
                port_id = candidate_port_id
                break
        if not port_id:
            return "", []

        edges: List[DotEdge] = []
        for edge in bundle.circuit.adj_out.get(source_node.node_id, []):
            if self._normalize_dot_port_id(edge.src_port) == port_id:
                edges.append(edge)
        return port_id, edges

    def _build_all_output_seed_edges(
        self,
        bundle: _ParentGraphBundle,
        child_node: Any,
        source_node: _GraphNodeInfo,
    ) -> List[Tuple[str, List[DotEdge]]]:
        output_seeds: List[Tuple[str, List[DotEdge]]] = []
        for port_id, port_name in source_node.port_name_map.items():
            if self._find_port_direction(str(getattr(child_node, "module_name", "") or "").strip(), port_name) != "output":
                continue
            seed_edges: List[DotEdge] = []
            for edge in bundle.circuit.adj_out.get(source_node.node_id, []):
                if self._normalize_dot_port_id(edge.src_port) == port_id:
                    seed_edges.append(edge)
            if seed_edges:
                output_seeds.append((port_name, seed_edges))
        return output_seeds

    def _collect_relay_children(
        self,
        *,
        graph_bundle: _ParentGraphBundle,
        parent_node: Any,
        child_node: Any,
        relay_cache: Dict[str, List[Dict[str, str]]],
    ) -> List[Dict[str, str]]:
        child_path = child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
        cache_key = str(child_path or "").strip()
        if cache_key in relay_cache:
            return list(relay_cache[cache_key])

        source_graph_node = self._find_submodule_node(
            graph_bundle,
            instance_name=str(getattr(child_node, "instance_name", "") or "").strip(),
            module_name=str(getattr(child_node, "module_name", "") or "").strip(),
        )
        if source_graph_node is None:
            relay_cache[cache_key] = []
            return []

        neighbors: List[Dict[str, str]] = []
        seen_paths: Set[str] = set()
        for port_name, seed_edges in self._build_all_output_seed_edges(graph_bundle, child_node, source_graph_node):
            resolutions, _ = self._walk_forward(
                graph_bundle=graph_bundle,
                parent_node=parent_node,
                source_child_node=child_node,
                source_parent_wire=self._normalize_signal_name(
                    self._lookup_port_connection(child_node, port_name)
                ),
                seed_edges=seed_edges,
                target_child_node=None,
            )
            for resolution in resolutions:
                if str(resolution.get("resolution_kind") or "").strip() != "sibling_child":
                    continue
                target_instance = str(resolution.get("target_instance") or "").strip()
                target_module = str(resolution.get("target_module") or "").strip()
                next_node = None
                if target_instance and target_instance in getattr(parent_node, "children", {}):
                    next_node = parent_node.children[target_instance]
                elif target_module:
                    for candidate in getattr(parent_node, "children", {}).values():
                        if str(getattr(candidate, "module_name", "") or "").strip() == target_module:
                            next_node = candidate
                            break
                if next_node is None:
                    continue
                next_ref = self._build_child_ref(next_node)
                next_path = str(next_ref.get("path") or "").strip()
                if not next_path or next_path == cache_key or next_path in seen_paths:
                    continue
                seen_paths.add(next_path)
                neighbors.append(next_ref)
        relay_cache[cache_key] = list(neighbors)
        return neighbors

    def _walk_forward(
        self,
        *,
        graph_bundle: _ParentGraphBundle,
        parent_node: Any,
        source_child_node: Any,
        source_parent_wire: str,
        seed_edges: List[DotEdge],
        target_child_node: Optional[Any],
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        queue: Deque[DotEdge] = deque(seed_edges)
        seen_arrivals: Set[Tuple[str, str]] = set()
        visited_nodes: Set[str] = set()
        seen_resolutions: Set[str] = set()
        resolutions: List[Dict[str, str]] = []
        debug: Dict[str, Any] = {
            "visited_count": 0,
            "target_hits": [],
            "sibling_hits": [],
            "exit_parent_hits": [],
            "stop_reasons": [],
        }
        parent_port_dirs = self._get_module_port_directions(parent_node.module_name)
        target_instance = str(getattr(target_child_node, "instance_name", "") or "").strip()
        target_module = str(getattr(target_child_node, "module_name", "") or "").strip()
        source_instance = str(getattr(source_child_node, "instance_name", "") or "").strip()
        visit_budget = 20000

        while queue:
            edge = queue.popleft()
            dst_port_id = self._normalize_dot_port_id(edge.dst_port)
            arrival_key = (edge.dst_node, dst_port_id)
            if arrival_key in seen_arrivals:
                continue
            seen_arrivals.add(arrival_key)
            visited_nodes.add(edge.dst_node)
            if len(seen_arrivals) > visit_budget:
                debug["stop_reasons"].append("visit_budget_reached")
                break

            node = graph_bundle.parser.nodes.get(edge.dst_node)
            if node is None:
                continue
            node_info = graph_bundle.node_info.get(edge.dst_node)
            node_type = node.node_type

            if node_type == "io_port":
                parent_port = str(node.label or "").strip().lstrip("\\")
                parent_dir = parent_port_dirs.get(parent_port, "")
                if parent_dir != "output":
                    debug["stop_reasons"].append("non_output_parent_port")
                    continue
                resolution = {
                    "resolution_kind": "exit_parent",
                    "parent_wire": source_parent_wire,
                    "parent_port": parent_port,
                    "match_policy": "graph_forward",
                }
                dedupe_key = f"exit_parent|{parent_port}|{source_parent_wire}"
                if dedupe_key not in seen_resolutions:
                    seen_resolutions.add(dedupe_key)
                    resolutions.append(resolution)
                    debug["exit_parent_hits"].append(
                        {
                            "parent_port": parent_port,
                            "node_id": edge.dst_node,
                        }
                    )
                debug["stop_reasons"].append("parent_output_reached")
                continue

            if node_type == "submodule" and node_info is not None:
                port_name = node_info.port_name_map.get(dst_port_id, "")
                if not port_name:
                    debug["stop_reasons"].append("submodule_port_unresolved")
                    continue
                port_dir = self._find_port_direction(node_info.module_name, port_name)
                is_target = False
                if target_instance and node_info.instance_name == target_instance:
                    is_target = True
                elif not target_instance and target_module and node_info.module_name == target_module:
                    is_target = True

                if port_dir == "input":
                    resolution = {
                        "resolution_kind": "sibling_child",
                        "parent_wire": source_parent_wire,
                        "target_instance": node_info.instance_name,
                        "target_module": node_info.module_name,
                        "target_port": port_name,
                        "match_policy": "graph_forward",
                    }
                    dedupe_key = "|".join(
                        [
                            "sibling_child",
                            node_info.instance_name,
                            node_info.module_name,
                            self._normalize_signal_name(port_name),
                            source_parent_wire,
                        ]
                    )
                    if dedupe_key not in seen_resolutions:
                        seen_resolutions.add(dedupe_key)
                        resolutions.append(resolution)
                    hit = {
                        "target_instance": node_info.instance_name,
                        "target_module": node_info.module_name,
                        "target_port": port_name,
                        "node_id": edge.dst_node,
                    }
                    if is_target:
                        debug["target_hits"].append(hit)
                        debug["stop_reasons"].append("target_child_reached")
                    elif node_info.instance_name != source_instance:
                        debug["sibling_hits"].append(hit)
                        debug["stop_reasons"].append("sibling_child_reached")
                else:
                    debug["stop_reasons"].append("non_input_submodule_port")
                continue

            for next_edge in graph_bundle.circuit.adj_out.get(edge.dst_node, []):
                next_arrival = (next_edge.dst_node, self._normalize_dot_port_id(next_edge.dst_port))
                if next_arrival in seen_arrivals:
                    continue
                queue.append(next_edge)

        debug["visited_count"] = len(visited_nodes)
        if not resolutions and "no_boundary_hit" not in debug["stop_reasons"]:
            debug["stop_reasons"].append("no_boundary_hit")
        return resolutions, debug

    def _get_backend(self) -> Any:
        resolver = getattr(getattr(self.g, "owner", None), "resolver", None)
        return getattr(resolver, "backend", None)

    def _get_module_port_directions(self, module_name: str) -> Dict[str, str]:
        if module_name in self._module_port_dir_cache:
            return dict(self._module_port_dir_cache[module_name])
        backend = self._get_backend()
        if backend is None:
            self._module_port_dir_cache[module_name] = {}
            return {}
        try:
            module = backend.get_module(module_name)
        except Exception:
            module = None
        if module is None:
            self._module_port_dir_cache[module_name] = {}
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
            directions = {}
        self._module_port_dir_cache[module_name] = dict(directions)
        return directions

    def _find_port_direction(self, module_name: str, port_name: str) -> str:
        norm_port = self._normalize_signal_name(port_name)
        if not norm_port:
            return ""
        for name, direction in self._get_module_port_directions(module_name).items():
            if self._normalize_signal_name(name) == norm_port:
                return direction
        return ""

    @staticmethod
    def _lookup_port_connection(node: Any, port_name: str) -> str:
        raw = dict(getattr(node, "port_connections", {}) or {})
        if port_name in raw:
            return str(raw.get(port_name) or "").strip()
        norm_port = Pass3InStrackTakeoverResolver._normalize_signal_name(port_name)
        for candidate, wire in raw.items():
            if Pass3InStrackTakeoverResolver._normalize_signal_name(candidate) == norm_port:
                return str(wire or "").strip()
        return ""

    @staticmethod
    def _extract_port_name_map(label: str) -> Dict[str, str]:
        port_map: Dict[str, str] = {}
        if not label:
            return port_map
        for match in re.finditer(r"<([^>]+)>\s*([^|{}]+)", label):
            port_id = match.group(1).strip()
            port_name = match.group(2).strip()
            if port_id:
                port_map[port_id] = port_name
        return port_map

    @staticmethod
    def _extract_submodule_identity(label: str, fallback_node_id: str) -> Tuple[str, str]:
        if not label:
            return fallback_node_id, ""
        match = re.search(r"\|([^|{}<>]+?)\\n([^|{}<>]+?)\|", label)
        if not match:
            return fallback_node_id, ""
        return match.group(1).strip(), match.group(2).strip()

    @staticmethod
    def _normalize_dot_port_id(port_ref: str) -> str:
        return str(port_ref or "").strip().split(":")[0].strip()

    @staticmethod
    def _normalize_signal_name(name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        text = text.lstrip("\\")
        text = re.sub(r"\[[^\]]+\]$", "", text)
        return text.strip()

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

    @staticmethod
    def _coerce_text_field(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

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
