#!/usr/bin/env python3
"""Rerun full pass3.3.3 APV for one instruction and emit a lifecycle YAML.

Usage:
    source env.sh
    python3 scripts/rerun_full_instrack_apv_lifecycle.py -c config_openc910_full.yaml --instruction ADD
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
import sys

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core import HierarchyBuilder, YosysBackend  # noqa: E402
from core.config import load_project_config  # noqa: E402
from agent.doc_generator import AgentDocGenerator  # noqa: E402
from agent.llm_backend import get_llm_backend  # noqa: E402
from agent.progress_tracker import ProgressTracker  # noqa: E402
from agent.source_resolver import SourceResolver  # noqa: E402


def _json_string(value: Any) -> str:
    return json.dumps(str(value if value is not None else ""), ensure_ascii=False)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _dump_yaml(value: Any, indent: int = 0) -> List[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: List[str] = []
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                if not child:
                    lines.append(f"{prefix}{key}: {{}}" if isinstance(child, dict) else f"{prefix}{key}: []")
                else:
                    lines.append(f"{prefix}{key}:")
                    lines.extend(_dump_yaml(child, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(child)}")
        return lines
    if isinstance(value, list):
        lines = []
        for child in value:
            if isinstance(child, dict):
                if not child:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first = True
                for key, grandchild in child.items():
                    if first:
                        if isinstance(grandchild, (dict, list)):
                            if not grandchild:
                                lines.append(f"{prefix}- {key}: {{}}" if isinstance(grandchild, dict) else f"{prefix}- {key}: []")
                            else:
                                lines.append(f"{prefix}- {key}:")
                                lines.extend(_dump_yaml(grandchild, indent + 4))
                        else:
                            lines.append(f"{prefix}- {key}: {_yaml_scalar(grandchild)}")
                        first = False
                    else:
                        if isinstance(grandchild, (dict, list)):
                            if not grandchild:
                                lines.append(f"{prefix}  {key}: {{}}" if isinstance(grandchild, dict) else f"{prefix}  {key}: []")
                            else:
                                lines.append(f"{prefix}  {key}:")
                                lines.extend(_dump_yaml(grandchild, indent + 4))
                        else:
                            lines.append(f"{prefix}  {key}: {_yaml_scalar(grandchild)}")
                continue
            if isinstance(child, list):
                if not child:
                    lines.append(f"{prefix}- []")
                else:
                    lines.append(f"{prefix}-")
                    lines.extend(_dump_yaml(child, indent + 2))
                continue
            lines.append(f"{prefix}- {_yaml_scalar(child)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _parse_quoted(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(json.loads(text))
    except Exception:
        return text


def _parse_apv_yaml(path: Path) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "module": "",
        "instance": "",
        "path": "",
        "status": "partial",
        "unknown": [],
        "tasks": [],
    }
    if not path.exists():
        payload["unknown"] = [f"Missing APV YAML artifact: {path.name}"]
        return payload

    lines = path.read_text(encoding="utf-8").splitlines()
    section: str = ""
    current_task: Optional[Dict[str, Any]] = None
    current_task_list_key: Optional[str] = None

    def _flush_task() -> None:
        nonlocal current_task, current_task_list_key
        if current_task is not None:
            payload["tasks"].append(current_task)
        current_task = None
        current_task_list_key = None

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("module: "):
            payload["module"] = _parse_quoted(line.split(": ", 1)[1])
            section = ""
            continue
        if line.startswith("instance: "):
            payload["instance"] = _parse_quoted(line.split(": ", 1)[1])
            section = ""
            continue
        if line.startswith("path: "):
            payload["path"] = _parse_quoted(line.split(": ", 1)[1])
            section = ""
            continue
        if line.startswith("status: "):
            payload["status"] = _parse_quoted(line.split(": ", 1)[1])
            section = ""
            continue
        if line == "unknown: []":
            payload["unknown"] = []
            section = ""
            continue
        if line == "unknown:":
            _flush_task()
            section = "unknown"
            continue
        if line == "tasks: []":
            _flush_task()
            payload["tasks"] = []
            section = ""
            continue
        if line == "tasks:":
            _flush_task()
            section = "tasks"
            continue
        if section == "unknown" and line.startswith("  - "):
            payload["unknown"].append(_parse_quoted(line[4:]))
            continue
        if section == "tasks" and line.startswith("  - id: "):
            _flush_task()
            current_task = {
                "id": _parse_quoted(line.split(": ", 1)[1]),
                "name": "",
                "dependsOn": "",
                "matchMode": "",
                "maxMatch": 0,
                "anchors": [],
                "condition": [],
                "capture": [],
                "logging": [],
            }
            current_task_list_key = None
            continue
        if current_task is None:
            continue
        if line.startswith("    name: "):
            current_task["name"] = _parse_quoted(line.split(": ", 1)[1])
            current_task_list_key = None
            continue
        if line.startswith("    dependsOn: "):
            current_task["dependsOn"] = _parse_quoted(line.split(": ", 1)[1])
            current_task_list_key = None
            continue
        if line.startswith("    matchMode: "):
            current_task["matchMode"] = _parse_quoted(line.split(": ", 1)[1])
            current_task_list_key = None
            continue
        if line.startswith("    maxMatch: "):
            try:
                current_task["maxMatch"] = int(line.split(": ", 1)[1].strip())
            except Exception:
                current_task["maxMatch"] = 0
            current_task_list_key = None
            continue
        if line == "    anchors:":
            current_task_list_key = "anchors"
            continue
        if line == "    condition:":
            current_task_list_key = "condition"
            continue
        if line == "    capture:":
            current_task_list_key = "capture"
            continue
        if line == "    logging:":
            current_task_list_key = "logging"
            continue
        if current_task_list_key == "anchors" and line.startswith("      - kind: "):
            current_task["anchors"].append(
                {
                    "kind": _parse_quoted(line.split(": ", 1)[1]),
                    "depSignals": [],
                    "localSignals": [],
                    "reason": "",
                }
            )
            continue
        if current_task_list_key == "anchors" and current_task["anchors"]:
            anchor = current_task["anchors"][-1]
            if line == "        depSignals: []":
                anchor["depSignals"] = []
                continue
            if line == "        localSignals: []":
                anchor["localSignals"] = []
                continue
            if line == "        depSignals:":
                current_task_list_key = "anchor_depSignals"
                continue
            if line == "        localSignals:":
                current_task_list_key = "anchor_localSignals"
                continue
            if line.startswith("        reason: "):
                anchor["reason"] = _parse_quoted(line.split(": ", 1)[1])
                current_task_list_key = "anchors"
                continue
        if current_task_list_key == "anchor_depSignals" and current_task["anchors"] and line.startswith("          - "):
            current_task["anchors"][-1]["depSignals"].append(_parse_quoted(line[12:]))
            continue
        if current_task_list_key == "anchor_localSignals" and current_task["anchors"] and line.startswith("          - "):
            current_task["anchors"][-1]["localSignals"].append(_parse_quoted(line[12:]))
            continue
        if current_task_list_key in {"anchor_depSignals", "anchor_localSignals"} and line.startswith("        reason: "):
            current_task["anchors"][-1]["reason"] = _parse_quoted(line.split(": ", 1)[1])
            current_task_list_key = "anchors"
            continue
        if current_task_list_key and line.startswith("      - "):
            current_task[current_task_list_key].append(_parse_quoted(line[8:]))
            continue

    _flush_task()
    return payload


def _remove_files(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _build_generator(cfg: Any) -> AgentDocGenerator:
    backend = YosysBackend(cache_dir=cfg.cache_dir, verbose=cfg.verbose)
    if cfg.rtlil:
        ok = backend.load_rtlil(cfg.rtlil)
    else:
        ok = backend.load_filelist(cfg.filelist, cfg.top_module, use_cache=True)
    if not ok:
        raise RuntimeError("Failed to load design.")

    hierarchy = HierarchyBuilder(backend, verbose=cfg.verbose).build(cfg.top_module)
    if hierarchy is None:
        raise RuntimeError("Failed to build hierarchy.")

    llm = get_llm_backend(
        {
            "backend": cfg.agent_backend,
            "model": cfg.agent_model,
            "api_key": cfg.agent_api_key,
            "base_url": cfg.agent_base_url,
            "thinking": cfg.agent_thinking,
            "enable_webui_monitoring": cfg.enable_webui,
        }
    )
    resolver = SourceResolver(backend, code_base_path=cfg.code_base_path)
    tracker = ProgressTracker(cfg.output_dir)
    return AgentDocGenerator(
        hierarchy=hierarchy,
        llm=llm,
        instrack_orchestrate_llm=None,
        composer_llm=None,
        resolver=resolver,
        tracker=tracker,
        output_dir=cfg.output_dir,
        max_modules=cfg.max_modules,
        skip_modules=cfg.skip_modules,
        schematic_gen=None,
        block_doc_threshold=cfg.block_doc_threshold,
        pass1_enabled=cfg.pass1_enabled,
        pass2_enabled=cfg.pass2_enabled,
        pass3_enabled=cfg.pass3_enabled,
        pass3_3_enabled=cfg.pass3_3_enabled,
        pass3_3_3_enabled=cfg.pass3_3_3_enabled,
        isa_profile=cfg.isa_profile,
        isa_instructions=cfg.isa_instructions,
        instrack_single_instruction=cfg.instrack_single_instruction,
        instrack_use_graph_markers=cfg.instrack_use_graph_markers,
        instrack_apv_source_access_mode=cfg.instrack_apv_source_access_mode,
        instrack_apv_max_tool_rounds=cfg.instrack_apv_max_tool_rounds,
        max_concurrent_modules=1,
        enable_webui_monitoring=cfg.enable_webui,
    )


def _build_lifecycle_artifacts(
    *,
    generator: AgentDocGenerator,
    instruction: str,
    instruction_slug: str,
    parsed_search: Dict[str, Any],
    orchestrate_payload: Dict[str, Any],
    apv_payload: Dict[str, Any],
    instruction_dir: Path,
) -> Dict[str, Dict[str, Any]]:
    flattened_tasks: List[Dict[str, Any]] = []
    aggregated_unknown: List[str] = []
    task_logging: List[Dict[str, Any]] = []

    apv_items = list(apv_payload.get("items") or [])
    for stage_index, item in enumerate(apv_items):
        yaml_rel = str(item.get("artifact_yaml") or "").strip()
        yaml_path = generator.chip_dir / yaml_rel if yaml_rel else Path()
        parsed_yaml = _parse_apv_yaml(yaml_path) if yaml_rel else {"tasks": [], "unknown": []}
        stage_path = str(parsed_yaml.get("path") or item.get("path") or "").strip()
        stage_unknown = list(parsed_yaml.get("unknown") or item.get("unknown") or [])
        for warning in stage_unknown:
            warning_text = str(warning or "").strip()
            if not warning_text:
                continue
            if stage_path:
                aggregated_unknown.append(f"[{stage_path}] {warning_text}")
            else:
                aggregated_unknown.append(warning_text)
        for task in list(parsed_yaml.get("tasks") or []):
            logging_lines = list(task.get("logging") or [])
            if logging_lines:
                task_logging.append(
                    {
                        "id": str(task.get("id") or "").strip(),
                        "path": stage_path,
                        "logging": logging_lines,
                    }
                )
            flattened_tasks.append(
                {
                    key: value
                    for key, value in task.items()
                    if key != "logging"
                }
            )

    status_values = [str(item.get("status") or "").strip().lower() for item in apv_items]
    if status_values and all(value == "complete" for value in status_values):
        overall_status = "complete"
    elif apv_items:
        overall_status = "partial"
    else:
        overall_status = "failed"

    dedup_unknown = list(dict.fromkeys(aggregated_unknown))
    lifecycle_path = f"{str(generator.hierarchy.module_name)}/{instruction_slug}/lifecycle"
    return {
        "apv": {
            "module": "instruction_lifecycle",
            "instance": instruction,
            "path": lifecycle_path,
            "status": overall_status,
            "unknown": [],
            "tasks": flattened_tasks,
        },
        "diagnostics": {
            "module": "instruction_lifecycle",
            "instance": instruction,
            "path": lifecycle_path,
            "status": overall_status,
            "unknown": dedup_unknown,
            "task_logging": task_logging,
        },
    }


async def _run(args: argparse.Namespace) -> int:
    cfg = load_project_config(args.config)
    cfg.instrack_single_instruction = args.instruction
    if args.source_access_mode:
        cfg.instrack_apv_source_access_mode = args.source_access_mode
    if args.max_tool_rounds:
        cfg.instrack_apv_max_tool_rounds = int(args.max_tool_rounds)
    cfg.verbose = bool(args.verbose)

    generator = _build_generator(cfg)
    pp = generator.pass3_generator
    instruction = args.instruction
    slug = pp._safe_slug(instruction).lower()
    instruction_dir = generator.chip_dir / "instrack" / slug
    artifacts_dir = instruction_dir / "artifacts"
    search_path = instruction_dir / f"{slug}.search.json"
    orchestrate_path = artifacts_dir / "orchestrate_index.json"
    apv_index_path = artifacts_dir / "apv_index.json"
    lifecycle_yaml_path = instruction_dir / f"{slug}.lifecycle.yaml"
    lifecycle_diag_path = instruction_dir / f"{slug}.lifecycle.diagnostics.yaml"

    if not search_path.exists():
        raise RuntimeError(f"Missing search artifact: {search_path}")
    if not orchestrate_path.exists():
        raise RuntimeError(f"Missing orchestrate artifact: {orchestrate_path}")

    parsed_search = json.loads(search_path.read_text(encoding="utf-8"))
    orchestrate_payload = json.loads(orchestrate_path.read_text(encoding="utf-8"))
    orchestrate_items = list(orchestrate_payload.get("items") or [])
    if not orchestrate_items:
        raise RuntimeError("Orchestrate index has no items.")

    if args.clean:
        cleanup_targets = list(artifacts_dir.glob("*.apv.yaml"))
        cleanup_targets.extend(artifacts_dir.glob("*.apv.raw.md"))
        cleanup_targets.extend(artifacts_dir.glob("*.apv.llm_error.md"))
        cleanup_targets.append(apv_index_path)
        cleanup_targets.append(artifacts_dir / "apv_index.ifu_only.json")
        cleanup_targets.append(lifecycle_yaml_path)
        cleanup_targets.append(lifecycle_diag_path)
        _remove_files(cleanup_targets)
        debug_dir = generator.chip_debug_dir
        _remove_files(debug_dir.glob(f"debug_pass3_instrack_apv_agent_{instruction}_*.md"))

    print(
        f"[lifecycle] instruction={instruction} items={len(orchestrate_items)} "
        f"source_access_mode={generator.instrack_apv_source_access_mode} "
        f"max_tool_rounds={generator.instrack_apv_max_tool_rounds}"
    )

    instruction_datasheet = pp._extract_instruction_datasheet_excerpt(pp._load_instrack_datasheet(), instruction)
    apv_result = await pp._instrack_apv.generate_instruction_apv(
        top_node=generator.hierarchy,
        instruction=instruction,
        instruction_slug=slug,
        instruction_datasheet=instruction_datasheet,
        parsed_search=parsed_search,
        orchestrate_items=orchestrate_items,
        orchestrate_index_text=json.dumps(orchestrate_payload, ensure_ascii=False, indent=2),
        artifacts_dir=artifacts_dir,
        cached_items=None,
    )

    apv_index_text = str(apv_result.get("index_text") or "")
    apv_index_path.write_text(apv_index_text, encoding="utf-8")
    generator.project_tracker.update(
        f"instrack/{slug}/artifacts/apv_index.json",
        apv_index_text,
        input_hash=str(apv_result.get("input_hash") or ""),
        meta={"top_module": generator.hierarchy.module_name, "instruction": instruction, "pass": "pass3_3_3_apv"},
    )

    apv_payload = dict(apv_result.get("index_payload") or {})
    apv_payload["lifecycle_modules"] = list(
        dict.fromkeys(str(item.get("module") or "").strip() for item in orchestrate_items if str(item.get("module") or "").strip())
    )
    lifecycle_artifacts = _build_lifecycle_artifacts(
        generator=generator,
        instruction=instruction,
        instruction_slug=slug,
        parsed_search=parsed_search,
        orchestrate_payload=orchestrate_payload,
        apv_payload=apv_payload,
        instruction_dir=instruction_dir,
    )
    lifecycle_yaml_text = "\n".join(_dump_yaml(lifecycle_artifacts["apv"])).rstrip() + "\n"
    lifecycle_diag_text = "\n".join(_dump_yaml(lifecycle_artifacts["diagnostics"])).rstrip() + "\n"
    lifecycle_yaml_path.write_text(lifecycle_yaml_text, encoding="utf-8")
    lifecycle_diag_path.write_text(lifecycle_diag_text, encoding="utf-8")
    generator.project_tracker.update(
        f"instrack/{slug}/{slug}.lifecycle.yaml",
        lifecycle_yaml_text,
        meta={"top_module": generator.hierarchy.module_name, "instruction": instruction, "pass": "pass3_3_3_lifecycle_yaml"},
    )
    generator.project_tracker.update(
        f"instrack/{slug}/{slug}.lifecycle.diagnostics.yaml",
        lifecycle_diag_text,
        meta={"top_module": generator.hierarchy.module_name, "instruction": instruction, "pass": "pass3_3_3_lifecycle_yaml_diagnostics"},
    )

    print(f"[lifecycle] wrote {apv_index_path}")
    print(f"[lifecycle] wrote {lifecycle_yaml_path}")
    print(f"[lifecycle] wrote {lifecycle_diag_path}")
    for item in apv_payload.get("items", []):
        print(
            f"[lifecycle] {item.get('path', '')} "
            f"status={item.get('status', '')} "
            f"tasks={item.get('task_count', 0)} "
            f"llm_error={item.get('artifact_llm_error', '')}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun full instruction APV lifecycle and emit one YAML.")
    parser.add_argument("-c", "--config", default="config_openc910_full.yaml", help="Path to config yaml")
    parser.add_argument("--instruction", required=True, help="Instruction name, e.g. ADD")
    parser.add_argument(
        "--source-access-mode",
        choices=("embedded_topology", "toolized_source"),
        default="",
        help="Override APV source access mode",
    )
    parser.add_argument("--max-tool-rounds", type=int, default=0, help="Override APV max tool rounds")
    parser.add_argument("--no-clean", action="store_true", help="Do not remove old APV outputs before rerun")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose backend loading")
    args = parser.parse_args()
    args.clean = not args.no_clean
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
