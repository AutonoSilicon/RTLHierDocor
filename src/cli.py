#!/usr/bin/env python3
"""Command-line interface for RTL Hierarchy Documentor.

Usage:
    rtl-hier-doc generate --filelist <file> --top <module> [--output <dir>]
    rtl-hier-doc hierarchy --filelist <file> --top <module>
    rtl-hier-doc schematic --filelist <file> --module <name> [--output <file>]
"""

import argparse
import sys
from pathlib import Path


def cmd_generate(args):
    """Generate complete documentation."""
    from output import DocumentGenerator

    if not args.filelist and not args.rtlil:
        print("Error: Either --filelist or --rtlil is required", file=sys.stderr)
        return 1

    generator = DocumentGenerator(
        filelist=args.filelist,
        rtlil_path=args.rtlil,
        top_module=args.top,
        output_dir=args.output,
        cache_dir=args.cache_dir,
        verbose=args.verbose,
        simplify=not args.no_simplify,
        strategy=args.strategy
    )

    stats = generator.generate(max_schematics=args.max_schematics)
    # Return 0 if any documentation was generated (hierarchy at minimum)
    # Schematics may fail on large modules but hierarchy is still valuable
    return 0 if stats else 1


def cmd_hierarchy(args):
    """Generate hierarchy tree only."""
    from core import YosysBackend, HierarchyBuilder

    backend = YosysBackend(cache_dir=args.cache_dir, verbose=args.verbose)
    if not backend.load_filelist(args.filelist, args.top, use_cache=True):
        print("Failed to load design", file=sys.stderr)
        return 1

    builder = HierarchyBuilder(backend, verbose=args.verbose)
    hierarchy = builder.build(args.top)

    if not hierarchy:
        print("Failed to build hierarchy", file=sys.stderr)
        return 1

    if args.format == "ascii":
        print(hierarchy.render_ascii(show_ports=args.show_ports, max_depth=args.max_depth))
    elif args.format == "json":
        print(hierarchy.to_json())

    return 0


def cmd_schematic(args):
    """Generate schematic for a single module."""
    from core import YosysBackend
    from schematic import SchematicGenerator

    backend = YosysBackend(cache_dir=args.cache_dir, verbose=args.verbose)
    if not backend.load_filelist(args.filelist, args.top, use_cache=True):
        print("Failed to load design", file=sys.stderr)
        return 1

    simplify = not args.no_simplify
    generator = SchematicGenerator(
        backend,
        verbose=args.verbose,
        simplify=simplify,
        strategy=args.strategy
    )

    if simplify:
        raw_dot, simplified_dot = generator.generate_simplified(args.module)
        dot = simplified_dot or raw_dot
    else:
        dot = generator.generate(args.module)

    if not dot:
        print(f"Failed to generate schematic for {args.module}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, 'w') as f:
            f.write(dot)
        print(f"Written to {args.output}")
    else:
        print(dot)

    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="RTL Hierarchy Documentor - Generate hierarchical documentation for RTL designs"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--cache-dir", default=".rtl_cache", help="Cache directory")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # generate command
    gen_parser = subparsers.add_parser("generate", help="Generate complete documentation")
    gen_parser.add_argument("--filelist", "-f", help="RTL filelist path")
    gen_parser.add_argument("--rtlil", "-r", help="Pre-compiled RTLIL file path (alternative to filelist)")
    gen_parser.add_argument("--top", "-t", required=True, help="Top module name")
    gen_parser.add_argument("--output", "-o", default="./rtl_docs", help="Output directory")
    gen_parser.add_argument("--max-schematics", type=int, default=0, help="Max schematics to generate (0 for all)")
    gen_parser.add_argument("--no-simplify", action="store_true",
                            help="Disable DOT schematic simplification")
    gen_parser.add_argument("--strategy", choices=["proc_group", "connected_component"],
                            default="proc_group",
                            help="Simplification strategy (default: proc_group)")

    # hierarchy command
    hier_parser = subparsers.add_parser("hierarchy", help="Generate hierarchy tree")
    hier_parser.add_argument("--filelist", "-f", required=True, help="RTL filelist path")
    hier_parser.add_argument("--top", "-t", required=True, help="Top module name")
    hier_parser.add_argument("--format", choices=["ascii", "json"], default="ascii", help="Output format")
    hier_parser.add_argument("--show-ports", action="store_true", help="Show port connections")
    hier_parser.add_argument("--max-depth", type=int, help="Maximum depth to display")

    # schematic command
    sch_parser = subparsers.add_parser("schematic", help="Generate module schematic")
    sch_parser.add_argument("--filelist", "-f", required=True, help="RTL filelist path")
    sch_parser.add_argument("--top", "-t", required=True, help="Top module name")
    sch_parser.add_argument("--module", "-m", required=True, help="Module to generate schematic for")
    sch_parser.add_argument("--output", "-o", help="Output file path")
    sch_parser.add_argument("--no-simplify", action="store_true",
                            help="Disable DOT schematic simplification")
    sch_parser.add_argument("--strategy", choices=["proc_group", "connected_component"],
                            default="proc_group",
                            help="Simplification strategy (default: proc_group)")

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
