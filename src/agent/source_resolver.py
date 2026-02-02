import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from models import SourceLocation

class SourceResolver:
    """Resolves module names to source file paths and reads content."""

    def __init__(self, backend: Any, code_base_path: Optional[str] = None):
        """
        Args:
            backend: YosysBackend instance
            code_base_path: Optional base path for source files
        """
        self.backend = backend
        self.code_base_path = code_base_path
        self._path_cache: Dict[str, str] = {}

    def resolve_path(self, module_name: str) -> Optional[str]:
        """Resolve module name to a Verilog source file path."""
        # 1. Check cache
        if module_name in self._path_cache:
            return self._path_cache[module_name]

        # 2. Try to get module from backend
        module = self.backend.get_module(module_name)
        if module:
            # Try to get src attribute from module
            path = self._get_path_from_module_attr(module)
            if path:
                self._path_cache[module_name] = path
                return path

        # 3. Handle parameterized modules ($paramod)
        if module_name.startswith("$paramod"):
            # Format is usually $paramod\ORIG_NAME\PARAM1=VAL1...
            # Or $paramod$NAME
            match = re.search(r'\$paramod(?:\\[^\\]+|(?:\$[^$]+))?(\\?[a-zA-Z_][a-zA-Z0-9_]*)', module_name)
            if match:
                base_name = match.group(1)
                path = self.resolve_path(base_name)
                if path:
                    self._path_cache[module_name] = path
                    return path

        # 4. Fallback to glob searching if code_base_path is provided
        if self.code_base_path:
            clean_name = module_name.lstrip('\\')
            # Strip parameterization if still present
            clean_name = clean_name.split('$')[0]
            
            # Common patterns in C910 and similar RTL
            patterns = [
                f"**/{clean_name}.v",
                f"**/{clean_name}.sv"
            ]
            
            base = Path(self.code_base_path)
            for pattern in patterns:
                matches = list(base.glob(pattern))
                if matches:
                    path = str(matches[0].absolute())
                    self._path_cache[module_name] = path
                    return path

        return None

    def _get_path_from_module_attr(self, module: Any) -> Optional[str]:
        """Extract path from Yosys module src attribute."""
        try:
            for attr_id in module.attributes:
                attr_name = attr_id.str()
                if "src" in attr_name.lower():
                    src_value = str(module.attributes[attr_id])
                    # Format: "/path/to/file.v:line.col-line.col"
                    path = src_value.split(':')[0]
                    # Yosys sometimes uses relative paths, try to resolve
                    if not os.path.isabs(path) and self.code_base_path:
                        abs_path = os.path.join(self.code_base_path, path)
                        if os.path.exists(abs_path):
                            return abs_path
                    return path
        except:
            pass
        return None

    def read_source(self, module_name: str, max_lines: int = 2000) -> Optional[str]:
        """Read source code for a module."""
        path = self.resolve_path(module_name)
        if not path or not os.path.exists(path):
            return None

        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"\n... (truncated after {max_lines} lines)")
                        break
                    lines.append(line)
                return "".join(lines)
        except Exception as e:
            print(f"[ERROR] Failed to read source at {path}: {e}")
            return None

    def get_port_summary(self, module_name: str) -> str:
        """Get a summary of ports for the module."""
        module = self.backend.get_module(module_name)
        if not module:
            return "No port information available."

        ports = []
        for wire_id in module.wires_:
            wire = module.wire(wire_id)
            if wire.port_input or wire.port_output:
                dir_str = "input" if wire.port_input else "output"
                width = wire.width
                name = wire.name.str().lstrip('\\')
                ports.append(f"{dir_str} [{width-1}:0] {name}")
        
        if not ports:
            return "No ports defined."

        return "\n".join(ports)

    def read_source_lines(self, file_path: str, start_line: int, end_line: int) -> Optional[str]:
        """Read specific line range from a source file.

        Args:
            file_path: Path to the source file
            start_line: Starting line number (1-indexed, inclusive)
            end_line: Ending line number (1-indexed, inclusive)

        Returns:
            Source code string or None if read failed
        """
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = []
                for i, line in enumerate(f, start=1):
                    if i >= start_line and i <= end_line:
                        lines.append(line)
                    if i > end_line:
                        break
                return "".join(lines)
        except Exception as e:
            print(f"[ERROR] Failed to read lines {start_line}-{end_line} from {file_path}: {e}")
            return None

    def read_block_source(self, source_locations: List[SourceLocation]) -> str:
        """Aggregate source code from multiple SourceLocation objects.

        Groups by file and reads the appropriate line ranges, formatting
        with file names and line number annotations for clarity.

        Args:
            source_locations: List of SourceLocation objects

        Returns:
            Formatted source code string with file/line annotations
        """
        if not source_locations:
            return "// No source code available"

        # Group by file and aggregate line ranges
        file_ranges: Dict[str, List[Tuple[int, int]]] = {}
        for loc in source_locations:
            if loc.file_path not in file_ranges:
                file_ranges[loc.file_path] = []
            if loc.end_line:
                file_ranges[loc.file_path].append((loc.start_line, loc.end_line))
            else:
                file_ranges[loc.file_path].append((loc.start_line, loc.start_line))

        # Merge overlapping/adjacent ranges per file
        for file_path in file_ranges:
            ranges = sorted(file_ranges[file_path])
            merged = []
            for start, end in ranges:
                if merged and start <= merged[-1][1] + 1:
                    # Overlapping or adjacent, merge
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            file_ranges[file_path] = merged

        # Read and format source code
        parts = []
        for file_path, ranges in sorted(file_ranges.items()):
            filename = file_path.split('/')[-1] if '/' in file_path else file_path
            for start_line, end_line in ranges:
                code = self.read_source_lines(file_path, start_line, end_line)
                if code:
                    if start_line == end_line:
                        parts.append(f"// {filename}:{start_line}")
                    else:
                        parts.append(f"// {filename}:{start_line}-{end_line}")
                    parts.append(code)
                    if not code.endswith('\n'):
                        parts.append('\n')

        return "".join(parts) if parts else "// Source code not found"
