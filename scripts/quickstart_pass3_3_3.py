#!/usr/bin/env python3
"""Quick-start helper to force a fresh pass3.3.3 APV rerun.

This script intentionally does not use CLI resume switches. Instead it:
1. Cleans only pass3.3.3 APV artifacts and related project progress entries.
2. Preserves pass3.3.1 search and pass3.3.2 orchestrate results.
3. Re-invokes `python3 -m cli ... docor` normally.

Usage:
    source env.sh
    python3 scripts/quickstart_pass3_3_3.py -c config_openc910_full.yaml --instruction ADD
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.config import load_project_config  # noqa: E402


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()
    return slug or "instruction"


def _resolve_output_dir(config_path: Path, configured_output_dir: str, override_output_dir: str = "") -> Path:
    chosen = override_output_dir.strip() or configured_output_dir.strip()
    output_dir = Path(chosen)
    if not output_dir.is_absolute():
        output_dir = (config_path.parent / output_dir).resolve()
    return output_dir


def _list_running_docor_processes(config_path: Path) -> List[Tuple[int, str]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    config_name = config_path.name
    config_abs = str(config_path)
    matches: List[Tuple[int, str]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_text, args = parts
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == os.getpid():
            continue
        if "python" not in args or "-m cli" not in args or " docor" not in args:
            continue
        if config_abs in args or config_name in args:
            matches.append((pid, args))
    return matches


def _wait_for_exit(pid: int, timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _terminate_processes(processes: Sequence[Tuple[int, str]], timeout_sec: float, dry_run: bool) -> None:
    if not processes:
        return
    for pid, args in processes:
        if dry_run:
            print(f"[dry-run] would terminate PID {pid}: {args}")
            continue
        print(f"[info] terminating PID {pid}: {args}")
        os.kill(pid, signal.SIGTERM)
    if dry_run:
        return
    stubborn: List[Tuple[int, str]] = []
    for pid, args in processes:
        if not _wait_for_exit(pid, timeout_sec):
            stubborn.append((pid, args))
    if stubborn:
        joined = ", ".join(str(pid) for pid, _ in stubborn)
        raise RuntimeError(f"Timed out waiting for docor process(es) to exit: {joined}")


def _resolve_instruction_names(cli_instructions: Sequence[str], config: object, instrack_dir: Path) -> List[str]:
    names: List[str] = [item.strip() for item in cli_instructions if item.strip()]
    if names:
        return names

    single_instruction = str(getattr(config, "instrack_single_instruction", "") or "").strip()
    if single_instruction:
        return [single_instruction]

    config_instructions = list(getattr(config, "isa_instructions", []) or [])
    names = [str(item).strip() for item in config_instructions if str(item).strip()]
    if names:
        return names

    if instrack_dir.exists():
        return sorted(path.name for path in instrack_dir.iterdir() if path.is_dir())
    return []


def _collect_cleanup_targets(
    chip_dir: Path,
    instruction_names: Sequence[str],
) -> Tuple[List[Path], Set[str]]:
    files_to_remove: List[Path] = []
    progress_keys: Set[str] = set()
    debug_dir = chip_dir / "debug"

    for instruction_name in instruction_names:
        slug = _safe_slug(instruction_name)
        instruction_dir = chip_dir / "instrack" / slug
        artifacts_dir = instruction_dir / "artifacts"

        progress_keys.add(f"instrack/{slug}/artifacts/apv_index.json")
        progress_keys.add(f"instrack/{slug}/{slug}.md")
        progress_keys.add(f"instrack/{slug}/{slug}.json")

        if artifacts_dir.exists():
            for path in sorted(artifacts_dir.iterdir()):
                if (
                    path.name == "apv_index.json"
                    or path.name.endswith(".apv.yaml")
                    or path.name.endswith(".apv.raw.md")
                    or path.name.endswith(".apv.llm_error.md")
                ):
                    files_to_remove.append(path)
                    progress_keys.add(f"instrack/{slug}/artifacts/{path.name}")

        summary_md = instruction_dir / f"{slug}.md"
        summary_json = instruction_dir / f"{slug}.json"
        if summary_md.exists():
            files_to_remove.append(summary_md)
        if summary_json.exists():
            files_to_remove.append(summary_json)

        if debug_dir.exists():
            lowered_markers = {
                instruction_name.lower(),
                slug.lower(),
                instruction_name.upper().lower(),
            }
            for path in sorted(debug_dir.glob("debug_pass3_instrack_apv_agent_*.md")):
                lowered_name = path.name.lower()
                if any(f"_{marker}_" in lowered_name for marker in lowered_markers):
                    files_to_remove.append(path)

    if instruction_names:
        progress_keys.add("instrack/index.json")
        instrack_index = chip_dir / "instrack" / "index.json"
        if instrack_index.exists():
            files_to_remove.append(instrack_index)

    unique_files = []
    seen: Set[Path] = set()
    for path in files_to_remove:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_files.append(path)

    return unique_files, progress_keys


def _rewrite_progress(progress_path: Path, progress_keys: Set[str], dry_run: bool) -> Tuple[int, List[str]]:
    if not progress_path.exists():
        return 0, []

    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected progress format in {progress_path}")

    removed_keys = sorted(key for key in payload.keys() if key in progress_keys)
    if dry_run:
        return len(removed_keys), removed_keys

    for key in removed_keys:
        payload.pop(key, None)

    if payload:
        progress_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        progress_path.unlink()
    return len(removed_keys), removed_keys


def _remove_files(paths: Iterable[Path], dry_run: bool) -> List[Path]:
    removed: List[Path] = []
    for path in paths:
        if not path.exists():
            continue
        removed.append(path)
        if dry_run:
            continue
        path.unlink()
    return removed


def _build_docor_command(config_path: Path) -> List[str]:
    return [sys.executable, "-m", "cli", "-c", str(config_path), "docor"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean pass3.3.3 APV outputs and rerun docor without using CLI resume switches.",
    )
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to config.yaml used for docor.",
    )
    parser.add_argument(
        "--instruction",
        action="append",
        default=[],
        help="Instruction name to clean/rerun. Repeatable. Defaults to config instruction(s) or existing instrack dirs.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Override config output dir for cleanup/rerun bookkeeping.",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Only clean 3.3.3 APV outputs; do not launch docor.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed or run without changing anything.",
    )
    parser.add_argument(
        "--kill-running",
        action="store_true",
        help="Terminate matching running docor process before cleanup.",
    )
    parser.add_argument(
        "--term-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait after SIGTERM when --kill-running is used.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[error] config file not found: {config_path}", file=sys.stderr)
        return 2

    config = load_project_config(str(config_path))
    if not getattr(config, "pass3_3_enabled", True):
        print("[error] config has pass3_3_enabled=false; 3.3.3 rerun would never execute.", file=sys.stderr)
        return 2
    if not getattr(config, "pass3_3_3_enabled", True):
        print("[error] config has pass3_3_3_enabled=false; APV rerun would never execute.", file=sys.stderr)
        return 2

    output_dir = _resolve_output_dir(config_path, str(getattr(config, "output_dir", "")), args.output_dir)
    chip_dir = output_dir / "chip"
    instrack_dir = chip_dir / "instrack"
    instruction_names = _resolve_instruction_names(args.instruction, config, instrack_dir)
    if not instruction_names:
        print("[error] no instruction scope found. Pass --instruction or run at least one instrack generation first.", file=sys.stderr)
        return 2

    running = _list_running_docor_processes(config_path)
    if running and not args.kill_running:
        print("[error] matching docor process is still running; rerun with --kill-running or stop it manually first.", file=sys.stderr)
        for pid, cmd in running:
            print(f"  PID {pid}: {cmd}", file=sys.stderr)
        return 2
    if running:
        _terminate_processes(running, timeout_sec=args.term_timeout, dry_run=args.dry_run)

    files_to_remove, progress_keys = _collect_cleanup_targets(chip_dir, instruction_names)
    progress_path = chip_dir / ".progress.json"
    removed_progress_count, removed_progress_keys = _rewrite_progress(progress_path, progress_keys, args.dry_run)
    removed_files = _remove_files(files_to_remove, args.dry_run)

    print(f"[info] output dir: {output_dir}")
    print(f"[info] instructions: {', '.join(instruction_names)}")
    print(f"[info] progress entries {'would be' if args.dry_run else 'were'} removed: {removed_progress_count}")
    for key in removed_progress_keys:
        print(f"  - {key}")
    print(f"[info] files {'would be' if args.dry_run else 'were'} removed: {len(removed_files)}")
    for path in removed_files:
        print(f"  - {path}")

    if args.clean_only:
        print("[info] clean-only mode; skipping docor launch.")
        return 0

    docor_cmd = _build_docor_command(config_path)
    print("[info] launching docor without CLI resume switches:")
    print(f"  {shlex.join(docor_cmd)}")
    if args.dry_run:
        return 0

    completed = subprocess.run(docor_cmd, cwd=str(REPO_ROOT))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
