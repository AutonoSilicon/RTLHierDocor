"""DOT schematic simplification for Yosys-generated schematics.

Merges combinational logic blocks into abstract COMB nodes while
preserving PROC blocks (pipeline stage boundaries) and I/O ports.

Ported from scripts/simplify_dot.py.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional

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
            if node.node_type in ('proc', 'io_port', 'submodule'):
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
                 remove_signals: Optional[List[str]] = None):
        self.parser = parser
        self.strategy = strategy
        self.cell_locations = cell_locations or {}
        self.remove_signals = remove_signals or []

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
                in_sources = set()
                for edge in adj_in.get(node_id, []):
                    in_sources.add(edge.src_node)
                no_input = in_sources and in_sources.issubset(nodes_to_remove)
                auto_generated = not _has_source_annotation(node_id)

                if fully_orphaned or (no_input and auto_generated):
                    nodes_to_remove.add(node_id)
                    for edge in adj_out.get(node_id, []):
                        edges_to_remove.add(id(edge))
                    for edge in adj_in.get(node_id, []):
                        edges_to_remove.add(id(edge))
                    changed = True

        # Apply removal to parser data
        for node_id in nodes_to_remove:
            self.parser.nodes.pop(node_id, None)

        self.parser.edges = [e for e in self.parser.edges
                             if id(e) not in edges_to_remove
                             and e.src_node not in nodes_to_remove
                             and e.dst_node not in nodes_to_remove]

    def simplify(self) -> str:
        """Execute simplification and return new DOT content."""
        boundary_nodes = self.graph.get_boundary_nodes()
        nodes_to_merge = self.graph.get_nodes_to_merge()

        if not nodes_to_merge:
            return self._generate_original()

        if self.strategy == "proc_group":
            return self._simplify_by_proc_group(boundary_nodes, nodes_to_merge)
        else:
            return self._simplify_by_connected_component(boundary_nodes, nodes_to_merge)

    def _simplify_by_proc_group(self, boundary_nodes: Set[str],
                                 nodes_to_merge: Set[str]) -> str:
        """Simplify by grouping combinational logic per PROC block."""
        components = self.graph.find_connected_components(nodes_to_merge)
        proc_groups = self.graph.group_by_proc_block(nodes_to_merge)

        comb_nodes: Dict[str, str] = {}
        comb_info: Dict[str, Tuple[str, int]] = {}
        comb_locations: Dict[str, CombLocationInfo] = {}

        for comp_idx, component in enumerate(components):
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

            comb_info[comb_id] = (label, len(component))

            location_info = CombLocationInfo()
            for node_id in component:
                node = self.parser.nodes.get(node_id)
                if node and node.cell_id and node.cell_id in self.cell_locations:
                    loc = self.cell_locations[node.cell_id]
                    location_info.add_location(loc.file_path, loc.start_line)
            comb_locations[comb_id] = location_info

            for node_id in component:
                comb_nodes[node_id] = comb_id

        # Handle orphaned nodes
        for node_id in nodes_to_merge:
            if node_id not in comb_nodes:
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

        comb_info = {k: v for k, v in comb_info.items() if k in used_combs}

        return self._generate_simplified_proc_group(boundary_nodes, comb_info, new_edges, comb_locations)

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

        return self._generate_simplified(boundary_nodes, comb_info, new_edges)

    def _generate_new_edges(self, nodes_to_merge: Set[str],
                            comb_nodes: Dict[str, str]) -> List[DotEdge]:
        """Remap edges after merging nodes into COMB groups.

        Merged nodes lose their port info (COMB nodes have no ports).
        Boundary nodes (PROC, I/O, submodule) keep their original port info
        so that arrows connect to the correct port positions.
        """
        new_edges = []
        edge_set = set()

        for edge in self.graph.edges:
            src_in_merge = edge.src_node in nodes_to_merge
            dst_in_merge = edge.dst_node in nodes_to_merge

            if src_in_merge and dst_in_merge:
                src_comb = comb_nodes.get(edge.src_node)
                dst_comb = comb_nodes.get(edge.dst_node)
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
                src_comb = comb_nodes.get(edge.src_node)
                if src_comb:
                    # Preserve dst port for boundary nodes (submodule, proc, etc.)
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
                dst_comb = comb_nodes.get(edge.dst_node)
                if dst_comb:
                    # Preserve src port for boundary nodes (submodule, proc, etc.)
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

        for node_id in sorted(boundary_nodes):
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'io_port':
                    lines.append(f'{node_id} [shape=octagon, label="{node.label}"];')
                elif node.node_type == 'submodule':
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

        for node_id in sorted(boundary_nodes):
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'io_port':
                    lines.append(f'{node_id} [shape=octagon, label="{node.label}"];')
                elif node.node_type == 'submodule':
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
    remove_signals: Optional[List[str]] = None
) -> str:
    """Simplify DOT content string.

    Args:
        content: Raw DOT file content from Yosys
        strategy: "proc_group" (default) or "connected_component"
        cell_locations: Optional mapping of cell IDs to SourceLocation objects
        remove_signals: Optional list of signal name patterns to remove
            (fnmatch-style, e.g. ["*rst*", "cpurst_b", "*scan_en*"])

    Returns:
        Simplified DOT content string
    """
    parser = DotParser()
    parser.parse(content)

    simplifier = DotSimplifier(
        parser, strategy=strategy,
        cell_locations=cell_locations,
        remove_signals=remove_signals
    )
    return simplifier.simplify()
