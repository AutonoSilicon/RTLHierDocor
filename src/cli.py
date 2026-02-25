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
import json
import os
import re
import sys
from contextlib import contextmanager
from tempfile import NamedTemporaryFile
from pathlib import Path

from core.config import load_project_config, ProjectConfig
from models import SourceLocation


@contextmanager
def _suppress_stdout_stderr():
    """Suppress native stdout/stderr (including C/C++ extension output)."""
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull)


def _run_proc_with_log_capture(backend):
    """Run proc pass and capture raw native logs from Yosys."""
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    with NamedTemporaryFile(mode='w+', delete=True) as tmp:
        tmp_fd = tmp.fileno()
        try:
            os.dup2(tmp_fd, 1)
            os.dup2(tmp_fd, 2)
            ok = backend.run_proc()
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)

        tmp.flush()
        tmp.seek(0)
        text = tmp.read()
    return ok, text


def _parse_proc_cell_source_map(proc_log_text: str):
    """Parse proc logs to map generated procdff cell IDs to source location."""
    signal_re = re.compile(
        r"Creating register for signal `\\([^.]*)\.\\([^']+)' using process `\\[^.]*\.\$proc\$(.+?):(\d+)\$(\d+)'."
    )
    cell_re = re.compile(r"created \$[a-z]+ cell `\$procdff\$(\d+)'", re.IGNORECASE)

    result = {}
    pending = None

    for raw_line in proc_log_text.splitlines():
        line = raw_line.strip()
        sig_match = signal_re.search(line)
        if sig_match:
            file_path = sig_match.group(3)
            line_num = int(sig_match.group(4))
            pending = SourceLocation(file_path=file_path, start_line=line_num)
            continue

        cell_match = cell_re.search(line)
        if cell_match and pending is not None:
            result[cell_match.group(1)] = pending
            pending = None

    return result


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
        if not cfg.filelist:
            print("Error: filelist is required when rtlil is not provided", file=sys.stderr)
            return 1
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
        if not cfg.filelist:
            print("Error: filelist is required when rtlil is not provided", file=sys.stderr)
            return 1
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
    from core import YosysBackend, HierarchyBuilder, SignalTracer
    from agent import AgentDocGenerator, SourceResolver, get_llm_backend, ProgressTracker
    from schematic import SchematicGenerator

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
        if not cfg.filelist:
            print("Error: filelist is required when rtlil is not provided", file=sys.stderr)
            return 1
        success = backend.load_filelist(cfg.filelist, cfg.top_module, use_cache=True)

    if not success:
        print("Failed to load design", file=sys.stderr)
        return 1

    builder = HierarchyBuilder(backend, verbose=cfg.verbose)
    hierarchy = builder.build(cfg.top_module)

    if not hierarchy:
        print("Failed to build hierarchy", file=sys.stderr)
        return 1

    # Initialize Signal Tracer (for schematic generation)
    traced_signals = {}
    if cfg.simplify and cfg.remove_signals:
        tracer = SignalTracer(backend, verbose=cfg.verbose)
        traced_signals = tracer.trace(cfg.top_module, cfg.remove_signals)

    # Initialize SchematicGenerator
    schematic_gen = SchematicGenerator(
        backend=backend,
        verbose=cfg.verbose,
        simplify=cfg.simplify,
        strategy=cfg.strategy,
        remove_signals=cfg.remove_signals,
        traced_signals=traced_signals
    )

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
        max_modules=cfg.max_modules,
        skip_modules=cfg.skip_modules,
        schematic_gen=schematic_gen,
        block_doc_threshold=cfg.block_doc_threshold
    )

    # Run async generator
    asyncio.run(generator.run())
    return 0


