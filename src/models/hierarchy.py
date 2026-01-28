"""Hierarchy tree data model."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Iterator
import json


@dataclass
class HierarchyNode:
    """A node in the RTL module hierarchy tree.
    
    Represents a module instance in the design hierarchy, with parent-child
    relationships and port connection information.
    
    Attributes:
        module_name: Name of the module type (without leading backslash)
        instance_name: Instance name in parent module
        parent: Parent node in the hierarchy
        children: Dictionary of child nodes (instance_name -> HierarchyNode)
        depth: Depth in the hierarchy tree (root = 0)
        port_connections: Port connections as dict {port_name: wire_name}
    """
    module_name: str
    instance_name: str
    parent: Optional['HierarchyNode'] = None
    children: Dict[str, 'HierarchyNode'] = field(default_factory=dict)
    depth: int = 0
    port_connections: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        # Clean module name
        if self.module_name.startswith("\\"):
            self.module_name = self.module_name[1:]

    def add_child(self, child: 'HierarchyNode') -> None:
        """Add a child node to this hierarchy node."""
        child.parent = self
        child.depth = self.depth + 1
        self.children[child.instance_name] = child

    def get_path(self) -> str:
        """Get the full hierarchical path of this node."""
        if self.parent is None:
            return self.instance_name
        return f"{self.parent.get_path()}/{self.instance_name}"

    def find_by_path(self, path: str) -> Optional['HierarchyNode']:
        """Find a node by its hierarchical path."""
        parts = path.split('/')
        current = self
        for part in parts[1:]:  # Skip root
            if part in current.children:
                current = current.children[part]
            else:
                return None
        return current

    def iter_depth_first(self) -> Iterator['HierarchyNode']:
        """Iterate through all nodes in depth-first order."""
        yield self
        for child in self.children.values():
            yield from child.iter_depth_first()

    def iter_breadth_first(self) -> Iterator['HierarchyNode']:
        """Iterate through all nodes in breadth-first order."""
        queue = [self]
        while queue:
            node = queue.pop(0)
            yield node
            queue.extend(node.children.values())

    def count_descendants(self) -> int:
        """Count total number of descendant nodes."""
        count = len(self.children)
        for child in self.children.values():
            count += child.count_descendants()
        return count

    def get_modules_by_type(self, module_name: str) -> List['HierarchyNode']:
        """Find all instances of a specific module type."""
        result = []
        if self.module_name == module_name:
            result.append(self)
        for child in self.children.values():
            result.extend(child.get_modules_by_type(module_name))
        return result

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "module_name": self.module_name,
            "instance_name": self.instance_name,
            "depth": self.depth,
            "port_connections": self.port_connections,
            "children": [child.to_dict() for child in self.children.values()]
        }

    @classmethod
    def from_dict(cls, data: dict, parent: Optional['HierarchyNode'] = None) -> 'HierarchyNode':
        """Create from dictionary."""
        node = cls(
            module_name=data["module_name"],
            instance_name=data["instance_name"],
            parent=parent,
            depth=data.get("depth", 0),
            port_connections=data.get("port_connections", {})
        )
        for child_data in data.get("children", []):
            child = cls.from_dict(child_data, parent=node)
            node.children[child.instance_name] = child
        return node

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> 'HierarchyNode':
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def render_ascii(self, show_ports: bool = False, max_depth: Optional[int] = None) -> str:
        """Render the hierarchy tree as ASCII art.
        
        Args:
            show_ports: Whether to show port connections
            max_depth: Maximum depth to display (None = unlimited)
            
        Returns:
            ASCII representation of the hierarchy tree
        """
        lines = []
        self._render_ascii_node(lines, "", True, show_ports, max_depth)
        return "\n".join(lines)

    def _render_ascii_node(
        self,
        lines: List[str],
        prefix: str,
        is_last: bool,
        show_ports: bool,
        max_depth: Optional[int]
    ) -> None:
        """Recursively render ASCII tree node."""
        if max_depth is not None and self.depth > max_depth:
            return

        is_root = self.depth == 0
        if is_root:
            lines.append(f"[ROOT] {self.instance_name} ({self.module_name})")
        else:
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{self.instance_name} ({self.module_name})")

        # Show port connections if requested
        if show_ports and self.port_connections and not is_root:
            port_prefix = prefix + ("    " if is_last else "│   ")
            for port, wire in list(self.port_connections.items())[:5]:
                lines.append(f"{port_prefix}  {port} <- {wire}")
            if len(self.port_connections) > 5:
                lines.append(f"{port_prefix}  ... ({len(self.port_connections) - 5} more)")

        # Render children
        children_list = list(self.children.values())
        for i, child in enumerate(children_list):
            is_last_child = (i == len(children_list) - 1)
            if is_root:
                next_prefix = ""
            else:
                next_prefix = prefix + ("    " if is_last else "│   ")
            child._render_ascii_node(lines, next_prefix, is_last_child, show_ports, max_depth)
