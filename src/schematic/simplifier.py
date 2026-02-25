"""DOT schematic simplification for Yosys-generated schematics.

Merges combinational logic blocks into abstract COMB nodes while
preserving PROC blocks (pipeline stage boundaries) and I/O ports.

Ported from scripts/simplify_dot.py.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional, Any

from models import SourceLocation


@dataclass
class CombLocationInfo:
    """Aggregated source location info for a merged COMB node."""
    files: Dict[str, int] = field(default_factory=dict)  # filename -> count
    line_ranges: Dict[str, Tuple[int, int]] = field(default_factory=dict)  # filename -> (min, max)

    def add_location(self, file_path: str, line: int):
        """Add a source location."""
        filename = file_path.split('/')[-1] if '/' in file_path else file_path
        self.files[filename] = self.files.get(filename, 0) + 1

        if filename in self.line_ranges:
            min_line, max_line = self.line_ranges[filename]
            self.line_ranges[filename] = (min(min_line, line), max(max_line, line))
        else:
            self.line_ranges[filename] = (line, line)

    def get_display_label(self) -> str:
        """Generate display label showing top 2 files with line ranges."""
        if not self.files:
            return ""

        parts = []
        for filename, count in sorted(self.files.items(), key=lambda x: -x[1])[:2]:
            min_line, max_line = self.line_ranges.get(filename, (0, 0))
            if min_line == max_line:
                parts.append(f"{filename}:{min_line}")
            else:
                parts.append(f"{filename}:{min_line}-{max_line}")

        return "\\n".join(parts)


@dataclass
class ProcNodeInfo:
    """Information about a PROC node in simplified graph."""
    node_id: str
    label: str
    source_location: Optional[SourceLocation] = None  # Parsed from PROC label


@dataclass
class CombNodeInfo:
    """Information about a COMB node in simplified graph."""
    node_id: str
    comb_type: str  # "IN_COMB" / "OUT_COMB" / "COMB"
    node_count: int
    location_info: CombLocationInfo
    source_locations: List[SourceLocation] = field(default_factory=list)  # Full paths for reading source
    associated_proc: Optional[str] = None


@dataclass
class SimplifiedGraph:
    """Structured representation of a simplified schematic."""
    module_name: str
    proc_nodes: Dict[str, ProcNodeInfo] = field(default_factory=dict)
    comb_nodes: Dict[str, CombNodeInfo] = field(default_factory=dict)
    io_ports: Dict[str, str] = field(default_factory=dict)  # node_id -> port_label
    submodules: Dict[str, str] = field(default_factory=dict)  # node_id -> instance_name
    seq_cells: Dict[str, str] = field(default_factory=dict)  # node_id -> cell_type
    edges: List[Tuple[str, str]] = field(default_factory=list)  # (src_id, dst_id)


@dataclass
class DotNode:
    """A node in the DOT graph."""
    node_id: str
    shape: str = ""
    label: str = ""
    style: str = ""
    color: str = ""
    fontcolor: str = ""
    fillcolor: str = ""
    raw_attrs: str = ""
    cell_id: Optional[str] = None

    def extract_cell_id(self) -> Optional[str]:
        """Extract cell ID from label (format: $<digits>\\n<logic_type>)."""
        if self.shape == "record" and self.label:
            match = re.search(r'\$(\d+)\\n', self.label)
            if match:
                return match.group(1)
        return None

    def extract_cell_type(self) -> str:
        """Extract cell type token from record label (e.g. $dff/$mux)."""
        if self.shape != "record" or not self.label:
            return ""
        match = re.search(r'\\n(\$[A-Za-z0-9_]+)\|', self.label)
        if match:
            return match.group(1)
        return ""

    def is_submodule(self) -> bool:
        """Check if this record node is a submodule instance (not a built-in cell).

        Submodule labels: {inputs}|instance_name\\nmodule_type|{outputs}
        Comb logic labels: {inputs}|$cell_id\\n$cell_type|{outputs}
        Key difference: submodule instance names don't start with '$'
        """
        if self.shape != "record" or not self.label:
            return False
        # Extract the instance/cell name before \n
        match = re.search(r'\|([^|{}<>]+?)\\n', self.label)
        if match:
            name = match.group(1).strip()
            return bool(name) and not name.startswith('$')
        return False

    @property
    def node_type(self) -> str:
        """Classify node type based on shape/style attributes."""
        if self.shape == "box" and "PROC" in self.label:
            return "proc"
        if self.shape == "record":
            cell_type = self.extract_cell_type().lower()
            if cell_type in {
                "$dff", "$adff", "$sdff", "$sdffe", "$dffe", "$dffsre", "$dlatch"
            }:
                return "seq_cell"
            if self.is_submodule():
                return "submodule"
            return "comb_logic"
        if self.shape == "octagon":
            return "io_port"
        if self.shape == "diamond":
            return "internal_signal"
        if self.shape == "point":
            return "junction"
        if self.node_id.startswith("v") and not self.shape:
            return "constant"
        if not self.shape and self.style == "rounded":
            return "slice"
        return "unknown"


@dataclass
class DotEdge:
    """A directed edge in the DOT graph."""
    src_node: str
    src_port: str
    dst_node: str
    dst_port: str
    attrs: Dict[str, str] = field(default_factory=dict)

    @property
    def src(self) -> str:
        return self.src_node

    @property
    def dst(self) -> str:
        return self.dst_node


class DotParser:
    """Regex-based parser for Yosys DOT output."""

    NODE_PATTERN = re.compile(
        r'^(\w+)\s*\[\s*(.+?)\s*\]\s*;?\s*$'
    )
    EDGE_PATTERN = re.compile(
        r'^([\w:]+)\s*->\s*([\w:]+)\s*(?:\[\s*(.+?)\s*\])?\s*;?\s*$'
    )
    ATTR_PATTERN = re.compile(
        r'(\w+)\s*=\s*(?:"([^"]*)"|([\w\']+))'
    )

    def __init__(self):
        self.graph_name = ""
        self.graph_attrs = {}
        self.nodes: Dict[str, DotNode] = {}
        self.edges: List[DotEdge] = []

    def parse(self, content: str) -> None:
        """Parse DOT file content."""
        lines = content.strip().split('\n')

        for line in lines:
            line = line.strip()
            if not line or line.startswith('//'):
                continue

            if line.startswith('digraph'):
                match = re.match(r'digraph\s+"?(\w+)"?\s*\{', line)
                if match:
                    self.graph_name = match.group(1)
                continue

            if line == '}':
                continue

            if '=' in line and '->' not in line and '[' not in line:
                match = re.match(r'(\w+)\s*=\s*(.+?)\s*;?\s*$', line)
                if match:
                    key = match.group(1)
                    value = match.group(2).strip('"')
                    self.graph_attrs[key] = value
                continue

            if '[' in line and '->' not in line:
                self._parse_node(line)
                continue

            if '->' in line:
                self._parse_edge(line)

    def _parse_node(self, line: str) -> None:
        """Parse a node definition line."""
        match = self.NODE_PATTERN.match(line)
        if not match:
            return

        node_id = match.group(1)
        attrs_str = match.group(2)

        attrs = {}
        for attr_match in self.ATTR_PATTERN.finditer(attrs_str):
            key = attr_match.group(1)
            value = attr_match.group(2) or attr_match.group(3)
            attrs[key] = value

        node = DotNode(
            node_id=node_id,
            shape=attrs.get('shape', ''),
            label=attrs.get('label', ''),
            style=attrs.get('style', ''),
            color=attrs.get('color', ''),
            fontcolor=attrs.get('fontcolor', ''),
            fillcolor=attrs.get('fillcolor', ''),
            raw_attrs=attrs_str
        )
        node.cell_id = node.extract_cell_id()
        self.nodes[node_id] = node

    def _parse_edge(self, line: str) -> None:
        """Parse an edge definition line."""
        match = self.EDGE_PATTERN.match(line)
        if not match:
            return

        src_full = match.group(1)
        dst_full = match.group(2)
        attrs_str = match.group(3) or ""

        src_parts = src_full.split(':')
        dst_parts = dst_full.split(':')

        src_node = src_parts[0]
        src_port = ':'.join(src_parts[1:]) if len(src_parts) > 1 else ""
        dst_node = dst_parts[0]
        dst_port = ':'.join(dst_parts[1:]) if len(dst_parts) > 1 else ""

        attrs = {}
        for attr_match in self.ATTR_PATTERN.finditer(attrs_str):
            key = attr_match.group(1)
            value = attr_match.group(2) or attr_match.group(3)
            attrs[key] = value

        self.edges.append(DotEdge(
            src_node=src_node,
            src_port=src_port,
            dst_node=dst_node,
            dst_port=dst_port,
            attrs=attrs
        ))


class CircuitGraph:
    """Circuit graph with adjacency list representation."""

    def __init__(self, parser: DotParser):
        self.parser = parser
        self.nodes = parser.nodes
        self.edges = parser.edges

        self.adj_out: Dict[str, List[DotEdge]] = defaultdict(list)
        self.adj_in: Dict[str, List[DotEdge]] = defaultdict(list)

        self._build_adjacency()

    def _build_adjacency(self) -> None:
        """Build adjacency lists from edges."""
        for edge in self.edges:
            self.adj_out[edge.src_node].append(edge)
            self.adj_in[edge.dst_node].append(edge)

    def get_boundary_nodes(self) -> Set[str]:
        """Get boundary nodes (PROC blocks, I/O ports, and submodule instances)."""
        boundary = set()
        for node_id, node in self.nodes.items():
            if node.node_type in ('proc', 'io_port', 'submodule', 'seq_cell'):
                boundary.add(node_id)
        return boundary

    def get_nodes_to_merge(self) -> Set[str]:
        """Get nodes eligible for merging (comb logic, junctions, constants, etc.)."""
        to_merge = set()
        for node_id, node in self.nodes.items():
            if node.node_type in ('comb_logic', 'junction', 'constant', 'internal_signal', 'slice'):
                to_merge.add(node_id)
        return to_merge

    def find_connected_components(self, nodes_to_merge: Set[str]) -> List[Set[str]]:
        """Find connected components among mergeable nodes using DFS."""
        visited = set()
        components = []

        def dfs(node_id: str, component: Set[str]):
            if node_id in visited or node_id not in nodes_to_merge:
                return
            visited.add(node_id)
            component.add(node_id)

            for edge in self.adj_out.get(node_id, []):
                if edge.dst_node in nodes_to_merge:
                    dfs(edge.dst_node, component)

            for edge in self.adj_in.get(node_id, []):
                if edge.src_node in nodes_to_merge:
                    dfs(edge.src_node, component)

        for node_id in nodes_to_merge:
            if node_id not in visited:
                component = set()
                dfs(node_id, component)
                if component:
                    components.append(component)

        return components

    def group_by_proc_block(self, nodes_to_merge: Set[str]) -> Dict[str, Dict[str, Set[str]]]:
        """Group combinational logic by associated PROC blocks.

        Strategy:
        - Nodes feeding into a PROC -> PROC's "input" group
        - Nodes driven by a PROC -> PROC's "output" group
        - Unassigned nodes propagated via BFS, then via I/O port connections

        Returns:
            Dict[proc_id, {"input": Set[node_ids], "output": Set[node_ids]}]
        """
        boundary_nodes = self.get_boundary_nodes()
        proc_nodes = {nid for nid in boundary_nodes
                      if self.nodes[nid].node_type == 'proc'}
        io_nodes = {nid for nid in boundary_nodes
                    if self.nodes[nid].node_type == 'io_port'}

        proc_groups: Dict[str, Dict[str, Set[str]]] = {}
        for proc_id in proc_nodes:
            proc_groups[proc_id] = {"input": set(), "output": set()}

        if not proc_nodes:
            proc_groups["_no_proc"] = {"input": set(), "output": set()}

        node_assignments: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)

        # Pass 1: direct connections to PROC
        for node_id in nodes_to_merge:
            for edge in self.adj_out.get(node_id, []):
                if edge.dst_node in proc_nodes:
                    node_assignments[node_id].add((edge.dst_node, "input"))

            for edge in self.adj_in.get(node_id, []):
                if edge.src_node in proc_nodes:
                    node_assignments[node_id].add((edge.src_node, "output"))

        # Pass 2: BFS propagation
        changed = True
        max_iterations = len(nodes_to_merge) + 1
        iteration = 0

        while changed and iteration < max_iterations:
            changed = False
            iteration += 1

            for node_id in nodes_to_merge:
                if node_id in node_assignments:
                    current_assignments = node_assignments[node_id].copy()

                    for edge in self.adj_out.get(node_id, []):
                        if edge.dst_node in nodes_to_merge:
                            for proc_id, direction in current_assignments:
                                if (proc_id, direction) not in node_assignments[edge.dst_node]:
                                    node_assignments[edge.dst_node].add((proc_id, direction))
                                    changed = True

                    for edge in self.adj_in.get(node_id, []):
                        if edge.src_node in nodes_to_merge:
                            for proc_id, direction in current_assignments:
                                if (proc_id, direction) not in node_assignments[edge.src_node]:
                                    node_assignments[edge.src_node].add((proc_id, direction))
                                    changed = True

        # Assign nodes to groups
        unassigned_nodes = set()
        for node_id in nodes_to_merge:
            assignments = node_assignments.get(node_id, set())
            if assignments:
                for proc_id, direction in assignments:
                    proc_groups[proc_id][direction].add(node_id)
            else:
                unassigned_nodes.add(node_id)

        # Handle unassigned nodes via I/O port connections
        if unassigned_nodes and proc_nodes:
            for node_id in unassigned_nodes:
                connected_ios = set()

                for edge in self.adj_out.get(node_id, []):
                    if edge.dst_node in io_nodes:
                        connected_ios.add(edge.dst_node)
                for edge in self.adj_in.get(node_id, []):
                    if edge.src_node in io_nodes:
                        connected_ios.add(edge.src_node)

                found_proc = None
                for io_id in connected_ios:
                    for edge in self.adj_out.get(io_id, []):
                        if edge.dst_node in proc_nodes:
                            found_proc = edge.dst_node
                            proc_groups[found_proc]["input"].add(node_id)
                            break
                    if found_proc:
                        break
                    for edge in self.adj_in.get(io_id, []):
                        if edge.src_node in proc_nodes:
                            found_proc = edge.src_node
                            proc_groups[found_proc]["output"].add(node_id)
                            break
                    if found_proc:
                        break

                if not found_proc and proc_nodes:
                    first_proc = sorted(proc_nodes)[0]
                    proc_groups[first_proc]["input"].add(node_id)
        elif unassigned_nodes and not proc_nodes:
            proc_groups["_no_proc"]["input"].update(unassigned_nodes)

        return proc_groups


class DotSimplifier:
    """Simplifies DOT schematics by merging combinational logic."""

    def __init__(self, parser: DotParser, strategy: str = "proc_group",
                 cell_locations: Optional[Dict[str, SourceLocation]] = None,
                 remove_signals: Optional[List[str]] = None,
                 verbose: bool = False,
                 graph_profile: str = "default"):
        self.parser = parser
        self.strategy = strategy
        self.cell_locations = cell_locations or {}
        self.remove_signals = remove_signals or []
        self.verbose = verbose
        self.graph_profile = graph_profile
        self._last_provenance: Dict[str, Any] = {}

        # Track which cell_ids from cell_locations are covered by the simplified output
        self._covered_cell_ids: Set[str] = set()
        # Track cell_ids removed by signal removal
        self._signal_removed_cell_ids: Set[str] = set()

        # Apply signal removal before building the graph
        if self.remove_signals:
            self._remove_signals()

        self.graph = CircuitGraph(parser)

    def _remove_signals(self) -> None:
        """Remove I/O port nodes matching remove_signals patterns and their edges.

        Walks from each matched I/O port along edges through intermediate
        combinational logic until reaching PROC blocks or other I/O ports.
        Removes the matched port nodes, the intermediate-only comb nodes,
        and all associated edges. This cleans up rst/scan paths before
        COMB merging so they don't clutter the simplified schematic.

        Uses cell_locations to distinguish auto-generated comb logic (no source
        annotation) from user-written logic. Auto-generated nodes on the signal
        path are removed even if they still have live outputs; user-written nodes
        are preserved.
        """
        import fnmatch
        patterns = [p.lower() for p in self.remove_signals]

        # Find I/O port nodes whose label matches any pattern
        matched_ports = set()
        for node_id, node in self.parser.nodes.items():
            if node.node_type != 'io_port':
                continue
            label = node.label.strip().lstrip('\\').strip()
            label_lower = label.lower()
            for pat in patterns:
                if fnmatch.fnmatch(label_lower, pat.lower()):
                    matched_ports.add(node_id)
                    break

        if not matched_ports:
            return

        # Build temporary adjacency for traversal
        adj_out: Dict[str, List[DotEdge]] = defaultdict(list)
        adj_in: Dict[str, List[DotEdge]] = defaultdict(list)
        for edge in self.parser.edges:
            adj_out[edge.src_node].append(edge)
            adj_in[edge.dst_node].append(edge)

        # Helper: check if a comb node has source code annotation
        def _has_source_annotation(node_id: str) -> bool:
            """Return True if the node has a real RTL source location."""
            node = self.parser.nodes.get(node_id)
            if not node or not node.cell_id:
                return False
            return node.cell_id in self.cell_locations

        # Phase 1: Seed removal set with matched I/O ports
        nodes_to_remove = set(matched_ports)
        edges_to_remove = set()

        for port_id in matched_ports:
            for edge in adj_out.get(port_id, []):
                edges_to_remove.add(id(edge))
            for edge in adj_in.get(port_id, []):
                edges_to_remove.add(id(edge))

        # Phase 2: Propagate removal through intermediate nodes.
        # A non-boundary node is removed if:
        #   (a) ALL its neighbors are already removed (fully orphaned), OR
        #   (b) ALL its input sources are removed (no data flows in) AND
        #       the node is auto-generated (no source annotation)
        # Condition (b) is the key enhancement: auto-generated comb logic
        # that only receives data from removed signal paths gets removed,
        # even if it still feeds into live PROC blocks.
        # User-written comb nodes (with source annotations) are preserved
        # under condition (b) to avoid losing real RTL logic.
        changed = True
        while changed:
            changed = False
            for node_id, node in list(self.parser.nodes.items()):
                if node_id in nodes_to_remove:
                    continue
                if node.node_type in ('proc', 'io_port', 'submodule'):
                    continue

                # Condition (a): all neighbors removed (fully orphaned)
                all_neighbors = set()
                for edge in adj_out.get(node_id, []):
                    all_neighbors.add(edge.dst_node)
                for edge in adj_in.get(node_id, []):
                    all_neighbors.add(edge.src_node)
                fully_orphaned = all_neighbors and all_neighbors.issubset(nodes_to_remove)

                # Condition (b): all input sources removed AND no source annotation
                #   Additional safety: do NOT remove if the node has live outputs
                #   to non-removed boundary nodes (fan-out safety)
                in_sources = set()
                for edge in adj_in.get(node_id, []):
                    in_sources.add(edge.src_node)
                no_input = in_sources and in_sources.issubset(nodes_to_remove)
                auto_generated = not _has_source_annotation(node_id)

                has_live_boundary_output = False
                if no_input and auto_generated:
                    for edge in adj_out.get(node_id, []):
                        dst = edge.dst_node
                        if dst not in nodes_to_remove:
                            dst_node = self.parser.nodes.get(dst)
                            if dst_node and dst_node.node_type in ('proc', 'io_port', 'submodule', 'seq_cell'):
                                has_live_boundary_output = True
                                break

                if fully_orphaned or (no_input and auto_generated and not has_live_boundary_output):
                    nodes_to_remove.add(node_id)
                    for edge in adj_out.get(node_id, []):
                        edges_to_remove.add(id(edge))
                    for edge in adj_in.get(node_id, []):
                        edges_to_remove.add(id(edge))
                    changed = True

        # Track cell_ids of removed nodes for completeness verification
        for node_id in nodes_to_remove:
            node = self.parser.nodes.get(node_id)
            if node and node.cell_id and node.cell_id in self.cell_locations:
                self._signal_removed_cell_ids.add(node.cell_id)

        # Apply removal to parser data
        for node_id in nodes_to_remove:
            self.parser.nodes.pop(node_id, None)

        self.parser.edges = [e for e in self.parser.edges
                             if id(e) not in edges_to_remove
                             and e.src_node not in nodes_to_remove
                             and e.dst_node not in nodes_to_remove]

    def simplify(self) -> str:
        """Execute simplification and return new DOT content."""
        strategy = self.strategy
        if self.graph_profile == "after_proc" and strategy == "proc_group":
            strategy = "connected_component"

        boundary_nodes = self.graph.get_boundary_nodes()
        nodes_to_merge = self.graph.get_nodes_to_merge()

        if not nodes_to_merge:
            self._last_provenance = {
                "schema_version": "1",
                "graph_profile": self.graph_profile,
                "strategy_used": strategy,
                "comb_groups": [],
                "raw_to_comb": {}
            }
            return self._generate_original()

        if strategy == "proc_group":
            return self._simplify_by_proc_group(boundary_nodes, nodes_to_merge)
        else:
            return self._simplify_by_connected_component(boundary_nodes, nodes_to_merge)

    def simplify_to_graph(self) -> Optional[SimplifiedGraph]:
        """Execute simplification and return structured SimplifiedGraph."""
        strategy = self.strategy
        if self.graph_profile == "after_proc" and strategy == "proc_group":
            strategy = "connected_component"

        boundary_nodes = self.graph.get_boundary_nodes()
        nodes_to_merge = self.graph.get_nodes_to_merge()

        if not nodes_to_merge:
            # Return graph with only boundary nodes
            return self._build_graph_from_original(boundary_nodes)

        if strategy == "proc_group":
            return self._build_proc_group_graph(boundary_nodes, nodes_to_merge)
        else:
            # For now, only proc_group strategy is fully supported
            return None

    def get_last_provenance(self) -> Dict[str, Any]:
        """Get provenance metadata for the latest simplify() execution."""
        return self._last_provenance

    def _build_comb_provenance(
        self,
        comb_nodes: Dict[str, str],
        comb_info: Dict[str, Any],
        strategy_used: str
    ) -> Dict[str, Any]:
        """Build provenance mapping from simplified COMB nodes to original graph."""
        groups: Dict[str, Dict[str, Any]] = {}
        for raw_node_id, comb_id in comb_nodes.items():
            if comb_id not in groups:
                comb_type = "COMB"
                info = comb_info.get(comb_id)
                if isinstance(info, tuple) and len(info) > 0:
                    comb_type = str(info[0])
                groups[comb_id] = {
                    "comb_id": comb_id,
                    "comb_type": comb_type,
                    "member_nodes": [],
                    "internal_edges": [],
                    "boundary_in_edges": [],
                    "boundary_out_edges": []
                }
            groups[comb_id]["member_nodes"].append(raw_node_id)

        internal_seen: Dict[str, Set[Tuple[str, str, str, str]]] = defaultdict(set)
        bin_seen: Dict[str, Set[Tuple[str, str, str, str, str]]] = defaultdict(set)
        bout_seen: Dict[str, Set[Tuple[str, str, str, str, str]]] = defaultdict(set)

        for edge in self.graph.edges:
            src_comb = comb_nodes.get(edge.src_node)
            dst_comb = comb_nodes.get(edge.dst_node)

            if src_comb and dst_comb and src_comb == dst_comb:
                key = (edge.src_node, edge.src_port, edge.dst_node, edge.dst_port)
                if key not in internal_seen[src_comb]:
                    internal_seen[src_comb].add(key)
                    groups[src_comb]["internal_edges"].append({
                        "src": edge.src_node,
                        "src_port": edge.src_port,
                        "dst": edge.dst_node,
                        "dst_port": edge.dst_port,
                    })
                continue

            if src_comb:
                outside_mapped = dst_comb or edge.dst_node
                key = (edge.src_node, edge.src_port, edge.dst_node, edge.dst_port, outside_mapped)
                if key not in bout_seen[src_comb]:
                    bout_seen[src_comb].add(key)
                    groups[src_comb]["boundary_out_edges"].append({
                        "src": edge.src_node,
                        "src_port": edge.src_port,
                        "dst": edge.dst_node,
                        "dst_port": edge.dst_port,
                        "outside_mapped": outside_mapped,
                    })

            if dst_comb:
                outside_mapped = src_comb or edge.src_node
                key = (edge.src_node, edge.src_port, edge.dst_node, edge.dst_port, outside_mapped)
                if key not in bin_seen[dst_comb]:
                    bin_seen[dst_comb].add(key)
                    groups[dst_comb]["boundary_in_edges"].append({
                        "src": edge.src_node,
                        "src_port": edge.src_port,
                        "dst": edge.dst_node,
                        "dst_port": edge.dst_port,
                        "outside_mapped": outside_mapped,
                    })

        for group in groups.values():
            group["member_nodes"].sort()

        return {
            "schema_version": "1",
            "graph_profile": self.graph_profile,
            "strategy_used": strategy_used,
            "comb_groups": [groups[k] for k in sorted(groups.keys())],
            "raw_to_comb": {k: v for k, v in sorted(comb_nodes.items())}
        }

    def _parse_proc_source_location(self, label: str) -> Optional[SourceLocation]:
        """Parse PROC label to extract source location.

        Label format: "PROC $43221\\n/path/to/file.v:1877.1-1907.4"
        """
        match = re.search(r'PROC \$\d+\\n(.+?):(\d+)\.\d+-(\d+)\.\d+', label)
        if match:
            file_path = match.group(1)
            start_line = int(match.group(2))
            end_line = int(match.group(3))
            return SourceLocation(file_path=file_path, start_line=start_line, end_line=end_line)
        return None

    def _build_proc_group_graph(self, boundary_nodes: Set[str],
                                 nodes_to_merge: Set[str]) -> SimplifiedGraph:
        """Build SimplifiedGraph using proc_group strategy."""
        components = self.graph.find_connected_components(nodes_to_merge)
        proc_groups = self.graph.group_by_proc_block(nodes_to_merge)

        graph = SimplifiedGraph(module_name=self.parser.graph_name)

        comb_nodes_mapping: Dict[str, str] = {}  # node_id -> comb_id
        comb_info: Dict[str, Tuple[str, int]] = {}  # comb_id -> (label, count)
        comb_locations: Dict[str, CombLocationInfo] = {}  # comb_id -> location info
        comb_source_locs: Dict[str, List[SourceLocation]] = {}  # comb_id -> source locations

        # Build COMB nodes (same logic as original)
        for comp_idx, component in enumerate(components):
            has_logic = any(
                self.parser.nodes.get(node_id) and
                self.parser.nodes[node_id].node_type == 'comb_logic'
                for node_id in component
            )
            if not has_logic:
                continue

            comb_logic_nodes = [
                node_id for node_id in component
                if self.parser.nodes.get(node_id) and
                self.parser.nodes[node_id].node_type == 'comb_logic'
            ]

            associated_procs: Dict[str, Set[str]] = {}
            for node_id in component:
                for proc_id, groups in proc_groups.items():
                    if node_id in groups["input"]:
                        if proc_id not in associated_procs:
                            associated_procs[proc_id] = set()
                        associated_procs[proc_id].add("input")
                    if node_id in groups["output"]:
                        if proc_id not in associated_procs:
                            associated_procs[proc_id] = set()
                        associated_procs[proc_id].add("output")

            if associated_procs:
                primary_proc = sorted(associated_procs.keys())[0]
                directions = associated_procs[primary_proc]

                if "input" in directions and "output" in directions:
                    comb_id = f"{primary_proc}_comb_{comp_idx}"
                    label = "COMB"
                elif "input" in directions:
                    comb_id = f"{primary_proc}_in_comb_{comp_idx}"
                    label = "IN_COMB"
                else:
                    comb_id = f"{primary_proc}_out_comb_{comp_idx}"
                    label = "OUT_COMB"
            else:
                comb_id = f"comb_{comp_idx}"
                label = "COMB"

            comb_info[comb_id] = (label, len(comb_logic_nodes))

            location_info = CombLocationInfo()
            source_locs = []
            for node_id in comb_logic_nodes:
                node = self.parser.nodes.get(node_id)
                if node and node.cell_id and node.cell_id in self.cell_locations:
                    loc = self.cell_locations[node.cell_id]
                    location_info.add_location(loc.file_path, loc.start_line)
                    source_locs.append(loc)
                    self._covered_cell_ids.add(node.cell_id)

            comb_locations[comb_id] = location_info
            comb_source_locs[comb_id] = source_locs

            for node_id in comb_logic_nodes:
                comb_nodes_mapping[node_id] = comb_id

        # Handle orphaned comb_logic nodes
        for node_id in nodes_to_merge:
            if node_id not in comb_nodes_mapping:
                node = self.parser.nodes.get(node_id)
                if node and node.node_type == 'comb_logic':
                    if "_orphan_comb" not in comb_info:
                        comb_info["_orphan_comb"] = ("COMB", 0)
                        comb_locations["_orphan_comb"] = CombLocationInfo()
                        comb_source_locs["_orphan_comb"] = []
                    comb_nodes_mapping[node_id] = "_orphan_comb"
                    label, count = comb_info["_orphan_comb"]
                    comb_info["_orphan_comb"] = (label, count + 1)

        comb_info = {k: v for k, v in comb_info.items() if v[1] > 0}

        new_edges = self._generate_new_edges(nodes_to_merge, comb_nodes_mapping)

        # Keep only COMB nodes referenced by edges
        used_combs = set()
        for edge in new_edges:
            if edge.src_node in comb_info:
                used_combs.add(edge.src_node)
            if edge.dst_node in comb_info:
                used_combs.add(edge.dst_node)

        comb_info = {k: v for k, v in comb_info.items() if k in used_combs}

        # Build COMB nodes for SimplifiedGraph
        for comb_id, (comb_type, node_count) in comb_info.items():
            # Extract associated_proc from comb_id
            assoc_proc = None
            if '_' in comb_id and comb_id.startswith('p'):
                parts = comb_id.split('_')
                if parts[0].startswith('p') and parts[0][1:].isdigit():
                    assoc_proc = parts[0]

            graph.comb_nodes[comb_id] = CombNodeInfo(
                node_id=comb_id,
                comb_type=comb_type,
                node_count=node_count,
                location_info=comb_locations.get(comb_id, CombLocationInfo()),
                source_locations=comb_source_locs.get(comb_id, []),
                associated_proc=assoc_proc
            )

        # Build PROC nodes
        for node_id in boundary_nodes:
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    source_loc = self._parse_proc_source_location(node.label)
                    graph.proc_nodes[node_id] = ProcNodeInfo(
                        node_id=node_id,
                        label=node.label,
                        source_location=source_loc
                    )
                    if node.cell_id and node.cell_id in self.cell_locations:
                        self._covered_cell_ids.add(node.cell_id)
                elif node.node_type == 'io_port':
                    graph.io_ports[node_id] = node.label
                elif node.node_type == 'submodule':
                    # Extract instance name from label (format: {inputs}|instance_name\nmodule_type|{outputs})
                    match = re.search(r'\|([^|{}<>]+?)\\n', node.label)
                    instance_name = match.group(1).strip() if match else node_id
                    graph.submodules[node_id] = instance_name
                elif node.node_type == 'seq_cell':
                    graph.seq_cells[node_id] = node.extract_cell_type()

        # Build edges
        for edge in new_edges:
            graph.edges.append((edge.src_node, edge.dst_node))

        # Completeness verification
        self._verify_completeness()

        return graph

    def _build_graph_from_original(self, boundary_nodes: Set[str]) -> SimplifiedGraph:
        """Build SimplifiedGraph from original DOT (no merging)."""
        graph = SimplifiedGraph(module_name=self.parser.graph_name)

        for node_id in boundary_nodes:
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    source_loc = self._parse_proc_source_location(node.label)
                    graph.proc_nodes[node_id] = ProcNodeInfo(
                        node_id=node_id,
                        label=node.label,
                        source_location=source_loc
                    )
                elif node.node_type == 'io_port':
                    graph.io_ports[node_id] = node.label
                elif node.node_type == 'submodule':
                    match = re.search(r'\|([^|{}<>]+?)\\n', node.label)
                    instance_name = match.group(1).strip() if match else node_id
                    graph.submodules[node_id] = instance_name
                elif node.node_type == 'seq_cell':
                    graph.seq_cells[node_id] = node.extract_cell_type()

        for edge in self.parser.edges:
            if edge.src_node in boundary_nodes and edge.dst_node in boundary_nodes:
                graph.edges.append((edge.src_node, edge.dst_node))

        return graph

    def _simplify_by_proc_group(self, boundary_nodes: Set[str],
                                 nodes_to_merge: Set[str]) -> str:
        """Simplify by grouping combinational logic per PROC block."""
        components = self.graph.find_connected_components(nodes_to_merge)
        proc_groups = self.graph.group_by_proc_block(nodes_to_merge)

        comb_nodes: Dict[str, str] = {}
        comb_info: Dict[str, Tuple[str, int]] = {}
        comb_locations: Dict[str, CombLocationInfo] = {}

        for comp_idx, component in enumerate(components):
            # Skip components with only junctions/constants/slices (no actual logic)
            # These are just connection points and should be bypassed in edges
            has_logic = any(
                self.parser.nodes.get(node_id) and
                self.parser.nodes[node_id].node_type == 'comb_logic'
                for node_id in component
            )
            if not has_logic:
                # Don't create COMB node for pure junction/constant components
                # These nodes will be bypassed when generating edges
                continue

            # Count only actual comb_logic nodes, excluding junctions/constants/slices
            comb_logic_nodes = [
                node_id for node_id in component
                if self.parser.nodes.get(node_id) and
                self.parser.nodes[node_id].node_type == 'comb_logic'
            ]

            associated_procs: Dict[str, Set[str]] = {}

            for node_id in component:
                for proc_id, groups in proc_groups.items():
                    if node_id in groups["input"]:
                        if proc_id not in associated_procs:
                            associated_procs[proc_id] = set()
                        associated_procs[proc_id].add("input")
                    if node_id in groups["output"]:
                        if proc_id not in associated_procs:
                            associated_procs[proc_id] = set()
                        associated_procs[proc_id].add("output")

            if associated_procs:
                primary_proc = sorted(associated_procs.keys())[0]
                directions = associated_procs[primary_proc]

                if "input" in directions and "output" in directions:
                    comb_id = f"{primary_proc}_comb_{comp_idx}"
                    label = "COMB"
                elif "input" in directions:
                    comb_id = f"{primary_proc}_in_comb_{comp_idx}"
                    label = "IN_COMB"
                else:
                    comb_id = f"{primary_proc}_out_comb_{comp_idx}"
                    label = "OUT_COMB"
            else:
                comb_id = f"comb_{comp_idx}"
                label = "COMB"

            # Only count comb_logic nodes, not junctions/constants/slices
            comb_info[comb_id] = (label, len(comb_logic_nodes))

            location_info = CombLocationInfo()
            missing_loc_count = 0
            # Only add comb_logic nodes to the COMB block, bypass junctions
            for node_id in comb_logic_nodes:
                node = self.parser.nodes.get(node_id)
                if node and node.cell_id and node.cell_id in self.cell_locations:
                    loc = self.cell_locations[node.cell_id]
                    location_info.add_location(loc.file_path, loc.start_line)
                    self._covered_cell_ids.add(node.cell_id)
                else:
                    missing_loc_count += 1
            if missing_loc_count > 0 and self.verbose:
                print(f"[WARN] COMB {comb_id}: {missing_loc_count}/{len(comb_logic_nodes)} "
                      f"nodes missing source location")
            comb_locations[comb_id] = location_info

            # Only map comb_logic nodes to COMB, junctions will be bypassed
            for node_id in comb_logic_nodes:
                comb_nodes[node_id] = comb_id

        # Handle orphaned comb_logic nodes only (skip junctions/constants/slices)
        for node_id in nodes_to_merge:
            if node_id not in comb_nodes:
                node = self.parser.nodes.get(node_id)
                # Only add actual comb_logic to orphan, bypass junctions/constants/slices
                if node and node.node_type == 'comb_logic':
                    if "_orphan_comb" not in comb_info:
                        comb_info["_orphan_comb"] = ("COMB", 0)
                    comb_nodes[node_id] = "_orphan_comb"
                    label, count = comb_info["_orphan_comb"]
                    comb_info["_orphan_comb"] = (label, count + 1)

        comb_info = {k: v for k, v in comb_info.items() if v[1] > 0}

        new_edges = self._generate_new_edges(nodes_to_merge, comb_nodes)

        # Keep only COMB nodes referenced by edges
        used_combs = set()
        for edge in new_edges:
            if edge.src_node in comb_info:
                used_combs.add(edge.src_node)
            if edge.dst_node in comb_info:
                used_combs.add(edge.dst_node)

        # Log filtered (edgeless) COMB nodes
        filtered_combs = {k: v for k, v in comb_info.items() if k not in used_combs}
        if filtered_combs and self.verbose:
            for comb_id, (label, count) in filtered_combs.items():
                loc_label = comb_locations.get(comb_id, CombLocationInfo()).get_display_label()
                print(f"[WARN] COMB {comb_id} filtered (no edges): {count} nodes, source: {loc_label or 'none'}")

        comb_info = {k: v for k, v in comb_info.items() if k in used_combs}

        # Track boundary node cell_ids (submodules have cell_ids in cell_locations)
        for node_id in boundary_nodes:
            node = self.parser.nodes.get(node_id)
            if node and node.cell_id and node.cell_id in self.cell_locations:
                self._covered_cell_ids.add(node.cell_id)

        # Completeness verification: check cell_locations against covered + removed
        self._verify_completeness()

        self._last_provenance = self._build_comb_provenance(
            comb_nodes=comb_nodes,
            comb_info=comb_info,
            strategy_used="proc_group"
        )

        return self._generate_simplified_proc_group(boundary_nodes, comb_info, new_edges, comb_locations)

    def _verify_completeness(self) -> None:
        """Verify that all cell_locations entries are accounted for in the output.

        Checks every cell_id in cell_locations against:
        - covered: appeared in a COMB node or boundary node in the simplified output
        - signal_removed: removed during signal removal phase
        - uncovered: not accounted for — these are potential RTL line losses
        """
        if not self.cell_locations:
            return

        all_ids = set(self.cell_locations.keys())
        covered = self._covered_cell_ids
        removed = self._signal_removed_cell_ids
        accounted = covered | removed
        uncovered = all_ids - accounted

        if self.verbose:
            print(f"[Completeness] cell_locations: {len(all_ids)}, "
                  f"covered: {len(covered)}, signal_removed: {len(removed)}, "
                  f"uncovered: {len(uncovered)}")

        if uncovered and self.verbose:
            # Group uncovered by file for readable output
            uncovered_by_file: Dict[str, List[int]] = defaultdict(list)
            for cell_id in sorted(uncovered):
                loc = self.cell_locations[cell_id]
                filename = loc.file_path.split('/')[-1] if '/' in loc.file_path else loc.file_path
                uncovered_by_file[filename].append(loc.start_line)

            for filename, lines in sorted(uncovered_by_file.items()):
                lines.sort()
                # Compress consecutive lines into ranges
                ranges = []
                start = lines[0]
                end = lines[0]
                for line in lines[1:]:
                    if line == end + 1:
                        end = line
                    else:
                        ranges.append(f"{start}" if start == end else f"{start}-{end}")
                        start = end = line
                ranges.append(f"{start}" if start == end else f"{start}-{end}")
                print(f"[WARN] Uncovered RTL lines in {filename}: {', '.join(ranges)}")

    def _simplify_by_connected_component(self, boundary_nodes: Set[str],
                                          nodes_to_merge: Set[str]) -> str:
        """Simplify by connected component (simpler strategy)."""
        components = self.graph.find_connected_components(nodes_to_merge)

        comb_nodes: Dict[str, str] = {}
        comb_info: Dict[str, int] = {}

        for i, component in enumerate(components):
            comb_id = f"comb{i}"
            comb_info[comb_id] = len(component)
            for node_id in component:
                comb_nodes[node_id] = comb_id

        new_edges = self._generate_new_edges(nodes_to_merge, comb_nodes)

        self._last_provenance = self._build_comb_provenance(
            comb_nodes=comb_nodes,
            comb_info=comb_info,
            strategy_used="connected_component"
        )

        return self._generate_simplified(boundary_nodes, comb_info, new_edges)

    def _generate_new_edges(self, nodes_to_merge: Set[str],
                            comb_nodes: Dict[str, str]) -> List[DotEdge]:
        """Remap edges after merging nodes into COMB groups.

        Merged nodes lose their port info (COMB nodes have no ports).
        Boundary nodes (PROC, I/O, submodule) keep their original port info
        so that arrows connect to the correct port positions.

        Nodes in nodes_to_merge but not in comb_nodes (pure junction/constant)
        are bypassed: they don't appear in the output, edges pass through them.
        """
        # Build bypass map for junction nodes
        # junction_bypass[node_id] = list of (target_node, target_port, src_port) reachable through junctions
        junction_nodes = {n for n in nodes_to_merge if n not in comb_nodes}

        def find_targets_through_junctions(start_node: str, start_port: str, visited: Set[str]) -> List[Tuple[str, str, str]]:
            """Trace through junction nodes to find all reachable endpoints.

            Returns list of (endpoint_node, endpoint_port, original_port)
            """
            if start_node in visited:
                return []
            visited = visited | {start_node}

            targets = []
            for edge in self.graph.adj_out.get(start_node, []):
                if edge.dst_node in junction_nodes:
                    # Continue through junction
                    targets.extend(find_targets_through_junctions(edge.dst_node, edge.dst_port, visited))
                else:
                    # Reached a non-junction node
                    targets.append((edge.dst_node, edge.dst_port, start_port))
            return targets

        # Build bypass edges for junctions: boundary → junction* → boundary/COMB
        bypass_edges: List[Tuple[str, str, str, str]] = []  # (src, src_port, dst, dst_port)

        for edge in self.graph.edges:
            src_is_junction = edge.src_node in junction_nodes
            dst_is_junction = edge.dst_node in junction_nodes

            if not src_is_junction and dst_is_junction:
                # Boundary/COMB → junction: trace through to find actual targets
                targets = find_targets_through_junctions(edge.dst_node, edge.dst_port, set())
                # Map src to COMB if it's a merged node
                src_node = edge.src_node
                src_port = edge.src_port
                if edge.src_node in comb_nodes:
                    src_node = comb_nodes[edge.src_node]
                    src_port = ""  # COMB nodes have no ports
                for target_node, target_port, _ in targets:
                    bypass_edges.append((src_node, src_port, target_node, target_port))

        new_edges = []
        edge_set = set()

        # Process bypass edges first (boundary/COMB → junction* → endpoint)
        for src, src_port, dst, dst_port in bypass_edges:
            # Map dst to COMB if it's in merge set
            if dst in nodes_to_merge:
                dst_comb = comb_nodes.get(dst)
                if dst_comb:
                    # Skip self-loops (same COMB block)
                    if src == dst_comb:
                        continue
                    edge_key = (src, src_port, dst_comb)
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        new_edges.append(DotEdge(
                            src_node=src,
                            src_port=src_port,
                            dst_node=dst_comb,
                            dst_port="",
                            attrs={}
                        ))
            else:
                # dst is boundary
                edge_key = (src, src_port, dst, dst_port)
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    new_edges.append(DotEdge(
                        src_node=src,
                        src_port=src_port,
                        dst_node=dst,
                        dst_port=dst_port,
                        attrs={}
                    ))

        # Process normal edges (skip those involving junctions, already handled above)
        for edge in self.graph.edges:
            src_is_junction = edge.src_node in junction_nodes
            dst_is_junction = edge.dst_node in junction_nodes

            # Skip edges involving junctions (handled by bypass logic)
            if src_is_junction or dst_is_junction:
                continue

            src_in_merge = edge.src_node in nodes_to_merge
            dst_in_merge = edge.dst_node in nodes_to_merge

            # Get COMB IDs
            src_comb = comb_nodes.get(edge.src_node) if src_in_merge else None
            dst_comb = comb_nodes.get(edge.dst_node) if dst_in_merge else None

            if src_in_merge and dst_in_merge:
                # Both have COMB nodes (we already filtered out junctions)
                if src_comb and dst_comb and src_comb != dst_comb:
                    edge_key = (src_comb, dst_comb)
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        new_edges.append(DotEdge(
                            src_node=src_comb,
                            src_port="",
                            dst_node=dst_comb,
                            dst_port="",
                            attrs={}
                        ))
            elif src_in_merge:
                # COMB → boundary
                if src_comb:
                    edge_key = (src_comb, edge.dst_node, edge.dst_port)
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        new_edges.append(DotEdge(
                            src_node=src_comb,
                            src_port="",
                            dst_node=edge.dst_node,
                            dst_port=edge.dst_port,
                            attrs=edge.attrs
                        ))
            elif dst_in_merge:
                # boundary → COMB
                if dst_comb:
                    edge_key = (edge.src_node, edge.src_port, dst_comb)
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        new_edges.append(DotEdge(
                            src_node=edge.src_node,
                            src_port=edge.src_port,
                            dst_node=dst_comb,
                            dst_port="",
                            attrs=edge.attrs
                        ))
            else:
                # Both are boundary nodes
                new_edges.append(edge)

        return new_edges

    def _generate_original(self) -> str:
        """Regenerate original DOT (when no simplification needed)."""
        lines = []
        lines.append(f'digraph "{self.parser.graph_name}" {{')

        for key, value in self.parser.graph_attrs.items():
            if key in ('label',):
                lines.append(f'{key}="{value}";')
            else:
                lines.append(f'{key}={value};')

        for node_id, node in self.parser.nodes.items():
            lines.append(f'{node_id} [ {node.raw_attrs} ];')

        for edge in self.parser.edges:
            src = f'{edge.src_node}:{edge.src_port}' if edge.src_port else edge.src_node
            dst = f'{edge.dst_node}:{edge.dst_port}' if edge.dst_port else edge.dst_node
            attrs = ', '.join(f'{k}="{v}"' for k, v in edge.attrs.items())
            if attrs:
                lines.append(f'{src} -> {dst} [{attrs}];')
            else:
                lines.append(f'{src} -> {dst};')

        lines.append('}')
        return '\n'.join(lines)

    def _generate_simplified(self, boundary_nodes: Set[str],
                            comb_info: Dict[str, int],
                            new_edges: List[DotEdge]) -> str:
        """Generate simplified DOT (connected component strategy)."""
        lines = []
        lines.append(f'digraph "{self.parser.graph_name}" {{')

        for key, value in self.parser.graph_attrs.items():
            if key in ('label',):
                lines.append(f'{key}="{value}";')
            else:
                lines.append(f'{key}={value};')

        # Add layout optimization parameters for better spacing
        lines.append('ranksep=1.5;')  # Increase vertical spacing between ranks
        lines.append('nodesep=0.8;')  # Increase horizontal spacing between nodes

        for node_id in sorted(boundary_nodes):
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'io_port':
                    lines.append(f'{node_id} [shape=octagon, label="{node.label}"];')
                elif node.node_type == 'submodule':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'seq_cell':
                    lines.append(f'{node_id} [{node.raw_attrs}];')

        for comb_id, count in sorted(comb_info.items()):
            lines.append(
                f'{comb_id} [shape=box, style="filled,rounded", '
                f'fillcolor=lightgray, label="COMB\\n({count} nodes)"];'
            )

        for edge in new_edges:
            src = f'{edge.src_node}:{edge.src_port}' if edge.src_port else edge.src_node
            dst = f'{edge.dst_node}:{edge.dst_port}' if edge.dst_port else edge.dst_node
            lines.append(f'{src} -> {dst};')

        lines.append('}')
        return '\n'.join(lines)

    def _generate_simplified_proc_group(self, boundary_nodes: Set[str],
                                         comb_info: Dict[str, Tuple[str, int]],
                                         new_edges: List[DotEdge],
                                         comb_locations: Dict[str, CombLocationInfo] = None) -> str:
        """Generate simplified DOT (proc-group strategy with color coding)."""
        comb_locations = comb_locations or {}
        lines = []
        lines.append(f'digraph "{self.parser.graph_name}" {{')

        for key, value in self.parser.graph_attrs.items():
            if key in ('label',):
                lines.append(f'{key}="{value}";')
            else:
                lines.append(f'{key}={value};')

        # Add layout optimization parameters for better spacing
        lines.append('ranksep=1.5;')  # Increase vertical spacing between ranks
        lines.append('nodesep=0.8;')  # Increase horizontal spacing between nodes

        for node_id in sorted(boundary_nodes):
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'io_port':
                    lines.append(f'{node_id} [shape=octagon, label="{node.label}"];')
                elif node.node_type == 'submodule':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'seq_cell':
                    lines.append(f'{node_id} [{node.raw_attrs}];')

        for comb_id, (label, count) in sorted(comb_info.items()):
            if count == 0:
                continue

            if "in_comb" in comb_id:
                fillcolor = "lightblue"
            elif "out_comb" in comb_id:
                fillcolor = "lightyellow"
            else:
                fillcolor = "lightgray"

            location_label = ""
            if comb_id in comb_locations:
                location_label = comb_locations[comb_id].get_display_label()

            if location_label:
                full_label = f"{label}\\n({count})\\n{location_label}"
            else:
                full_label = f"{label}\\n({count})"

            lines.append(
                f'{comb_id} [shape=box, style="filled,rounded", '
                f'fillcolor={fillcolor}, label="{full_label}"];'
            )

        for edge in new_edges:
            src = f'{edge.src_node}:{edge.src_port}' if edge.src_port else edge.src_node
            dst = f'{edge.dst_node}:{edge.dst_port}' if edge.dst_port else edge.dst_node
            lines.append(f'{src} -> {dst};')

        lines.append('}')
        return '\n'.join(lines)


def simplify_dot_content(
    content: str,
    strategy: str = "proc_group",
    cell_locations: Optional[Dict[str, SourceLocation]] = None,
    remove_signals: Optional[List[str]] = None,
    verbose: bool = False,
    graph_profile: str = "default"
) -> str:
    """Simplify DOT content string.

    Args:
        content: Raw DOT file content from Yosys
        strategy: "proc_group" (default) or "connected_component"
        cell_locations: Optional mapping of cell IDs to SourceLocation objects
        remove_signals: Optional list of signal name patterns to remove
            (fnmatch-style, e.g. ["*rst*", "cpurst_b", "*scan_en*"])
        verbose: Enable completeness verification warnings
        graph_profile: Graph profile hint, e.g. "default" or "after_proc"

    Returns:
        Simplified DOT content string
    """
    parser = DotParser()
    parser.parse(content)

    simplifier = DotSimplifier(
        parser, strategy=strategy,
        cell_locations=cell_locations,
        remove_signals=remove_signals,
        verbose=verbose,
        graph_profile=graph_profile
    )
    return simplifier.simplify()


def simplify_dot_content_with_provenance(
    content: str,
    strategy: str = "proc_group",
    cell_locations: Optional[Dict[str, SourceLocation]] = None,
    remove_signals: Optional[List[str]] = None,
    verbose: bool = False,
    graph_profile: str = "default"
) -> Tuple[str, Dict[str, Any]]:
    """Simplify DOT and return (simplified_dot, provenance)."""
    parser = DotParser()
    parser.parse(content)

    simplifier = DotSimplifier(
        parser, strategy=strategy,
        cell_locations=cell_locations,
        remove_signals=remove_signals,
        verbose=verbose,
        graph_profile=graph_profile
    )
    simplified = simplifier.simplify()
    return simplified, simplifier.get_last_provenance()


def simplify_dot_to_graph(
    content: str,
    strategy: str = "proc_group",
    cell_locations: Optional[Dict[str, SourceLocation]] = None,
    remove_signals: Optional[List[str]] = None,
    verbose: bool = False,
    graph_profile: str = "default"
) -> Optional[SimplifiedGraph]:
    """Simplify DOT content and return structured SimplifiedGraph.

    Args:
        content: Raw DOT file content from Yosys
        strategy: "proc_group" (default) or "connected_component"
        cell_locations: Optional mapping of cell IDs to SourceLocation objects
        remove_signals: Optional list of signal name patterns to remove
            (fnmatch-style, e.g. ["*rst*", "cpurst_b", "*scan_en*"])
        verbose: Enable completeness verification warnings
        graph_profile: Graph profile hint, e.g. "default" or "after_proc"

    Returns:
        SimplifiedGraph object or None if simplification failed
    """
    parser = DotParser()
    parser.parse(content)

    simplifier = DotSimplifier(
        parser, strategy=strategy,
        cell_locations=cell_locations,
        remove_signals=remove_signals,
        verbose=verbose,
        graph_profile=graph_profile
    )
    return simplifier.simplify_to_graph()
