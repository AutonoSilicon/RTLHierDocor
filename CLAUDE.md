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

## Commands

```bash
# Full documentation generation (hierarchy tree + schematics + index)
python3 -m cli generate -f <filelist.f> -t <top_module> -o <output_dir> [--max-schematics N]

# Generate from cached RTLIL (skips parsing, faster)
python3 -m cli generate -r <cached.il> -t <top_module> -o <output_dir>

# Hierarchy tree only (ascii or json format)
python3 -m cli hierarchy -f <filelist.f> -t <top_module> --format ascii

# Single module schematic
python3 -m cli schematic -r <cached.il> -m <module_name> -o output.dot

# Convert DOT schematics to images (requires Graphviz)
dot -Tsvg schematics/module.dot -o module.svg
```

## Architecture

The CLI entry point is `src/cli.py`. The codebase follows a layered structure:

- **`src/models/`** — Data models: `SourceLocation`/`AggregatedLocation` (source_loc.py), `ModuleInfo`/`PortInfo`/`CellInfo` (module.py), `HierarchyNode` tree (hierarchy.py)
- **`src/core/`** — Processing: `YosysBackend` wraps pyosys for RTL loading, filelist parsing, and MD5-based RTLIL caching (yosys_backend.py); `HierarchyBuilder` constructs the module tree (hierarchy_builder.py); `SourceExtractor` extracts source file locations from RTLIL cells (source_extractor.py)
- **`src/schematic/`** — `SchematicGenerator` invokes Yosys `show` command to produce DOT schematics (generator.py)
- **`src/output/`** — `DocumentGenerator` orchestrates the full generation workflow: hierarchy tree → JSON/ASCII output → schematic generation → index (generator.py)

### Schematic Simplification Pipeline

1. Parse DOT output from Yosys (regex-based node/edge extraction)
2. Classify nodes by shape/style into PROC (sequential logic), I/O ports, and combinational logic
3. Group unassociated combinational logic by connectivity
4. Aggregate each group into a single `IN_COMB` node with source location ranges
5. Reconnect edges to preserve PROC and I/O boundary connections

### Caching

RTLIL parse results are cached under `.rtl_cache/` using MD5 hashes of filelist contents. Use `-r` flag to load from cache directly.

## Dependencies

- Python 3.8+
- Yosys via oss-cad-suite (provides pyosys)
- Graphviz (optional, for rendering DOT to PNG/SVG)
- Dev: pytest, pytest-cov (`pip install -e ".[dev]"`)

## Known Limitations

- Large modules (e.g., ct_ifu_lbuf) may fail due to recursion depth limits
- Parameterized modules (`$paramod`) are skipped by default
- Empty modules (no logic cells) do not generate schematics
