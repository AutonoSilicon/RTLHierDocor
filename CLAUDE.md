# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RTLHierDocor is a Python tool that generates hierarchical documentation for RTL (Register Transfer Level) designs using Yosys. It produces module hierarchy trees, simplified schematics with source code annotations, and AI-powered module documentation. Originally built for the OpenC910 CPU design project.

**Key Features:**
- Hierarchical module tree extraction from RTL designs
- DOT schematic generation with optional simplification (combines combinational logic)
- Signal tracing through module hierarchy for intelligent filtering
- Source code location annotations for PROC blocks and logic
- AI-powered documentation generation using LLM backends (Anthropic/OpenAI)

## Environment Setup

**Must source before running any commands:**
```bash
source env.sh
```

This script:
- Activates the Python 3.10 virtual environment (`.venv`)
- Adds oss-cad-suite binaries to PATH
- Sets `LD_LIBRARY_PATH` for Yosys libraries
- Sets `PYTHONPATH` to include `src/`
- Exports `CODE_BASE_PATH` pointing to target RTL source

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
agent:
  backend: "openai"          # anthropic | openai | agent_sdk
  model: "glm-5"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key: "sk-..."          # optional, falls back to env var
  thinking: true
  skip_modules:
    - "ct_had*"
  max_source_lines: 10000
  max_modules: 0             # 0 = unlimited, limit for testing
  resume: true
  code_base_path: "$CODE_BASE_PATH"  # for source file resolution
  block_doc_threshold: 64    # min lines for block-level LLM call
verbose: true
```

- Environment variables (e.g., `$CODE_BASE_PATH`) are expanded automatically
- `remove_signals` patterns are matched against I/O port names; the `SignalTracer` resolves them through the module hierarchy via port-to-wire connections, so renamed signals in child modules are also removed
- Config is parsed by a built-in minimal YAML parser (no pyyaml dependency)

## Commands

### Core Commands

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

# Connectivity/path condition check (after-proc graph)
python3 -m cli connectivity -m <module_name> --from-signal <src_sig> --to-signal <dst_sig> [-o out.json]

# AI-powered documentation generation
python3 -m cli docor -f <filelist.f> -t <top_module> -o <output_dir>
```

### Utility Scripts

```bash
# Convert DOT files to PNG/SVG (batch or single file)
./dot2png.sh <directory>     # batch convert all .dot files
./dot2png.sh input.dot [output.png]  # single file
```

### Connectivity Check Options

```bash
# Path visualization modes
python3 -m cli connectivity -m <module> --from-signal <src> --to-signal <dst> \
    --path-overlay-mode simplified        # simplified view

python3 -m cli connectivity -m <module> --from-signal <src> --to-signal <dst> \
    --path-overlay-mode simplified-expand # with COMB node expansion
```

## Architecture

The CLI entry point is `src/cli.py`. The codebase follows a layered structure:

### Core Modules (`src/core/`)

- **`YosysBackend`** — wraps pyosys for RTL loading, filelist parsing, and MD5-based RTLIL caching (yosys_backend.py)
- **`HierarchyBuilder`** — constructs the module tree (hierarchy_builder.py)
- **`SourceExtractor`** — extracts source file locations from RTLIL cells (source_extractor.py)
- **`ProjectConfig`** — dataclass loaded from config.yaml, overridable by CLI args (config.py)
- **`SignalTracer`** — recursively traces signal patterns through the module hierarchy via port-to-wire connections (signal_tracer.py)

### Data Models (`src/models/`)

- **`SourceLocation`** / **`AggregatedLocation`** — source code position tracking (source_loc.py)
- **`ModuleInfo`** / **`PortInfo`** / **`CellInfo`** — RTL module metadata (module.py)
- **`HierarchyNode`** — tree structure representing module hierarchy (hierarchy.py)

### Schematic Generation (`src/schematic/`)

- **`SchematicGenerator`** — invokes Yosys `show` command to produce DOT schematics (generator.py)
- **`DotSimplifier`** — parses DOT, removes signals, merges combinational logic into COMB nodes (simplifier.py)

### AI Documentation (`src/agent/`)

- **`AgentDocGenerator`** — orchestrates multi-pass LLM-based documentation (doc_generator.py)
- **`LLMBackend`** — abstraction for Anthropic/OpenAI APIs (llm_backend.py)
- **`SourceResolver`** — resolves source files for LLM context (source_resolver.py)
- **`ProgressTracker`** — resume-capable progress tracking with content hash-based change detection (progress_tracker.py)
- **`BlockDocGenerator`** — generates block-level documentation for large modules (block_doc_generator.py)
- **`PassRunner`** — manages parallel execution of documentation passes (pass_runner.py)
- **`prompts/`** — LLM prompt templates organized by pass (pass1_preview.py, pass2_*.py, block_level.py)

### Output Generation (`src/output/`)

- **`DocumentGenerator`** — orchestrates the full generation workflow: hierarchy tree → JSON/ASCII output → signal tracing → schematic generation → index (generator.py)

## Schematic Simplification Pipeline

