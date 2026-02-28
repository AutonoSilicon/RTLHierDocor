# RTLHierDocor - RTL Hierarchy Documentor

## Project Overview

RTLHierDocor is a Python tool for generating hierarchical documentation for RTL (Register Transfer Level) designs using Yosys. It produces module hierarchy trees, simplified schematics with source code annotations, and AI-powered module documentation. Originally built for the OpenC910 CPU design project.

**Key Features:**
- Hierarchical module tree extraction from RTL designs
- DOT schematic generation with optional simplification (combines combinational logic)
- Signal tracing through module hierarchy for intelligent filtering
- Source code location annotations for PROC blocks and logic
- AI-powered documentation generation using LLM backends (Anthropic/OpenAI)

## Technology Stack

- **Language**: Python 3.8+
- **RTL Parsing**: Yosys with pyosys Python bindings
- **EDA Toolchain**: oss-cad-suite (provides Yosys and related tools)
- **Schematic Rendering**: Graphviz (optional, for DOT to PNG/SVG conversion)
- **Configuration**: YAML (custom minimal parser, no pyyaml dependency)

## Project Structure

```
src/
├── cli.py                    # CLI entry point with 4 commands: generate, hierarchy, schematic, docor
├── core/                     # Core processing modules
│   ├── yosys_backend.py      # Yosys/pyosys wrapper, RTL loading, caching
│   ├── hierarchy_builder.py  # Builds HierarchyNode tree from design
│   ├── source_extractor.py   # Extracts source file locations from RTLIL
│   ├── signal_tracer.py      # Traces signals through hierarchy
│   └── config.py             # ProjectConfig dataclass, YAML parsing
├── models/                   # Data models
│   ├── source_loc.py         # SourceLocation, AggregatedLocation
│   ├── module.py             # ModuleInfo, PortInfo, CellInfo
│   └── hierarchy.py          # HierarchyNode tree structure
├── schematic/                # Schematic generation
│   ├── generator.py          # SchematicGenerator using Yosys show
│   └── simplifier.py         # DotSimplifier, DOT parsing and simplification
├── output/                   # Output generation
│   └── generator.py          # DocumentGenerator orchestration
└── agent/                    # AI-powered documentation
    ├── doc_generator.py      # AgentDocGenerator multi-pass workflow
    ├── llm_backend.py        # LLM backend abstraction (Anthropic/OpenAI)
    ├── source_resolver.py    # Source file resolution for LLM context
    ├── progress_tracker.py   # Resume-capable progress tracking
    ├── block_doc_generator.py # Block-level documentation
    └── prompts/              # LLM prompt templates
```

## Environment Setup

**MUST run before any commands:**
```bash
source env.sh
```

This script:
- Activates the Python 3.10 virtual environment (`.venv`)
- Adds oss-cad-suite binaries to PATH
- Sets `LD_LIBRARY_PATH` for Yosys libraries
- Sets `PYTHONPATH` to include `src/`
- Exports `CODE_BASE_PATH` pointing to target RTL source

## Build and Run Commands

### Core Commands

```bash
# Generate complete documentation (hierarchy + schematics + index)
python3 -m cli generate -f <filelist.f> -t <top_module> -o <output_dir> [--max-schematics N]

# Generate from config.yaml (no CLI args needed if config is complete)
python3 -m cli generate

# Generate from cached RTLIL (skips parsing, faster)
python3 -m cli generate -r <cached.il> -t <top_module> -o <output_dir>

# Hierarchy tree only (ascii or json format)
python3 -m cli hierarchy -f <filelist.f> -t <top_module> --format ascii

# Single module schematic
python3 -m cli schematic -r <cached.il> -m <module_name> -o output.dot

# Connectivity/path condition check on after-proc graph
python3 -m cli connectivity -m <module_name> --from-signal <src_sig> --to-signal <dst_sig> [-o out.json]

# AI-powered documentation generation
python3 -m cli docor -f <filelist.f> -t <top_module> -o <output_dir>
```

### Utility Scripts

```bash
# Convert DOT files to PNG (batch or single file)
./dot2png.sh <directory>     # batch convert all .dot files
./dot2png.sh input.dot [output.png]  # single file
```

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
  remove_signals:            # fnmatch patterns, traced recursively
    - "cpurst_b"
    - "*rst_b"
    - "*scan_en*"
