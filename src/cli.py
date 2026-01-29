#!/usr/bin/env python3
"""Command-line interface for RTL Hierarchy Documentor.

All parameters can be set in config.yaml and overridden via CLI flags.

Usage:
    python3 -m cli generate                         # uses config.yaml
    python3 -m cli generate -t other_top            # override top_module
    python3 -m cli generate -c project.yaml         # use custom config
    python3 -m cli hierarchy --format json
    python3 -m cli schematic -m ct_ifu_addrgen -o out.dot
"""

import argparse
import sys

from core.config import load_project_config, ProjectConfig


def _load_config(args) -> ProjectConfig:
    """Load config.yaml then override with CLI args."""
    config_path = getattr(args, 'config', None)
    try:
        cfg = load_project_config(config_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        cfg = ProjectConfig()

    cfg.override_from_args(args)
    return cfg


def cmd_generate(args):
    """Generate complete documentation."""
    from output import DocumentGenerator

    cfg = _load_config(args)

    errors = cfg.validate("generate")
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if cfg.verbose:
        print(f"[config] filelist:     {cfg.filelist}")
        print(f"[config] top_module:   {cfg.top_module}")
        print(f"[config] output_dir:   {cfg.output_dir}")
        print(f"[config] simplify:     {cfg.simplify} ({cfg.strategy})")
        if cfg.remove_signals:
            print(f"[config] remove:       {', '.join(cfg.remove_signals)}")

    generator = DocumentGenerator.from_config(cfg)
    stats = generator.generate(max_schematics=cfg.max_schematics)
    return 0 if stats else 1


def cmd_hierarchy(args):
    """Generate hierarchy tree only."""
    from core import YosysBackend, HierarchyBuilder

    cfg = _load_config(args)

    errors = cfg.validate("hierarchy")
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    backend = YosysBackend(cache_dir=cfg.cache_dir, verbose=cfg.verbose)

    if cfg.rtlil:
        success = backend.load_rtlil(cfg.rtlil)
    else:
        success = backend.load_filelist(cfg.filelist, cfg.top_module, use_cache=True)

    if not success:
        print("Failed to load design", file=sys.stderr)
        return 1

    builder = HierarchyBuilder(backend, verbose=cfg.verbose)
    hierarchy = builder.build(cfg.top_module)

    if not hierarchy:
        print("Failed to build hierarchy", file=sys.stderr)
        return 1

    fmt = getattr(args, 'format', 'ascii')
    if fmt == "ascii":
        show_ports = getattr(args, 'show_ports', False)
        max_depth = getattr(args, 'max_depth', None)
        print(hierarchy.render_ascii(show_ports=show_ports, max_depth=max_depth))
    elif fmt == "json":
        print(hierarchy.to_json())

    return 0


def cmd_schematic(args):
    """Generate schematic for a single module."""
    from core import YosysBackend
    from core.signal_tracer import trace_remove_signals
    from schematic import SchematicGenerator

    cfg = _load_config(args)

    module_name = getattr(args, 'module', None)
    if not module_name:
        print("Error: --module/-m is required for schematic command", file=sys.stderr)
        return 1

    errors = cfg.validate("schematic")
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    backend = YosysBackend(cache_dir=cfg.cache_dir, verbose=cfg.verbose)

    if cfg.rtlil:
        success = backend.load_rtlil(cfg.rtlil)
    else:
        success = backend.load_filelist(cfg.filelist, cfg.top_module, use_cache=True)

    if not success:
        print("Failed to load design", file=sys.stderr)
        return 1

    # Trace remove_signals through the hierarchy
    traced_signals = {}
    if cfg.remove_signals and cfg.top_module:
        try:
            traced_signals = trace_remove_signals(
                backend, cfg.top_module,
                cfg.remove_signals, verbose=cfg.verbose
            )
        except Exception as e:
            if cfg.verbose:
                print(f"[WARN] Signal tracing failed: {e}")

    generator = SchematicGenerator(
        backend,
        verbose=cfg.verbose,
        simplify=cfg.simplify,
        strategy=cfg.strategy,
        remove_signals=cfg.remove_signals,
        traced_signals=traced_signals
    )

    if cfg.simplify:
        raw_dot, simplified_dot = generator.generate_simplified(module_name)
        dot = simplified_dot or raw_dot
    else:
        dot = generator.generate(module_name)

    if not dot:
        print(f"Failed to generate schematic for {module_name}", file=sys.stderr)
        return 1

    output_path = getattr(args, 'output', None)
    if output_path:
        with open(output_path, 'w') as f:
            f.write(dot)
        print(f"Written to {output_path}")
    else:
        print(dot)

    return 0


def cmd_docor(args):
    """Generate AI-powered module documentation."""
    import asyncio
    from core import YosysBackend, HierarchyBuilder
    from agent import AgentDocGenerator, SourceResolver, get_llm_backend, ProgressTracker

    cfg = _load_config(args)

    errors = cfg.validate("docor")
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    backend = YosysBackend(cache_dir=cfg.cache_dir, verbose=cfg.verbose)
    if cfg.rtlil:
        success = backend.load_rtlil(cfg.rtlil)
    else:
        success = backend.load_filelist(cfg.filelist, cfg.top_module, use_cache=True)

    if not success:
        print("Failed to load design", file=sys.stderr)
        return 1

    builder = HierarchyBuilder(backend, verbose=cfg.verbose)
    hierarchy = builder.build(cfg.top_module)

    if not hierarchy:
        print("Failed to build hierarchy", file=sys.stderr)
        return 1

    # Initialize Agent components
    llm_config = {
        "backend": cfg.agent_backend,
        "model": cfg.agent_model,
        "api_key": cfg.agent_api_key,
        "base_url": cfg.agent_base_url,
        "thinking": cfg.agent_thinking
    }
    llm = get_llm_backend(llm_config)
    resolver = SourceResolver(backend, code_base_path=cfg.code_base_path)
    tracker = ProgressTracker(cfg.output_dir)
    
    if not cfg.resume:
        tracker.clear()

    generator = AgentDocGenerator(
        hierarchy=hierarchy,
        llm=llm,
        resolver=resolver,
        tracker=tracker,
        output_dir=cfg.output_dir,
        max_source_lines=cfg.max_source_lines,
        max_modules=cfg.max_modules
    )

    # Run async generator
    asyncio.run(generator.run())
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="RTL Hierarchy Documentor - Generate hierarchical documentation for RTL designs",
        epilog="All options can also be set in config.yaml (auto-detected or via -c)."
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("-c", "--config", default=None,
                        help="Path to config.yaml (auto-detected if not specified)")
    parser.add_argument("--cache-dir", default=None,
                        help="Cache directory (default: from config or .rtl_cache)")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # ── generate command ──
    gen_parser = subparsers.add_parser("generate",
        help="Generate complete documentation (hierarchy + schematics + index)")
    gen_parser.add_argument("-f", "--filelist",
                            help="RTL filelist path (overrides config)")
    gen_parser.add_argument("-r", "--rtlil",
                            help="Pre-compiled RTLIL file path (overrides config)")
    gen_parser.add_argument("-t", "--top",
                            help="Top module name (overrides config)")
    gen_parser.add_argument("-o", "--output", default=None,
                            help="Output directory (overrides config)")
    gen_parser.add_argument("--max-schematics", type=int, default=None,
                            help="Max schematics to generate, 0=all (overrides config)")
    gen_parser.add_argument("--no-simplify", action="store_true",
                            help="Disable DOT schematic simplification")
    gen_parser.add_argument("--strategy",
                            choices=["proc_group", "connected_component"],
                            default=None,
                            help="Simplification strategy (overrides config)")

    # ── hierarchy command ──
    hier_parser = subparsers.add_parser("hierarchy",
        help="Generate hierarchy tree only")
    hier_parser.add_argument("-f", "--filelist",
                             help="RTL filelist path (overrides config)")
    hier_parser.add_argument("-r", "--rtlil",
                             help="Pre-compiled RTLIL file path (overrides config)")
    hier_parser.add_argument("-t", "--top",
                             help="Top module name (overrides config)")
    hier_parser.add_argument("--format", choices=["ascii", "json"],
                             default="ascii", help="Output format")
    hier_parser.add_argument("--show-ports", action="store_true",
                             help="Show port connections in ASCII tree")
    hier_parser.add_argument("--max-depth", type=int,
                             help="Maximum depth to display")

    # ── schematic command ──
    sch_parser = subparsers.add_parser("schematic",
        help="Generate schematic for a single module")
    sch_parser.add_argument("-f", "--filelist",
                            help="RTL filelist path (overrides config)")
    sch_parser.add_argument("-r", "--rtlil",
                            help="Pre-compiled RTLIL file path (overrides config)")
    sch_parser.add_argument("-t", "--top",
                            help="Top module name (overrides config)")
    sch_parser.add_argument("-m", "--module", required=True,
                            help="Module to generate schematic for")
    sch_parser.add_argument("-o", "--output",
                            help="Output DOT file path (stdout if omitted)")
    sch_parser.add_argument("--no-simplify", action="store_true",
                            help="Disable DOT schematic simplification")
    sch_parser.add_argument("--strategy",
                            choices=["proc_group", "connected_component"],
                            default=None,
                            help="Simplification strategy (overrides config)")

    # ── docor command ──
    doc_parser = subparsers.add_parser("docor",
        help="Generate AI-powered hierarchical module documentation")
    doc_parser.add_argument("-f", "--filelist",
                            help="RTL filelist path (overrides config)")
    doc_parser.add_argument("-r", "--rtlil",
                            help="Pre-compiled RTLIL file path (overrides config)")
    doc_parser.add_argument("-t", "--top",
                            help="Top module name (overrides config)")
    doc_parser.add_argument("-o", "--output",
                            help="Output directory (overrides config)")
    doc_parser.add_argument("--backend", choices=["anthropic", "openai", "agent_sdk"],
                            help="LLM backend (overrides config)")
    doc_parser.add_argument("--model",
                            help="LLM model (overrides config)")
    doc_parser.add_argument("--base-url",
                            help="LLM API base URL (overrides config)")
    doc_parser.add_argument("--thinking", action="store_true",
                            help="Enable thinking/reasoning mode (for supported models)")
    doc_parser.add_argument("--max-source-lines", type=int,
                            help="Truncate source files after N lines")
    doc_parser.add_argument("--no-resume", action="store_true",
                            help="Ignore progress and start from scratch")
    doc_parser.add_argument("--max-modules", type=int,
                            help="Limit number of modules to process (for debugging)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "generate":
        return cmd_generate(args)
    elif args.command == "hierarchy":
        return cmd_hierarchy(args)
    elif args.command == "schematic":
        return cmd_schematic(args)
    elif args.command == "docor":
        return cmd_docor(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
