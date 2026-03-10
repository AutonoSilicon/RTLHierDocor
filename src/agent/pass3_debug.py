"""Pass3 debug logging helpers.

This module encapsulates debug logging infrastructure for pass3 generation,
including fork trace logging and agent I/O snapshots.
"""

import datetime
import json
from pathlib import Path
from typing import Any, Dict

from .pass3_utils import safe_slug


class Pass3Debug:
    """Helper object encapsulating pass3 debug logging infrastructure."""

    def __init__(self, generator: Any):
        self.g = generator

    def project_log_path(self, name: str) -> str:
        """Get path for a debug log file."""
        project_debug_dir = self.g.owner.chip_debug_dir
        project_debug_dir.mkdir(parents=True, exist_ok=True)
        return str(project_debug_dir / f"debug_{name}.md")

    def next_agent_log_seq(self) -> int:
        """Get the next agent log sequence number."""
        self.g._agent_log_seq += 1
        return self.g._agent_log_seq

    def build_pass3_instrack_agent_log_path(
        self,
        *,
        instruction: str,
        node_path: str,
        level: int,
        role: str,
    ) -> str:
        """Create a unique debug log path for one pass3 instrack agent invocation."""
        seq = self.next_agent_log_seq()
        inst_slug = safe_slug(instruction or "unknown")
        node_slug = safe_slug((node_path or "top").replace("/", "__"))
        role_slug = safe_slug(role or "agent")
        return self.project_log_path(
            f"pass3_instrack_{role_slug}_{inst_slug}_{node_slug}_L{level}_R{seq:04d}"
        )

    def build_pass3_3_1_agent_log_path(
        self,
        *,
        instruction: str,
        node_path: str,
        level: int,
        role: str,
    ) -> str:
        """Backward-compatible wrapper for legacy call sites."""
        return self.build_pass3_instrack_agent_log_path(
            instruction=instruction,
            node_path=node_path,
            level=level,
            role=role,
        )

    def fork_trace_log_path(self) -> Path:
        """Get path for the fork trace log."""
        log_dir = self.g.owner.chip_debug_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "debug_pass3_fork_trace.jsonl"

    def fork_trace_log_path_pass3_3_1(self) -> Path:
        """Get path for the pass3.3.1 fork trace log."""
        log_dir = self.g.owner.chip_debug_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "debug_pass3_3_1_fork_trace.jsonl"

    def append_fork_trace(self, event: str, payload: Dict[str, Any]):
        """Append one fork-related trace record as JSONL."""
        trace_path = self.fork_trace_log_path()
        record = {
            "ts": datetime.datetime.now().isoformat(),
            "event": event,
        }
        record.update(payload or {})
        try:
            with open(trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            # Emit concise runtime progress to terminal for pass3.3 instrack fork flows.
            prompt_style = str((payload or {}).get("prompt_style") or "")
            pass_name = str((payload or {}).get("pass") or "")
            is_instrack = prompt_style.startswith("instrack") or pass_name.startswith("pass3_3")
            if is_instrack:
                stage_tag = "pass3.3"
                if prompt_style == "instrack_search" or pass_name == "pass3_3_1":
                    stage_tag = "pass3.3.1/search"
                elif prompt_style == "instrack_draw" or pass_name == "pass3_3_2_draw":
                    stage_tag = "pass3.3.2/draw"
                elif prompt_style.startswith("instrack"):
                    stage_tag = f"pass3.3/{prompt_style}"

                if event in {"fork_dispatch", "pass3_1_fork_dispatch", "pass3_2_fork_dispatch"}:
                    child_inst = str((payload or {}).get("child_instance") or "?")
                    child_mod = str((payload or {}).get("child_module") or "?")
                    parent_level = int((payload or {}).get("parent_level") or 0)
                    child_level = int((payload or {}).get("child_level") or (parent_level + 1))
                    print(
                        f"[InStrack][{stage_tag}][fork][dispatch] L{parent_level}->L{child_level} "
                        f"{child_inst}({child_mod})"
                    )
                elif event in {"fork_return", "pass3_1_fork_return", "pass3_2_fork_return"}:
                    child_inst = str((payload or {}).get("child_instance") or "?")
                    child_mod = str((payload or {}).get("child_module") or "?")
                    report_chars = int((payload or {}).get("report_chars") or 0)
                    print(
                        f"[InStrack][{stage_tag}][fork][return] {child_inst}({child_mod}) "
                        f"report_chars={report_chars}"
                    )
                elif event in {"fork_rejected", "pass3_1_fork_rejected", "pass3_2_fork_rejected"}:
                    module_selector = str((payload or {}).get("module_selector") or "")
                    reason = str((payload or {}).get("reason") or "")
                    if len(reason) > 220:
                        reason = reason[:220] + "..."
                    print(f"[InStrack][{stage_tag}][fork][rejected] {module_selector} | {reason}")

            # Keep a dedicated pass3.3.1 trace stream for easier triage.
            is_pass3_3_1 = pass_name == "pass3_3_1" or prompt_style == "instrack_search"
            if is_pass3_3_1:
                trace_331 = self.fork_trace_log_path_pass3_3_1()
                with open(trace_331, "a", encoding="utf-8") as f331:
                    f331.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # Debug log failure should not block the generation flow.
            pass

    def append_recursive_context_log(
        self,
        log_path: str,
        *,
        top_node: Any,
        current_node: Any,
        level: int,
        prompt_style: str,
        task: str,
        cache_key: str,
        child_overview: str,
    ):
        """Append deterministic recursive call context to per-call debug file."""
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write("## Recursive Context\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"- top_module: {top_node.module_name}\n")
                f.write(f"- node: {current_node.instance_name} ({current_node.module_name})\n")
                f.write(f"- level: {level}\n")
                f.write(f"- prompt_style: {prompt_style}\n")
                f.write(f"- task: {task}\n")
                f.write(f"- cache_key: {cache_key}\n")
                f.write("\n### Direct children snapshot\n")
                f.write((child_overview or "- 无子模块") + "\n")
        except Exception:
            # Debug log failure should not block generation.
            pass

    def append_agent_io_snapshot(
        self,
        log_path: str,
        *,
        stage: str,
        system: str,
        prompt: str,
        output: str = "",
        error: str = "",
    ):
        """Append explicit agent input/output snapshot for deterministic debugging."""
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write(f"## Pass3 InStrack Agent {stage}\n")
                f.write("=" * 80 + "\n\n")
                if stage.lower() == "input":
                    f.write("### System\n")
                    f.write((system or "") + "\n\n")
                    f.write("### Prompt\n")
                    f.write((prompt or "") + "\n")
                else:
                    if error:
                        f.write("### Error\n")
                        f.write(error + "\n\n")
                    f.write("### Output\n")
                    f.write((output or "") + "\n")
        except Exception:
            # Debug log failure should not block generation.
            pass
