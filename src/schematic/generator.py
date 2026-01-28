"""Schematic generator for RTL modules.

Generates DOT format schematics using Yosys show command,
with optional simplification.
"""

from typing import Optional, Dict, Any, List
from pathlib import Path

from core.source_extractor import extract_module_locations


class SchematicGenerator:
    """Generates schematics for RTL modules.
    
    Uses Yosys to generate DOT format schematics.
    """

    def __init__(self, backend: Any, verbose: bool = False):
        """Initialize the schematic generator.
        
        Args:
            backend: YosysBackend instance
            verbose: Enable verbose output
        """
        self._backend = backend
        self._verbose = verbose

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
