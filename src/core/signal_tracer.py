"""Hierarchical signal tracer.

Traces signal names (e.g. reset, scan_en) from the top module through
the design hierarchy, resolving port-to-wire renaming at each level.
Produces a per-module set of port names that carry the traced signals.
"""

import fnmatch
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set


class SignalTracer:
    """Traces signal patterns through the module hierarchy.

    Starting from the top module, matches I/O port/wire names against
    fnmatch patterns (e.g. "*rst_b"). For each matched wire, follows
    it into child module instances via their port connections, discovering
    the renamed port name in the child. Recurses through the full tree.

    Result: a Dict[module_name -> Set[port_name]] that tells the
    simplifier which I/O ports to remove for each module's schematic.
    """

    def __init__(self, backend: Any, verbose: bool = False):
        self._backend = backend
        self._verbose = verbose

    def trace(self, top_module: str, patterns: List[str]) -> Dict[str, Set[str]]:
        """Trace signal patterns through the hierarchy from top_module.

        Args:
            top_module: Name of the top-level module
            patterns: fnmatch-style signal name patterns (e.g. ["*rst_b", "*scan_en*"])

        Returns:
            Dict mapping module_name -> set of port/wire names to remove
            in that module's schematic.
        """
        if not patterns:
            return {}

        result: Dict[str, Set[str]] = defaultdict(set)
        visited: Set[str] = set()

        self._trace_module(top_module, patterns, set(), result, visited)

        if self._verbose:
            total = sum(len(v) for v in result.values())
            print(f"[signal_tracer] Traced {total} signal ports "
                  f"across {len(result)} modules")

        return dict(result)

    def _trace_module(
        self,
        module_name: str,
        patterns: List[str],
        incoming_ports: Set[str],
        result: Dict[str, Set[str]],
        visited: Set[str]
    ) -> None:
        """Recursively trace signals through a module.

        Args:
            module_name: Current module type name
            patterns: Global fnmatch patterns
            incoming_ports: Port names on this module that carry traced signals
                (determined by parent's connection mapping)
            result: Accumulator for per-module port sets
            visited: Set of module names already processed (prevent infinite loops)
        """
        # Normalize name
        clean_name = module_name.lstrip("\\")

        if clean_name in visited:
            # Already traced this module type — just merge incoming_ports
            if incoming_ports:
                result[clean_name].update(incoming_ports)
            return

        visited.add(clean_name)

        module = self._backend.get_module(module_name)
        if not module:
            return

        # Step 1: Find all wire/port names in this module that match patterns
        # or that were passed in from the parent as incoming_ports.
        matched_wires: Set[str] = set(incoming_ports)

        # Match module's own port names against global patterns
        for port_id in module.ports:
            port_name = port_id.str().lstrip("\\")
            for pat in patterns:
                if fnmatch.fnmatch(port_name.lower(), pat.lower()):
                    matched_wires.add(port_name)
                    break

        # Also match internal wire names against patterns
        for wire_id in module.wires_:
            wire = module.wire(wire_id)
            wire_name = wire.name.str().lstrip("\\")
            for pat in patterns:
                if fnmatch.fnmatch(wire_name.lower(), pat.lower()):
                    matched_wires.add(wire_name)
                    break

        if not matched_wires:
            return

        # Record matched ports for this module
        result[clean_name].update(matched_wires)

        # Step 2: For each cell instance, check if any of the matched wires
        # are connected to its ports. If so, note the child's port name.
        for cell_id in module.cells_:
            cell = module.cell(cell_id)
            cell_type = cell.type.str()

            # Skip internal primitives
            if cell_type.startswith("$") and not cell_type.startswith("$paramod"):
                continue

            # Find which child ports receive matched wires
            child_incoming: Set[str] = set()
            for conn_id in cell.connections_:
                port_name = conn_id.str().lstrip("\\")
                sig = cell.connections_[conn_id]

                # Get the wire name connected to this port
                wire_name = self._sig_to_name(sig)
                if wire_name and wire_name in matched_wires:
                    child_incoming.add(port_name)

            if child_incoming:
                self._trace_module(cell_type, patterns, child_incoming, result, visited)

    def _sig_to_name(self, sig: Any) -> Optional[str]:
        """Extract wire name from a Yosys SigSpec."""
        try:
            if sig.is_wire():
                return sig.as_wire().name.str().lstrip("\\")
        except Exception:
            pass
        return None


def trace_remove_signals(
    backend: Any,
    top_module: str,
    patterns: List[str],
    verbose: bool = False
) -> Dict[str, Set[str]]:
    """Convenience function to trace remove_signals through the hierarchy.

    Args:
        backend: YosysBackend instance
        top_module: Top module name
        patterns: Signal name patterns to trace
        verbose: Enable verbose output

    Returns:
        Dict[module_name -> Set[port_names_to_remove]]
    """
    tracer = SignalTracer(backend, verbose=verbose)
    return tracer.trace(top_module, patterns)
