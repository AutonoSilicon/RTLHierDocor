"""Bridge APV-like condition YAML to dep->local connectivity relations.

This module performs static extraction only:
- Parse APV-style YAML task conditions.
- Locate sub-expressions containing $dep references.
- Extract local (non-$dep) signal candidates from the same sub-expression.

It intentionally does not evaluate conditions or depend on FSDB runtime state.
"""

from dataclasses import dataclass
import re
from typing import Any, Iterable, List, Optional, Set


@dataclass
class DepRelation:
    """A dep->local relation extracted from one condition fragment."""

    relation_id: str
    task_id: str
    dep_task_id: str
    dep_signal: str
    local_signal: str
    expr_fragment: str


class DepConditionBridge:
    """Parse APV-style YAML and extract dep->local relation pairs."""

    _dep_ref_re = re.compile(r"\$dep\.(\w+)\.(\w+)")
    _logic_split_re = re.compile(r"\s*(?:&&|\|\||\band\b|\bor\b)\s*")
    _signal_token_re = re.compile(
        r"[A-Za-z_][A-Za-z0-9_{}]*(?:\.[A-Za-z_][A-Za-z0-9_{}]*)*(?:\[\s*\d+\s*:\s*\d+\s*\]|\[\s*\d+\s*\])?"
    )

    _reserved_tokens = {
        "and", "or", "not", "in", "True", "False", "None", "_split"
    }

    def _parse_tasks_from_text(self, text: str) -> List[dict]:
        """Parse APV-style YAML text and extract task id + condition.

        This parser is intentionally lightweight and dependency-free.
        It supports the project conventions used in AgenticPipeViewer tests:
        - tasks:
          - id: <task_id>
            condition: <str or list>
        """
        lines = text.splitlines()
        tasks_started = False

        tasks: List[dict] = []
        current_task = None
        in_condition_block = False
        condition_indent = -1

        def _flush_task():
            nonlocal current_task
            if not current_task:
                return
            cond_items = current_task.get("_condition_items", [])
            if cond_items:
                current_task["condition"] = " ".join(cond_items).strip()
            else:
                current_task.setdefault("condition", "")
            current_task.pop("_condition_items", None)
            tasks.append(current_task)
            current_task = None

        for raw_line in lines:
            line_no_comment = raw_line.split("#", 1)[0]
            if not line_no_comment.strip():
                continue

            indent = len(line_no_comment) - len(line_no_comment.lstrip())
            stripped = line_no_comment.strip()

            if not tasks_started:
                if stripped == "tasks:":
                    tasks_started = True
                continue

            task_match = re.match(r"^-\s*id\s*:\s*(.+)$", stripped)
            if task_match:
                _flush_task()
                task_id = task_match.group(1).strip().strip('"').strip("'")
                current_task = {
                    "id": task_id,
                    "condition": "",
                    "_condition_items": [],
                }
                in_condition_block = False
                condition_indent = -1
                continue

            if current_task is None:
                continue

            condition_match = re.match(r"^condition\s*:\s*(.*)$", stripped)
            if condition_match:
                rest = condition_match.group(1).strip()
                if rest:
                    current_task["condition"] = rest.strip('"').strip("'")
                    in_condition_block = False
                    condition_indent = -1
                else:
                    in_condition_block = True
                    condition_indent = indent
                continue

            if in_condition_block:
                if indent <= condition_indent:
                    in_condition_block = False
                    condition_indent = -1
                else:
                    cond_item_match = re.match(r"^-\s*(.+)$", stripped)
                    if cond_item_match:
                        item = cond_item_match.group(1).strip().strip('"').strip("'")
                        if item:
                            current_task["_condition_items"].append(item)
                    continue

        _flush_task()
        return tasks

    def extract_relations(
        self,
        yaml_path: str,
        task_filters: Optional[Iterable[str]] = None,
    ) -> List[DepRelation]:
        """Extract dep->local relations from APV-style YAML task conditions."""
        with open(yaml_path, "r", encoding="utf-8") as handle:
            text = handle.read()

        tasks = self._parse_tasks_from_text(text)

        allowed: Optional[Set[str]] = None
        if task_filters is not None:
            allowed = {item for item in task_filters if item}

        relations: List[DepRelation] = []
        rel_index = 0

        for task_obj in tasks:
            if not isinstance(task_obj, dict):
                continue

            task_id = str(task_obj.get("id", "")).strip()
            if not task_id:
                continue
            if allowed is not None and task_id not in allowed:
                continue

            condition_text = self._normalize_condition(task_obj.get("condition", ""))
            if not condition_text:
                continue

            fragments = self._split_condition_fragments(condition_text)
            for fragment in fragments:
                dep_refs = self._extract_dep_refs(fragment)
                if not dep_refs:
                    continue

                local_signals = self._extract_local_signals(fragment)
                if not local_signals:
                    continue

                for dep_task_id, dep_signal in dep_refs:
                    for local_signal in local_signals:
                        rel_index += 1
                        relations.append(
                            DepRelation(
                                relation_id=f"rel_{rel_index:05d}",
                                task_id=task_id,
                                dep_task_id=dep_task_id,
                                dep_signal=dep_signal,
                                local_signal=local_signal,
                                expr_fragment=fragment.strip(),
                            )
                        )

        return relations

    def _normalize_condition(self, cond: Any) -> str:
        if isinstance(cond, str):
            return cond.strip()
        if isinstance(cond, list):
            return " ".join(str(item).strip() for item in cond if str(item).strip())
        if cond is None:
            return ""
        return str(cond).strip()

    def _split_condition_fragments(self, condition_text: str) -> List[str]:
        text = condition_text.replace("\n", " ")
        parts = self._logic_split_re.split(text)
        result = [item.strip() for item in parts if item and item.strip()]
        return result if result else [condition_text]

    def _extract_dep_refs(self, fragment: str) -> List[tuple]:
        refs = self._dep_ref_re.findall(fragment)
        deduped: List[tuple] = []
        seen: Set[str] = set()
        for dep_task_id, dep_signal in refs:
            key = f"{dep_task_id}.{dep_signal}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append((dep_task_id, dep_signal))
        return deduped

    def _extract_local_signals(self, fragment: str) -> List[str]:
        removed_dep = self._dep_ref_re.sub(" ", fragment)
        removed_dep = re.sub(r"\$split\s*\(", " ", removed_dep)
        removed_literals = re.sub(r"\d+'[bhdoBHDO][0-9a-fA-F_xXzZ]+", " ", removed_dep)

        candidates: List[str] = []
        seen: Set[str] = set()
        for token in self._signal_token_re.findall(removed_literals):
            if token in self._reserved_tokens:
                continue
            if token.startswith("$"):
                continue
            if token.startswith("_"):
                continue
            if token in seen:
                continue
            seen.add(token)
            candidates.append(token)

        return candidates