def cmd_connectivity(args):
    """Check signal connectivity on after-proc schematic for a module."""
    from core import YosysBackend, ConnectivityChecker
    from core.source_extractor import extract_module_locations
    from schematic import SchematicGenerator
    from schematic.simplifier import simplify_dot_content_with_provenance

    cfg = _load_config(args)
    directed = not getattr(args, 'undirected', False)

    def _safe_name(text) -> str:
        sanitized = re.sub(r'[^A-Za-z0-9_.-]+', '_', text or '')
        return sanitized.strip('_') or 'sig'

    def _default_output_path() -> Path:
        out_base = Path(cfg.output_dir) / "pathcheck"
        filename = (
            f"{_safe_name(module_name)}"
            f"__{_safe_name(from_signal)}"
            f"__to__{_safe_name(to_signal)}.json"
        )
        return out_base / filename

    requested_output = getattr(args, 'output', None)
    output_path = Path(requested_output) if requested_output else None
    path_overlay_mode = getattr(args, 'path_overlay_mode', 'raw')
    expand_max_combs = max(0, int(getattr(args, 'expand_max_combs', 8) or 0))
    expand_max_nodes = max(0, int(getattr(args, 'expand_max_nodes', 200) or 0))

    def _emit_json_and_return(exit_code: int, error: str = "", result_dict=None):
        payload = {
            "module": module_name.lstrip("\\") if module_name else "",
            "from_signal": from_signal or "",
            "to_signal": to_signal or "",
            "directed": directed,
            "exists": False,
            "matched_from_nodes": [],
            "matched_to_nodes": [],
            "path_nodes": [],
            "all_path_nodes": [],
            "path_count": 0,
            "truncated": False,
            "has_conditions": False,
            "conditions": [],
            "path_conditions": [],
            "path_dot_file": "",
        }
        if result_dict:
            payload.update(result_dict)
        if error:
            payload["error"] = error

        final_output = output_path if output_path else _default_output_path()
        payload["output_file"] = str(final_output)
        final_output.parent.mkdir(parents=True, exist_ok=True)
        final_output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return exit_code

    module_name = getattr(args, 'module', None)
    if not module_name:
        return _emit_json_and_return(1, "--module/-m is required for connectivity command")

    from_signal = getattr(args, 'from_signal', None)
    to_signal = getattr(args, 'to_signal', None)
    if not from_signal or not to_signal:
        return _emit_json_and_return(1, "--from-signal and --to-signal are required")

    errors = cfg.validate("connectivity")
    if errors:
        return _emit_json_and_return(1, "; ".join(errors))

    backend = YosysBackend(cache_dir=cfg.cache_dir, verbose=False)

    with _suppress_stdout_stderr():
        if cfg.rtlil:
            success = backend.load_rtlil(cfg.rtlil)
        else:
            if not cfg.filelist:
                return _emit_json_and_return(1, "filelist is required when rtlil is not provided")
            success = backend.load_filelist(cfg.filelist, cfg.top_module, use_cache=True)

    if not success:
        return _emit_json_and_return(1, "Failed to load design")

    proc_ok, proc_log = _run_proc_with_log_capture(backend)
    if not proc_ok:
        return _emit_json_and_return(1, "Failed to run proc on design")

    schematic_gen = SchematicGenerator(
        backend,
        verbose=False,
        simplify=False,
        strategy=cfg.strategy
    )

    with _suppress_stdout_stderr():
        proc_dot = schematic_gen.generate_from_proc_design(module_name)
    if not proc_dot:
        return _emit_json_and_return(1, f"Failed to generate afterproc schematic for {module_name}")

    checker = ConnectivityChecker(verbose=False)
    module_obj = backend.get_module(module_name)
    cell_locations = {}
    if module_obj is not None:
        cell_locations = extract_module_locations(module_obj, verbose=False)

    proc_cell_locations = _parse_proc_cell_source_map(proc_log)
    if proc_cell_locations:
        cell_locations.update(proc_cell_locations)

    result = checker.check_connectivity(
        dot_content=proc_dot,
        from_signal=from_signal,
        to_signal=to_signal,
        module_name=module_name.lstrip("\\"),
        directed=directed,
        cell_locations=cell_locations,
        max_paths=getattr(args, 'max_paths', 0),
        max_depth=getattr(args, 'max_depth', 0),
    )

    final_payload = result.to_dict()
    final_output = output_path if output_path else _default_output_path()
    final_output.parent.mkdir(parents=True, exist_ok=True)

    if path_overlay_mode == "raw":
        path_dot_output = final_output.with_suffix(".paths.dot")
        paths_dot_text = checker.render_paths_dot(proc_dot, result)
        path_dot_output.write_text(paths_dot_text, encoding="utf-8")
    else:
        simplified_dot, provenance = simplify_dot_content_with_provenance(
            proc_dot,
            strategy="connected_component",
            cell_locations=cell_locations,
            remove_signals=None,
            verbose=False,
            graph_profile="after_proc"
        )

        simplified_dot_output = final_output.with_suffix(".afterproc.simplified.dot")
        provenance_output = final_output.with_suffix(".afterproc.simplified.prov.json")
        simplified_dot_output.write_text(simplified_dot, encoding="utf-8")
        provenance_output.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

        if path_overlay_mode == "simplified":
            path_dot_output = final_output.with_suffix(".simplified.paths.dot")
            paths_dot_text, simp_meta = checker.render_paths_on_simplified_dot(
                raw_dot_content=proc_dot,
                simplified_dot_content=simplified_dot,
                result=result,
                provenance=provenance,
                expand_comb=False,
            )
        else:
            path_dot_output = final_output.with_suffix(".expanded.paths.dot")
            paths_dot_text, simp_meta = checker.render_paths_on_simplified_dot(
                raw_dot_content=proc_dot,
                simplified_dot_content=simplified_dot,
                result=result,
                provenance=provenance,
                expand_comb=True,
                max_expand_combs=expand_max_combs,
                max_expand_nodes=expand_max_nodes,
            )

        path_dot_output.write_text(paths_dot_text, encoding="utf-8")
        final_payload["simplified_dot_file"] = str(simplified_dot_output)
        final_payload["provenance_file"] = str(provenance_output)
        final_payload.update(simp_meta)

    final_payload["path_overlay_mode"] = path_overlay_mode
    final_payload["path_dot_file"] = str(path_dot_output)
    final_payload["output_file"] = str(final_output)
    final_output.write_text(json.dumps(final_payload, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(final_payload, ensure_ascii=False))
    return 0 if result.exists else 1


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

    # ── connectivity command ──
    conn_parser = subparsers.add_parser("connectivity",
        help="Check if a path exists between two signals on after-proc schematic")
    conn_parser.add_argument("-f", "--filelist",
                             help="RTL filelist path (overrides config)")
    conn_parser.add_argument("-r", "--rtlil",
                             help="Pre-compiled RTLIL file path (overrides config)")
    conn_parser.add_argument("-t", "--top",
                             help="Top module name (overrides config)")
    conn_parser.add_argument("-m", "--module", required=True,
                             help="Target module to check connectivity on")
    conn_parser.add_argument("--from-signal", required=True,
                             help="Start signal name (exact label match)")
    conn_parser.add_argument("--to-signal", required=True,
                             help="End signal name (exact label match)")
    conn_parser.add_argument("--undirected", action="store_true",
                             help="Treat graph as undirected (default: directed)")
    conn_parser.add_argument("--max-paths", type=int, default=0,
                             help="Maximum number of simple paths to return, 0 means unlimited")
    conn_parser.add_argument("--max-depth", type=int, default=0,
                             help="Maximum number of nodes in each path, 0 means unlimited")
    conn_parser.add_argument("--path-overlay-mode",
                             choices=["raw", "simplified", "simplified-expand"],
                             default="raw",
                             help="Path visualization mode: raw (default), simplified, or simplified-expand")
    conn_parser.add_argument("--expand-max-combs", type=int, default=8,
                             help="When using simplified-expand, max number of hit COMB nodes to expand")
    conn_parser.add_argument("--expand-max-nodes", type=int, default=200,
                             help="When using simplified-expand, max total expanded detail nodes")
    conn_parser.add_argument("-o", "--output",
                             help="Output JSON file path (default: <output_dir>/pathcheck/<auto_name>.json)")


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
    elif args.command == "connectivity":
        return cmd_connectivity(args)
    elif args.command == "docor":
        return cmd_docor(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