1. Parse DOT output from Yosys (regex-based node/edge extraction in `DotParser`)
2. **Signal removal** (optional): match I/O ports against `remove_signals` patterns (including hierarchy-traced port names), cascade-remove auto-generated intermediate comb nodes (those without source code annotations), preserve user-written comb nodes
3. Classify nodes by shape/style into PROC (sequential logic), I/O ports, and combinational logic
4. Group combinational logic by PROC block association (`proc_group` strategy) or by connected component
5. Aggregate each group into a single `IN_COMB`/`OUT_COMB`/`COMB` node with source location ranges
6. Reconnect edges to preserve PROC and I/O boundary connections

## Signal Tracing

`SignalTracer` resolves `remove_signals` patterns across the module hierarchy:
1. Starting from the top module, match port/wire names against fnmatch patterns
2. For each cell instance, map parent wire names to child port names via cell connections
3. Recurse into child modules with the resolved port names
4. Result: `Dict[module_name -> Set[port_names]]` — per-module exact port names to remove

This ensures that a signal like `cpurst_b` at the top level is still removed in child modules even if the port is renamed (e.g., `rst_in`).

## AI Documentation Workflow (docor command)

Multi-pass process in `AgentDocGenerator`:
- **Pass 0**: Precompute simplified graphs for all modules (cached for LLM context)
- **Pass 1**: Top-down preview generation (context from parent modules)
- **Pass 1.5**: Block-level documentation for large blocks (threshold: `block_doc_threshold` lines)
  - For PROC/COMB blocks exceeding the line threshold, generates dedicated functional analysis
  - Referenced by downstream passes for complex logic understanding
- **Pass 2.1**: Design highlights extraction
- **Pass 2.2**: Behavior flowchart generation (Mermaid)
- **Pass 2.3**: Interface specification generation
- **Pass 2.4**: Functional detailed description (with anti-hallucination constraints)
- **Pass 2.5**: Register description
- **Pass 2.6**: Timing constraints and CDC documentation
- **Pass 2.7**: Architecture design documentation
- **Pass 2**: Bottom-up synthesis documentation (combines all sub-pass outputs)

Pass 2.1 through Pass 2.7 run in parallel (they only depend on Pass 1 and Pass 1.5).

The workflow is resume-capable via `ProgressTracker` with content hash-based change detection. Each pass result is stored with an MD5 hash; if the input hasn't changed, the pass is skipped on resume.

### Pass 2.4 Anti-Hallucination Constraints

The functional description pass includes strict constraints to prevent LLM hallucination:
- **Only write what exists**: Don't fabricate FIFO, timeout, exception, or power management logic if not present in source
- **FSM only with explicit evidence**: Detailed FSM analysis only when explicit state registers and case statements are found
- **Traceable values**: All bit widths, constants, and table sizes must be directly traceable to code
- **Causal relationships from assignments**: Connections must be based on actual `assign`/`if`/`case` conditions, not signal name similarity

## Connectivity / Pathcheck

- Uses after-proc DOT netlist graph and checks directed data-path reachability.
- Connectivity BFS filters control-only edges:
  - mux select pins (`S/SEL/S*`)
  - sequential control pins (`CLK/reset/EN/CE/LOAD/GATE` family)
- Condition-point extraction semantics:
  - mux: report select conditions
  - sequential: report data-enable conditions only (`EN/CE/LOAD/GATE`)
  - `CLK/reset` are intentionally excluded from condition points
- Source locations on condition points come from decoded Yosys `src` attributes (binary-encoded strings are decoded), with proc-log fallback.

## Output Structure

```
output_dir/
├── hierarchy_tree.txt       # ASCII module hierarchy
├── hierarchy_tree.json      # JSON module hierarchy
├── schematics/              # Raw DOT schematics (complete, unmodified)
├── simplified/              # Simplified DOT schematics (signals removed, COMB merged)
├── index.json               # Module index with cell/port info
├── modules/                 # AI-generated docs (docor command)
│   └── {module_name}/
│       ├── description.md
│       ├── preview.md
│       ├── block_docs.md
│       ├── design_highlights.md
│       ├── flowchart.mmd
│       ├── interface_spec.md
│       ├── functional_desc.md
│       ├── register_desc.md
│       ├── timing_cdc.md      # Pass 2.6: Timing constraints and CDC
│       ├── architecture.md    # Pass 2.7: Architecture design
│       └── metadata.json
└── debug/                   # Debug logs and token statistics
```

## Caching

RTLIL parse results are cached under `.rtl_cache/` using MD5 hashes of filelist contents. Use `-r` flag to load from cache directly.

## Testing

No dedicated test suite in the project. Testing is done via:
1. Manual CLI command execution
2. Running against actual RTL designs (OpenC910)
3. Verifying output files (DOT, JSON, ASCII tree)

To install dev dependencies:
```bash
pip install -e ".[dev]"  # installs pytest, pytest-cov
```

## Dependencies

- Python 3.8+
- Yosys via oss-cad-suite (provides pyosys)
- Graphviz (optional, for rendering DOT to PNG/SVG)
- anthropic>=0.3.0 (optional, for agent docor command with Anthropic backend)
- openai (optional, for agent docor command with OpenAI-compatible backends)

## Known Limitations

- Large modules (e.g., `ct_ifu_lbuf`) may fail due to recursion depth limits
- Parameterized modules (`$paramod`) may cause slow DOT generation
- Empty modules (no logic cells) do not generate schematics
