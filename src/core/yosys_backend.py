"""Yosys backend wrapper for RTL parsing and analysis.

This module provides a clean abstraction layer over pyosys (Yosys Python bindings),
handling RTL file loading, design elaboration, and caching.
"""

import os
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator, TYPE_CHECKING
from dataclasses import dataclass

try:
    from pyosys import libyosys as ys
    PYOSYS_AVAILABLE = True
except ImportError:
    ys = None  # type: ignore
    PYOSYS_AVAILABLE = False


@dataclass
class DesignStats:
    """Statistics about the loaded design."""
    module_count: int
    total_cells: int
    total_wires: int
    blackbox_count: int


class YosysBackend:
    """Wrapper for Yosys RTL parsing functionality.
    
    Provides a clean interface for:
    - Loading RTL files from a filelist
    - Elaborating designs with a top module
    - Caching RTLIL for faster subsequent loads
    - Iterating over modules, cells, and wires
    
    Example:
        backend = YosysBackend()
        backend.load_filelist("files.fl", top_module="top")
        for module in backend.iter_modules():
            print(module.name)
    """

    def __init__(self, cache_dir: str = ".rtl_cache", verbose: bool = False):
        """Initialize the Yosys backend.
        
        Args:
            cache_dir: Directory for caching RTLIL files
            verbose: Enable verbose output
        """
        if not PYOSYS_AVAILABLE:
            raise ImportError(
                "pyosys is not available. Please ensure Yosys is installed "
                "and the environment is properly configured."
            )
        
        self._cache_dir = Path(cache_dir)
        self._verbose = verbose
        self._design: Optional[ys.Design] = None
        self._filelist_path: Optional[str] = None
        self._top_module: Optional[str] = None

    @property
    def design(self) -> Optional[Any]:
        """Get the current Yosys design object."""
        return self._design

    @property
    def is_loaded(self) -> bool:
        """Check if a design is currently loaded."""
        return self._design is not None

    def load_filelist(
        self,
        filelist_path: str,
        top_module: str,
        include_dirs: Optional[List[str]] = None,
        defines: Optional[Dict[str, str]] = None,
        use_cache: bool = True
    ) -> bool:
        """Load RTL files from a filelist and elaborate the design.
        
        Args:
            filelist_path: Path to the filelist (one file per line)
            top_module: Name of the top-level module
            include_dirs: Additional include directories
            defines: Verilog defines as dict
            use_cache: Whether to use RTLIL caching
            
        Returns:
            True if loading succeeded
        """
        self._filelist_path = filelist_path
        self._top_module = top_module
        
        # Check for cached RTLIL
        cache_path = self._get_cache_path(filelist_path)
        if use_cache and cache_path.exists():
            if self._load_from_cache(cache_path):
                return True
        
        # Load from source files
        success = self._load_from_source(
            filelist_path, top_module, include_dirs, defines
        )
        
        # Save to cache if successful
        if success and use_cache:
            self._save_to_cache(cache_path)
        
        return success

    def load_rtlil(self, rtlil_path: str) -> bool:
        """Load design directly from an RTLIL file.
        
        Args:
            rtlil_path: Path to the RTLIL (.il) file
            
        Returns:
            True if loading succeeded
        """
        try:
            self._design = ys.Design()
            ys.run_pass(f"read_rtlil {rtlil_path}", self._design)
            if self._verbose:
                print(f"[INFO] Loaded RTLIL: {rtlil_path}")
            return True
        except Exception as e:
            if self._verbose:
                print(f"[ERROR] Failed to load RTLIL: {e}")
            self._design = None
            return False

    def _get_cache_path(self, filelist_path: str) -> Path:
        """Generate cache file path based on filelist hash."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Hash the filelist content
        with open(filelist_path, 'r') as f:
            content = f.read()
        hash_value = hashlib.md5(content.encode()).hexdigest()
        
        return self._cache_dir / f"{hash_value}.il"

    def _load_from_cache(self, cache_path: Path) -> bool:
        """Load design from cached RTLIL file."""
        try:
            self._design = ys.Design()
            ys.run_pass(f"read_rtlil {cache_path}", self._design)
            if self._verbose:
                print(f"[INFO] Loaded from cache: {cache_path}")
            return True
        except Exception as e:
            if self._verbose:
                print(f"[WARN] Cache load failed: {e}")
            self._design = None
            return False

    def _save_to_cache(self, cache_path: Path) -> None:
        """Save current design to RTLIL cache."""
        if not self._design:
            return
        try:
            ys.run_pass(f"write_rtlil {cache_path}", self._design)
            if self._verbose:
                print(f"[INFO] Saved to cache: {cache_path}")
        except Exception as e:
            if self._verbose:
                print(f"[WARN] Cache save failed: {e}")

    def _load_from_source(
        self,
        filelist_path: str,
        top_module: str,
        include_dirs: Optional[List[str]],
        defines: Optional[Dict[str, str]]
    ) -> bool:
        """Load design from Verilog source files."""
        try:
            self._design = ys.Design()
            
            # Parse filelist
            files = self._parse_filelist(filelist_path)
            if not files:
                raise ValueError(f"No files found in filelist: {filelist_path}")
            
            # Build read_verilog command
            cmd_parts = ["read_verilog -sv"]
            
            # Add include directories
            if include_dirs:
                for inc_dir in include_dirs:
                    cmd_parts.append(f"-I{inc_dir}")
            
            # Add defines
            if defines:
                for name, value in defines.items():
                    if value:
                        cmd_parts.append(f"-D{name}={value}")
                    else:
                        cmd_parts.append(f"-D{name}")
            
            # Add files
            cmd_parts.extend(files)
            
            # Execute read_verilog
            read_cmd = " ".join(cmd_parts)
            if self._verbose:
                print(f"[INFO] Loading {len(files)} Verilog files...")
            ys.run_pass(read_cmd, self._design)
            
            # Elaborate hierarchy
            if self._verbose:
                print(f"[INFO] Elaborating design with top: {top_module}")
            ys.run_pass(f"hierarchy -check -top {top_module}", self._design)
            
            # Run proc to convert processes to netlists
            # ys.run_pass("proc", self._design)
            
            return True
            
        except Exception as e:
            if self._verbose:
                print(f"[ERROR] Failed to load design: {e}")
            self._design = None
            return False

    def _parse_filelist(self, filelist_path: str) -> List[str]:
        """Parse a filelist and return list of absolute file paths."""
        files = []
        base_dir = Path(filelist_path).parent
        
        with open(filelist_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#') or line.startswith('//'):
                    continue
                # Handle -f include directive
                if line.startswith('-f '):
                    included_fl = line[3:].strip()
                    included_path = base_dir / included_fl
                    files.extend(self._parse_filelist(str(included_path)))
                    continue
                # Skip other flags
                if line.startswith('-'):
                    continue
                # Expand environment variables (e.g., ${CODE_BASE_PATH})
                line = os.path.expandvars(line)
                # Resolve relative paths
                if not os.path.isabs(line):
                    line = str(base_dir / line)
                files.append(line)
        
        return files

    def iter_modules(self, skip_parameterized: bool = False) -> Iterator[Any]:
        """Iterate over all modules in the design.
        
        Args:
            skip_parameterized: Skip $paramod modules if True
            
        Yields:
            Yosys Module objects
        """
        if not self._design:
            return
        
        for module_id in self._design.modules_:
            module = self._design.module(module_id)
            module_name = module.name.str()
            
            if skip_parameterized and module_name.startswith("$paramod"):
                continue
            
            yield module

    def get_module(self, module_name: str) -> Optional[Any]:
        """Get a specific module by name.
        
        Args:
            module_name: Module name (with or without leading backslash)
            
        Returns:
            Yosys Module object or None
        """
        if not self._design:
            return None
        
        # Try with backslash prefix
        if not module_name.startswith("\\"):
            module_name = "\\" + module_name
        
        try:
            return self._design.module(module_name)
        except:
            return None

    def get_stats(self) -> Optional[DesignStats]:
        """Get statistics about the loaded design."""
        if not self._design:
            return None
        
        module_count = 0
        total_cells = 0
        total_wires = 0
        blackbox_count = 0
        
        for module in self.iter_modules():
            module_count += 1
            total_cells += len(list(module.cells_))
            total_wires += len(list(module.wires_))
            if module.get_blackbox_attribute():
                blackbox_count += 1
        
        return DesignStats(
            module_count=module_count,
            total_cells=total_cells,
            total_wires=total_wires,
            blackbox_count=blackbox_count
        )

    def run_command(self, command: str) -> None:
        """Run a raw Yosys command on the design.
        
        Args:
            command: Yosys command string
        """
        if self._design:
            ys.run_pass(command, self._design)

    def generate_dot(self, module_name: str) -> Optional[str]:
        """Generate DOT schematic for a module.
        
        Args:
            module_name: Name of the module
            
        Returns:
            DOT content as string, or None if failed
        """
        if not self._design:
            return None
        
        import tempfile
        
        # Ensure module name has backslash
        if not module_name.startswith("\\"):
            module_name = "\\" + module_name
        
        # Ensure cache dir exists for temp files
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.dot', delete=False, dir=str(self._cache_dir)) as f:
                dot_path = f.name
            
            # Generate DOT using Yosys show command
            ys.run_pass(f"show -format dot -prefix {dot_path[:-4]} {module_name}", self._design)
            
            # Read and return DOT content
            with open(dot_path, 'r') as f:
                return f.read()
        except Exception as e:
            if self._verbose:
                print(f"[WARN] DOT generation failed for {module_name}: {e}")
            return None
        finally:
            # Cleanup temp file
            try:
                os.unlink(dot_path)
            except:
                pass
    
    def run_proc(self) -> bool:
        """Run 'proc' command on the entire design.
        
        This converts all processes (always blocks) to netlist elements
        (flip-flops, latches, multiplexers). This is a destructive operation
        that modifies the design in-place.
        
        Returns:
            True if successful
        """
        if not self._design:
            return False
        
        try:
            ys.run_pass("proc", self._design)
            if self._verbose:
                print("[INFO] Ran 'proc' command on entire design")
            return True
        except Exception as e:
            if self._verbose:
                print(f"[WARN] Failed to run 'proc': {e}")
            return False
