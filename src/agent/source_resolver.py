import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

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
