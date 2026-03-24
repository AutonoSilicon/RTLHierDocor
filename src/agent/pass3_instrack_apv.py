"""Pass3.3.3 APV fragment generation helpers."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .prompts import PASS3_3_3_APV_PROMPT, PASS3_3_3_APV_SYSTEM


class Pass3InStrackAPV:
    """Helper object encapsulating pass3.3.3 APV generation."""

    _APV_INDEX_SCHEMA_VERSION = "pass3_3_3_apv_index_v8"
    _MAX_VISIBLE_DEP_CANDIDATES = 16
    _UPSTREAM_CANDIDATE_OVERFLOW_LIMIT = 8
    _DEFAULT_APV_MAX_TOOL_ROUNDS = 32
    _MAX_READ_LINE_SPAN = 80
    _ALLOWED_SOURCE_ACCESS_MODES = {"embedded_topology", "toolized_source"}
    _ALLOWED_ANCHOR_KINDS = {"identity", "path", "gating"}
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
    _DECL_KEYWORD_RE = re.compile(
        r"^\s*(?:input|output|inout|wire|reg|logic|tri|tri0|tri1|wand|wor|supply0|supply1)\b"
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
    _TOPOLOGY_NOISE_TOKENS = {
        "topology",
        "module",
        "inputs",
        "outputs",
        "blocks",
        "block",
        "from",
        "to",
        "source",
        "code",
        "none",
        "port",
        "proc",
        "submodule",
    }
    _SIGNAL_GUIDANCE_MARKERS = ("(port)", "->", "Inputs from blocks:", "Outputs to blocks:", "[Source code]")
    _ANCHOR_HINT_TOKENS = ("pc", "vpc", "data", "inst", "dispatch", "issue", "valid", "vld")
    _ANCHOR_REASON_SEMANTIC_HINTS = {
        "align",
        "aligns",
        "bind",
        "binds",
        "block",
        "blocks",
        "carry",
        "carries",
        "channel",
        "confirm",
        "confirms",
        "continuity",
        "correlate",
        "correlates",
        "disambiguate",
        "disambiguates",
        "distinguish",
        "distinguishes",
        "enable",
        "enables",
        "family",
        "gate",
        "gates",
        "gating",
        "identify",
        "identifies",
        "identity",
        "instruction",
        "link",
        "links",
        "map",
        "maps",
        "match",
        "matches",
        "mux",
        "path",
        "pc",
        "pipe",
        "preserve",
        "preserves",
        "route",
        "same",
        "select",
        "selects",
        "slot",
        "stall",
        "timing",
        "track",
        "tracks",
    }
    _ANCHOR_REASON_STOPWORDS = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
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

        duplicate_paths = self._find_duplicate_item_paths(orchestrate_items)
        if duplicate_paths:
            error = (
                "Duplicate `item.path` detected in one instruction route during pass3.3.3: "
                + ", ".join(duplicate_paths)
                + ". Multi-leaf v2 requires every route item path to be unique so artifact/cache keys do not collide."
            )
            index_payload = {
                "instruction": instruction,
                "start_module": parsed_search.get("start_module", ""),
                "schema_version": self._APV_INDEX_SCHEMA_VERSION,
                "status": "failed",
                "error": error,
                "items": [],
            }
            index_text = json.dumps(index_payload, ensure_ascii=False, indent=2)
            source_access_mode = self._source_access_mode()
            input_hash = self.g._hash.build_pass3_3_apv_index_input_hash(
                top_module=top_node.module_name,
                instruction=instruction,
                start_module=str(parsed_search.get("start_module") or "").strip(),
                orchestration_json_text=orchestrate_index_text,
                source_access_mode=source_access_mode,
                apv_max_tool_rounds=self._apv_max_tool_rounds(),
            )
            return {
                "items": [],
                "index_payload": index_payload,
                "index_text": index_text,
                "input_hash": input_hash,
                "status": "failed",
                "error": error,
            }

        items: List[Dict[str, Any]] = []
        previous_task_contexts: List[Dict[str, Any]] = []
        source_access_mode = self._source_access_mode()

        for item_index, item in enumerate(orchestrate_items):
            path_key = self._item_path(item)
            result = await self._generate_single_item(
                top_node=top_node,
                instruction=instruction,
                instruction_slug=instruction_slug,
                instruction_datasheet=instruction_datasheet,
                item=item,
                item_index=item_index,
                previous_task_contexts=previous_task_contexts,
                artifacts_dir=artifacts_dir,
                cached_entry=cached_by_path.get(path_key),
            )
            items.append(result)
            previous_task_contexts.extend(self._build_task_contexts_from_entry(result))

        index_payload = {
            "instruction": instruction,
            "start_module": parsed_search.get("start_module", ""),
            "schema_version": self._APV_INDEX_SCHEMA_VERSION,
            "items": items,
        }
        index_text = json.dumps(index_payload, ensure_ascii=False, indent=2)
        input_hash = self.g._hash.build_pass3_3_apv_index_input_hash(
            top_module=top_node.module_name,
            instruction=instruction,
            start_module=str(parsed_search.get("start_module") or "").strip(),
            orchestration_json_text=orchestrate_index_text,
            source_access_mode=source_access_mode,
            apv_max_tool_rounds=self._apv_max_tool_rounds(),
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
        previous_task_contexts: Optional[List[Dict[str, Any]]],
        artifacts_dir: Path,
        cached_entry: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        path_slug = self._path_slug(item)
        item_path = self._item_path(item)
        artifact_yaml = f"instrack/{instruction_slug}/artifacts/{path_slug}.apv.yaml"
        artifact_raw = f"instrack/{instruction_slug}/artifacts/{path_slug}.apv.raw.md"
        artifact_llm_error = f"instrack/{instruction_slug}/artifacts/{path_slug}.apv.llm_error.md"
        yaml_path = artifacts_dir / f"{path_slug}.apv.yaml"
        raw_path = artifacts_dir / f"{path_slug}.apv.raw.md"
        llm_error_path = artifacts_dir / f"{path_slug}.apv.llm_error.md"

        module_name = str(item.get("module") or "").strip()
        source_access_mode = self._source_access_mode()
        apv_max_tool_rounds = self._apv_max_tool_rounds()
        module_source_slice = self._get_module_source_slice(module_name)
        current_module_topology = self.g._prompts.build_instrack_current_module_topology(module_name)
        current_module_metadata = self._build_current_module_source_metadata(
            module_name=module_name,
            module_source_slice=module_source_slice,
        )
        current_module_metadata_text = json.dumps(current_module_metadata, ensure_ascii=False, indent=2)
        if source_access_mode == "toolized_source":
            current_module_evidence_text = current_module_metadata_text
            current_module_evidence_block = self._build_current_module_source_metadata_block(current_module_metadata_text)
            source_tool_guidance_block = self._build_source_tool_guidance_block()
            local_signal_evidence_text = ""
            local_signal_source_text = str(module_source_slice.get("text") or "") if module_source_slice else ""
        else:
            current_module_evidence_text = current_module_topology
            current_module_evidence_block = self._build_current_module_topology_block(current_module_topology)
            source_tool_guidance_block = ""
            local_signal_evidence_text = current_module_topology
            local_signal_source_text = ""
        normalized_previous_task_contexts = self._normalize_visible_dep_context_list(previous_task_contexts)
        local_signal_guidance = self._build_local_signal_guidance(
            item=item,
            current_module_evidence=local_signal_evidence_text,
            current_module_source=local_signal_source_text,
            previous_task_contexts=normalized_previous_task_contexts,
        )
        local_signal_guidance_text = json.dumps(local_signal_guidance, ensure_ascii=False, indent=2)
        item_json_text = json.dumps(self._build_current_item_context(item), ensure_ascii=False, indent=2)
        visible_dep_payload = self._build_visible_dep_payload(normalized_previous_task_contexts)
        visible_dep_text = json.dumps(visible_dep_payload, ensure_ascii=False, indent=2)
        item_input_hash = self.g._hash.build_pass3_3_apv_item_input_hash(
            top_module=top_node.module_name,
            instruction=instruction,
            item_json_text=item_json_text,
            history_task_json_text=visible_dep_text,
            current_module_evidence_text=current_module_evidence_text,
            source_access_mode=source_access_mode,
            apv_max_tool_rounds=apv_max_tool_rounds,
            local_signal_guidance_json_text=local_signal_guidance_text,
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

        overflow_reason = self._detect_candidate_overflow(normalized_previous_task_contexts)
        if overflow_reason:
            yaml_text = self._render_yaml(item=item, status="partial", unknown=[overflow_reason], tasks=[])
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
            return self._build_item_entry(
                item=item,
                status="partial",
                unknown=[overflow_reason],
                artifact_yaml=artifact_yaml,
                artifact_raw=artifact_raw,
                artifact_llm_error="",
                tasks=[],
                behavior_hint="",
                input_hash=item_input_hash,
                token_stats={},
            )

        prompt = PASS3_3_3_APV_PROMPT.format(
            instruction=instruction,
            instruction_datasheet=instruction_datasheet or "Instruction unavailable",
            current_module_evidence_block=current_module_evidence_block,
            current_item_json=item_json_text,
            visible_dep_json=visible_dep_text,
            source_tool_guidance_block=source_tool_guidance_block,
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

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if source_access_mode != "toolized_source":
                return json.dumps(
                    {
                        "tool": tool_name,
                        "status": "error",
                        "error": "APV source tools are disabled in the current source access mode.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            if tool_name == "grepSource":
                return self._run_grep_source_tool(module_name=module_name, module_source_slice=module_source_slice, args=args)
            if tool_name == "readLine":
                return self._run_read_line_tool(module_name=module_name, module_source_slice=module_source_slice, args=args)
            return json.dumps(
                {
                    "tool": tool_name,
                    "status": "error",
                    "error": f"Unknown APV source tool `{tool_name}`.",
                },
                ensure_ascii=False,
                indent=2,
            )

        content, token_stats = await self.g.owner.llm.generate(
            PASS3_3_3_APV_SYSTEM,
            prompt,
            log_path=log_path,
            tools_enabled=(source_access_mode == "toolized_source"),
            tools=self.g._pass3_3_3_tools() if source_access_mode == "toolized_source" else None,
            tool_callback=_tool_callback if source_access_mode == "toolized_source" else None,
            max_tool_rounds=apv_max_tool_rounds,
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
        behavior_hint = self._normalize_hint_text(parsed_payload.get("behavior_hint"))
        tasks, task_errors = self._materialize_tasks(
            top_node=top_node,
            item=item,
            item_index=item_index,
            raw_tasks=list(parsed_payload.get("tasks") or []),
            previous_task_contexts=normalized_previous_task_contexts,
            local_signal_guidance=local_signal_guidance,
        )
        if task_errors:
            unknown.extend(task_errors)
            status = "partial"
        quality_errors = self._quality_gate_tasks(
            tasks=tasks,
            item=item,
            status=status,
            previous_task_contexts=normalized_previous_task_contexts,
            local_signal_guidance=local_signal_guidance,
        )
        if quality_errors:
            unknown.extend(quality_errors)
            status = "partial"
        if status == "complete" and not tasks:
            unknown.append("Model returned complete without any valid tasks.")
            status = "partial"

        llm_error_text = self._build_llm_error_log_text(
            item=item,
            log_path=log_path,
            raw_text=raw_text,
            unknown=unknown,
            token_stats=token_stats,
            source_access_mode=source_access_mode,
            apv_max_tool_rounds=apv_max_tool_rounds,
        )
        artifact_llm_error_value = ""
        if llm_error_text:
            llm_error_path.write_text(llm_error_text, encoding="utf-8")
            self.g.owner.project_tracker.update(
                artifact_llm_error,
                llm_error_text,
                input_hash=item_input_hash,
                meta={
                    "top_module": top_node.module_name,
                    "instruction": instruction,
                    "module": module_name,
                    "instance": str(item.get("instance") or "").strip(),
                    "path": item_path,
                    "pass": "pass3_3_3_apv_llm_error",
                },
            )
            artifact_llm_error_value = artifact_llm_error
        elif llm_error_path.exists():
            llm_error_path.unlink()

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
        return self._build_item_entry(
            item=item,
            status=status,
            unknown=unknown,
            artifact_yaml=artifact_yaml,
            artifact_raw=artifact_raw,
            artifact_llm_error=artifact_llm_error_value,
            tasks=tasks,
            behavior_hint=behavior_hint,
            input_hash=item_input_hash,
            token_stats=token_stats,
        )

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
        behavior_hint = self._normalize_hint_text(payload.get("behavior_hint"))
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
            "behavior_hint": behavior_hint,
        }

    def _materialize_tasks(
        self,
        *,
        top_node: Any,
        item: Dict[str, Any],
        item_index: int,
        raw_tasks: List[Dict[str, Any]],
        previous_task_contexts: Optional[List[Dict[str, Any]]],
        local_signal_guidance: Optional[Dict[str, Any]] = None,
    ) -> (List[Dict[str, Any]], List[str]):
        scope_prefix = self._scope_prefix(item_path=self._item_path(item), top_node=top_node)
        root_prefixes = self._root_prefixes(top_node)
        tasks: List[Dict[str, Any]] = []
        errors: List[str] = []
        dep_symbols: Dict[str, Dict[str, Any]] = {}
        reserved_ref_names: Set[str] = set()
        normalized_previous_task_contexts = self._normalize_visible_dep_context_list(previous_task_contexts)
        trusted_local_signal_catalog = bool((local_signal_guidance or {}).get("trusted_local_signal_catalog"))
        allowed_local_signals = set((local_signal_guidance or {}).get("allowed_local_signals") or [])
        for previous_task_context in normalized_previous_task_contexts:
            upstream_ref_name = str(previous_task_context.get("ref_name") or "").strip()
            upstream_task_id = str(previous_task_context.get("task_id") or "").strip()
            upstream_capture_names = list(previous_task_context.get("capture_names") or [])
            if upstream_ref_name and upstream_task_id and upstream_capture_names:
                dep_symbols[upstream_ref_name] = self._build_dep_symbol(
                    ref_name=upstream_ref_name,
                    task_id=upstream_task_id,
                    capture_names=upstream_capture_names,
                    branch_lineage=list(previous_task_context.get("branch_lineage") or []),
                    item=previous_task_context,
                    source="upstream",
                    upstream_hint=str(previous_task_context.get("upstream_hint") or "").strip(),
                    anchors=list(previous_task_context.get("anchors") or []),
                    anchor_capture_names=list(previous_task_context.get("anchor_capture_names") or []),
                    anchor_kinds=list(previous_task_context.get("anchor_kinds") or []),
                    anchor_reasons=list(previous_task_context.get("anchor_reasons") or []),
                )
                reserved_ref_names.add(upstream_ref_name)

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
            handoff_hint = self._normalize_hint_text(raw_task.get("handoff_hint"))
            dep_name_raw = raw_task.get("dep_name") if "dep_name" in raw_task else None
            dep_name = str(dep_name_raw or "").strip() if dep_name_raw is not None else ""
            raw_match_mode = str(raw_task.get("match_mode") or "first").strip().lower()
            match_mode = self._MATCH_MODE_ALIASES.get(raw_match_mode, raw_match_mode)
            legacy_dep_source = str(raw_task.get("dep_source") or "").strip()
            legacy_dep_leaf_name = str(raw_task.get("dep_leaf_name") or "").strip()

            try:
                max_match = int(raw_task.get("max_match", 0))
            except Exception:
                task_errors.append(f"Task `{task_name or task_index}` has non-integer `max_match`.")
                max_match = 0

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
            elif ref_name in reserved_ref_names:
                task_errors.append(
                    f"Task `{task_name or task_index}` uses duplicate `ref_name={ref_name}` which is already visible in the dep namespace."
                )
            if dep_name_raw is None:
                task_errors.append(f"Task `{task_name or task_index}` is missing `dep_name`.")
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
                match_mode = "first"
            if max_match < 0:
                task_errors.append(f"Task `{task_name or task_index}` uses negative `max_match`.")
                max_match = 0

            if not ref_name or not self._REF_NAME_RE.fullmatch(ref_name) or self._TASK_ID_LIKE_RE.fullmatch(ref_name) or ref_name in reserved_ref_names:
                ref_name = self._salvage_ref_name(
                    raw_ref_name=ref_name,
                    task_name=task_name,
                    task_index=task_index,
                    reserved_ref_names=reserved_ref_names,
                )
            if not task_name:
                task_name = ref_name or f"task_{task_index + 1}"
            if dep_name_raw is None:
                dep_name = ""
            errors.extend(task_errors)

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
            dep_symbol: Optional[Dict[str, Any]] = None
            dep_ref_name = dep_ref_names[0] if len(dep_ref_names) == 1 else ""
            if bare_dep_refs:
                errors.append(
                    f"Task `{task_name}` must use full `$dep.<ref_name>.<signal>` references in `condition_lines`, got bare refs: {', '.join(bare_dep_refs)}."
                )
            elif len(dep_ref_names) > 1:
                errors.append(
                    f"Task `{task_name}` must reference at most one unique dep `ref_name` in `condition_lines`, got: {', '.join(dep_ref_names)}."
                )
            elif dep_name and not dep_ref_name:
                errors.append(
                    f"Task `{task_name}` declares `dep_name={dep_name}` but has no `$dep.<ref_name>.<signal>` reference in `condition_lines`."
                )
            elif dep_ref_name and not dep_name:
                errors.append(
                    f"Task `{task_name}` references `$dep.{dep_ref_name}.*` but must declare matching `dep_name`."
                )
                dep_name = dep_ref_name
            elif dep_ref_name and dep_name and dep_ref_name != dep_name:
                errors.append(
                    f"Task `{task_name}` declares `dep_name={dep_name}` but its `condition_lines` reference `$dep.{dep_ref_name}.*`."
                )
                dep_name = dep_ref_name
            if dep_name:
                dep_symbol = dep_symbols.get(dep_name)
                if not dep_symbol:
                    available_ref_names = ", ".join(sorted(dep_symbols.keys())) or "<none>"
                    if self._TASK_ID_LIKE_RE.fullmatch(dep_name):
                        errors.append(
                            f"Task `{task_name}` must not emit final `$dep.<task_id>.<signal>` references in raw APV JSON; use a declared `ref_name` instead of `{dep_name}`."
                        )
                    elif dep_name in future_ref_names:
                        errors.append(
                            f"Task `{task_name}` forward-references `ref_name={dep_name}` before that task is declared."
                        )
                    else:
                        errors.append(
                            f"Task `{task_name}` references unknown dep `ref_name={dep_name}`; available: {available_ref_names}."
                        )
                else:
                    dep_task_id = str(dep_symbol.get("task_id") or "").strip()
                    available_capture_names = list(dep_symbol.get("capture_names") or [])
                    missing_capture_names = sorted(
                        {
                            capture_name
                            for current_ref_name, capture_name in dep_references
                            if current_ref_name == dep_name and capture_name not in available_capture_names
                        }
                    )
                    if missing_capture_names:
                        errors.append(
                            f"Task `{task_name}` references unknown dep capture name(s) for `ref_name={dep_name}`: {', '.join(missing_capture_names)}; available: {', '.join(available_capture_names)}."
                        )
                    if not dep_task_id:
                        errors.append(
                            f"Task `{task_name}` is missing the resolved upstream task id for `ref_name={dep_name}`."
                        )
            elif legacy_dep_source or legacy_dep_leaf_name:
                errors.append(
                    f"Task `{task_name}` declares legacy dependency fields without any `$dep.<ref_name>.<signal>` reference in `condition_lines`."
                )
            if legacy_dep_source:
                errors.append(
                    f"Task `{task_name}` must not emit legacy `dep_source={legacy_dep_source}`; use `dep_name` plus `$dep.<ref_name>.<signal>` references only."
                )
            if legacy_dep_leaf_name:
                errors.append(
                    f"Task `{task_name}` must not emit legacy `dep_leaf_name={legacy_dep_leaf_name}`; use `dep_name` plus `$dep.<ref_name>.<signal>` references only."
                )

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
            if trusted_local_signal_catalog and allowed_local_signals:
                missing_local_signals = sorted(
                    (
                        self._extract_local_signal_names(normalized_conditions)
                        | self._extract_local_signal_names(normalized_capture)
                    )
                    - allowed_local_signals
                )
                if missing_local_signals:
                    errors.append(
                        f"Task `{task_name}` references unknown local signal(s) for this module item: {', '.join(missing_local_signals)}."
                    )
            capture_names = self._capture_names(normalized_capture)
            if not capture_names:
                errors.append(f"Task `{task_name}` does not expose any usable capture names.")
            normalized_anchors, anchor_errors = self._normalize_anchor_entries(
                raw_task=raw_task,
                task_name=task_name,
                dep_name=dep_name,
                dep_task_id=dep_task_id,
                condition_lines=condition_lines,
                normalized_capture=normalized_capture,
                scope_prefix=scope_prefix,
                root_prefixes=root_prefixes,
            )
            if anchor_errors:
                errors.extend(anchor_errors)
            anchor_capture_names = self._anchor_capture_names(normalized_anchors)
            anchor_kinds = self._anchor_kinds(normalized_anchors)
            anchor_reasons = self._anchor_reasons(normalized_anchors)
            branch_lineage = self._derive_branch_lineage(dep_symbol=dep_symbol, ref_name=ref_name)

            tasks.append(
                {
                    "id": task_id,
                    "ref_name": ref_name,
                    "dep_name": dep_name,
                    "task_name": task_name,
                    "depends_on": dep_task_id,
                    "condition_lines": normalized_conditions,
                    "capture_signals": normalized_capture,
                    "logging_lines": logging_lines,
                    "match_mode": match_mode,
                    "max_match": max_match,
                    "capture_names": capture_names,
                    "branch_lineage": branch_lineage,
                    "handoff_hint": handoff_hint,
                    "anchors": normalized_anchors,
                    "anchor_kinds": anchor_kinds,
                    "anchor_capture_names": anchor_capture_names,
                    "anchor_reasons": anchor_reasons,
                }
            )
            reserved_ref_names.add(ref_name)
            dep_symbols[ref_name] = self._build_dep_symbol(
                ref_name=ref_name,
                task_id=task_id,
                capture_names=capture_names,
                branch_lineage=branch_lineage,
                upstream_hint=handoff_hint,
                item=item,
                source="local",
                anchors=normalized_anchors,
                anchor_capture_names=anchor_capture_names,
                anchor_kinds=anchor_kinds,
                anchor_reasons=anchor_reasons,
            )

        return tasks, errors

    def _sanitize_ref_name_candidate(self, value: str) -> str:
        candidate = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
        candidate = re.sub(r"_+", "_", candidate)
        if candidate and candidate[0].isdigit():
            candidate = f"task_{candidate}"
        return candidate

    def _salvage_ref_name(
        self,
        *,
        raw_ref_name: str,
        task_name: str,
        task_index: int,
        reserved_ref_names: Set[str],
    ) -> str:
        base = self._sanitize_ref_name_candidate(raw_ref_name) or self._sanitize_ref_name_candidate(task_name)
        if not base or not self._REF_NAME_RE.fullmatch(base) or self._TASK_ID_LIKE_RE.fullmatch(base):
            base = f"task_{task_index + 1}"
        candidate = base
        suffix = 2
        while candidate in reserved_ref_names or not self._REF_NAME_RE.fullmatch(candidate) or self._TASK_ID_LIKE_RE.fullmatch(candidate):
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _quality_gate_tasks(
        self,
        *,
        tasks: List[Dict[str, Any]],
        item: Dict[str, Any],
        status: str,
        previous_task_contexts: Optional[List[Dict[str, Any]]],
        local_signal_guidance: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        if status != "complete" or not tasks:
            return []

        errors: List[str] = []
        module_name = str(item.get("module") or "").strip() or "<unknown>"
        terminal_tasks = self._terminal_tasks(tasks)
        if len(terminal_tasks) > 1:
            seen_branch_lineages: Set[str] = set()
            for task in terminal_tasks:
                capture_names = list(task.get("capture_names") or [])
                if not capture_names:
                    errors.append(
                        f"Terminal task `{str(task.get('task_name') or task.get('ref_name') or task.get('id') or '<unknown>').strip()}` "
                        "does not expose any capture names and cannot be exported as a downstream leaf."
                    )
                    continue
                branch_lineage = list(task.get("branch_lineage") or [])
                if not branch_lineage:
                    errors.append(
                        f"Terminal task `{str(task.get('task_name') or task.get('ref_name') or task.get('id') or '<unknown>').strip()}` "
                        "does not expose a usable `branch_lineage` for downstream export."
                    )
                    continue
                branch_key = self._branch_lineage_key(branch_lineage)
                if branch_key in seen_branch_lineages:
                    errors.append(
                        f"Terminal task `{str(task.get('task_name') or task.get('ref_name') or task.get('id') or '<unknown>').strip()}` "
                        f"reuses duplicate `branch_lineage={self._format_branch_lineage(branch_lineage)}` within one multi-leaf item."
                    )
                    continue
                seen_branch_lineages.add(branch_key)

        identity_anchor_tasks = [task for task in tasks if "identity" in list(task.get("anchor_kinds") or [])]
        if not identity_anchor_tasks:
            errors.append(
                f"Module `{module_name}` was marked complete without any `identity` anchor; "
                "`path`/`gating` anchors alone cannot justify `complete`."
            )
            return errors

        normalized_previous_task_contexts = self._normalize_visible_dep_context_list(previous_task_contexts)
        if normalized_previous_task_contexts and not any(
            str(task.get("depends_on") or "").strip()
            and any(
                str(anchor.get("kind") or "").strip() == "identity" and list(anchor.get("dep_signals") or [])
                for anchor in list(task.get("anchors") or [])
            )
            for task in tasks
        ):
            errors.append(
                f"Module `{module_name}` was marked complete without any upstream-linked `identity` anchor continuity."
            )

        found_same_line_identity = False
        for task in identity_anchor_tasks:
            task_name = str(task.get("task_name") or task.get("ref_name") or task.get("id") or "<unknown>").strip()
            dep_task_id = str(task.get("depends_on") or "").strip()
            for anchor in list(task.get("anchors") or []):
                if str(anchor.get("kind") or "").strip() != "identity":
                    continue
                dep_signals = list(anchor.get("dep_signals") or [])
                local_signals = list(anchor.get("local_signals") or [])
                if dep_task_id and not dep_signals:
                    errors.append(
                        f"Module `{module_name}` task `{task_name}` was marked complete with an `identity` anchor "
                        "that has no upstream `dep_signals`."
                    )
                    continue
                if dep_task_id and dep_signals and local_signals:
                    if any(
                        any(dep_signal in str(condition or "") for dep_signal in dep_signals)
                        and any(local_signal in str(condition or "") for local_signal in local_signals)
                        for condition in list(task.get("condition_lines") or [])
                    ):
                        found_same_line_identity = True
                        break

        if normalized_previous_task_contexts and not found_same_line_identity:
            errors.append(
                f"Module `{module_name}` was marked complete without any same-line `identity` relation tying an upstream anchor to a local anchor."
            )

        return errors

    def _build_local_signal_guidance(
        self,
        *,
        item: Dict[str, Any],
        current_module_evidence: str,
        current_module_source: str,
        previous_task_contexts: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        payload = dict(item.get("orchestration") or {})
        structured_topology = self._has_structured_signal_catalog(current_module_evidence)
        topology_signal_catalog = self._extract_topology_signal_catalog(current_module_evidence)
        verilog_signal_catalog = self._extract_verilog_signal_catalog(current_module_source)
        allowed_local_signals = set(topology_signal_catalog) | set(verilog_signal_catalog)

        for entry in list(payload.get("boundary_takeover") or []):
            input_port = str((entry or {}).get("input_port") or "").strip()
            if input_port:
                allowed_local_signals.add(input_port)
        for entry in list(payload.get("boundary_handoffs") or []):
            output_port = str((entry or {}).get("output_port") or "").strip()
            if output_port:
                allowed_local_signals.add(output_port)

        preferred_local_anchor_names = sorted(
            {
                name
                for name in allowed_local_signals
                if self._is_anchor_like_name(name)
            }
        )
        preferred_upstream_anchor_names_by_task_id: Dict[str, List[str]] = {}
        preferred_upstream_anchor_names: Set[str] = set()
        for previous_task_context in self._normalize_visible_dep_context_list(previous_task_contexts):
            task_id = str(previous_task_context.get("task_id") or "").strip()
            anchor_names = sorted(
                {
                    str(name or "").strip()
                    for name in (
                        list(previous_task_context.get("anchor_capture_names") or [])
                        or list(previous_task_context.get("capture_names") or [])
                    )
                    if self._is_anchor_like_name(str(name or "").strip())
                }
            )
            if task_id and anchor_names:
                preferred_upstream_anchor_names_by_task_id[task_id] = anchor_names
                preferred_upstream_anchor_names.update(anchor_names)
        packed_bus_signals = sorted(
            {
                name
                for name in allowed_local_signals
                if "_data" in name or name.endswith("_pkt") or name.endswith("_bus")
            }
        )

        guidance = {
            "trusted_local_signal_catalog": bool(allowed_local_signals and (structured_topology or verilog_signal_catalog)),
            "allowed_local_signals": sorted(allowed_local_signals),
            "preferred_local_anchor_names": preferred_local_anchor_names,
            "preferred_upstream_anchor_names": sorted(preferred_upstream_anchor_names),
            "preferred_upstream_anchor_names_by_task_id": preferred_upstream_anchor_names_by_task_id,
            "packed_bus_signals": packed_bus_signals,
        }
        if guidance["trusted_local_signal_catalog"]:
            guidance["notes"] = [
                "Use only these real local signal names for this module item.",
                "If a needed value only exists inside a packed bus, express it with a slice on that real bus.",
            ]
        else:
            guidance["notes"] = [
                "No trusted local signal catalog could be extracted from the provided source for this item.",
                "Do not invent new local alias signals; prefer explicit ports and source-visible names only.",
            ]
        return guidance

    def _normalize_source_access_mode(self, value: Any) -> str:
        mode = str(value or "").strip().lower()
        if mode not in self._ALLOWED_SOURCE_ACCESS_MODES:
            return "embedded_topology"
        return mode

    def _source_access_mode(self) -> str:
        return self._normalize_source_access_mode(
            getattr(self.g.owner, "instrack_apv_source_access_mode", "embedded_topology")
        )

    def _apv_max_tool_rounds(self) -> int:
        raw_value = getattr(self.g.owner, "instrack_apv_max_tool_rounds", self._DEFAULT_APV_MAX_TOOL_ROUNDS)
        try:
            return max(1, int(raw_value or self._DEFAULT_APV_MAX_TOOL_ROUNDS))
        except Exception:
            return self._DEFAULT_APV_MAX_TOOL_ROUNDS

    def _get_module_source_slice(self, module_name: str) -> Optional[Dict[str, Any]]:
        resolver = getattr(self.g.owner, "resolver", None)
        if resolver is None or not module_name:
            return None
        try:
            if hasattr(resolver, "get_module_source_slice"):
                return resolver.get_module_source_slice(module_name)
        except Exception:
            return None
        return None

    def _build_current_module_source_metadata(
        self,
        *,
        module_name: str,
        module_source_slice: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        source_slice = dict(module_source_slice or {})
        source_path = str(source_slice.get("source_path") or "").strip()
        module_line_count = int(source_slice.get("module_line_count") or 0)
        line_start = int(source_slice.get("module_file_line_start") or 0)
        line_end = int(source_slice.get("module_file_line_end") or 0)
        return {
            "module": str(module_name or "").strip(),
            "source_path": source_path,
            "module_line_count": module_line_count,
            "module_file_line_start": line_start,
            "module_file_line_end": line_end,
            "source_available": bool(source_path and module_line_count > 0),
        }

    @staticmethod
    def _build_current_module_topology_block(current_module_topology: str) -> str:
        return (
            "## Current Module Topology\n"
            f"{current_module_topology}\n"
        )

    @staticmethod
    def _build_current_module_source_metadata_block(current_module_metadata_text: str) -> str:
        return (
            "## Current Module Source Metadata\n"
            "```json\n"
            f"{current_module_metadata_text}\n"
            "```"
        )

    @staticmethod
    def _build_source_tool_guidance_block() -> str:
        return """
