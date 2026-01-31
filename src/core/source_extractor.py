"""Source code location extractor.

Extracts source file location information from Yosys RTLIL cells
using the 'src' attribute embedded by Yosys during parsing.
"""

import re
from typing import Dict, Optional, Any

from models import SourceLocation


class SourceExtractor:
    """Extracts source code locations from Yosys RTLIL.
    
    Yosys embeds source file information in cell attributes and cell names.
    This class extracts that information and builds a mapping from cell IDs
    to source locations.
    
    Example:
        extractor = SourceExtractor(module)
        locations = extractor.extract_all()
        for cell_id, loc in locations.items():
            print(f"Cell {cell_id}: {loc.short_display()}")
    """

    # Pattern for cell names with embedded path: $type$/path/file.v:line$id
    EMBEDDED_PATH_PATTERN = re.compile(
        r'\$\w+\$([^:]+\.v):(\d+)\$(\d+)$'
    )

    def __init__(self, module: Any, verbose: bool = False):
        """Initialize the source extractor.
        
        Args:
            module: Yosys Module object
            verbose: Enable verbose output
        """
        self._module = module
        self._verbose = verbose
        self._locations: Dict[str, SourceLocation] = {}

    def extract_all(self) -> Dict[str, SourceLocation]:
        """Extract source locations for all cells in the module.

        Returns:
            Dict mapping cell IDs to SourceLocation objects
        """
        self._locations.clear()

        total_cells = 0
        skipped_no_id = 0
        skipped_no_loc = 0

        for cell_id in self._module.cells_:
            total_cells += 1
            cell = self._module.cell(cell_id)
            cell_name = cell.name.str()

            # Extract numeric ID from cell name (e.g., "$add$file.v:10$123" -> "123")
            numeric_id = self._extract_numeric_id(cell_name)
            if not numeric_id:
                skipped_no_id += 1
                if self._verbose:
                    print(f"[WARN] Cell skipped (no numeric ID): {cell_name}")
                continue

            # Try to get location from attributes first
            location = self._get_location_from_attributes(cell)

            # Fall back to parsing cell name
            if not location:
                location = self._parse_location_from_name(cell_name)

            if location:
                self._locations[numeric_id] = location
            else:
                skipped_no_loc += 1
                if self._verbose:
                    print(f"[WARN] Cell skipped (no source location): {cell_name} (id={numeric_id})")

        if self._verbose:
            extracted = len(self._locations)
            print(f"[INFO] Source extraction: {extracted}/{total_cells} cells have locations "
                  f"(skipped: {skipped_no_id} no ID, {skipped_no_loc} no location)")

        return self._locations

    def _extract_numeric_id(self, cell_name: str) -> Optional[str]:
        """Extract numeric ID from cell name (the trailing $number)."""
        match = re.search(r'\$(\d+)$', cell_name)
        if match:
            return match.group(1)
        return None

    def _get_location_from_attributes(self, cell: Any) -> Optional[SourceLocation]:
        """Get location from cell's src attribute."""
        try:
            for attr_id in cell.attributes:
                attr_name = attr_id.str()
                if "src" in attr_name.lower():
                    src_value = str(cell.attributes[attr_id])
                    return SourceLocation.parse(src_value)
        except:
            pass
        return None

    def _parse_location_from_name(self, cell_name: str) -> Optional[SourceLocation]:
        """Parse location from cell name with embedded path."""
        match = self.EMBEDDED_PATH_PATTERN.search(cell_name)
        if match:
            return SourceLocation(
                file_path=match.group(1),
                start_line=int(match.group(2))
            )
        return None

    def get_location(self, cell_id: str) -> Optional[SourceLocation]:
        """Get location for a specific cell ID.
        
        Args:
            cell_id: Cell ID (numeric or with $ prefix)
            
        Returns:
            SourceLocation or None
        """
        # Normalize ID
        numeric_id = cell_id.lstrip('$')
        return self._locations.get(numeric_id)


def extract_module_locations(module: Any, verbose: bool = False) -> Dict[str, SourceLocation]:
    """Convenience function to extract all cell locations from a module.
    
    Args:
        module: Yosys Module object
        verbose: Enable verbose output
        
    Returns:
        Dict mapping cell IDs to SourceLocation objects
    """
    extractor = SourceExtractor(module, verbose=verbose)
    return extractor.extract_all()
