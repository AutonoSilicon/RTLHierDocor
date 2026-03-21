"""Pass3.3.3 APV fragment generation helpers."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .prompts import PASS3_3_3_APV_PROMPT, PASS3_3_3_APV_SYSTEM


class Pass3InStrackAPV:
    """Helper object encapsulating pass3.3.3 APV generation."""

    _ALLOWED_MATCH_MODES = {"first", "all", "unique_per_var"}
    _MATCH_MODE_ALIASES = {
        "single": "first",
        "once": "first",
        "condition": "first",
    }
    _REF_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
    _TASK_ID_LIKE_RE = re.compile(r"^s\d{2}_t\d{2}_[a-z0-9_]+$")
    _DEP_REFERENCE_RE = re.compile(r"\$dep\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
    _DEP_BARE_REF_RE = re.compile(r"\$dep\.([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_.])")
    _ASSIGNMENT_RE = re.compile(r"(?<![<>=!])=(?!=)")
    _SIGNAL_TOKEN_RE = re.compile(
        r"[A-Za-z_][A-Za-z0-9_{}]*(?:\.[A-Za-z_][A-Za-z0-9_{}]*)*(?:\[\s*\d+\s*:\s*\d+\s*\]|\[\s*\d+\s*\])?"
    )
    _INDEX_RE = re.compile(r"\[[^\]]+\]")
    _CONST_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]*")
    _RESERVED_TOKENS = {
        "and",
        "or",
        "not",
        "if",
        "else",
        "true",
        "false",
        "none",
        "True",
        "False",
        "None",
    }

    def __init__(self, generator: Any):
        self.g = generator

    async def generate_instruction_apv(
        self,
        *,
        top_node: Any,
        instruction: str,
        instruction_slug: str,
        instruction_datasheet: str,
        parsed_search: Dict[str, Any],
        orchestrate_items: List[Dict[str, Any]],
        orchestrate_index_text: str,
        artifacts_dir: Path,
        cached_items: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        cached_by_path = {
            str(item.get("path") or "").strip(): dict(item)
            for item in list(cached_items or [])
            if isinstance(item, dict)
        }

        items: List[Dict[str, Any]] = []
        previous_leaf_context: Optional[Dict[str, Any]] = None

        for item_index, item in enumerate(orchestrate_items):
            path_key = self._item_path(item)
            result = await self._generate_single_item(
                top_node=top_node,
                instruction=instruction,
                instruction_slug=instruction_slug,
                instruction_datasheet=instruction_datasheet,
                item=item,
                item_index=item_index,
                previous_leaf_context=previous_leaf_context,
                artifacts_dir=artifacts_dir,
                cached_entry=cached_by_path.get(path_key),
            )
            items.append(result)
            previous_leaf_context = self._build_leaf_context_from_entry(result)

        index_payload = {
            "instruction": instruction,
            "start_module": parsed_search.get("start_module", ""),
            "schema_version": "pass3_3_3_apv_index_v1",
            "items": items,
        }
        index_text = json.dumps(index_payload, ensure_ascii=False, indent=2)
        input_hash = self.g._hash.build_pass3_3_apv_index_input_hash(
            top_module=top_node.module_name,
            instruction=instruction,
            start_module=str(parsed_search.get("start_module") or "").strip(),
            orchestration_json_text=orchestrate_index_text,
        )
        return {
            "items": items,
            "index_payload": index_payload,
            "index_text": index_text,
            "input_hash": input_hash,
        }

    async def _generate_single_item(
        self,
        *,
        top_node: Any,
        instruction: str,
        instruction_slug: str,
        instruction_datasheet: str,
        item: Dict[str, Any],
        item_index: int,
        previous_leaf_context: Optional[Dict[str, Any]],
        artifacts_dir: Path,
        cached_entry: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        path_slug = self._path_slug(item)
        item_path = self._item_path(item)
        artifact_yaml = f"instrack/{instruction_slug}/artifacts/{path_slug}.apv.yaml"
        artifact_raw = f"instrack/{instruction_slug}/artifacts/{path_slug}.apv.raw.md"
        yaml_path = artifacts_dir / f"{path_slug}.apv.yaml"
        raw_path = artifacts_dir / f"{path_slug}.apv.raw.md"

        item_json_text = json.dumps(self._build_current_item_context(item), ensure_ascii=False, indent=2)
        visible_dep_payload = self._build_visible_dep_payload(previous_leaf_context)
        visible_dep_text = json.dumps(visible_dep_payload, ensure_ascii=False, indent=2)
        item_input_hash = self.g._hash.build_pass3_3_apv_item_input_hash(
            top_module=top_node.module_name,
            instruction=instruction,
            item_json_text=item_json_text,
            previous_leaf_json_text=visible_dep_text,
        )

        cached_ok = False
        if isinstance(cached_entry, dict) and cached_entry.get("input_hash") == item_input_hash:
            cached_ok = self.g.owner.project_tracker.is_done(
                artifact_yaml,
                str(yaml_path),
                expected_input_hash=item_input_hash,
            )
        if cached_ok:
            return self._normalize_cached_entry(cached_entry, artifact_yaml, artifact_raw)

        module_name = str(item.get("module") or "").strip()
        current_module_topology = self.g._prompts.build_instrack_current_module_topology(module_name)
        prompt = PASS3_3_3_APV_PROMPT.format(
            instruction=instruction,
            instruction_datasheet=instruction_datasheet or "Instruction unavailable",
            current_module_topology=current_module_topology,
            current_item_json=item_json_text,
            visible_dep_json=visible_dep_text,
        )
        log_path = self.g._build_pass3_instrack_agent_log_path(
            instruction=instruction,
            node_path=item_path or path_slug,
            level=max(0, item_path.count("/")),
            role="apv_agent",
        )

        self.g._append_agent_io_snapshot(
            log_path,
            stage="Input",
            system=PASS3_3_3_APV_SYSTEM,
            prompt=prompt,
            prompt_redaction_policy="instrack_context",
        )

        content, token_stats = await self.g.owner.llm.generate(
            PASS3_3_3_APV_SYSTEM,
            prompt,
            log_path=log_path,
        )

        self.g._append_agent_io_snapshot(
            log_path,
            stage="Output",
            system="",
            prompt="",
            output=content or "",
        )

        raw_text = str(content or "")
        raw_path.write_text(raw_text, encoding="utf-8")
        self.g.owner.project_tracker.update(
            artifact_raw,
            raw_text,
            input_hash=item_input_hash,
            meta={
                "top_module": top_node.module_name,
                "instruction": instruction,
                "module": module_name,
                "instance": str(item.get("instance") or "").strip(),
                "path": item_path,
                "pass": "pass3_3_3_apv_raw",
            },
        )

        parsed_payload = self._parse_model_payload(raw_text)
        status = str(parsed_payload.get("status") or "partial").strip().lower()
        unknown = list(parsed_payload.get("unknown") or [])
        tasks, task_errors = self._materialize_tasks(
            top_node=top_node,
            item=item,
            item_index=item_index,
            raw_tasks=list(parsed_payload.get("tasks") or []),
            previous_leaf_context=previous_leaf_context,
        )
        if task_errors:
            unknown.extend(task_errors)
            status = "partial"
        if status == "complete" and not tasks:
            unknown.append("Model returned complete without any valid tasks.")
            status = "partial"

        yaml_text = self._render_yaml(item=item, status=status, unknown=unknown, tasks=tasks)
        yaml_path.write_text(yaml_text, encoding="utf-8")
        self.g.owner.project_tracker.update(
            artifact_yaml,
            yaml_text,
            input_hash=item_input_hash,
            meta={
                "top_module": top_node.module_name,
                "instruction": instruction,
                "module": module_name,
                "instance": str(item.get("instance") or "").strip(),
                "path": item_path,
                "pass": "pass3_3_3_apv",
            },
        )

        entry = {
            "module": module_name,
            "instance": str(item.get("instance") or "").strip(),
            "path": item_path,
            "status": status,
            "unknown": unknown,
            "artifact_yaml": artifact_yaml,
            "artifact_raw": artifact_raw,
            "task_ids": [task["id"] for task in tasks],
            "task_count": len(tasks),
            "first_task_id": tasks[0]["id"] if tasks else "",
            "leaf_task_id": tasks[-1]["id"] if tasks else "",
            "leaf_ref_name": str(tasks[-1].get("ref_name") or "").strip() if tasks else "",
            "leaf_capture_names": list(tasks[-1].get("capture_names") or []) if tasks else [],
            "input_hash": item_input_hash,
            "token_stats": dict(token_stats or {}),
        }
        return entry

    def _parse_model_payload(self, text: str) -> Dict[str, Any]:
        json_text = self.g._extract_json_code(text or "") or str(text or "").strip()
        try:
            payload = json.loads(json_text)
        except Exception as exc:
            return {
                "status": "partial",
                "tasks": [],
                "unknown": [f"Invalid APV JSON output: {exc}"],
            }

        unknown = self._normalize_unknown(payload.get("unknown"))
        status = str(payload.get("status") or "partial").strip().lower()
        if status not in {"complete", "partial"}:
            unknown.append(f"Unsupported APV status: {status or '<empty>'}.")
            status = "partial"
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            unknown.append("`tasks` must be a list.")
            tasks = []
        return {
            "status": status,
            "tasks": tasks,
            "unknown": unknown,
        }

    def _materialize_tasks(
        self,
        *,
        top_node: Any,
        item: Dict[str, Any],
        item_index: int,
        raw_tasks: List[Dict[str, Any]],
        previous_leaf_context: Optional[Dict[str, Any]],
    ) -> (List[Dict[str, Any]], List[str]):
        scope_prefix = self._scope_prefix(item_path=self._item_path(item), top_node=top_node)
        root_prefixes = self._root_prefixes(top_node)
        tasks: List[Dict[str, Any]] = []
        errors: List[str] = []
        dep_symbols: Dict[str, Dict[str, Any]] = {}
        if previous_leaf_context:
            upstream_ref_name = str(previous_leaf_context.get("ref_name") or "").strip()
            upstream_task_id = str(previous_leaf_context.get("task_id") or "").strip()
            upstream_capture_names = list(previous_leaf_context.get("capture_names") or [])
            if upstream_ref_name and upstream_task_id and upstream_capture_names:
                dep_symbols[upstream_ref_name] = dict(previous_leaf_context)

        for task_index, raw_task in enumerate(raw_tasks):
            task_errors: List[str] = []
            if not isinstance(raw_task, dict):
                errors.append(f"Task #{task_index + 1} is not an object.")
                continue

            ref_name = str(raw_task.get("ref_name") or "").strip()
            task_name = str(raw_task.get("task_name") or raw_task.get("name") or "").strip()
            condition_lines = self._coerce_string_list(
                raw_task.get("condition_lines")
                if raw_task.get("condition_lines") is not None
                else raw_task.get("condition")
            )
            capture_signals = self._coerce_string_list(
                raw_task.get("capture_signals")
                if raw_task.get("capture_signals") is not None
                else raw_task.get("capture")
            )
            logging_lines = self._coerce_string_list(
                raw_task.get("logging_lines")
                if raw_task.get("logging_lines") is not None
                else raw_task.get("logging")
            )
            raw_match_mode = str(raw_task.get("match_mode") or "first").strip().lower()
            match_mode = self._MATCH_MODE_ALIASES.get(raw_match_mode, raw_match_mode)
            legacy_dep_source = str(raw_task.get("dep_source") or "").strip()
            legacy_dep_leaf_name = str(raw_task.get("dep_leaf_name") or "").strip()

            try:
                max_match = int(raw_task.get("max_match", 0))
            except Exception:
                errors.append(f"Task `{task_name or task_index}` has non-integer `max_match`.")
                continue

            if not ref_name:
                task_errors.append(f"Task `{task_name or task_index}` is missing `ref_name`.")
            elif not self._REF_NAME_RE.fullmatch(ref_name):
                task_errors.append(
                    f"Task `{task_name or task_index}` uses invalid `ref_name={ref_name}`; use snake_case like `decode_accept`."
                )
            elif self._TASK_ID_LIKE_RE.fullmatch(ref_name):
                task_errors.append(
                    f"Task `{task_name or task_index}` uses task-id-like `ref_name={ref_name}`; raw APV JSON must use stable aliases, not final task ids."
                )
            elif ref_name in dep_symbols:
                task_errors.append(
                    f"Task `{task_name or task_index}` uses duplicate `ref_name={ref_name}` which is already visible in the dep namespace."
                )
            if not task_name:
                task_errors.append(f"Task #{task_index + 1} is missing `task_name`.")
            if not condition_lines:
                task_errors.append(f"Task `{task_name or task_index}` is missing `condition_lines`.")
            if not capture_signals:
                task_errors.append(f"Task `{task_name or task_index}` is missing `capture_signals`.")
            if not logging_lines:
                task_errors.append(f"Task `{task_name or task_index}` is missing `logging_lines`.")
            if match_mode not in self._ALLOWED_MATCH_MODES:
                task_errors.append(
                    f"Task `{task_name or task_index}` uses unsupported `match_mode={raw_match_mode}`."
                )
            if max_match < 0:
                task_errors.append(f"Task `{task_name or task_index}` uses negative `max_match`.")

            if task_errors:
                errors.extend(task_errors)
                continue

            dep_references = [
                (match.group(1), match.group(2))
                for line in condition_lines
                for match in self._DEP_REFERENCE_RE.finditer(str(line or ""))
            ]
            dep_ref_names = sorted({dep_ref_name for dep_ref_name, _ in dep_references})
            bare_dep_refs = sorted(
                {
                    match.group(1)
                    for line in condition_lines
                    for match in self._DEP_BARE_REF_RE.finditer(str(line or ""))
                }
            )
            future_ref_names = {
                str(future_task.get("ref_name") or "").strip()
                for future_task in raw_tasks[task_index + 1 :]
                if isinstance(future_task, dict)
            }
            dep_task_id = ""
            if bare_dep_refs:
                task_errors.append(
                    f"Task `{task_name}` must use full `$dep.<ref_name>.<signal>` references in `condition_lines`, got bare refs: {', '.join(bare_dep_refs)}."
                )
            elif len(dep_ref_names) > 1:
                task_errors.append(
                    f"Task `{task_name}` must reference at most one unique dep `ref_name` in `condition_lines`, got: {', '.join(dep_ref_names)}."
                )
            elif dep_ref_names:
                dep_ref_name = dep_ref_names[0]
                dep_symbol = dep_symbols.get(dep_ref_name)
                if not dep_symbol:
                    available_ref_names = ", ".join(sorted(dep_symbols.keys())) or "<none>"
                    if self._TASK_ID_LIKE_RE.fullmatch(dep_ref_name):
                        task_errors.append(
                            f"Task `{task_name}` must not emit final `$dep.<task_id>.<signal>` references in raw APV JSON; use a declared `ref_name` instead of `{dep_ref_name}`."
                        )
                    elif dep_ref_name in future_ref_names:
                        task_errors.append(
                            f"Task `{task_name}` forward-references `ref_name={dep_ref_name}` before that task is declared."
                        )
                    else:
                        task_errors.append(
                            f"Task `{task_name}` references unknown dep `ref_name={dep_ref_name}`; available: {available_ref_names}."
                        )
                else:
                    dep_task_id = str(dep_symbol.get("task_id") or "").strip()
                    available_capture_names = list(dep_symbol.get("capture_names") or [])
                    missing_capture_names = sorted(
                        {
                            capture_name
                            for current_ref_name, capture_name in dep_references
                            if current_ref_name == dep_ref_name and capture_name not in available_capture_names
                        }
                    )
                    if missing_capture_names:
                        task_errors.append(
                            f"Task `{task_name}` references unknown dep capture name(s) for `ref_name={dep_ref_name}`: {', '.join(missing_capture_names)}; available: {', '.join(available_capture_names)}."
                        )
                    if not dep_task_id:
                        task_errors.append(
                            f"Task `{task_name}` is missing the resolved upstream task id for `ref_name={dep_ref_name}`."
                        )
                if legacy_dep_source:
                    task_errors.append(
                        f"Task `{task_name}` must not emit legacy `dep_source={legacy_dep_source}`; use `$dep.<ref_name>.<signal>` references only."
                    )
                if legacy_dep_leaf_name:
                    task_errors.append(
                        f"Task `{task_name}` must not emit legacy `dep_leaf_name={legacy_dep_leaf_name}`; use `$dep.<ref_name>.<signal>` references only."
                    )
            elif legacy_dep_source or legacy_dep_leaf_name:
                task_errors.append(
                    f"Task `{task_name}` declares legacy dependency fields without any `$dep.<ref_name>.<signal>` reference in `condition_lines`."
                )

            if task_errors:
                errors.extend(task_errors)
                continue

            task_id = self._build_task_id(item=item, item_index=item_index, task_index=task_index)
            normalized_conditions = [
                self._normalize_signal_expression(
                    self._normalize_condition_expression(line),
                    scope_prefix,
                    root_prefixes,
                )
                for line in condition_lines
            ]
            if dep_references and dep_task_id:
                normalized_conditions = [
                    self._DEP_REFERENCE_RE.sub(lambda m: f"$dep.{dep_task_id}.{m.group(2)}", line)
                    for line in normalized_conditions
                ]

            normalized_capture = [
                self._normalize_signal_expression(signal, scope_prefix, root_prefixes)
                for signal in capture_signals
            ]
            capture_names = self._capture_names(normalized_capture)
            if not capture_names:
                errors.append(f"Task `{task_name}` does not expose any usable capture names.")
                continue

            tasks.append(
                {
                    "id": task_id,
                    "ref_name": ref_name,
                    "task_name": task_name,
                    "depends_on": dep_task_id,
                    "condition_lines": normalized_conditions,
                    "capture_signals": normalized_capture,
                    "logging_lines": logging_lines,
                    "match_mode": match_mode,
                    "max_match": max_match,
                    "capture_names": capture_names,
                }
            )
            dep_symbols[ref_name] = self._build_dep_symbol(
                ref_name=ref_name,
                task_id=task_id,
                capture_names=capture_names,
                item=item,
            )

        return tasks, errors

    def _render_yaml(self, *, item: Dict[str, Any], status: str, unknown: List[str], tasks: List[Dict[str, Any]]) -> str:
        lines = [
            f"module: {self._yaml_quote(str(item.get('module') or '').strip())}",
            f"instance: {self._yaml_quote(str(item.get('instance') or '').strip())}",
            f"path: {self._yaml_quote(self._item_path(item))}",
            f"status: {self._yaml_quote(status)}",
        ]
        if unknown:
            lines.append("unknown:")
            for reason in unknown:
                lines.append(f"  - {self._yaml_quote(reason)}")
        else:
            lines.append("unknown: []")

        if not tasks:
            lines.append("tasks: []")
            return "\n".join(lines).strip() + "\n"

        lines.append("tasks:")
        for task in tasks:
            lines.append(f"  - id: {self._yaml_quote(task['id'])}")
            lines.append(f"    name: {self._yaml_quote(task['task_name'])}")
            if task.get("depends_on"):
                lines.append(f"    dependsOn: {self._yaml_quote(task['depends_on'])}")
            lines.append(f"    matchMode: {self._yaml_quote(task['match_mode'])}")
            lines.append(f"    maxMatch: {int(task['max_match'])}")
            lines.append("    condition:")
            for entry in task.get("condition_lines") or []:
                lines.append(f"      - {self._yaml_quote(entry)}")
            lines.append("    capture:")
            for entry in task.get("capture_signals") or []:
                lines.append(f"      - {self._yaml_quote(entry)}")
            lines.append("    logging:")
            for entry in task.get("logging_lines") or []:
                lines.append(f"      - {self._yaml_quote(entry)}")
        return "\n".join(lines).strip() + "\n"

    def _normalize_condition_expression(self, text: str) -> str:
        raw_text = str(text or "").strip()
        if not raw_text:
            return ""

        matches = list(self._ASSIGNMENT_RE.finditer(raw_text))
        if len(matches) != 1:
            return raw_text

        match = matches[0]
        lhs = raw_text[: match.start()].strip()
        rhs = raw_text[match.end() :].strip()
        if not lhs or not rhs:
            return raw_text
        return f"{lhs} == ({rhs})"

    def _normalize_signal_expression(self, text: str, scope_prefix: str, root_prefixes: List[str]) -> str:
        raw_text = str(text or "").strip()
        if not raw_text:
            return ""

        def _replace(match: re.Match) -> str:
            token = match.group(0)
            if match.start() > 0 and raw_text[match.start() - 1] in {"$", "'"}:
                return token
            if token in self._RESERVED_TOKENS or token.lower() in self._RESERVED_TOKENS:
                return token
            if "." not in token and self._CONST_TOKEN_RE.fullmatch(token):
                return token
            normalized = self._strip_root_prefix(token, root_prefixes)
            if "." in normalized:
                return normalized
            if scope_prefix:
                return f"{scope_prefix}.{normalized}"
            return normalized

        return self._SIGNAL_TOKEN_RE.sub(_replace, raw_text)

    def _strip_root_prefix(self, token: str, root_prefixes: List[str]) -> str:
        normalized = str(token or "").strip()
        for prefix in root_prefixes:
            if prefix and normalized.startswith(prefix + "."):
                return normalized[len(prefix) + 1 :]
        return normalized

    def _scope_prefix(self, *, item_path: str, top_node: Any) -> str:
        parts = [part for part in str(item_path or "").split("/") if part]
        if not parts:
            return ""
        root_candidates = {
            str(getattr(top_node, "instance_name", "") or "").strip(),
            str(getattr(top_node, "module_name", "") or "").strip(),
        }
        if len(parts) > 1 or (parts and parts[0] in root_candidates):
            if parts[0] in root_candidates or len(parts) > 1:
                parts = parts[1:]
        return ".".join(parts)

    def _root_prefixes(self, top_node: Any) -> List[str]:
        prefixes: List[str] = []
        for candidate in (
            str(getattr(top_node, "instance_name", "") or "").strip(),
            str(getattr(top_node, "module_name", "") or "").strip(),
        ):
            if candidate and candidate not in prefixes:
                prefixes.append(candidate)
        return prefixes

    def _capture_names(self, capture_signals: List[str]) -> List[str]:
        capture_names: List[str] = []
        for signal in capture_signals:
            token = str(signal or "").strip()
            if not token:
                continue
            token = self._strip_dep_prefix(token)
            token = self._INDEX_RE.sub("", token)
            capture_name = token.split(".")[-1].strip()
            if capture_name and capture_name not in capture_names:
                capture_names.append(capture_name)
        return capture_names

    @staticmethod
    def _strip_dep_prefix(token: str) -> str:
        text = str(token or "")
        if text.startswith("$dep."):
            parts = text.split(".", 2)
            if len(parts) == 2:
                return parts[1]
            if len(parts) == 3:
                return parts[2]
        if text.startswith("$dep_leaf."):
            return text.split(".", 1)[1]
        return text

    @staticmethod
    def _coerce_string_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _normalize_unknown(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    def _build_current_item_context(self, item: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(item.get("orchestration") or {})
        lifecycle_context = str(payload.get("lifecycle_context") or "").strip()
        boundary_takeover = list(payload.get("boundary_takeover") or [])
        boundary_handoffs = list(payload.get("boundary_handoffs") or [])
        confidence = str(payload.get("confidence") or "").strip().lower()
        unknown = self._normalize_unknown(payload.get("unknown"))

        instruction_state: Dict[str, Any] = {
            "lifecycle_context": lifecycle_context,
            "boundary_takeover": boundary_takeover,
            "boundary_handoffs": boundary_handoffs,
        }
        if confidence:
            instruction_state["confidence"] = confidence
        if unknown:
            instruction_state["unknown"] = unknown if len(unknown) > 1 else unknown[0]

        return {
            "module": str(item.get("module") or "").strip(),
            "instance": str(item.get("instance") or "").strip(),
            "path": self._item_path(item),
            "orchestrator_role": str(item.get("orchestrator_role") or "").strip(),
            "instruction_state": instruction_state,
        }

    def _build_visible_dep_payload(self, previous_leaf_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not previous_leaf_context:
            return {"candidates": []}
        candidate = {
            "ref_name": str(previous_leaf_context.get("ref_name") or "").strip(),
            "task_id": str(previous_leaf_context.get("task_id") or "").strip(),
            "capture_names": list(previous_leaf_context.get("capture_names") or []),
            "module": str(previous_leaf_context.get("module") or "").strip(),
            "instance": str(previous_leaf_context.get("instance") or "").strip(),
            "path": str(previous_leaf_context.get("path") or "").strip(),
        }
        if not candidate["ref_name"] or not candidate["task_id"] or not candidate["capture_names"]:
            return {"candidates": []}
        return {"candidates": [candidate]}

    def _build_dep_symbol(
        self,
        *,
        ref_name: str,
        task_id: str,
        capture_names: List[str],
        item: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "ref_name": str(ref_name or "").strip(),
            "task_id": str(task_id or "").strip(),
            "capture_names": list(capture_names or []),
            "module": str(item.get("module") or "").strip(),
            "instance": str(item.get("instance") or "").strip(),
            "path": self._item_path(item),
        }

    def _build_leaf_context_from_entry(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        task_id = str(entry.get("leaf_task_id") or "").strip()
        ref_name = str(entry.get("leaf_ref_name") or "").strip()
        capture_names = list(entry.get("leaf_capture_names") or [])
        if not task_id or not ref_name or not capture_names:
            return None
        return {
            "ref_name": ref_name,
            "task_id": task_id,
            "capture_names": capture_names,
            "module": str(entry.get("module") or "").strip(),
            "instance": str(entry.get("instance") or "").strip(),
            "path": str(entry.get("path") or "").strip(),
        }

    @staticmethod
    def _normalize_cached_entry(entry: Dict[str, Any], artifact_yaml: str, artifact_raw: str) -> Dict[str, Any]:
        cached = dict(entry or {})
        cached.setdefault("status", "partial")
        cached.setdefault("unknown", [])
        cached.setdefault("artifact_yaml", artifact_yaml)
        cached.setdefault("artifact_raw", artifact_raw)
        cached.setdefault("task_ids", [])
        cached.setdefault("task_count", len(list(cached.get("task_ids") or [])))
        cached.setdefault("first_task_id", "")
        cached.setdefault("leaf_task_id", "")
        cached.setdefault("leaf_ref_name", "")
        cached.setdefault("leaf_capture_names", [])
        return cached

    def _build_task_id(self, *, item: Dict[str, Any], item_index: int, task_index: int) -> str:
        instance_slug = self.g._safe_slug(str(item.get("instance") or item.get("module") or "inst")).lower()
        return f"s{item_index:02d}_t{task_index:02d}_{instance_slug}"

    def _path_slug(self, item: Dict[str, Any]) -> str:
        module_slug = self.g._safe_slug(str(item.get("module") or "unknown")).lower()
        instance_slug = self.g._safe_slug(str(item.get("instance") or "inst")).lower()
        raw_path = self._item_path(item) or f"{module_slug}__{instance_slug}"
        return self.g._safe_slug(raw_path.replace("/", "__")).lower()

    @staticmethod
    def _item_path(item: Dict[str, Any]) -> str:
        return str(item.get("path") or "").strip()

    @staticmethod
    def _yaml_quote(value: Any) -> str:
        return json.dumps(str(value or ""), ensure_ascii=False)