## Optional Source Tools
- Source tools are enabled for this item and scoped to the current module only.
- Use `grepSource(pattern, ignore_case=false, max_matches=20)` to locate candidate signals, assignments, or always blocks by module-relative line.
- Use `readLine(start_line, end_line)` only after grep narrows the area. `readLine` uses module-relative line numbers and allows at most 80 lines per call.
- Prefer zero or a few targeted tool calls. Do not scan the whole module linearly.
- If you never read a signal or logic region through the provided evidence or tool results, do not invent it in task conditions.
""".strip()

    def _run_grep_source_tool(
        self,
        *,
        module_name: str,
        module_source_slice: Optional[Dict[str, Any]],
        args: Dict[str, Any],
    ) -> str:
        source_slice = dict(module_source_slice or {})
        metadata = self._build_current_module_source_metadata(
            module_name=module_name,
            module_source_slice=source_slice,
        )
        pattern = str(args.get("pattern") or "").strip()
        try:
            max_matches = int(args.get("max_matches", 20))
        except Exception:
            max_matches = 20
        max_matches = max(1, min(max_matches, 50))
        ignore_case = bool(args.get("ignore_case", False))
        if not pattern:
            return json.dumps(
                {
                    "tool": "grepSource",
                    "status": "error",
                    "error": "Empty pattern is not allowed.",
                    **metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return json.dumps(
                {
                    "tool": "grepSource",
                    "status": "error",
                    "error": f"Invalid search pattern: {exc}",
                    **metadata,
                },
                ensure_ascii=False,
                indent=2,
            )

        results: List[Dict[str, Any]] = []
        for entry in list(source_slice.get("line_map") or []):
            text = str(entry.get("text") or "")
            if regex.search(text):
                results.append(
                    {
                        "module_line": int(entry.get("module_line") or 0),
                        "file_line": int(entry.get("file_line") or 0),
                        "text": text,
                    }
                )
                if len(results) >= max_matches:
                    break
        return json.dumps(
            {
                "tool": "grepSource",
                "status": "ok",
                **metadata,
                "pattern": pattern,
                "ignore_case": ignore_case,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _run_read_line_tool(
        self,
        *,
        module_name: str,
        module_source_slice: Optional[Dict[str, Any]],
        args: Dict[str, Any],
    ) -> str:
        source_slice = dict(module_source_slice or {})
        metadata = self._build_current_module_source_metadata(
            module_name=module_name,
            module_source_slice=source_slice,
        )
        module_line_count = int(source_slice.get("module_line_count") or 0)
        try:
            start_line = int(args.get("start_line", 0))
        except Exception:
            start_line = 0
        try:
            end_line = int(args.get("end_line", start_line))
        except Exception:
            end_line = start_line
        if end_line <= 0:
            end_line = start_line
        if start_line <= 0:
            return json.dumps(
                {
                    "tool": "readLine",
                    "status": "error",
                    "error": "start_line must be a positive module-relative line number.",
                    **metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        if end_line < start_line:
            start_line, end_line = end_line, start_line
        if end_line - start_line + 1 > self._MAX_READ_LINE_SPAN:
            return json.dumps(
                {
                    "tool": "readLine",
                    "status": "error",
                    "error": f"range too wide: requested {end_line - start_line + 1} lines, max is {self._MAX_READ_LINE_SPAN}.",
                    **metadata,
                    "valid_module_line_range": [1, module_line_count],
                },
                ensure_ascii=False,
                indent=2,
            )
        if module_line_count <= 0 or start_line > module_line_count or end_line > module_line_count:
            return json.dumps(
                {
                    "tool": "readLine",
                    "status": "error",
                    "error": "Requested line range is outside the current module source slice.",
                    **metadata,
                    "valid_module_line_range": [1, module_line_count],
                },
                ensure_ascii=False,
                indent=2,
            )

        line_map = list(source_slice.get("line_map") or [])
        selected = [entry for entry in line_map if start_line <= int(entry.get("module_line") or 0) <= end_line]
        file_start = int(selected[0].get("file_line") or 0) if selected else 0
        file_end = int(selected[-1].get("file_line") or 0) if selected else 0
        return json.dumps(
            {
                "tool": "readLine",
                "status": "ok",
                **metadata,
                "module_range": [start_line, end_line],
                "file_range": [file_start, file_end],
                "lines": [
                    {
                        "module_line": int(entry.get("module_line") or 0),
                        "file_line": int(entry.get("file_line") or 0),
                        "text": str(entry.get("text") or ""),
                    }
                    for entry in selected
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    def _has_structured_signal_catalog(self, topology_text: str) -> bool:
        text = str(topology_text or "")
        return any(marker in text for marker in self._SIGNAL_GUIDANCE_MARKERS)

    def _extract_topology_signal_catalog(self, topology_text: str) -> Set[str]:
        text = str(topology_text or "")
        if not self._has_structured_signal_catalog(text):
            return set()

        names: Set[str] = set()
        for match in self._SIGNAL_TOKEN_RE.finditer(text):
            token = match.group(0)
            if match.start() > 0 and text[match.start() - 1] in {"$", "'"}:
                continue
            token = self._INDEX_RE.sub("", token)
            name = token.split(".")[-1].strip()
            if not name:
                continue
            if name in self._RESERVED_TOKENS or name.lower() in self._RESERVED_TOKENS:
                continue
            if self._CONST_TOKEN_RE.fullmatch(name):
                continue
            if name.lower() in self._TOPOLOGY_NOISE_TOKENS:
                continue
            names.add(name)
        return names

    def _extract_verilog_signal_catalog(self, source_text: str) -> Set[str]:
        text = str(source_text or "")
        if not text:
            return set()

        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        names: Set[str] = set()
        for raw_line in text.splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line or not self._DECL_KEYWORD_RE.match(line):
                continue
            normalized = re.sub(
                r"\b(?:input|output|inout|wire|reg|logic|signed|unsigned|tri|tri0|tri1|wand|wor|supply0|supply1)\b",
                " ",
                line,
            )
            normalized = self._INDEX_RE.sub(" ", normalized)
            normalized = normalized.rstrip(",);")
            for candidate in normalized.split(","):
                candidate = candidate.split("=", 1)[0].strip()
                if not candidate:
                    continue
                tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", candidate)
                if not tokens:
                    continue
                name = tokens[-1].strip()
                if not name:
                    continue
                if name in self._RESERVED_TOKENS or name.lower() in self._RESERVED_TOKENS:
                    continue
                if self._CONST_TOKEN_RE.fullmatch(name):
                    continue
                names.add(name)
        return names

    def _extract_local_signal_names(self, values: Any) -> Set[str]:
        expressions = values if isinstance(values, list) else [values]
        names: Set[str] = set()
        for value in expressions:
            text = self._DEP_REFERENCE_RE.sub("", str(value or ""))
            for match in self._SIGNAL_TOKEN_RE.finditer(text):
                token = match.group(0)
                if match.start() > 0 and text[match.start() - 1] in {"$", "'"}:
                    continue
                token = self._INDEX_RE.sub("", token)
                name = token.split(".")[-1].strip()
                if not name:
                    continue
                if name in self._RESERVED_TOKENS or name.lower() in self._RESERVED_TOKENS:
                    continue
                if self._CONST_TOKEN_RE.fullmatch(name):
                    continue
                names.add(name)
        return names

    def _is_anchor_like_name(self, name: str) -> bool:
        lowered = str(name or "").strip().lower()
        if not lowered:
            return False
        return any(token in lowered for token in self._ANCHOR_HINT_TOKENS)

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
            anchors = list(task.get("anchors") or [])
            if anchors:
                lines.append("    anchors:")
                for anchor in anchors:
                    lines.append(f"      - kind: {self._yaml_quote(anchor.get('kind'))}")
                    dep_signals = list(anchor.get("dep_signals") or [])
                    if dep_signals:
                        lines.append("        depSignals:")
                        for dep_signal in dep_signals:
                            lines.append(f"          - {self._yaml_quote(dep_signal)}")
                    else:
                        lines.append("        depSignals: []")
                    local_signals = list(anchor.get("local_signals") or [])
                    if local_signals:
                        lines.append("        localSignals:")
                        for local_signal in local_signals:
                            lines.append(f"          - {self._yaml_quote(local_signal)}")
                    else:
                        lines.append("        localSignals: []")
                    lines.append(f"        reason: {self._yaml_quote(anchor.get('reason'))}")
            else:
                lines.append("    anchors: []")
            condition_lines = list(task.get("condition_lines") or [])
            if condition_lines:
                lines.append("    condition:")
                for entry in condition_lines:
                    lines.append(f"      - {self._yaml_quote(entry)}")
            else:
                lines.append("    condition: []")
            capture_signals = list(task.get("capture_signals") or [])
            if capture_signals:
                lines.append("    capture:")
                for entry in capture_signals:
                    lines.append(f"      - {self._yaml_quote(entry)}")
            else:
                lines.append("    capture: []")
            logging_lines = list(task.get("logging_lines") or [])
            if logging_lines:
                lines.append("    logging:")
                for entry in logging_lines:
                    lines.append(f"      - {self._yaml_quote(entry)}")
            else:
                lines.append("    logging: []")
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

    def _coerce_anchor_list(self, value: Any) -> List[Dict[str, Any]]:
        entries = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        anchors: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            anchors.append(
                {
                    "kind": str(entry.get("kind") or "").strip().lower(),
                    "dep_signals": self._coerce_string_list(entry.get("dep_signals")),
                    "local_signals": self._coerce_string_list(entry.get("local_signals")),
                    "reason": self._normalize_hint_text(entry.get("reason")),
                }
            )
        return anchors

    def _validate_anchor_reason(self, *, kind: str, reason: str, signals: List[str]) -> Optional[str]:
        normalized_reason = self._normalize_hint_text(reason)
        if not normalized_reason:
            return f"`{kind}` anchor is missing a non-empty `reason`."

        reason_tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", normalized_reason)
            if token.strip()
        ]
        if len(reason_tokens) < 3:
            return (
                f"`{kind}` anchor reason `{normalized_reason}` is too short; explain why the anchor is sufficient, "
                "not just which signals are used."
            )

        signal_tokens: Set[str] = set()
        for signal in list(signals or []):
            signal_tokens.update(
                token.lower()
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(signal or ""))
                if token.strip()
            )
        meaningful_reason_tokens = [
            token
            for token in reason_tokens
            if token not in self._ANCHOR_REASON_STOPWORDS and token not in signal_tokens
        ]
        semantic_tokens = [token for token in reason_tokens if token in self._ANCHOR_REASON_SEMANTIC_HINTS]
        if not semantic_tokens or not meaningful_reason_tokens:
            return (
                f"`{kind}` anchor reason `{normalized_reason}` must describe why the anchor is sufficient; "
                "do not only restate signal names or `== 1'b1` facts."
            )
        return None

    def _normalize_anchor_entries(
        self,
        *,
        raw_task: Dict[str, Any],
        task_name: str,
        dep_name: str,
        dep_task_id: str,
        condition_lines: List[str],
        normalized_capture: List[str],
        scope_prefix: str,
        root_prefixes: List[str],
    ) -> (List[Dict[str, Any]], List[str]):
        anchors = self._coerce_anchor_list(raw_task.get("anchors"))
        if not anchors:
            return [], [f"Task `{task_name}` is missing non-empty `anchors`."]

        normalized_anchors: List[Dict[str, Any]] = []
        errors: List[str] = []
        normalized_condition_lines = [str(line or "") for line in list(condition_lines or [])]

        for anchor_index, raw_anchor in enumerate(anchors, start=1):
            kind = str(raw_anchor.get("kind") or "").strip().lower()
            dep_signals = self._coerce_string_list(raw_anchor.get("dep_signals"))
            local_signals = self._coerce_string_list(raw_anchor.get("local_signals"))
            reason = self._normalize_hint_text(raw_anchor.get("reason"))
            anchor_label = f"Task `{task_name}` anchor #{anchor_index}"

            if kind not in self._ALLOWED_ANCHOR_KINDS:
                errors.append(
                    f"{anchor_label} uses unsupported `kind={kind or '<empty>'}`; expected one of: "
                    + ", ".join(sorted(self._ALLOWED_ANCHOR_KINDS))
                    + "."
                )
                kind = "gating"
            if not dep_signals and not local_signals:
                errors.append(f"{anchor_label} must declare at least one `dep_signals` or `local_signals` entry.")
                local_signals = list(normalized_capture[:1])

            reason_error = self._validate_anchor_reason(
                kind=kind,
                reason=reason,
                signals=dep_signals + local_signals,
            )
            if reason_error:
                errors.append(f"{anchor_label} {reason_error}")
                if not reason:
                    reason = (
                        f"This {kind} anchor is retained as warning-only evidence; its sufficiency rationale was missing or malformed."
                    )

            normalized_local_signals = [
                self._normalize_signal_expression(signal, scope_prefix, root_prefixes)
                for signal in local_signals
            ]
            missing_local_signals = [
                signal for signal in normalized_local_signals if signal not in normalized_capture
            ]
            if missing_local_signals:
                errors.append(
                    f"{anchor_label} local signal(s) must also appear in `capture_signals`: "
                    + ", ".join(missing_local_signals)
                    + "."
                )

            normalized_dep_signals: List[str] = []
            for dep_signal in dep_signals:
                dep_match = self._DEP_REFERENCE_RE.search(dep_signal)
                if not dep_match:
                    errors.append(
                        f"{anchor_label} dep signal `{dep_signal}` must use `$dep.<ref_name>.<signal>` form."
                    )
                    normalized_dep_signals.append(str(dep_signal or "").strip())
                    continue
                dep_ref_name = dep_match.group(1)
                if not dep_name:
                    errors.append(f"{anchor_label} must not declare `dep_signals` on a root task.")
                elif dep_ref_name != dep_name:
                    errors.append(
                        f"{anchor_label} dep signal `{dep_signal}` must use the task's unique `dep_name={dep_name}`."
                    )
                if not any(dep_signal in condition_line for condition_line in normalized_condition_lines):
                    errors.append(
                        f"{anchor_label} dep signal `{dep_signal}` must appear in `condition_lines`."
                    )
                normalized_dep_signal = self._normalize_signal_expression(dep_signal, scope_prefix, root_prefixes)
                if dep_task_id and dep_name and dep_ref_name == dep_name:
                    normalized_dep_signal = self._DEP_REFERENCE_RE.sub(
                        lambda m: f"$dep.{dep_task_id}.{m.group(2)}",
                        normalized_dep_signal,
                    )
                normalized_dep_signals.append(normalized_dep_signal)

            if kind == "identity":
                if dep_name and (not normalized_dep_signals or not normalized_local_signals):
                    errors.append(
                        f"{anchor_label} `identity` anchors on dependent tasks must include both `dep_signals` and `local_signals`."
                    )
                if not dep_name and not normalized_local_signals:
                    errors.append(
                        f"{anchor_label} `identity` anchors on root tasks must include non-empty `local_signals`."
                    )

            normalized_anchors.append(
                {
                    "kind": kind,
                    "dep_signals": normalized_dep_signals,
                    "local_signals": normalized_local_signals,
                    "reason": reason,
                }
            )

        if dep_name and normalized_anchors and not any(anchor.get("dep_signals") for anchor in normalized_anchors):
            errors.append(
                f"Task `{task_name}` depends on `dep_name={dep_name}` but none of its anchors declare any `dep_signals`."
            )
        if not dep_name and any(anchor.get("dep_signals") for anchor in normalized_anchors):
            errors.append(f"Task `{task_name}` is a root task and must not declare any anchor `dep_signals`.")
        return normalized_anchors, errors

    def _anchor_capture_names(self, anchors: List[Dict[str, Any]]) -> List[str]:
        capture_names: List[str] = []
        for anchor in list(anchors or []):
            for capture_name in self._capture_names(list(anchor.get("local_signals") or [])):
                if capture_name and capture_name not in capture_names:
                    capture_names.append(capture_name)
        return capture_names

    def _anchor_kinds(self, anchors: List[Dict[str, Any]]) -> List[str]:
        kinds: List[str] = []
        for anchor in list(anchors or []):
            kind = str(anchor.get("kind") or "").strip()
            if kind and kind not in kinds:
                kinds.append(kind)
        return kinds

    def _anchor_reasons(self, anchors: List[Dict[str, Any]]) -> List[str]:
        reasons: List[str] = []
        for anchor in list(anchors or []):
            reason = self._normalize_hint_text(anchor.get("reason"))
            if reason and reason not in reasons:
                reasons.append(reason)
        return reasons

    @staticmethod
    def _normalize_unknown(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    @staticmethod
    def _normalize_hint_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return " ".join(text.split())

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

    def _build_visible_dep_payload(self, previous_task_contexts: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        normalized = self._normalize_visible_dep_context_list(previous_task_contexts)
        candidates = [
            {
                "ref_name": str(candidate.get("ref_name") or "").strip(),
                "task_id": str(candidate.get("task_id") or "").strip(),
                "local_ref_name": str(candidate.get("local_ref_name") or "").strip(),
                "task_name": str(candidate.get("task_name") or "").strip(),
                "capture_names": list(candidate.get("capture_names") or []),
                "anchors": list(candidate.get("anchors") or []),
                "anchor_kinds": list(candidate.get("anchor_kinds") or []),
                "anchor_capture_names": list(candidate.get("anchor_capture_names") or []),
                "anchor_reasons": list(candidate.get("anchor_reasons") or []),
                "branch_lineage": list(candidate.get("branch_lineage") or []),
                "upstream_hint": self._normalize_hint_text(candidate.get("upstream_hint")),
                "module": str(candidate.get("module") or "").strip(),
                "instance": str(candidate.get("instance") or "").strip(),
                "path": str(candidate.get("path") or "").strip(),
            }
            for candidate in normalized
        ]
        return {
            "candidates": candidates,
            "total_candidates": len(normalized),
        }

    def _build_dep_symbol(
        self,
        *,
        ref_name: str,
        task_id: str,
        capture_names: List[str],
        branch_lineage: Optional[List[str]],
        item: Dict[str, Any],
        source: str,
        upstream_hint: str = "",
        anchors: Optional[List[Dict[str, Any]]] = None,
        anchor_capture_names: Optional[List[str]] = None,
        anchor_kinds: Optional[List[str]] = None,
        anchor_reasons: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "ref_name": str(ref_name or "").strip(),
            "task_id": str(task_id or "").strip(),
            "capture_names": list(capture_names or []),
            "branch_lineage": list(branch_lineage or []),
            "upstream_hint": self._normalize_hint_text(upstream_hint),
            "anchors": [
                {
                    "kind": str(anchor.get("kind") or "").strip(),
                    "dep_signals": list(anchor.get("dep_signals") or []),
                    "local_signals": list(anchor.get("local_signals") or []),
                    "reason": self._normalize_hint_text(anchor.get("reason")),
                }
                for anchor in list(anchors or [])
                if isinstance(anchor, dict)
            ],
            "anchor_capture_names": list(anchor_capture_names or []),
            "anchor_kinds": list(anchor_kinds or []),
            "anchor_reasons": list(anchor_reasons or []),
            "module": str(item.get("module") or "").strip(),
            "instance": str(item.get("instance") or "").strip(),
            "path": self._item_path(item),
            "source": str(source or "").strip(),
        }

    @staticmethod
    def _terminal_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not tasks:
            return []

        depended_on_ids = {
            str(task.get("depends_on") or "").strip()
            for task in tasks
            if str(task.get("depends_on") or "").strip()
        }
        return [
            task
            for task in tasks
            if str(task.get("id") or "").strip()
            and str(task.get("id") or "").strip() not in depended_on_ids
        ]

    def _build_leaf_contexts_from_entry(self, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        leaf_contexts = self._normalize_leaf_context_list(entry.get("leaf_contexts") or [])
        if leaf_contexts:
            return leaf_contexts

        task_id = str(entry.get("leaf_task_id") or "").strip()
        ref_name = str(entry.get("leaf_ref_name") or "").strip()
        capture_names = list(entry.get("leaf_capture_names") or [])
        if not task_id or not ref_name or not capture_names:
            return []
        return [
            {
                "ref_name": ref_name,
                "task_id": task_id,
                "capture_names": capture_names,
                "anchors": list(entry.get("anchors") or []),
                "anchor_kinds": list(entry.get("anchor_kinds") or []),
                "anchor_capture_names": list(entry.get("anchor_capture_names") or []),
                "anchor_reasons": list(entry.get("anchor_reasons") or []),
                "branch_lineage": [ref_name],
                "module": str(entry.get("module") or "").strip(),
                "instance": str(entry.get("instance") or "").strip(),
                "path": str(entry.get("path") or "").strip(),
            }
        ]

    def _build_task_contexts_from_entry(self, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        task_contexts = self._normalize_task_context_list(entry.get("task_contexts") or [])
        if task_contexts:
            return task_contexts
        return self._normalize_task_context_list(entry.get("leaf_contexts") or [])

    @staticmethod
    def _normalize_cached_entry(entry: Dict[str, Any], artifact_yaml: str, artifact_raw: str) -> Dict[str, Any]:
        cached = dict(entry or {})
        cached.setdefault("status", "partial")
        cached.setdefault("unknown", [])
        cached.setdefault("behavior_hint", "")
        cached.setdefault("artifact_yaml", artifact_yaml)
        cached.setdefault("artifact_raw", artifact_raw)
        cached.setdefault("artifact_llm_error", "")
        cached.setdefault("task_ids", [])
        cached.setdefault("task_count", len(list(cached.get("task_ids") or [])))
        cached.setdefault("first_task_id", "")
        cached.setdefault("leaf_contexts", [])
        cached.setdefault("leaf_task_id", "")
        cached.setdefault("leaf_ref_name", "")
        cached.setdefault("leaf_capture_names", [])
        cached.setdefault("anchors", [])
        cached.setdefault("anchor_kinds", [])
        cached.setdefault("anchor_capture_names", [])
        cached.setdefault("anchor_reasons", [])
        cached.setdefault("task_contexts", [])
        if not cached.get("leaf_contexts") and cached.get("leaf_task_id") and cached.get("leaf_ref_name"):
            cached["leaf_contexts"] = [
                {
                    "task_id": str(cached.get("leaf_task_id") or "").strip(),
                    "ref_name": str(cached.get("leaf_ref_name") or "").strip(),
                    "capture_names": list(cached.get("leaf_capture_names") or []),
                    "anchors": list(cached.get("anchors") or []),
                    "anchor_kinds": list(cached.get("anchor_kinds") or []),
                    "anchor_capture_names": list(cached.get("anchor_capture_names") or []),
                    "anchor_reasons": list(cached.get("anchor_reasons") or []),
                    "branch_lineage": [str(cached.get("leaf_ref_name") or "").strip()],
                    "upstream_hint": "",
                    "module": str(cached.get("module") or "").strip(),
                    "instance": str(cached.get("instance") or "").strip(),
                    "path": str(cached.get("path") or "").strip(),
                }
            ]
        if not cached.get("task_contexts"):
            cached["task_contexts"] = list(cached.get("leaf_contexts") or [])
        return cached

    def _build_item_entry(
        self,
        *,
        item: Dict[str, Any],
        status: str,
        unknown: List[str],
        artifact_yaml: str,
        artifact_raw: str,
        artifact_llm_error: str,
        tasks: List[Dict[str, Any]],
        behavior_hint: str,
        input_hash: str,
        token_stats: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        task_contexts = self._build_task_contexts_from_tasks(
            item=item,
            tasks=tasks,
            behavior_hint=behavior_hint,
        )
        leaf_contexts = self._build_leaf_contexts_from_tasks(
            item=item,
            tasks=tasks,
            behavior_hint=behavior_hint,
        )
        single_leaf_context = leaf_contexts[0] if len(leaf_contexts) == 1 else None
        return {
            "module": str(item.get("module") or "").strip(),
            "instance": str(item.get("instance") or "").strip(),
            "path": self._item_path(item),
            "status": status,
            "unknown": list(unknown or []),
            "behavior_hint": self._normalize_hint_text(behavior_hint),
            "artifact_yaml": artifact_yaml,
            "artifact_raw": artifact_raw,
            "artifact_llm_error": artifact_llm_error,
            "task_ids": [task["id"] for task in tasks],
            "task_count": len(tasks),
            "first_task_id": tasks[0]["id"] if tasks else "",
            "task_contexts": task_contexts,
            "leaf_contexts": leaf_contexts,
            "leaf_task_id": str(single_leaf_context.get("task_id") or "").strip() if single_leaf_context else "",
            "leaf_ref_name": str(single_leaf_context.get("ref_name") or "").strip() if single_leaf_context else "",
            "leaf_capture_names": list(single_leaf_context.get("capture_names") or []) if single_leaf_context else [],
            "input_hash": input_hash,
            "token_stats": dict(token_stats or {}),
        }

    def _build_llm_error_log_text(
        self,
        *,
        item: Dict[str, Any],
        log_path: str,
        raw_text: str,
        unknown: List[str],
        token_stats: Optional[Dict[str, Any]],
        source_access_mode: str,
        apv_max_tool_rounds: int,
    ) -> str:
        normalized_unknown = [str(entry).strip() for entry in list(unknown or []) if str(entry).strip()]
        raw_error_text = str(raw_text or "").strip()
        parse_errors = [entry for entry in normalized_unknown if entry.startswith("Invalid APV JSON output:")]
        if not raw_error_text.startswith("Error:") and not parse_errors:
            return ""

        raw_preview = raw_error_text if raw_error_text else "<empty>"
        if len(raw_preview) > 4000:
            raw_preview = raw_preview[:4000] + "\n\n[... truncated ...]"

        lines = [
            f"# APV LLM Error Log: {self._item_path(item)}",
            "",
            "## Item",
            f"- module: {str(item.get('module') or '').strip()}",
            f"- instance: {str(item.get('instance') or '').strip()}",
            f"- path: {self._item_path(item)}",
            "",
            "## Runtime",
            f"- source_access_mode: {source_access_mode}",
            f"- apv_max_tool_rounds: {int(apv_max_tool_rounds)}",
            f"- debug_log: {log_path}",
            "",
            "## Token Stats",
            f"- input_tokens: {int((token_stats or {}).get('input_tokens', 0) or 0)}",
            f"- output_tokens: {int((token_stats or {}).get('output_tokens', 0) or 0)}",
            f"- total_tokens: {int((token_stats or {}).get('total_tokens', 0) or 0)}",
            "",
        ]
        if parse_errors:
            lines.extend(
                [
                    "## Parse Errors",
                    *[f"- {entry}" for entry in parse_errors],
                    "",
                ]
            )
        if raw_error_text.startswith("Error:"):
            lines.extend(
                [
                    "## Raw Error",
                    f"- {raw_error_text}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Raw Response Preview",
                "```text",
                raw_preview,
                "```",
                "",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    def _build_task_contexts_from_tasks(
        self,
        *,
        item: Dict[str, Any],
        tasks: List[Dict[str, Any]],
        behavior_hint: str = "",
    ) -> List[Dict[str, Any]]:
        task_contexts: List[Dict[str, Any]] = []
        for task in list(tasks or []):
            task_id = str(task.get("id") or "").strip()
            ref_name = str(task.get("ref_name") or "").strip()
            capture_names = list(task.get("capture_names") or [])
            if not task_id or not ref_name or not capture_names:
                continue
            upstream_hint = self._normalize_hint_text(task.get("handoff_hint")) or self._normalize_hint_text(behavior_hint)
            task_contexts.append(
                {
                    "task_id": task_id,
                    "ref_name": ref_name,
                    "task_name": str(task.get("task_name") or "").strip(),
                    "capture_names": capture_names,
                    "anchors": list(task.get("anchors") or []),
                    "anchor_kinds": list(task.get("anchor_kinds") or []),
                    "anchor_capture_names": list(task.get("anchor_capture_names") or []),
                    "anchor_reasons": list(task.get("anchor_reasons") or []),
                    "branch_lineage": list(task.get("branch_lineage") or []),
                    "upstream_hint": upstream_hint,
                    "module": str(item.get("module") or "").strip(),
                    "instance": str(item.get("instance") or "").strip(),
                    "path": self._item_path(item),
                }
            )
        return task_contexts

    def _build_leaf_contexts_from_tasks(
        self,
        *,
        item: Dict[str, Any],
        tasks: List[Dict[str, Any]],
        behavior_hint: str = "",
    ) -> List[Dict[str, Any]]:
        leaf_contexts: List[Dict[str, Any]] = []
        for task in self._terminal_tasks(tasks):
            task_id = str(task.get("id") or "").strip()
            ref_name = str(task.get("ref_name") or "").strip()
            capture_names = list(task.get("capture_names") or [])
            if not task_id or not ref_name or not capture_names:
                continue
            upstream_hint = self._normalize_hint_text(task.get("handoff_hint")) or self._normalize_hint_text(behavior_hint)
            leaf_contexts.append(
                {
                    "task_id": task_id,
                    "ref_name": ref_name,
                    "capture_names": capture_names,
                    "anchors": list(task.get("anchors") or []),
                    "anchor_kinds": list(task.get("anchor_kinds") or []),
                    "anchor_capture_names": list(task.get("anchor_capture_names") or []),
                    "anchor_reasons": list(task.get("anchor_reasons") or []),
                    "branch_lineage": list(task.get("branch_lineage") or []),
                    "upstream_hint": upstream_hint,
                    "module": str(item.get("module") or "").strip(),
                    "instance": str(item.get("instance") or "").strip(),
                    "path": self._item_path(item),
                }
            )
        return leaf_contexts

    def _normalize_task_context_list(self, task_contexts: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for entry in list(task_contexts or []):
            if not isinstance(entry, dict):
                continue
            task_id = str(entry.get("task_id") or entry.get("id") or "").strip()
            ref_name = str(entry.get("ref_name") or "").strip()
            local_ref_name = str(entry.get("local_ref_name") or ref_name or "").strip()
            capture_names = self._coerce_string_list(entry.get("capture_names"))
            anchors = self._coerce_anchor_list(entry.get("anchors"))
            anchor_kinds = self._coerce_string_list(entry.get("anchor_kinds")) or self._anchor_kinds(anchors)
            anchor_capture_names = self._coerce_string_list(entry.get("anchor_capture_names")) or self._anchor_capture_names(anchors)
            anchor_reasons = self._coerce_string_list(entry.get("anchor_reasons")) or self._anchor_reasons(anchors)
            branch_lineage = self._coerce_string_list(entry.get("branch_lineage"))
            upstream_hint = self._normalize_hint_text(entry.get("upstream_hint"))
            task_name = str(entry.get("task_name") or entry.get("name") or "").strip()
            if ref_name and not branch_lineage:
                branch_lineage = [ref_name]
            if not task_id or not ref_name or not capture_names:
                continue
            normalized.append(
                {
                    "task_id": task_id,
                    "ref_name": ref_name,
                    "local_ref_name": local_ref_name,
                    "task_name": task_name,
                    "capture_names": capture_names,
                    "anchors": anchors,
                    "anchor_kinds": anchor_kinds,
                    "anchor_capture_names": anchor_capture_names,
                    "anchor_reasons": anchor_reasons,
                    "branch_lineage": branch_lineage,
                    "upstream_hint": upstream_hint,
                    "module": str(entry.get("module") or "").strip(),
                    "instance": str(entry.get("instance") or "").strip(),
                    "path": str(entry.get("path") or "").strip(),
                }
            )
        return normalized

    def _normalize_leaf_context_list(self, leaf_contexts: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for entry in list(leaf_contexts or []):
            if not isinstance(entry, dict):
                continue
            task_id = str(entry.get("task_id") or "").strip()
            ref_name = str(entry.get("ref_name") or "").strip()
            capture_names = self._coerce_string_list(entry.get("capture_names"))
            anchors = self._coerce_anchor_list(entry.get("anchors"))
            anchor_kinds = self._coerce_string_list(entry.get("anchor_kinds")) or self._anchor_kinds(anchors)
            anchor_capture_names = self._coerce_string_list(entry.get("anchor_capture_names")) or self._anchor_capture_names(anchors)
            anchor_reasons = self._coerce_string_list(entry.get("anchor_reasons")) or self._anchor_reasons(anchors)
            branch_lineage = self._coerce_string_list(entry.get("branch_lineage"))
            upstream_hint = self._normalize_hint_text(entry.get("upstream_hint"))
            if ref_name and not branch_lineage:
                branch_lineage = [ref_name]
            if not task_id or not ref_name or not capture_names:
                continue
            normalized.append(
                {
                    "task_id": task_id,
                    "ref_name": ref_name,
                    "capture_names": capture_names,
                    "anchors": anchors,
                    "anchor_kinds": anchor_kinds,
                    "anchor_capture_names": anchor_capture_names,
                    "anchor_reasons": anchor_reasons,
                    "branch_lineage": branch_lineage,
                    "upstream_hint": upstream_hint,
                    "module": str(entry.get("module") or "").strip(),
                    "instance": str(entry.get("instance") or "").strip(),
                    "path": str(entry.get("path") or "").strip(),
                }
            )
        return normalized

    def _normalize_visible_dep_context_list(self, dep_contexts: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for entry in self._normalize_task_context_list(dep_contexts):
            task_id = str(entry.get("task_id") or "").strip()
            local_ref_name = str(entry.get("local_ref_name") or entry.get("ref_name") or "").strip()
            if not task_id:
                continue
            normalized.append(
                {
                    "task_id": task_id,
                    "ref_name": task_id,
                    "local_ref_name": local_ref_name,
                    "task_name": str(entry.get("task_name") or "").strip(),
                    "capture_names": list(entry.get("capture_names") or []),
                    "anchors": list(entry.get("anchors") or []),
                    "anchor_kinds": list(entry.get("anchor_kinds") or []),
                    "anchor_capture_names": list(entry.get("anchor_capture_names") or []),
                    "anchor_reasons": list(entry.get("anchor_reasons") or []),
                    "branch_lineage": list(entry.get("branch_lineage") or []),
                    "upstream_hint": self._normalize_hint_text(entry.get("upstream_hint")),
                    "module": str(entry.get("module") or "").strip(),
                    "instance": str(entry.get("instance") or "").strip(),
                    "path": str(entry.get("path") or "").strip(),
                }
            )
        return normalized

    def _detect_candidate_overflow(self, previous_task_contexts: Optional[List[Dict[str, Any]]]) -> str:
        _ = previous_task_contexts
        return ""

    def _find_duplicate_item_paths(self, orchestrate_items: List[Dict[str, Any]]) -> List[str]:
        counts: Dict[str, int] = {}
        for item in list(orchestrate_items or []):
            path = self._item_path(item)
            if not path:
                continue
            counts[path] = counts.get(path, 0) + 1
        return sorted(path for path, count in counts.items() if count > 1)

    @staticmethod
    def _branch_lineage_key(branch_lineage: List[str]) -> str:
        return json.dumps(list(branch_lineage or []), ensure_ascii=False)

    def _format_branch_lineage(self, branch_lineage: List[str]) -> str:
        return self._branch_lineage_key(branch_lineage)

    def _derive_branch_lineage(self, *, dep_symbol: Optional[Dict[str, Any]], ref_name: str) -> List[str]:
        normalized_ref_name = str(ref_name or "").strip()
        if not dep_symbol:
            return [normalized_ref_name] if normalized_ref_name else []

        parent_lineage = self._coerce_string_list(dep_symbol.get("branch_lineage"))
        if not parent_lineage:
            parent_ref_name = str(dep_symbol.get("ref_name") or "").strip()
            if parent_ref_name:
                parent_lineage = [parent_ref_name]
        if normalized_ref_name:
            return parent_lineage + [normalized_ref_name]
        return parent_lineage

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