agent:
  backend: "openai"          # anthropic | openai | agent_sdk
  model: "glm-5"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  thinking: true
  skip_modules:
    - "ct_had*"
  resume: true
verbose: true
```

Environment variables (e.g., `$CODE_BASE_PATH`) are expanded automatically.

## Code Style Guidelines

- **Type hints**: Use Python 3.8+ type hints (`typing.List`, `typing.Dict`, `typing.Optional`)
- **Docstrings**: Google-style docstrings with Args/Returns sections
- **Naming**: 
  - Classes: `PascalCase`
  - Functions/variables: `snake_case`
  - Constants: `UPPER_CASE`
- **Imports**: Group as: stdlib, third-party, local (with absolute imports from `src/` root)
- **Error handling**: Use verbose-aware error messages; print warnings with `[WARN]` prefix

## Architecture Details

### Schematic Simplification Pipeline

1. Parse DOT output from Yosys (regex-based in `DotParser`)
2. **Signal removal** (optional): Match I/O ports against `remove_signals` patterns
3. Classify nodes: PROC (sequential), I/O ports, combinational logic
4. Group combinational logic by PROC association (`proc_group`) or connected component
5. Aggregate into `IN_COMB`/`OUT_COMB`/`COMB` nodes with source location ranges
6. Reconnect edges preserving PROC and I/O boundaries

### Signal Tracing

`SignalTracer` resolves `remove_signals` patterns across hierarchy:
1. Start from top module, match port/wire names against fnmatch patterns
2. For each cell, map parent wire names to child port names via connections
3. Recurse into child modules with resolved port names
4. Result: `Dict[module_name -> Set[port_names]]` for exact per-module removal

### AI Documentation Workflow (docor command)

Multi-pass process in `AgentDocGenerator`:
- **Pass 0**: Precompute simplified graphs for all modules
- **Pass 1**: Top-down preview generation (context from parent modules)
- **Pass 1.5**: Block-level documentation for large blocks
- **Pass 2.1**: Design highlights extraction
- **Pass 2.2**: Behavior flowchart generation
- **Pass 2.3**: Interface specification generation
- **Pass 2**: Bottom-up synthesis documentation

### Connectivity / Pathcheck Notes

- `connectivity` uses the after-proc DOT graph and checks directed data-path reachability.
- BFS excludes control-only edges to avoid false data connectivity:
  - mux select pins (`S/SEL/S*`)
  - sequential control pins (`CLK/reset/EN/CE/LOAD/GATE` family)
- `conditions` currently reports:
  - mux select conditions (`$mux/$pmux/$procmux`, select pins)
  - sequential data-enable conditions only (`EN/CE/LOAD/GATE`)
  - clock/reset are not emitted as condition points
- `conditions[*].source_locations` are attached from decoded Yosys `src` attributes, with proc-log fallback when needed.

## Output Structure

```
output_dir/
├── hierarchy_tree.txt        # ASCII module hierarchy
├── hierarchy_tree.json       # JSON module hierarchy
├── schematics/               # Raw DOT schematics (complete)
├── simplified/               # Simplified DOT schematics
├── index.json                # Module index with cell/port info
└── modules/                  # AI-generated docs (docor command)
    └── {module_name}.md
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

## Known Limitations

- Large modules (e.g., `ct_ifu_lbuf`) may fail due to recursion depth limits
- Parameterized modules (`$paramod`) may cause slow DOT generation
- Empty modules (no logic cells) do not generate schematics

## Dependencies

### Required
- Python 3.8+
- Yosys (via oss-cad-suite, provides pyosys)

### Optional
- Graphviz (for rendering DOT to PNG/SVG)
- anthropic>=0.3.0 (for agent docor command)

## Development Workflow

1. Source environment: `source env.sh`
2. Make changes to source files
3. Test with: `python3 -m cli generate -f <filelist> -t <top> -o test_out/`
4. Check outputs in `test_out/` directory

## Security Considerations

- API keys for LLM backends should be set via environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) rather than committed to config.yaml
- The `config.yaml` contains example API keys that should not be used in production
- Signal patterns in `remove_signals` use fnmatch which does not support regex special characters escaping
