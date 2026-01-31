"""Schematic generator for RTL modules.

Generates DOT format schematics using Yosys show command,
with optional simplification.
"""

from typing import Optional, Dict, Any, List, Set, Tuple
from pathlib import Path

from core.source_extractor import extract_module_locations
from schematic.simplifier import simplify_dot_content


class SchematicGenerator:
    """Generates schematics for RTL modules.

    Uses Yosys to generate DOT format schematics, with optional
    simplification that merges combinational logic into COMB nodes.
    """

    def __init__(self, backend: Any, verbose: bool = False,
                 simplify: bool = True, strategy: str = "proc_group",
                 remove_signals: Optional[List[str]] = None,
                 traced_signals: Optional[Dict[str, Set[str]]] = None):
        """Initialize the schematic generator.

        Args:
            backend: YosysBackend instance
            verbose: Enable verbose output
            simplify: Enable DOT simplification (default True)
            strategy: Simplification strategy - "proc_group" or "connected_component"
            remove_signals: Signal name patterns to remove before simplification
                (global patterns applied as fnmatch to all modules)
            traced_signals: Pre-computed per-module signal names from SignalTracer.
                Dict[module_name -> Set[port_names]]. These are exact port names
                resolved through the hierarchy, merged with pattern-matched names.
        """
        self._backend = backend
        self._verbose = verbose
        self._simplify = simplify
        self._strategy = strategy
        self._remove_signals = remove_signals or []
        self._traced_signals = traced_signals or {}

    def generate(
        self,
        module_name: str
    ) -> Optional[str]:
        """Generate DOT schematic for a module.

        Args:
            module_name: Name of the module

        Returns:
            DOT content as string, or None if generation failed
        """
        return self._backend.generate_dot(module_name)

    def generate_simplified(
        self,
        module_name: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Generate both raw and simplified DOT schematics for a module.

        Args:
            module_name: Name of the module

        Returns:
            Tuple of (raw_dot, simplified_dot). simplified_dot is None
            if simplification is disabled or raw_dot is None.
        """
        raw_dot = self._backend.generate_dot(module_name)
        if not raw_dot:
            return None, None

        if not self._simplify:
            return raw_dot, None

        # Extract cell locations for source annotations
        cell_locations = self._extract_cell_locations(module_name)

        # Build effective remove_signals for this module:
        # global patterns + traced per-module port names
        clean_name = module_name.lstrip("\\")
        effective_signals = list(self._remove_signals)
        if clean_name in self._traced_signals:
            for port_name in self._traced_signals[clean_name]:
                if port_name not in effective_signals:
                    effective_signals.append(port_name)

        simplified_dot = simplify_dot_content(
            raw_dot,
            strategy=self._strategy,
            cell_locations=cell_locations,
            remove_signals=effective_signals if effective_signals else None,
            verbose=self._verbose
        )

        return raw_dot, simplified_dot

    def _extract_cell_locations(self, module_name: str) -> Dict:
        """Extract cell source locations from the module via Yosys backend."""
        try:
            module = self._backend.get_module(module_name)
            if module:
                return extract_module_locations(module, verbose=self._verbose)
        except Exception:
            pass
        return {}

    def generate_batch(
        self,
        module_names: list,
        output_dir: str
    ) -> Dict[str, bool]:
        """Generate schematics for multiple modules.

        Args:
            module_names: List of module names
            output_dir: Directory to save DOT files

        Returns:
            Dict mapping module names to success status
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        results = {}
        total = len(module_names)

        for i, module_name in enumerate(module_names):
            clean_name = module_name.lstrip("\\")
            if self._verbose:
                print(f"[{i+1}/{total}] Generating: {clean_name}")

            try:
                dot = self.generate(module_name)
                if dot:
                    filepath = output_path / f"{clean_name}.dot"
                    with open(filepath, 'w') as f:
                        f.write(dot)
                    results[module_name] = True
                else:
                    results[module_name] = False
            except Exception as e:
                if self._verbose:
                    print(f"  Failed: {e}")
                results[module_name] = False

        return results
