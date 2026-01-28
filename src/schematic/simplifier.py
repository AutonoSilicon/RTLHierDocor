"""DOT schematic simplifier.

Simplifies Yosys-generated DOT schematics by merging combinational logic
into COMB nodes while preserving PROC blocks and I/O ports.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple
from collections import defaultdict

from ..models import SourceLocation, AggregatedLocation


@dataclass
class DotNode:
    """Represents a node in the DOT graph."""
    node_id: str
    shape: str = ""
    label: str = ""
    style: str = ""
    color: str = ""
    fillcolor: str = ""
    raw_attrs: str = ""
    node_type: str = ""  # 'proc', 'io_port', 'comb_logic', 'junction', etc.
    cell_id: Optional[str] = None

    def classify(self) -> None:
        """Classify the node type based on its attributes."""
        label_lower = self.label.lower()
        
        if self.shape == "box" and "proc" in label_lower:
            self.node_type = "proc"
        elif self.shape == "octagon":
            self.node_type = "io_port"
        elif self.shape == "diamond":
            self.node_type = "junction"
        elif self.shape in ("", "ellipse", "box") and not self.label:
            self.node_type = "internal_signal"
        elif "const" in label_lower or self.label.isdigit():
            self.node_type = "constant"
        else:
            self.node_type = "comb_logic"
        
        # Extract cell ID from label if present
        self._extract_cell_id()

    def _extract_cell_id(self) -> None:
        """Extract cell ID from the label."""
        # Match patterns like "$add$file.v:10$123" -> extract "123"
        match = re.search(r'\$(\d+)(?:\s|$|\\n)', self.label)
        if match:
            self.cell_id = match.group(1)


@dataclass
class DotEdge:
    """Represents an edge in the DOT graph."""
    src_node: str
    src_port: str
    dst_node: str
    dst_port: str
    attrs: Dict[str, str] = field(default_factory=dict)


class DotParser:
    """Parser for Yosys-generated DOT files."""
    
    NODE_PATTERN = re.compile(r'^(\w+)\s*\[([^\]]*)\];?\s*$')
    EDGE_PATTERN = re.compile(r'^([\w:]+)\s*->\s*([\w:]+)(?:\s*\[([^\]]*)\])?\s*;?\s*$')
    ATTR_PATTERN = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|(\S+))')
    GRAPH_ATTR_PATTERN = re.compile(r'^(\w+)\s*=\s*(?:"([^"]*)"|(\S+))\s*;?\s*$')

    def __init__(self):
        self.graph_name: str = ""
        self.graph_attrs: Dict[str, str] = {}
        self.nodes: Dict[str, DotNode] = {}
        self.edges: List[DotEdge] = []

    def parse(self, content: str) -> None:
        """Parse DOT content."""
        self.nodes.clear()
        self.edges.clear()
        self.graph_attrs.clear()
        
        lines = content.split('\n')
        in_graph = False
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('//'):
                continue
            
            # Graph start
            if line.startswith('digraph'):
                in_graph = True
                match = re.search(r'digraph\s+"?([^"{\s]+)"?\s*{', line)
                if match:
                    self.graph_name = match.group(1)
                continue
            
            if not in_graph:
                continue
            
            if line == '}':
                break
            
            # Try to parse as graph attribute
            attr_match = self.GRAPH_ATTR_PATTERN.match(line)
            if attr_match:
                key = attr_match.group(1)
                value = attr_match.group(2) or attr_match.group(3)
                self.graph_attrs[key] = value
                continue
            
            # Try to parse as node
            node_match = self.NODE_PATTERN.match(line)
            if node_match:
                self._parse_node(node_match)
                continue
            
            # Try to parse as edge
            edge_match = self.EDGE_PATTERN.match(line)
            if edge_match:
                self._parse_edge(edge_match)
                continue

    def _parse_node(self, match: re.Match) -> None:
        """Parse a node definition."""
        node_id = match.group(1)
        attrs_str = match.group(2)
        
        attrs = {}
        for attr_match in self.ATTR_PATTERN.finditer(attrs_str):
            key = attr_match.group(1)
            value = attr_match.group(2) or attr_match.group(3)
            # Clean trailing commas from values
            if value:
                value = value.rstrip(',')
            attrs[key] = value
        
        node = DotNode(
            node_id=node_id,
            shape=attrs.get('shape', '').rstrip(','),
            label=attrs.get('label', '').rstrip(','),
            style=attrs.get('style', '').rstrip(','),
            color=attrs.get('color', '').rstrip(','),
            fillcolor=attrs.get('fillcolor', '').rstrip(','),
            raw_attrs=attrs_str
        )
        node.classify()
        self.nodes[node_id] = node

    def _parse_edge(self, match: re.Match) -> None:
        """Parse an edge definition."""
        src_full = match.group(1)
        dst_full = match.group(2)
        attrs_str = match.group(3) or ""
        
        src_parts = src_full.split(':')
        dst_parts = dst_full.split(':')
        
        attrs = {}
        for attr_match in self.ATTR_PATTERN.finditer(attrs_str):
            key = attr_match.group(1)
            value = attr_match.group(2) or attr_match.group(3)
            attrs[key] = value
        
        edge = DotEdge(
            src_node=src_parts[0],
            src_port=':'.join(src_parts[1:]) if len(src_parts) > 1 else "",
            dst_node=dst_parts[0],
            dst_port=':'.join(dst_parts[1:]) if len(dst_parts) > 1 else "",
            attrs=attrs
        )
        self.edges.append(edge)


class DotSimplifier:
    """Simplifies DOT schematics by merging combinational logic.
    
    Strategy:
    1. Identify boundary nodes (PROC blocks and I/O ports)
    2. Find connected components of combinational logic
    3. Merge each component into a single COMB node
    4. Preserve edges to/from boundary nodes
    """

    def __init__(
        self,
        parser: DotParser,
        cell_locations: Optional[Dict[str, SourceLocation]] = None
    ):
        self.parser = parser
        self.cell_locations = cell_locations or {}
        
        # Build adjacency lists
        self.adj_out: Dict[str, List[DotEdge]] = defaultdict(list)
        self.adj_in: Dict[str, List[DotEdge]] = defaultdict(list)
        
        for edge in parser.edges:
            self.adj_out[edge.src_node].append(edge)
            self.adj_in[edge.dst_node].append(edge)

    def simplify(self) -> str:
        """Execute simplification and return new DOT content."""
        boundary = self._get_boundary_nodes()
        to_merge = self._get_nodes_to_merge()
        
        if not to_merge:
            return self._generate_original()
        
        # Find connected components and group by PROC
        components = self._find_connected_components(to_merge)
        proc_groups = self._group_by_proc(to_merge)
        
        # Create COMB nodes
        comb_mapping, comb_info, comb_locations = self._create_comb_nodes(
            components, proc_groups
        )
        
        # Generate new edges
        new_edges = self._generate_new_edges(to_merge, comb_mapping)
        
        # Filter to used COMB nodes
        used_combs = {e.src_node for e in new_edges if e.src_node in comb_info}
        used_combs |= {e.dst_node for e in new_edges if e.dst_node in comb_info}
        comb_info = {k: v for k, v in comb_info.items() if k in used_combs}
        
        return self._generate_simplified(boundary, comb_info, new_edges, comb_locations)

    def _get_boundary_nodes(self) -> Set[str]:
        """Get nodes that should be preserved (PROC and I/O)."""
        return {
            nid for nid, node in self.parser.nodes.items()
            if node.node_type in ('proc', 'io_port')
        }

    def _get_nodes_to_merge(self) -> Set[str]:
        """Get nodes that should be merged into COMB nodes."""
        return {
            nid for nid, node in self.parser.nodes.items()
            if node.node_type in ('comb_logic', 'junction', 'constant', 'internal_signal')
        }

    def _find_connected_components(self, nodes: Set[str]) -> List[Set[str]]:
        """Find connected components among nodes to merge."""
        visited = set()
        components = []
        
        def dfs(node_id: str, component: Set[str]):
            if node_id in visited or node_id not in nodes:
                return
            visited.add(node_id)
            component.add(node_id)
            
            for edge in self.adj_out.get(node_id, []):
                if edge.dst_node in nodes:
                    dfs(edge.dst_node, component)
            for edge in self.adj_in.get(node_id, []):
                if edge.src_node in nodes:
                    dfs(edge.src_node, component)
        
        for node_id in nodes:
            if node_id not in visited:
                component = set()
                dfs(node_id, component)
                if component:
                    components.append(component)
        
        return components

    def _group_by_proc(self, nodes: Set[str]) -> Dict[str, Dict[str, Set[str]]]:
        """Group nodes by their associated PROC blocks."""
        boundary = self._get_boundary_nodes()
        proc_nodes = {
            nid for nid in boundary
            if self.parser.nodes[nid].node_type == 'proc'
        }
        
        proc_groups: Dict[str, Dict[str, Set[str]]] = {
            proc_id: {"input": set(), "output": set()}
            for proc_id in proc_nodes
        }
        proc_groups["_no_proc"] = {"input": set(), "output": set()}
        
        # Assign nodes to PROC groups based on connectivity
        for node_id in nodes:
            assigned = False
            
            # Check outgoing edges to PROC
            for edge in self.adj_out.get(node_id, []):
                if edge.dst_node in proc_nodes:
                    proc_groups[edge.dst_node]["input"].add(node_id)
                    assigned = True
            
            # Check incoming edges from PROC
            for edge in self.adj_in.get(node_id, []):
                if edge.src_node in proc_nodes:
                    proc_groups[edge.src_node]["output"].add(node_id)
                    assigned = True
            
            if not assigned:
                proc_groups["_no_proc"]["input"].add(node_id)
        
        return proc_groups

    def _create_comb_nodes(
        self,
        components: List[Set[str]],
        proc_groups: Dict[str, Dict[str, Set[str]]]
    ) -> Tuple[Dict[str, str], Dict[str, Tuple[str, int]], Dict[str, AggregatedLocation]]:
        """Create COMB node mappings and info."""
        comb_mapping: Dict[str, str] = {}  # original -> comb_id
        comb_info: Dict[str, Tuple[str, int]] = {}  # comb_id -> (label, count)
        comb_locations: Dict[str, AggregatedLocation] = {}
        
        for comp_idx, component in enumerate(components):
            # Determine PROC association and direction
            associated: Dict[str, Set[str]] = {}
            for node_id in component:
                for proc_id, groups in proc_groups.items():
                    if node_id in groups["input"]:
                        associated.setdefault(proc_id, set()).add("input")
                    if node_id in groups["output"]:
                        associated.setdefault(proc_id, set()).add("output")
            
            # Generate COMB ID and label
            if associated:
                primary_proc = sorted(associated.keys())[0]
                directions = associated[primary_proc]
                
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
            
            # Aggregate locations
            loc_info = AggregatedLocation()
            for node_id in component:
                node = self.parser.nodes.get(node_id)
                if node and node.cell_id and node.cell_id in self.cell_locations:
                    loc_info.add_location(self.cell_locations[node.cell_id])
            comb_locations[comb_id] = loc_info
            
            # Map nodes to COMB
            for node_id in component:
                comb_mapping[node_id] = comb_id
        
        return comb_mapping, comb_info, comb_locations

    def _generate_new_edges(
        self,
        to_merge: Set[str],
        comb_mapping: Dict[str, str]
    ) -> List[DotEdge]:
        """Generate simplified edges."""
        new_edges = []
        edge_set = set()
        
        for edge in self.parser.edges:
            src_merge = edge.src_node in to_merge
            dst_merge = edge.dst_node in to_merge
            
            if src_merge and dst_merge:
                src_comb = comb_mapping.get(edge.src_node)
                dst_comb = comb_mapping.get(edge.dst_node)
                if src_comb and dst_comb and src_comb != dst_comb:
                    key = (src_comb, dst_comb)
                    if key not in edge_set:
                        edge_set.add(key)
                        new_edges.append(DotEdge(src_comb, "", dst_comb, "", {}))
            elif src_merge:
                src_comb = comb_mapping.get(edge.src_node)
                if src_comb:
                    key = (src_comb, edge.dst_node)
                    if key not in edge_set:
                        edge_set.add(key)
                        new_edges.append(DotEdge(src_comb, "", edge.dst_node, "", edge.attrs))
            elif dst_merge:
                dst_comb = comb_mapping.get(edge.dst_node)
                if dst_comb:
                    key = (edge.src_node, dst_comb)
                    if key not in edge_set:
                        edge_set.add(key)
                        new_edges.append(DotEdge(edge.src_node, "", dst_comb, "", edge.attrs))
            else:
                new_edges.append(edge)
        
        return new_edges

    def _generate_original(self) -> str:
        """Generate original DOT (when no simplification needed)."""
        lines = [f'digraph "{self.parser.graph_name}" {{']
        
        for key, value in self.parser.graph_attrs.items():
            if key == 'label':
                lines.append(f'{key}="{value}";')
            else:
                lines.append(f'{key}={value};')
        
        for node_id, node in self.parser.nodes.items():
            lines.append(f'{node_id} [{node.raw_attrs}];')
        
        for edge in self.parser.edges:
            src = f'{edge.src_node}:{edge.src_port}' if edge.src_port else edge.src_node
            dst = f'{edge.dst_node}:{edge.dst_port}' if edge.dst_port else edge.dst_node
            lines.append(f'{src} -> {dst};')
        
        lines.append('}')
        return '\n'.join(lines)

    def _generate_simplified(
        self,
        boundary: Set[str],
        comb_info: Dict[str, Tuple[str, int]],
        new_edges: List[DotEdge],
        comb_locations: Dict[str, AggregatedLocation]
    ) -> str:
        """Generate simplified DOT."""
        lines = [f'digraph "{self.parser.graph_name}" {{']
        
        for key, value in self.parser.graph_attrs.items():
            if key == 'label':
                lines.append(f'{key}="{value}";')
            else:
                lines.append(f'{key}={value};')
        
        # Boundary nodes
        for node_id in sorted(boundary):
            node = self.parser.nodes.get(node_id)
            if node:
                if node.node_type == 'proc':
                    lines.append(f'{node_id} [{node.raw_attrs}];')
                elif node.node_type == 'io_port':
                    lines.append(f'{node_id} [shape=octagon, label="{node.label}"];')
        
        # COMB nodes
        for comb_id, (label, count) in sorted(comb_info.items()):
            if count == 0:
                continue
            
            # Color by type
            if "in_comb" in comb_id:
                fillcolor = "lightblue"
            elif "out_comb" in comb_id:
                fillcolor = "lightyellow"
            else:
                fillcolor = "lightgray"
            
            # Build label with location info
            loc_label = comb_locations.get(comb_id, AggregatedLocation()).get_display_label()
            if loc_label:
                full_label = f"{label}\\n({count})\\n{loc_label}"
            else:
                full_label = f"{label}\\n({count})"
            
            lines.append(
                f'{comb_id} [shape=box, style="filled,rounded", '
                f'fillcolor={fillcolor}, label="{full_label}"];'
            )
        
        # Edges
        for edge in new_edges:
            lines.append(f'{edge.src_node} -> {edge.dst_node};')
        
        lines.append('}')
        return '\n'.join(lines)
