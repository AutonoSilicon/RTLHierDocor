"""Schematic generator for RTL modules.

Generates DOT format schematics using Yosys show command,
with optional simplification.
"""

from typing import Optional, Dict, Any
from pathlib import Path

from ..core.source_extractor import extract_module_locations
from ..models import SourceLocation


class SchematicGenerator:
    """Generates schematics for RTL modules.
    
    Uses Yosys to generate DOT format schematics and optionally
    simplifies them using DotSimplifier.
    
    Example:
        gen = SchematicGenerator(backend)
        dot = gen.generate("ct_ifu_top", simplify=True)
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
        module_name: str,
        simplify: bool = True,
        include_locations: bool = True
    ) -> Optional[str]:
        """Generate DOT schematic for a module.
        
        Args:
            module_name: Name of the module
            simplify: Apply simplification to reduce complexity
            include_locations: Include source code locations in labels
            
        Returns:
            DOT content as string, or None if generation failed
        """
        # Generate raw DOT from Yosys
        dot_content = self._backend.generate_dot(module_name)
        if not dot_content:
            return None
        
        if not simplify:
            return dot_content
        
        # Get cell locations if requested
        cell_locations: Dict[str, SourceLocation] = {}
        if include_locations:
            module = self._backend.get_module(module_name)
            if module:
                cell_locations = extract_module_locations(module)
        
        # Apply simplification
        from .simplifier import DotParser, DotSimplifier
        
        parser = DotParser()
        parser.parse(dot_content)
        
        simplifier = DotSimplifier(parser, cell_locations=cell_locations)
        return simplifier.simplify()

    def generate_batch(
        self,
        module_names: list,
        output_dir: str,
        simplify: bool = True
    ) -> Dict[str, bool]:
        """Generate schematics for multiple modules.
        
        Args:
            module_names: List of module names
            output_dir: Directory to save DOT files
            simplify: Apply simplification
            
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
                dot = self.generate(module_name, simplify=simplify)
                if dot:
                    suffix = "_simplified" if simplify else ""
                    filepath = output_path / f"{clean_name}{suffix}.dot"
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
