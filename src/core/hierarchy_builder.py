"""Hierarchy tree builder module.

Extracts module hierarchy from a Yosys design and builds a tree structure.
"""

from typing import Optional, Dict, Set, Any

from ..models import HierarchyNode, ModuleInfo


class HierarchyBuilder:
    """Builds module hierarchy tree from Yosys design.
    
    Traverses the design starting from the top module and constructs
    a tree of HierarchyNode objects representing the instance hierarchy.
    
    Example:
        builder = HierarchyBuilder(backend)
        root = builder.build("openC910")
        print(root.render_ascii())
    """

    def __init__(self, backend: Any, verbose: bool = False):
        """Initialize the hierarchy builder.
        
        Args:
            backend: YosysBackend instance with loaded design
            verbose: Enable verbose output
        """
        self._backend = backend
        self._verbose = verbose
        self._visited: Set[str] = set()
        self._module_info: Dict[str, ModuleInfo] = {}

    def build(self, top_module: str) -> Optional[HierarchyNode]:
        """Build the hierarchy tree starting from the top module.
        
        Args:
            top_module: Name of the top-level module
            
        Returns:
            Root HierarchyNode of the hierarchy tree
        """
        if not self._backend.is_loaded:
            return None
        
        # Ensure proper module name format
        if not top_module.startswith("\\"):
            top_module = "\\" + top_module
        
        self._visited.clear()
        
        # Create root node
        root = HierarchyNode(
            module_name=top_module,
            instance_name=top_module.lstrip("\\")
        )
        
        # Build subtree
        self._build_subtree(root, top_module)
        
        if self._verbose:
            total_nodes = sum(1 for _ in root.iter_depth_first())
            print(f"[INFO] Built hierarchy with {total_nodes} nodes")
        
        return root

    def _build_subtree(self, node: HierarchyNode, module_name: str) -> None:
        """Recursively build hierarchy subtree for a module.
        
        Args:
            node: Parent HierarchyNode
            module_name: Module name to process
        """
        # Prevent infinite recursion on recursive modules
        node_path = node.get_path()
        if node_path in self._visited:
            return
        self._visited.add(node_path)
        
        module = self._backend.get_module(module_name)
        if not module:
            return
        
        # Iterate over cell instances
        for cell_id in module.cells_:
            cell = module.cell(cell_id)
            cell_name = cell.name.str()
            cell_type = cell.type.str()
            
            # Skip internal primitives (start with $)
            if cell_type.startswith("$") and not cell_type.startswith("$paramod"):
                continue
            
            # Create child node
            child = HierarchyNode(
                module_name=cell_type,
                instance_name=cell_name.lstrip("\\")
            )
            
            # Extract port connections
            child.port_connections = self._extract_connections(cell)
            
            # Add to parent
            node.add_child(child)
            
            # Recurse into child module
            self._build_subtree(child, cell_type)

    def _extract_connections(self, cell: Any) -> Dict[str, str]:
        """Extract port connections from a cell instance.
        
        Args:
            cell: Yosys Cell object
            
        Returns:
            Dict mapping port names to connected wire names
        """
        connections = {}
        for conn_id in cell.connections_:
            port_name = conn_id.str().lstrip("\\")
            # Get the connected signal
            sig = cell.connections_[conn_id]
            # Convert to string representation
            wire_name = self._sig_to_string(sig)
            connections[port_name] = wire_name
        return connections

    def _sig_to_string(self, sig: Any) -> str:
        """Convert a Yosys SigSpec to a string representation."""
        try:
            # Handle different signal types
            if sig.is_wire():
                return sig.as_wire().name.str().lstrip("\\")
            elif sig.is_fully_const():
                return str(sig.as_const())
            else:
                # Complex signal - just return a representation
                return f"[{len(sig)} bits]"
        except:
            return "[signal]"

    def get_module_count(self) -> int:
        """Get count of unique module types in hierarchy."""
        return len(self._visited)


def build_hierarchy(backend: Any, top_module: str, verbose: bool = False) -> Optional[HierarchyNode]:
    """Convenience function to build hierarchy tree.
    
    Args:
        backend: YosysBackend instance
        top_module: Top module name
        verbose: Enable verbose output
        
    Returns:
        Root HierarchyNode
    """
    builder = HierarchyBuilder(backend, verbose=verbose)
    return builder.build(top_module)
