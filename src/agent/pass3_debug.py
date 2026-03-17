"""Pass3 debug logging helpers.

This module encapsulates debug logging infrastructure for pass3 generation,
including fork trace logging and agent I/O snapshots.
"""

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict

from .pass3_utils import safe_slug


class Pass3Debug:
    """Helper object encapsulating pass3 debug logging infrastructure."""

    CONTEXT_WINDOW_TOKENS = 200000

    def __init__(self, generator: Any):
        self.g = generator
        self._reset_agent_logs: set = set()
        self._printed_instrack_stage_headers: set = set()

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
        """Create a stable debug log path for one pass3 instrack agent invocation.

        The file is overwritten on the first write in each run so repeated
        executions update the same debug artifact instead of creating R000x
        variants.
        """
        inst_slug = safe_slug(instruction or "unknown")
        node_slug = safe_slug((node_path or "top").replace("/", "__"))
        role_slug = safe_slug(role or "agent")
        log_path = self.project_log_path(
            f"pass3_instrack_{role_slug}_{inst_slug}_{node_slug}_L{level}"
        )
        if log_path not in self._reset_agent_logs:
            try:
                Path(log_path).write_text("", encoding="utf-8")
            except Exception:
                pass
            self._reset_agent_logs.add(log_path)
        return log_path

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

    def fork_trace_log_path_pass3_3_1(self) -> Path:
        """Get path for the pass3.3.1 fork trace log."""
        log_dir = self.g.owner.chip_debug_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "debug_pass3_3_1_fork_trace.jsonl"

    def fork_trace_log_path_pass3_3_2(self) -> Path:
        """Get path for the pass3.3.2 fork trace log."""
        log_dir = self.g.owner.chip_debug_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "debug_pass3_3_2_fork_trace.jsonl"

    def append_fork_trace(self, event: str, payload: Dict[str, Any]):
        """Append one fork-related trace record as JSONL."""
        record = {
            "ts": datetime.datetime.now().isoformat(),
            "event": event,
        }
        record.update(payload or {})
        try:
            # Emit concise runtime progress to terminal for pass3.3 instrack fork flows.
            prompt_style = str((payload or {}).get("prompt_style") or "")
            pass_name = str((payload or {}).get("pass") or "")
            is_instrack = prompt_style.startswith("instrack") or pass_name.startswith("pass3_3")
            if is_instrack:
                stage_tag = "pass3.3"
                if prompt_style == "instrack_search" or pass_name == "pass3_3_1":
                    stage_tag = "pass3.3.1/search"
                elif prompt_style == "instrack_draw" or pass_name in {"pass3_3_2_draw", "pass3_3_2_orchestrate"}:
                    stage_tag = "pass3.3.2/orchestrate"
                elif pass_name == "pass3_3_3_render":
                    stage_tag = "pass3.3.3/render"
                elif prompt_style.startswith("instrack"):
                    stage_tag = f"pass3.3/{prompt_style}"

                self._print_instrack_stage_header(stage_tag)
                if event in {"fork_dispatch", "pass3_1_fork_dispatch", "pass3_2_fork_dispatch"}:
                    child_inst = str((payload or {}).get("child_instance") or "?")
                    child_mod = str((payload or {}).get("child_module") or "?")
                    parent_level = int((payload or {}).get("parent_level") or 0)
                    child_level = int((payload or {}).get("child_level") or (parent_level + 1))
                    indent = self._instrack_indent(child_level)
                    print(f"{indent}dispatch L{parent_level}->L{child_level} {child_inst}({child_mod})")
                elif event in {"fork_return", "pass3_1_fork_return", "pass3_2_fork_return"}:
                    child_inst = str((payload or {}).get("child_instance") or "?")
                    child_mod = str((payload or {}).get("child_module") or "?")
                    child_level = int((payload or {}).get("child_level") or 0)
                    prompt_tokens = int((payload or {}).get("prompt_tokens") or 0)
                    context_pct = (prompt_tokens / float(self.CONTEXT_WINDOW_TOKENS)) * 100.0
                    indent = self._instrack_indent(child_level)
                    print(
                        f"{indent}return   L{child_level} {child_inst}({child_mod}) "
                        f"prompt_tokens={prompt_tokens} ctx={context_pct:.1f}%"
                    )
                elif event in {"fork_rejected", "pass3_1_fork_rejected", "pass3_2_fork_rejected"}:
                    module_selector = str((payload or {}).get("module_selector") or "")
                    reason = str((payload or {}).get("reason") or "")
                    child_level = int((payload or {}).get("child_level") or (payload or {}).get("parent_level") or 0)
                    if len(reason) > 220:
                        reason = reason[:220] + "..."
                    indent = self._instrack_indent(child_level)
                    print(f"{indent}rejected L{child_level} {module_selector} | {reason}")

            # Keep a dedicated pass3.3.1 trace stream for easier triage.
            is_pass3_3_1 = pass_name == "pass3_3_1" or prompt_style == "instrack_search"
            if is_pass3_3_1:
                trace_331 = self.fork_trace_log_path_pass3_3_1()
                with open(trace_331, "a", encoding="utf-8") as f331:
                    f331.write(json.dumps(record, ensure_ascii=False) + "\n")

            is_pass3_3_2 = pass_name in {"pass3_3_2_draw", "pass3_3_2_orchestrate"} or prompt_style == "instrack_draw"
            if is_pass3_3_2:
                trace_332 = self.fork_trace_log_path_pass3_3_2()
                with open(trace_332, "a", encoding="utf-8") as f332:
                    f332.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # Debug log failure should not block the generation flow.
            pass

    def _print_instrack_stage_header(self, stage_tag: str):
        """Print each instrack stage header once for cleaner terminal logs."""
        if stage_tag in self._printed_instrack_stage_headers:
            return
        self._printed_instrack_stage_headers.add(stage_tag)
        print("")
        print(f"[InStrack][{stage_tag}]")
        print("-" * (len(stage_tag) + 12))

    @staticmethod
    def _instrack_indent(level: int) -> str:
        """Return a stable indentation prefix based on hierarchy level."""
        safe_level = max(int(level or 0), 0)
        return "  " * safe_level + "- "

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
                    f.write(self._compact_system_snapshot(system or "") + "\n\n")
                    f.write("### Prompt\n")
                    f.write(self._compact_prompt_snapshot(prompt or "") + "\n")
                else:
                    if error:
                        f.write("### Error\n")
                        f.write(error + "\n\n")
                    f.write("### Output\n")
                    f.write((output or "") + "\n")
        except Exception:
            # Debug log failure should not block generation.
            pass

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _truncate_text(text: str, *, max_lines: int, max_chars: int) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        lines = raw.splitlines()
        clipped = "\n".join(lines[:max_lines]).strip()
        if len(clipped) > max_chars:
            clipped = clipped[: max_chars - 3].rstrip() + "..."
        omitted_lines = max(0, len(lines) - max_lines)
        omitted_chars = max(0, len(raw) - len(clipped))
        if omitted_lines > 0 or omitted_chars > 0:
            clipped += (
                f"\n\n[debug-compact] omitted_lines={omitted_lines} "
                f"omitted_chars~={omitted_chars}"
            )
        return clipped

    def _compact_system_snapshot(self, system: str) -> str:
        text = str(system or "").strip()
        if not text:
            return ""
        header = (
            f"[debug-compact] chars={len(text)} lines={len(text.splitlines())} "
            f"sha256={self._hash_text(text)}"
        )
        return header + "\n\n" + self._truncate_text(text, max_lines=18, max_chars=2200)

    def _compact_prompt_snapshot(self, prompt: str) -> str:
        text = str(prompt or "").strip()
        if not text:
            return ""
        header = (
            f"[debug-compact] chars={len(text)} lines={len(text.splitlines())} "
            f"sha256={self._hash_text(text)}"
        )
        sections = self._split_markdown_sections(text)
        if not sections:
            return header + "\n\n" + self._truncate_text(text, max_lines=40, max_chars=5000)

        rendered = [header]
        for title, body in sections:
            rendered.append("")
            rendered.append(f"## {title}")
            rendered.append(self._compact_prompt_section(title, body))
        return "\n".join(rendered).strip()

    @staticmethod
    def _split_markdown_sections(text: str) -> list:
        pattern = re.compile(r"^##\s+(.*)$", flags=re.M)
        matches = list(pattern.finditer(text or ""))
        if not matches:
            return []
        sections = []
        for idx, match in enumerate(matches):
            title = (match.group(1) or "").strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[start:end].strip("\n")
            sections.append((title, body.strip()))
        return sections

    def _compact_prompt_section(self, title: str, body: str) -> str:
        if title in {"Current Module Description", "Current Module Preview"}:
            return self._truncate_text(body, max_lines=28, max_chars=2600)
        if title == "Direct Child Preview List":
            return self._truncate_text(body, max_lines=28, max_chars=2600)
        if title == "Instruction Datasheet":
            return self._truncate_text(body, max_lines=22, max_chars=1800)
        if title in {"Draw State", "Continuation State"}:
            return self._truncate_text(body, max_lines=8, max_chars=1800)
        return self._truncate_text(body, max_lines=16, max_chars=1800)
