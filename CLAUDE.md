# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RTLHierDocor is a Python tool that generates hierarchical documentation for RTL (Register Transfer Level) designs using Yosys. It produces module hierarchy trees, simplified schematics with source code annotations, and JSON indexes. Built for the OpenC910 CPU design project.

## Environment Setup

**Must source before running any commands:**
```bash
source env.sh
```
This loads the oss-cad-suite toolchain (Yosys/pyosys), activates the Python venv, sets `PYTHONPATH` to `src/`, and exports `CODE_BASE_PATH` pointing to the C910 RTL source.

## Configuration

All parameters can be set in `config.yaml` (project root). CLI args override config values.

```yaml
design:
  filelist: "$CODE_BASE_PATH/gen_rtl/filelists/C910_asic_rtl.fl"
  top_module: "openC910"
output:
  dir: "./rtl_docs"
  cache_dir: ".rtl_cache"
  max_schematics: 0          # 0 = unlimited
simplify:
  enabled: true
  strategy: "proc_group"     # or "connected_component"
  remove_signals:            # fnmatch patterns, traced recursively through hierarchy
    - "cpurst_b"
    - "*rst_b"
    - "*scan_en*"
verbose: true
```

- Environment variables (e.g. `$CODE_BASE_PATH`) are expanded automatically
- `remove_signals` patterns are matched against I/O port names; the `SignalTracer` resolves them through the module hierarchy via port-to-wire connections, so renamed signals in child modules are also removed
- Config is parsed by a built-in minimal YAML parser (no pyyaml dependency)

## Commands

```bash
# Full documentation generation (hierarchy tree + schematics + index)
python3 -m cli generate -f <filelist.f> -t <top_module> -o <output_dir> [--max-schematics N]

# Generate from config.yaml (no CLI args needed if config is complete)
python3 -m cli generate

# Generate from cached RTLIL (skips parsing, faster)
python3 -m cli generate -r <cached.il> -t <top_module> -o <output_dir>

# Hierarchy tree only (ascii or json format)
python3 -m cli hierarchy -f <filelist.f> -t <top_module> --format ascii

# Single module schematic
python3 -m cli schematic -r <cached.il> -m <module_name> -o output.dot

# Disable simplification
python3 -m cli generate --no-simplify

# Convert DOT schematics to images (requires Graphviz)
dot -Tsvg schematics/module.dot -o module.svg
```

## Architecture

The CLI entry point is `src/cli.py`. The codebase follows a layered structure:

- **`src/models/`** — Data models: `SourceLocation`/`AggregatedLocation` (source_loc.py), `ModuleInfo`/`PortInfo`/`CellInfo` (module.py), `HierarchyNode` tree (hierarchy.py)
- **`src/core/`** — Processing:
  - `YosysBackend` — wraps pyosys for RTL loading, filelist parsing, and MD5-based RTLIL caching (yosys_backend.py)
  - `HierarchyBuilder` — constructs the module tree (hierarchy_builder.py)
  - `SourceExtractor` — extracts source file locations from RTLIL cells (source_extractor.py)
  - `ProjectConfig` — dataclass loaded from config.yaml, overridable by CLI args (config.py)
  - `SignalTracer` — recursively traces signal patterns through the module hierarchy via port-to-wire connections (signal_tracer.py)
- **`src/schematic/`** — DOT schematic generation and simplification:
  - `SchematicGenerator` — invokes Yosys `show` command to produce DOT schematics (generator.py)
  - `DotSimplifier` — parses DOT, removes signals, merges combinational logic into COMB nodes (simplifier.py)
- **`src/output/`** — `DocumentGenerator` orchestrates the full generation workflow: hierarchy tree → JSON/ASCII output → signal tracing → schematic generation → index (generator.py)

### Schematic Simplification Pipeline

1. Parse DOT output from Yosys (regex-based node/edge extraction in `DotParser`)
2. **Signal removal** (optional): match I/O ports against `remove_signals` patterns (including hierarchy-traced port names), cascade-remove auto-generated intermediate comb nodes (those without source code annotations), preserve user-written comb nodes
3. Classify nodes by shape/style into PROC (sequential logic), I/O ports, and combinational logic
4. Group combinational logic by PROC block association (`proc_group` strategy) or by connected component
5. Aggregate each group into a single `IN_COMB`/`OUT_COMB`/`COMB` node with source location ranges
6. Reconnect edges to preserve PROC and I/O boundary connections

### Signal Tracing

`SignalTracer` resolves `remove_signals` patterns across the module hierarchy:
1. Starting from the top module, match port/wire names against fnmatch patterns
2. For each cell instance, map parent wire names to child port names via cell connections
3. Recurse into child modules with the resolved port names
4. Result: `Dict[module_name -> Set[port_names]]` — per-module exact port names to remove

This ensures that a signal like `cpurst_b` at the top level is still removed in child modules even if the port is renamed (e.g., `rst_in`).

### Output Structure

```
output_dir/
  hierarchy_tree.txt       # ASCII module hierarchy
  hierarchy_tree.json      # JSON module hierarchy
  schematics/              # Raw DOT schematics (complete, unmodified)
  schematics/simplified/   # Simplified DOT schematics (signals removed, COMB merged)
  index.json               # Module index with cell/port info
```

### Caching

RTLIL parse results are cached under `.rtl_cache/` using MD5 hashes of filelist contents. Use `-r` flag to load from cache directly.

## Dependencies

- Python 3.8+
- Yosys via oss-cad-suite (provides pyosys)
- Graphviz (optional, for rendering DOT to PNG/SVG)
- Dev: pytest, pytest-cov (`pip install -e ".[dev]"`)

## Known Limitations

- Large modules (e.g., ct_ifu_lbuf) may fail due to recursion depth limits
- Parameterized modules (`$paramod`) may cause slow DOT generation
- Empty modules (no logic cells) do not generate schematics
