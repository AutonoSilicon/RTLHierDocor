"""Document output generator.

Generates hierarchy tree documentation and simplified schematics.
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from core import YosysBackend, HierarchyBuilder
from core.config import load_config, get_remove_signals, ProjectConfig
from core.signal_tracer import trace_remove_signals
from schematic import SchematicGenerator
from models import HierarchyNode


class DocumentGenerator:
    """Generates RTL hierarchy documentation.

    Coordinates the complete documentation generation workflow:
    1. Load RTL design via YosysBackend
    2. Build hierarchy tree
    3. Generate simplified schematics
    4. Output documentation files

    Example:
        gen = DocumentGenerator(
            filelist="files.fl",
            top_module="top",
            output_dir="docs"
        )
        gen.generate()
    """

    def __init__(
        self,
        filelist: Optional[str] = None,
        rtlil_path: Optional[str] = None,
        top_module: str = "",
        output_dir: str = "./rtl_docs",
        cache_dir: str = ".rtl_cache",
        verbose: bool = False,
        simplify: bool = True,
        strategy: str = "proc_group",
        config_path: Optional[str] = None,
        remove_signals: Optional[List[str]] = None
    ):
        """Initialize the document generator.

        Args:
            filelist: Path to RTL filelist (optional if rtlil_path provided)
            rtlil_path: Path to pre-compiled RTLIL file (alternative to filelist)
            top_module: Name of top-level module
            output_dir: Output directory for documentation
            cache_dir: Cache directory for RTLIL
            verbose: Enable verbose output
            simplify: Enable DOT simplification (default True)
            strategy: Simplification strategy - "proc_group" or "connected_component"
            config_path: Path to config.yaml (auto-detected if None)
            remove_signals: Signal patterns to remove (overrides config.yaml if set)
        """
        self.filelist = filelist
        self.rtlil_path = rtlil_path
        self.top_module = top_module
        self.output_dir = Path(output_dir)
        self.cache_dir = cache_dir
        self.verbose = verbose
        self.simplify = simplify
        self.strategy = strategy

        # Load remove_signals: explicit arg > config.yaml > empty
        if remove_signals is not None:
            self.remove_signals = remove_signals
        else:
            try:
                config = load_config(config_path)
                self.remove_signals = get_remove_signals(config)
            except Exception as e:
                if verbose:
                    print(f"[WARN] Failed to load config: {e}")
                self.remove_signals = []

        self._backend: Optional[YosysBackend] = None
        self._hierarchy: Optional[HierarchyNode] = None
        self._config_path = config_path
        self._stats = {
            "modules_total": 0,
            "schematics_generated": 0,
            "schematics_failed": 0,
            "schematics_simplified": 0,
            "schematics_afterproc": 0
        }

    @classmethod
    def from_config(cls, cfg: ProjectConfig) -> 'DocumentGenerator':
        """Create DocumentGenerator from a ProjectConfig instance.

        Args:
            cfg: ProjectConfig with all settings resolved

        Returns:
            DocumentGenerator instance
        """
        return cls(
            filelist=cfg.filelist,
            rtlil_path=cfg.rtlil,
            top_module=cfg.top_module,
            output_dir=cfg.output_dir,
            cache_dir=cfg.cache_dir,
            verbose=cfg.verbose,
            simplify=cfg.simplify,
            strategy=cfg.strategy,
            remove_signals=cfg.remove_signals,
        )

    def generate(self, max_schematics: int = 0) -> Dict[str, Any]:
        """Generate complete documentation.

        Args:
            max_schematics: Maximum number of schematics to generate (0 for all)

        Returns:
            Statistics dictionary
        """
        self._setup_output_dirs()

        # Step 1: Load design
        self._log("=" * 60)
        self._log("Step 1: Loading RTL design")
        self._log("=" * 60)
        if not self._load_design():
            return self._stats

        # Step 2: Build hierarchy
        self._log("\n" + "=" * 60)
        self._log("Step 2: Building hierarchy tree")
        self._log("=" * 60)
        self._build_hierarchy()

        # Step 3: Generate schematics
        self._log("\n" + "=" * 60)
        self._log("Step 3: Generating schematics")
        self._log("=" * 60)
        self._generate_schematics(max_schematics)

        # Step 4: Write index
        self._log("\n" + "=" * 60)
        self._log("Step 4: Writing index")
        self._log("=" * 60)
        self._write_index()

        # Summary
        self._log("\n" + "=" * 60)
        self._log("Generation complete!")
        self._log("=" * 60)
        self._log(f"Total modules: {self._stats['modules_total']}")
        self._log(f"Schematics generated: {self._stats['schematics_generated']}")
        self._log(f"Schematics simplified: {self._stats['schematics_simplified']}")
        self._log(f"Schematics afterproc: {self._stats['schematics_afterproc']}")
        self._log(f"Schematics failed: {self._stats['schematics_failed']}")
        self._log(f"Output directory: {self.output_dir}")

        return self._stats

    def _setup_output_dirs(self) -> None:
        """Create output directory structure."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "schematics").mkdir(exist_ok=True)
        (self.output_dir / "afterproc").mkdir(exist_ok=True)
        if self.simplify:
            (self.output_dir / "simplified").mkdir(exist_ok=True)

    def _load_design(self) -> bool:
        """Load RTL design using YosysBackend."""
        self._backend = YosysBackend(
            cache_dir=self.cache_dir,
            verbose=self.verbose
        )

        # Try RTLIL first if provided
        if self.rtlil_path:
            success = self._backend.load_rtlil(self.rtlil_path)
        elif self.filelist:
            success = self._backend.load_filelist(
                self.filelist,
                self.top_module,
                use_cache=True
            )
        else:
            self._log("[ERROR] No filelist or rtlil_path provided")
            return False

        if success:
            stats = self._backend.get_stats()
            if stats:
                self._stats["modules_total"] = stats.module_count
                self._log(f"Loaded {stats.module_count} modules")

        return success

    def _build_hierarchy(self) -> None:
        """Build and output hierarchy tree."""
        builder = HierarchyBuilder(self._backend, verbose=self.verbose)
        self._hierarchy = builder.build(self.top_module)

        if not self._hierarchy:
            self._log("[WARN] Failed to build hierarchy")
            return

        # Output ASCII tree
        ascii_path = self.output_dir / "hierarchy_tree.txt"
        with open(ascii_path, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("RTL Module Hierarchy Tree\n")
            f.write("=" * 80 + "\n\n")
            f.write(self._hierarchy.render_ascii())
        self._log(f"[OK] ASCII tree: {ascii_path}")

        # Output JSON tree
        json_path = self.output_dir / "hierarchy_tree.json"
        with open(json_path, 'w') as f:
            f.write(self._hierarchy.to_json(indent=2))
        self._log(f"[OK] JSON tree: {json_path}")

    def _generate_schematics(self, max_count: int) -> None:
        """Generate simplified schematics for modules."""
        if not self._backend:
            return

        # Get modules, prefer modules with more cells (more interesting)
        modules = []
        for module in self._backend.iter_modules(skip_parameterized=False):
            name = module.name.str()
            cell_count = len(list(module.cells_))
            # Skip very simple modules (likely blackboxes or empty wrappers)
            # But keep them if they are the top module
            if cell_count > 0 or name == self.top_module:
                modules.append((name, cell_count))

        # Sort by cell count (more complex modules first)
        modules.sort(key=lambda x: -x[1])
        modules = [m[0] for m in modules]

        self._log(f"Found {len(modules)} modules with logic")
        if self.simplify:
            self._log(f"Simplification: enabled (strategy: {self.strategy})")
            if self.remove_signals:
                self._log(f"Removing signals: {', '.join(self.remove_signals)}")
        else:
            self._log(f"Simplification: disabled")

        if max_count > 0 and len(modules) > max_count:
            self._log(f"[INFO] Limiting to first {max_count} modules")
            modules = modules[:max_count]

        # Trace remove_signals through the hierarchy to get per-module port names
        traced_signals = {}
        if self.remove_signals and self.top_module:
            try:
                traced_signals = trace_remove_signals(
                    self._backend, self.top_module,
                    self.remove_signals, verbose=self.verbose
                )
                if self.verbose and traced_signals:
                    total_ports = sum(len(v) for v in traced_signals.values())
                    self._log(f"[signal_tracer] Traced {total_ports} signal ports "
                              f"across {len(traced_signals)} modules")
            except Exception as e:
                self._log(f"[WARN] Signal tracing failed: {e}")

        generator = SchematicGenerator(
            self._backend,
            verbose=self.verbose,
            simplify=self.simplify,
            strategy=self.strategy,
            remove_signals=self.remove_signals,
            traced_signals=traced_signals
        )
        schematic_dir  = self.output_dir / "schematics"
        simplified_dir = self.output_dir / "simplified"
        afterproc_dir  = self.output_dir / "afterproc"

        # === Pass 1: Generate normal schematics (before proc) ===
        self._log("\n--- Generating normal schematics ---")
        for i, module_name in enumerate(modules):
            clean_name = module_name.lstrip("\\")
            self._log(f"[{i+1}/{len(modules)}] {clean_name}", end=" ")

            try:
                raw_dot, simplified_dot = generator.generate_simplified(module_name)

                if raw_dot and len(raw_dot) > 100:
                    # Save raw DOT
                    raw_filepath = schematic_dir / f"{clean_name}.dot"
                    with open(raw_filepath, 'w') as f:
                        f.write(raw_dot)
                    self._stats["schematics_generated"] += 1

                    # Save simplified DOT if available
                    if simplified_dot:
                        simp_filepath = simplified_dir / f"{clean_name}.dot"
                        with open(simp_filepath, 'w') as f:
                            f.write(simplified_dot)
                        self._stats["schematics_simplified"] += 1
                        self._log("- OK (simplified)")
                    else:
                        self._log("- OK")
                else:
                    self._stats["schematics_failed"] += 1
                    self._log("- skipped (empty)")
            except Exception as e:
                self._stats["schematics_failed"] += 1
                self._log(f"- failed: {e}")

        # === Pass 2: Generate afterproc schematics ===
        # Run proc on entire design (destructive, but we don't need original design anymore)
        self._log("\n--- Running 'proc' on entire design ---")
        if self._backend.run_proc():
            self._log("--- Generating afterproc schematics ---")
            for i, module_name in enumerate(modules):
                clean_name = module_name.lstrip("\\")
                self._log(f"[{i+1}/{len(modules)}] {clean_name} (afterproc)", end=" ")

                try:
                    # Generate from proc'd design (no simplification for afterproc)
                    proc_dot = generator.generate_from_proc_design(module_name)

                    if proc_dot and len(proc_dot) > 100:
                        proc_filepath = afterproc_dir / f"{clean_name}.dot"
                        with open(proc_filepath, 'w') as f:
                            f.write(proc_dot)
                        self._stats["schematics_afterproc"] += 1
                        self._log("- OK")
                    else:
                        self._log("- skipped (empty)")
                except Exception as e:
                    self._log(f"- failed: {e}")
        else:
            self._log("[WARN] Failed to run 'proc', skipping afterproc schematics")

    def _write_index(self) -> None:
        """Write index.json with metadata."""
        index = {
            "project": {
                "filelist": self.filelist,
                "top_module": self.top_module,
            },
            "stats": self._stats,
            "modules": []
        }

        # Add module list
        if self._backend:
            for module in self._backend.iter_modules(skip_parameterized=False):
                module_name = module.name.str().lstrip("\\")
                index["modules"].append({
                    "name": module_name,
                    "cell_count": len(list(module.cells_)),
                    "wire_count": len(list(module.wires_))
                })

        index_path = self.output_dir / "index.json"
        with open(index_path, 'w') as f:
            json.dump(index, f, indent=2)
        self._log(f"[OK] Index: {index_path}")

    def _log(self, message: str, end: str = "\n") -> None:
        """Print log message if verbose."""
        if self.verbose:
            print(message, end=end, flush=True)
