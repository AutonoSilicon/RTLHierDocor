"""Pass3 generation module.

This module isolates chip-level (pass3) orchestration from the main
doc generator to keep responsibilities focused and files maintainable.
"""
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .pass3_instrack_stages import Pass3InStrackStages
from .pass3_instrack_apv import Pass3InStrackAPV
from .pass3_partition_helpers import Pass3PartitionHelpers
from .pass3_debug import Pass3Debug
from .pass3_hash import Pass3Hash
from .pass3_isa import Pass3ISA
from .pass3_instrack_parsing import Pass3InStrackParsing
from .pass3_tools import Pass3Tools
from .pass3_prompts import Pass3Prompts
from .pass3_topology import Pass3Topology
from .pass3_utils import (
    hash_text,
    safe_slug,
    encode_rel_path,
    normalize_instruction,
    split_chain,
    extract_mermaid_code,
    extract_json_code,
    classify_instruction,
    parse_roots_cell_tokens,
)

from .prompts import (
    PASS3_1_SYSTEM,
    PASS3_1_PROMPT,
    PASS3_2_SYSTEM,
    PASS3_2_PROMPT,
    PASS3_3_1_SEARCH_SYSTEM,
    PASS3_3_1_SEARCH_PROMPT,
)


class Pass3Generator:
    """Encapsulates all pass3 logic and helper tools."""

    def __init__(self, owner: Any):
        self.owner = owner
        self._subagent_cache: Dict[str, str] = {}
        self._subagent_token_stats: Dict[str, Dict[str, int]] = {}
        self._agent_log_seq: int = 0

        # Helper objects for delegation
        self._instrack_stages = Pass3InStrackStages(self)
        self._instrack_apv = Pass3InStrackAPV(self)
        self._partition_helpers = Pass3PartitionHelpers(self)
        self._debug = Pass3Debug(self)
        self._hash = Pass3Hash(self)
        self._isa = Pass3ISA(self)
        self._instrack_parsing = Pass3InStrackParsing(self)
        self._tools = Pass3Tools(self)
        self._prompts = Pass3Prompts(self)
        self._topology = Pass3Topology(self)

    def clear_progress(self):
        self.owner.project_tracker.clear()

    def _project_log_path(self, name: str) -> str:
        return self._debug.project_log_path(name)

    def _next_agent_log_seq(self) -> int:
        return self._debug.next_agent_log_seq()

    def _build_pass3_instrack_agent_log_path(
        self,
        *,
        instruction: str,
        node_path: str,
        level: int,
        role: str,
    ) -> str:
        return self._debug.build_pass3_instrack_agent_log_path(
            instruction=instruction,
            node_path=node_path,
            level=level,
            role=role,
        )

    def _build_pass3_3_1_agent_log_path(
        self,
        *,
        instruction: str,
        node_path: str,
        level: int,
        role: str,
    ) -> str:
        return self._debug.build_pass3_3_1_agent_log_path(
            instruction=instruction,
            node_path=node_path,
            level=level,
            role=role,
        )

    def _fork_trace_log_path_pass3_3_1(self) -> Path:
        return self._debug.fork_trace_log_path_pass3_3_1()

    def _fork_trace_log_path_pass3_3_2(self) -> Path:
        return self._debug.fork_trace_log_path_pass3_3_2()

    def _append_fork_trace(self, event: str, payload: Dict[str, Any]):
        self._debug.append_fork_trace(event, payload)

    def _append_recursive_context_log(
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
        self._debug.append_recursive_context_log(
            log_path,
            top_node=top_node,
            current_node=current_node,
            level=level,
            prompt_style=prompt_style,
            task=task,
            cache_key=cache_key,
            child_overview=child_overview,
        )

    def _append_agent_io_snapshot(
        self,
        log_path: str,
        *,
        stage: str,
        system: str,
        prompt: str,
        output: str = "",
        error: str = "",
        prompt_redaction_policy: str = "",
    ):
        self._debug.append_agent_io_snapshot(
            log_path,
            stage=stage,
            system=system,
            prompt=prompt,
            output=output,
            error=error,
            prompt_redaction_policy=prompt_redaction_policy,
        )

    def _append_instrack_continuation_snapshot(
        self,
        log_path: str,
        *,
        round_idx: int,
        child_instance: str,
        child_module: str,
        cached: bool,
        continuation_state_json: str,
    ):
        self._debug.append_instrack_continuation_snapshot(
            log_path,
            round_idx=round_idx,
            child_instance=child_instance,
            child_module=child_module,
            cached=cached,
            continuation_state_json=continuation_state_json,
        )

    @staticmethod
    def _hash_text(text: str) -> str:
        return hash_text(text)

    @staticmethod
    def _build_recursive_cache_key(
        prompt_style: str,
        node_path: str,
        task: str,
        level: int,
    ) -> str:
        """Build a stable cache key for recursive sub-agent calls."""
        return f"{prompt_style}||{node_path}||{task}||L{level}"

    async def _dispatch_direct_child_fork_subagent(
        self,
        *,
        scope_node: Any,
        module_selector: str,
        child_task: str,
        parent_path: str,
        parent_instance: str,
        parent_module: str,
        parent_level: int,
        prompt_style: str,
        runner: Callable[[Any, str], Awaitable[Dict[str, Any]]],
        request_event: str = "fork_request",
        reject_event: str = "fork_rejected",
        dispatch_event: str = "fork_dispatch",
        return_event: str = "fork_return",
        default_task_builder: Optional[Callable[[Any], str]] = None,
        task_error_message: str = "",
        require_task: bool = True,
    ) -> str:
        module_text = str(module_selector or "")
        resolved_task = str(child_task or "").strip()

        self._append_fork_trace(
            request_event,
            {
                "parent_path": parent_path,
                "parent_instance": parent_instance,
                "parent_module": parent_module,
                "parent_level": parent_level,
                "module_selector": module_text,
                "task": resolved_task,
                "prompt_style": prompt_style,
            },
        )

        child_node, error = self._resolve_scope_node(scope_node, module_text, child_only=True)
        if child_node is None:
            self._append_fork_trace(
                reject_event,
                {
                    "parent_path": parent_path,
                    "parent_level": parent_level,
                    "module_selector": module_text,
                    "reason": error,
                    "prompt_style": prompt_style,
                },
            )
            return error

        if not resolved_task and default_task_builder is not None:
            resolved_task = str(default_task_builder(child_node) or "").strip()

        if require_task and not resolved_task:
            error_message = (
                task_error_message
                or "Error: forkSubAgent requires a non-empty 'task'. The parent agent must define a free-form goal for the child agent."
            )
            self._append_fork_trace(
                reject_event,
                {
                    "parent_path": parent_path,
                    "parent_level": parent_level,
                    "child_instance": child_node.instance_name,
                    "child_module": child_node.module_name,
                    "child_level": parent_level + 1,
                    "module_selector": module_text,
                    "reason": error_message,
                    "prompt_style": prompt_style,
                },
            )
            return error_message

        child_level = parent_level + 1
        self._append_fork_trace(
            dispatch_event,
            {
                "parent_path": parent_path,
                "parent_level": parent_level,
                "child_instance": child_node.instance_name,
                "child_module": child_node.module_name,
                "child_level": child_level,
                "task": resolved_task,
                "prompt_style": prompt_style,
            },
        )

        result_payload = await runner(child_node, resolved_task)
        result_payload = dict(result_payload or {})
        tool_result = str(result_payload.get("tool_result") or "")
        report_chars = int(result_payload.get("report_chars") or len(tool_result))
        prompt_tokens = int(result_payload.get("prompt_tokens") or 0)

        trace_payload = {
            "parent_path": parent_path,
            "parent_level": parent_level,
            "child_instance": child_node.instance_name,
            "child_module": child_node.module_name,
            "child_level": child_level,
            "report_chars": report_chars,
            "prompt_tokens": prompt_tokens,
            "prompt_style": prompt_style,
        }
        extra_trace_payload = result_payload.get("trace_payload")
        if isinstance(extra_trace_payload, dict):
            trace_payload.update(extra_trace_payload)
        self._append_fork_trace(return_event, trace_payload)
        return tool_result

    def _build_instance_path_index(self, top_node: Any) -> Dict[str, Any]:
        return self._topology.build_instance_path_index(top_node)

    def _build_pass3_1_input_hash(self, top_module: str, top_description: str) -> str:
        return self._hash.build_pass3_1_input_hash(top_module, top_description)

    def _build_pass3_2_input_hash(
        self, top_module: str, top_description: str, subsystem_partition: str
    ) -> str:
        return self._hash.build_pass3_2_input_hash(top_module, top_description, subsystem_partition)

    def _build_pass3_3_search_input_hash(
        self,
        top_module: str,
        instruction: str,
        top_preview: str,
        core_partition: str,
        instruction_datasheet: str,
    ) -> str:
        return self._hash.build_pass3_3_search_input_hash(
            top_module, instruction, top_preview, core_partition, instruction_datasheet
        )

    @staticmethod
    def _pass3_3_1_resume_enabled() -> bool:
        """Pass 3.3.1 search artifacts are reusable across reruns."""
        return True

    @staticmethod
    def _pass3_3_2_resume_enabled() -> bool:
        """Pass 3.3.2 orchestration artifacts follow normal cache reuse."""
        return True

    @staticmethod
    def _instrack_datasheet_path() -> Path:
        from .pass3_isa import instrack_datasheet_path
        return instrack_datasheet_path()

    def _load_instrack_datasheet(self) -> str:
        return self._isa.load_instrack_datasheet()

    def _extract_instruction_datasheet_excerpt(self, datasheet_text: str, instruction: str) -> str:
        return self._isa.extract_instruction_datasheet_excerpt(datasheet_text, instruction)

    @staticmethod
    def _instruction_profiles() -> Dict[str, List[str]]:
        from .pass3_isa import instruction_profiles
        return instruction_profiles()

    @staticmethod
    def _normalize_instruction(inst: str) -> str:
        return normalize_instruction(inst)

    def _resolve_instrack_instructions(self) -> List[str]:
        return self._isa.resolve_instrack_instructions()

    @staticmethod
    def _safe_slug(text: str) -> str:
        return safe_slug(text)

    @staticmethod
    def _encode_rel_path(rel_path: str) -> str:
        return encode_rel_path(rel_path)

    @staticmethod
    def _parse_roots_cell_tokens(roots_cell: str) -> List[str]:
        return parse_roots_cell_tokens(roots_cell)

    def _resolve_root_token_to_paths(self, token: str, path_index: Dict[str, Any]) -> List[str]:
        return self._topology.resolve_root_token_to_paths(token, path_index)

    def _extract_pass3_3_instrack_json(
        self,
        markdown: str,
        top_node: Any,
        instruction: str,
    ) -> Dict[str, Any]:
        return self._instrack_parsing.extract_pass3_3_instrack_json(markdown, top_node, instruction)

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> List[str]:
        from .pass3_utils import contains_any
        return contains_any(text, keywords)

    def _discover_core_candidates(self, top_node: Any) -> List[Dict[str, Any]]:
        return self._topology.discover_core_candidates(top_node)

    def _tool_explore_core(self, top_node: Any, max_candidates: int = 10) -> str:
        return self._topology.tool_explore_core(top_node, max_candidates)

    @staticmethod
    def _classify_instruction(instruction: str) -> str:
        return classify_instruction(instruction)

    @staticmethod
    def _split_chain(text: str) -> List[str]:
        return split_chain(text)

    @staticmethod
    def _extract_mermaid_code(markdown: str) -> str:
        return extract_mermaid_code(markdown)

    @staticmethod
    def _extract_json_code(markdown: str) -> str:
        return extract_json_code(markdown)

    def _extract_routes_from_mermaid(self, mermaid_code: str) -> Tuple[List[str], List[str], List[str]]:
        return self._instrack_parsing.extract_routes_from_mermaid(mermaid_code)

    def _coerce_instrack_mermaid_only(self, content: str) -> str:
        return self._instrack_parsing.coerce_instrack_mermaid_only(content)

    def _extract_pass3_3_search_json(self, content: str, top_node: Any, instruction: str) -> Dict[str, Any]:
        return self._instrack_parsing.extract_pass3_3_search_json(content, top_node, instruction)

    @staticmethod
    def _normalize_line_range(value: Any) -> Dict[str, int]:
        return Pass3InStrackParsing.normalize_line_range(value)

    def _coerce_instrack_search_json_only(self, content: str, top_node: Any, instruction: str) -> str:
        return self._instrack_parsing.coerce_instrack_search_json_only(content, top_node, instruction)

    def _record_path_entry(
        self,
        *,
        instruction: str,
        current_node: Any,
        level: int,
        relation: str,
        start_block: str,
        key_path: str,
        evidence: str,
        confidence: str,
        handoff_to: str,
        register_reads: Optional[List[str]] = None,
        register_writes: Optional[List[str]] = None,
        key_conditions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self._instrack_parsing.record_path_entry(
            instruction=instruction,
            current_node=current_node,
            level=level,
            relation=relation,
            start_block=start_block,
            key_path=key_path,
            evidence=evidence,
            confidence=confidence,
            handoff_to=handoff_to,
            register_reads=register_reads,
            register_writes=register_writes,
            key_conditions=key_conditions,
        )

    @staticmethod
    def _normalize_str_list(values: Optional[List[str]]) -> List[str]:
        from .pass3_utils import normalize_str_list
        return normalize_str_list(values)

    @staticmethod
    def _coerce_optional_list(value: Any) -> List[str]:
        from .pass3_utils import coerce_optional_list
        return coerce_optional_list(value)

    def _is_register_level_record(self, item: Dict[str, Any]) -> bool:
        return self._instrack_parsing.is_register_level_record(item)

    def _enforce_register_level_records(self, search_json: Dict[str, Any], records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._instrack_parsing.enforce_register_level_records(search_json, records)

    @staticmethod
    def _dedupe_path_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return Pass3InStrackParsing.dedupe_path_records(records)

    def _render_instrack_search_markdown(self, search_json: Dict[str, Any], records: List[Dict[str, Any]]) -> str:
        return self._instrack_parsing.render_instrack_search_markdown(search_json, records)

    def _build_topology_block(self, module_name: str) -> str:
        return self._topology.build_topology_block(module_name)

    def _build_pass2_style_topology_block(self, module_name: str) -> str:
        return self._build_topology_block(module_name)

    def _tool_explore_inst_route(self, top_node: Any, instruction: str, max_domains: int = 12) -> str:
        return self._topology.tool_explore_inst_route(top_node, instruction, max_domains)

    def _pass3_1_tools(self) -> List[Dict[str, Any]]:
        return self._tools.pass3_1_tools()

    def _pass3_2_tools(self) -> List[Dict[str, Any]]:
        return self._tools.pass3_2_tools()

    def _pass3_3_1_tools(self) -> List[Dict[str, Any]]:
        return self._tools.pass3_3_1_tools()

    def _pass3_3_2_tools(self) -> List[Dict[str, Any]]:
        return self._tools.pass3_3_2_tools()

    def _pass3_3_2_parent_tools(self) -> List[Dict[str, Any]]:
        return self._tools.pass3_3_2_parent_tools()

    def _pass3_3_tools(self) -> List[Dict[str, Any]]:
        return self._tools.pass3_3_tools()

    def _build_instrack_orchestrate_state_json(
        self,
        *,
        current_module: str,
        current_instance: str,
        boundary_takeover: Optional[List[Dict[str, Any]]] = None,
        lifecycle_context: Optional[List[Dict[str, Any]]] = None,
        continuation_source: Optional[Dict[str, Any]] = None,
        override_hint: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self._prompts.build_instrack_orchestrate_state_json(
            current_module=current_module,
            current_instance=current_instance,
            boundary_takeover=boundary_takeover,
            lifecycle_context=lifecycle_context,
            continuation_source=continuation_source,
            override_hint=override_hint,
        )

    def _pass3_recursive_tools(self, prompt_style: str = "architecture") -> List[Dict[str, Any]]:
        return self._tools.pass3_recursive_tools(prompt_style)

    @staticmethod
    def _normalize_pass_section(section: str) -> str:
        return Pass3Tools.normalize_pass_section(section)

    def _get_module_section_content(self, module_name: str, canonical_section: str) -> str:
        tracker = self.owner.tracker
        getters = {
            "pass2_1": tracker.get_pass2_1_content,
            "pass2_2": tracker.get_pass2_2_content,
            "pass2_3": tracker.get_pass2_3_content,
            "pass2_4": tracker.get_pass2_4_content,
            "pass2_5": tracker.get_pass2_5_content,
            "pass2_6": tracker.get_pass2_6_content,
            "pass2_7": tracker.get_pass2_7_content,
        }
        getter = getters.get(canonical_section)
        if getter is None:
            return ""
        return getter(module_name) or ""

    def _resolve_scope_node(self, current_node: Any, module_selector: str, child_only: bool = False) -> Tuple[Optional[Any], str]:
        selector = (module_selector or "").strip()
        if not selector:
            return None, "Error: module is empty"

        if child_only and selector in ["self", "<self>", ".", current_node.instance_name, current_node.module_name]:
            return None, (
                "Error: cannot fork the current module back into itself. "
                "Use the current-module evidence already in context (or readSource on the current module) "
                "to verify same-module facts, and use forkSubAgent only for direct children."
            )

        children = list(current_node.children.values())
        visible = children if child_only else [current_node] + children

        if not child_only and selector in ["self", "<self>", ".", current_node.instance_name, current_node.module_name]:
            return current_node, ""

        for n in visible:
            if selector == n.instance_name:
                return n, ""

        candidates = [n for n in visible if selector == n.module_name]
        if candidates:
            candidates.sort(key=lambda n: (n.depth, n.instance_name))
            return candidates[0], ""

        child_hints = ", ".join(sorted({f"{c.instance_name}({c.module_name})" for c in children})[:12])
        if child_only:
            return None, (
                "Error: module is not a direct child. "
                "Use forkSubAgent only on direct children. "
                f"Children: {child_hints or 'N/A'}"
            )

        visible_hints = ", ".join(sorted({f"{n.instance_name}({n.module_name})" for n in visible})[:16])
        return None, (
            "Error: module is outside current read scope. "
            "At this level you can read only current module and direct children. "
            "For deeper nodes, call forkSubAgent on the relevant child first. "
            f"Visible: {visible_hints or 'N/A'}"
        )

    def _tool_read_doc_scoped(self, current_node: Any, module: str, section: str) -> str:
        target, error = self._resolve_scope_node(current_node, module, child_only=False)
        if target is None:
            return error

        canonical = self._normalize_pass_section(section)
        if not canonical:
            return (
                "Error: unsupported section. Allowed: "
                "pass2.1, pass2.2, pass2.3, pass2.4, pass2.5, pass2.6, pass2.7"
            )

        content = self._get_module_section_content(target.module_name, canonical)
        if not content:
            return (
                f"[readDoc] module={target.instance_name}({target.module_name}), section={canonical}\n\n"
                "No content found (possibly not generated yet)."
            )

        return (
            f"[readDoc] module={target.instance_name}({target.module_name}), section={canonical}\n\n"
            f"{content}"
        )

    @staticmethod
    def _extract_pass3_1_output_schema() -> str:
        return Pass3Prompts.extract_pass3_1_output_schema()

    def _build_pass3_recursive_system(self, prompt_style: str = "architecture") -> str:
        return self._prompts.build_pass3_recursive_system(prompt_style)

    def _build_pass3_recursive_prompt(
        self,
        top_node: Any,
        current_node: Any,
        level: int,
        task: str,
        instruction: str,
        instruction_datasheet: str,
        current_description: str,
        child_overview: str,
        prompt_style: str = "architecture",
        path_records: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        return self._prompts.build_pass3_recursive_prompt(
            top_node=top_node,
            current_node=current_node,
            level=level,
            task=task,
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            current_description=current_description,
            child_overview=child_overview,
            prompt_style=prompt_style,
            path_records=path_records,
        )

    async def _run_pass3_recursive_agent(
        self,
        top_node: Any,
        current_node: Any,
        task: str = "",
        level: int = 0,
        max_depth: int = 6,
        prompt_style: str = "architecture",
        instruction: str = "",
        instruction_datasheet: str = "",
        path_records: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        node_path = current_node.get_path() if hasattr(current_node, "get_path") else current_node.instance_name
        norm_task = (task or "").strip() or "生成该层级的top-down架构概览和可执行任务拆解"
        cache_key = self._build_recursive_cache_key(prompt_style, node_path, norm_task, level)
        if path_records is None:
            path_records = []

        self._append_fork_trace(
            "recursive_enter",
            {
                "top_module": top_node.module_name,
                "node_path": node_path,
                "instance": current_node.instance_name,
                "module": current_node.module_name,
                "level": level,
                "prompt_style": prompt_style,
                "task": norm_task,
                "cache_key": cache_key,
            },
        )

        if prompt_style != "instrack_search" and cache_key in self._subagent_cache:
            self._append_fork_trace(
                "recursive_cache_hit",
                {
                    "node_path": node_path,
                    "instance": current_node.instance_name,
                    "module": current_node.module_name,
                    "level": level,
                    "prompt_style": prompt_style,
                    "cache_key": cache_key,
                },
            )
            return self._subagent_cache[cache_key]

        if level > max_depth:
            self._append_fork_trace(
                "recursive_depth_limit",
                {
                    "node_path": node_path,
                    "instance": current_node.instance_name,
                    "module": current_node.module_name,
                    "level": level,
                    "max_depth": max_depth,
                    "prompt_style": prompt_style,
                },
            )
            return f"[SubAgent depth={level}] Reached max_depth={max_depth}. Stop recursion."

        grouped_children: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        for child in sorted(current_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_arch = self.owner.tracker.get_pass1_content(child.module_name) or "无可用 preview 文档"
            group_key = f"{child.module_name}\0{child_arch}"
            bucket = grouped_children.get(group_key)
            if bucket is None:
                bucket = {
                    "module_name": child.module_name,
                    "child_arch": child_arch,
                    "instances": [],
                }
                grouped_children[group_key] = bucket
            bucket["instances"].append(child.instance_name)

        child_lines = []
        for bucket in grouped_children.values():
            instance_names = [str(name).strip() for name in bucket["instances"] if str(name).strip()]
            if len(instance_names) > 6:
                hidden = len(instance_names) - 6
                instance_label = f"{', '.join(instance_names[:6])} ... (+{hidden} more)"
            else:
                instance_label = ", ".join(instance_names)
            child_lines.append(
                f"- {instance_label} ({bucket['module_name']}): {bucket['child_arch']}"
            )
        child_overview = "\n".join(child_lines) if child_lines else "- 无子模块"

        current_description = self.owner.tracker.get_pass1_content(current_node.module_name) or "无可用 preview 文档"

        system = self._build_pass3_recursive_system(prompt_style=prompt_style)
        prompt = self._build_pass3_recursive_prompt(
            top_node=top_node,
            current_node=current_node,
            level=level,
            task=norm_task,
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            current_description=current_description,
            child_overview=child_overview,
            prompt_style=prompt_style,
            path_records=path_records,
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "readSource":
                module = str(args.get("module") or "").strip()
                scoped_node, scope_error = self._resolve_scope_node(current_node, module, child_only=False)
                if scoped_node is None:
                    if prompt_style == "instrack_search":
                        return (
                            "Error: instrack readSource scope violation. "
                            "At this level readSource can read only current module. "
                            "To inspect child modules, use forkSubAgent(module, task). "
                            f"Details: {scope_error}"
                        )
                    return (
                        "Error: instrack readSource scope violation. "
                        "At this level you can read only current module and direct children. "
                        "For deeper modules, call forkSubAgent(module, task) first. "
                        f"Details: {scope_error}"
                    )
                if prompt_style == "instrack_search" and scoped_node is not current_node:
                    return (
                        "Error: instrack readSource scope violation. "
                        "At this level readSource can read only current module. "
                        "To inspect child modules, use forkSubAgent(module, task)."
                    )
                topology = self._build_topology_block(str(scoped_node.module_name))
                return f"[readSource] module={scoped_node.module_name}\n\n{topology}"

            if tool_name == "readPreview":
                module = str(args.get("module") or "").strip()
                max_chars = args.get("max_chars", 0)
                try:
                    max_chars = int(max_chars)
                except Exception:
                    max_chars = 0
                scoped_node, scope_error = self._resolve_scope_node(current_node, module, child_only=False)
                if scoped_node is None:
                    return (
                        "Error: recursive readPreview scope violation. "
                        "At this level you can read only current module and direct children. "
                        "For deeper modules, call forkSubAgent(module, task) first. "
                        f"Details: {scope_error}"
                    )
                preview = self.owner.tracker.get_pass1_content(str(scoped_node.module_name)) or "无预览"
                cap = max_chars if max_chars > 0 else 0
                if cap > 0:
                    preview = preview[:cap]
                return (
                    f"[readPreview] module={scoped_node.module_name}\n\n"
                    f"{preview}"
                )

            if tool_name == "readDoc":
                if prompt_style == "instrack_orchestrate":
                    return (
                        "Error: readDoc is disabled in instrack_orchestrate mode to reduce context size. "
                        "Use readSource(module) instead."
                    )
                if prompt_style == "instrack_search":
                    return (
                        "Error: readDoc is disabled in instrack_search mode to reduce context size. "
                        "Use readSource(module) and forkSubAgent(module, task) instead."
                    )
                module = str(args.get("module") or "")
                section = str(args.get("section") or "")
                return self._tool_read_doc_scoped(current_node, module, section)

            if tool_name == "forkSubAgent":
                module = str(args.get("module") or "")
                child_task = str(args.get("task") or "").strip()

                async def _run_recursive_child(child_node: Any, resolved_task: str) -> Dict[str, Any]:
                    child_node_path = (
                        child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                    )
                    child_cache_key = self._build_recursive_cache_key(
                        prompt_style,
                        child_node_path,
                        resolved_task,
                        level + 1,
                    )

                    report = await self._run_pass3_recursive_agent(
                        top_node=top_node,
                        current_node=child_node,
                        task=resolved_task,
                        level=level + 1,
                        max_depth=max_depth,
                        prompt_style=prompt_style,
                        instruction=instruction,
                        instruction_datasheet=instruction_datasheet,
                        path_records=path_records,
                    )
                    if len(report) > 16000:
                        report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                    child_token_stats = dict(self._subagent_token_stats.get(child_cache_key) or {})
                    if str(prompt_style).startswith("instrack"):
                        tool_result = report
                    else:
                        tool_result = (
                            f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), "
                            f"level={level + 1}\n\n{report}"
                        )
                    return {
                        "tool_result": tool_result,
                        "report_chars": len(report),
                        "prompt_tokens": int(child_token_stats.get("input_tokens", 0) or 0),
                    }

                return await self._dispatch_direct_child_fork_subagent(
                    scope_node=current_node,
                    module_selector=module,
                    child_task=child_task,
                    parent_path=node_path,
                    parent_instance=current_node.instance_name,
                    parent_module=current_node.module_name,
                    parent_level=level,
                    prompt_style=prompt_style,
                    runner=_run_recursive_child,
                )

            return f"Error: unknown tool '{tool_name}'"

        if prompt_style == "instrack_search":
            log_path = self._build_pass3_instrack_agent_log_path(
                instruction=instruction,
                node_path=node_path,
                level=level,
                role="subagent",
            )
        else:
            log_path = self._project_log_path(
                f"pass3_recursive_{self._safe_slug(current_node.instance_name)}_L{level}"
            )

        self._append_recursive_context_log(
            log_path,
            top_node=top_node,
            current_node=current_node,
            level=level,
            prompt_style=prompt_style,
            task=norm_task,
            cache_key=cache_key,
            child_overview=child_overview,
        )

        self._append_fork_trace(
            "recursive_llm_start",
            {
                "node_path": node_path,
                "instance": current_node.instance_name,
                "module": current_node.module_name,
                "level": level,
                "prompt_style": prompt_style,
                "log_path": log_path,
            },
        )

        if prompt_style == "instrack_search":
            self._append_agent_io_snapshot(
                log_path,
                stage="Input",
                system=system,
                prompt=prompt,
                prompt_redaction_policy="instrack_context",
            )

        report, token_stats = await self.owner.llm.generate(
            system,
            prompt,
            log_path=log_path,
            tools_enabled=True,
            tools=self._pass3_recursive_tools(prompt_style=prompt_style),
            tool_callback=_tool_callback,
            max_tool_rounds=14,
        )

        if prompt_style == "instrack_search":
            self._append_agent_io_snapshot(
                log_path,
                stage="Output",
                system="",
                prompt="",
                output=report or "",
            )

        self._append_fork_trace(
            "recursive_llm_done",
            {
                "node_path": node_path,
                "instance": current_node.instance_name,
                "module": current_node.module_name,
                "level": level,
                "prompt_style": prompt_style,
                "report_chars": len(report or ""),
                "prompt_tokens": int(token_stats.get("input_tokens", 0) or 0),
            },
        )

        self._subagent_cache[cache_key] = report
        self._subagent_token_stats[cache_key] = dict(token_stats or {})
        return report

    def _tool_tree_codebase(
        self,
        root: str = "src",
        max_depth: int = 4,
        max_entries: int = 400,
        include_files: bool = True,
        ignore: Optional[List[str]] = None,
    ) -> str:
        ignore_set = set(ignore or [
            ".git",
            ".venv",
            "__pycache__",
            ".rtl_cache",
            "oss-cad-suite",
            "rtl_docs",
            "target",
        ])

        repo_root = Path.cwd()
        root_path = (repo_root / (root or "src")).resolve()
        try:
            root_path.relative_to(repo_root.resolve())
        except Exception:
            return "Error: root must be within repo"

        if not root_path.exists() or not root_path.is_dir():
            return f"Error: root not found or not a directory: {root}"

        lines: List[str] = []
        entries = 0

        def walk(dir_path: Path, depth: int):
            nonlocal entries
            if entries >= max_entries:
                return
            if depth > max_depth:
                return

            try:
                children = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except Exception:
                return

            for child in children:
                if entries >= max_entries:
                    return
                if child.name in ignore_set:
                    continue
                if child.is_dir():
                    prefix = "  " * depth
                    rel = child.relative_to(repo_root).as_posix()
                    lines.append(f"{prefix}- {rel}/")
                    entries += 1
                    walk(child, depth + 1)
                else:
                    if not include_files:
                        continue
                    prefix = "  " * depth
                    rel = child.relative_to(repo_root).as_posix()
                    lines.append(f"{prefix}- {rel}")
                    entries += 1

        lines.append(f"- {root_path.relative_to(repo_root).as_posix()}/")
        walk(root_path, 1)

        if entries >= max_entries:
            lines.append(f"\n[... truncated: reached max_entries={max_entries} ...]")

        return "[treeCodebase]\n\n" + "\n".join(lines)

    def _tool_read_doc(
        self,
        module: str,
        doc: str = "auto",
        sections: Optional[List[str]] = None,
        max_chars: int = 0,
    ) -> str:
        module_name = (module or "").strip()
        if not module_name:
            return "Error: module is empty"

        doc_key = (doc or "auto").strip().lower()
        name_map = {
            "preview": "preview.md",
            "architecture": "architecture.md",
            "description": "description.md",
            "interface_spec": "interface_spec.md",
            "design_highlights": "design_highlights.md",
            "functional_desc": "functional_desc.md",
            "register_desc": "register_desc.md",
            "timing_cdc": "timing_cdc.md",
            "block_docs": "block_docs.md",
            "flowchart": "flowchart.mmd",
            "metadata": "metadata.json",
        }

        module_dir = self.owner.modules_dir / module_name
        if not module_dir.exists():
            return f"Error: module docs not found for '{module_name}'"

        if doc_key == "auto":
            candidates = ["architecture.md", "description.md", "preview.md", "interface_spec.md"]
            chosen = None
            for fn in candidates:
                p = module_dir / fn
                if p.exists():
                    chosen = p
                    break
            if chosen is None:
                return f"Error: no doc files found for '{module_name}'"
            path = chosen
        else:
            fn = name_map.get(doc_key)
            if not fn:
                return f"Error: unknown doc type '{doc_key}'"
            path = module_dir / fn

        if not path.exists():
            return f"Error: doc file not found: {path.name}"

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error: failed to read {path.name}: {e}"

        if path.suffix == ".json":
            try:
                obj = json.loads(text)
                text = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                pass

        extracted_parts: List[str] = []
        if sections:
            for sec in sections:
                sec = (sec or "").strip()
                if not sec:
                    continue
                block = self.owner._extract_named_section(text, [sec])
                if block:
                    extracted_parts.append(block)
            if extracted_parts:
                text = "\n\n---\n\n".join(extracted_parts)

        if max_chars and len(text) > int(max_chars):
            text = text[: int(max_chars)] + "\n\n[... truncated ...]"

        return f"[readDoc] module={module_name}, file={path.name}\n\n{text}"

    async def run_pass3_1(self, top_node: Any) -> Optional[str]:
        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)

        artifact_md = "subsystem_partition.md"
        artifact_json = "subsystem_partition.json"
        out_md = self.owner.chip_dir / artifact_md
        out_json = self.owner.chip_dir / artifact_json

        top_preview = self.owner.tracker.get_pass1_content(top_node.module_name) or ""
        if not top_preview:
            top_preview = self.owner.tracker.get_pass2_1_content(top_node.module_name) or ""
        if not top_preview:
            top_preview = "无可用 preview 文档"
        top_child_preview_list = self._instrack_stages.build_child_overview(top_node)

        input_hash = self._build_pass3_1_input_hash(
            top_module=top_node.module_name,
            top_description=top_description,
        )

        if self.owner.project_tracker.is_done(artifact_md, str(out_md), expected_input_hash=input_hash):
            try:
                content = out_md.read_text(encoding="utf-8")
                if (not out_json.exists()) or (
                    not self.owner.project_tracker.is_done(artifact_json, str(out_json), expected_input_hash=input_hash)
                ):
                    partition_json = self._partition_helpers.extract_pass3_1_partition_json(content, top_node)
                    if partition_json:
                        json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
                        out_json.write_text(json_text, encoding="utf-8")
                        self.owner.project_tracker.update(
                            artifact_json,
                            json_text,
                            input_hash=input_hash,
                            meta={"source": artifact_md, "parser": "partition_table_v1"},
                        )
                return content
            except Exception:
                return None

        prompt = PASS3_1_PROMPT.format(
            top_module=top_node.module_name,
            top_description=top_description,
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "readDoc":
                module = args.get("module") or args.get("module_name") or ""
                doc = args.get("doc") or "auto"
                sections = args.get("sections")
                section = args.get("section")
                if section and sections is None:
                    sections = [str(section)]
                if sections is not None and not isinstance(sections, list):
                    sections = [str(sections)]
                max_chars = args.get("max_chars", 0)
                try:
                    max_chars = int(max_chars)
                except Exception:
                    max_chars = 0
                return self._tool_read_doc(module=str(module), doc=str(doc), sections=sections, max_chars=max_chars)

            if tool_name == "forkSubAgent":
                module = str(args.get("module") or "")
                task = str(args.get("task") or "").strip()

                async def _run_partition_child(child_node: Any, resolved_task: str) -> Dict[str, Any]:
                    child_node_path = (
                        child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                    )
                    child_cache_key = self._build_recursive_cache_key(
                        "partition",
                        child_node_path,
                        resolved_task,
                        1,
                    )
                    report = await self._run_pass3_recursive_agent(
                        top_node=top_node,
                        current_node=child_node,
                        task=resolved_task,
                        level=1,
                        prompt_style="partition",
                    )
                    if len(report) > 16000:
                        report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                    child_token_stats = dict(self._subagent_token_stats.get(child_cache_key) or {})
                    return {
                        "tool_result": (
                            f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), level=1\n\n"
                            f"{report}"
                        ),
                        "report_chars": len(report),
                        "prompt_tokens": int(child_token_stats.get("input_tokens", 0) or 0),
                    }

                return await self._dispatch_direct_child_fork_subagent(
                    scope_node=top_node,
                    module_selector=module,
                    child_task=task,
                    parent_path=top_node.module_name,
                    parent_instance=top_node.instance_name if hasattr(top_node, "instance_name") else "<top>",
                    parent_module=top_node.module_name,
                    parent_level=0,
                    prompt_style="partition",
                    request_event="pass3_1_fork_request",
                    reject_event="pass3_1_fork_rejected",
                    dispatch_event="pass3_1_fork_dispatch",
                    return_event="pass3_1_fork_return",
                    default_task_builder=lambda child_node: (
                        f"请针对 {child_node.instance_name}({child_node.module_name}) 补充 SoC 子系统边界证据，"
                        "输出可归属的 SoC 子系统、边界依据与待确认项，并按需继续 fork。"
                    ),
                    runner=_run_partition_child,
                )

            return f"Error: unknown tool '{tool_name}'"

        log_path = self._project_log_path("pass3_1_partition")
        self.owner.project_tracker.mark_running(
            artifact_md,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_1"},
        )
        try:
            content, _token_stats = await self.owner.llm.generate(
                PASS3_1_SYSTEM,
                prompt,
                log_path=log_path,
                tools_enabled=True,
                tools=self._pass3_1_tools(),
                tool_callback=_tool_callback,
                max_tool_rounds=10,
            )
        except Exception as e:
            self.owner.project_tracker.mark_failed(artifact_md, str(e))
            return None

        out_md.write_text(content, encoding="utf-8")
        self.owner.project_tracker.update(
            artifact_md,
            content,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_1"},
        )

        partition_json = self._partition_helpers.extract_pass3_1_partition_json(content, top_node)
        if partition_json:
            json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
            out_json.write_text(json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_json,
                json_text,
                input_hash=input_hash,
                meta={"source": artifact_md, "parser": "partition_table_v1"},
            )

        return content

    async def run_pass3_2(self, top_node: Any) -> Optional[str]:
        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)

        artifact_md = "core_partition.md"
        artifact_json = "core_partition.json"
        out_md = self.owner.chip_dir / artifact_md
        out_json = self.owner.chip_dir / artifact_json

        top_preview = self.owner.tracker.get_pass1_content(top_node.module_name) or "无可用 preview 文档"
        top_child_preview_list = self._instrack_stages.build_child_overview(top_node)

        pass3_1_path = self.owner.chip_dir / "subsystem_partition.md"
        subsystem_partition_text = "未检测到 pass3.1 输出，可基于工具证据自行定位 core。"
        if pass3_1_path.exists():
            try:
                subsystem_partition_text = pass3_1_path.read_text(encoding="utf-8")
            except Exception:
                pass

        input_hash = self._build_pass3_2_input_hash(
            top_module=top_node.module_name,
            top_description=top_description,
            subsystem_partition=subsystem_partition_text,
        )

        if self.owner.project_tracker.is_done(artifact_md, str(out_md), expected_input_hash=input_hash):
            try:
                content = out_md.read_text(encoding="utf-8")
                if (not out_json.exists()) or (
                    not self.owner.project_tracker.is_done(artifact_json, str(out_json), expected_input_hash=input_hash)
                ):
                    partition_json = self._partition_helpers.extract_pass3_2_partition_json(content, top_node)
                    if partition_json:
                        json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
                        out_json.write_text(json_text, encoding="utf-8")
                        self.owner.project_tracker.update(
                            artifact_json,
                            json_text,
                            input_hash=input_hash,
                            meta={"source": artifact_md, "parser": "core_partition_table_v1"},
                        )
                return content
            except Exception:
                return None

        prompt = PASS3_2_PROMPT.format(
            top_module=top_node.module_name,
            top_description=top_description,
            subsystem_partition=subsystem_partition_text,
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "exploreCore":
                max_candidates = args.get("max_candidates", 10)
                try:
                    max_candidates = int(max_candidates)
                except Exception:
                    max_candidates = 10
                return self._tool_explore_core(top_node, max_candidates=max_candidates)

            if tool_name == "readDoc":
                module = args.get("module") or args.get("module_name") or ""
                doc = args.get("doc") or "auto"
                sections = args.get("sections")
                section = args.get("section")
                if section and sections is None:
                    sections = [str(section)]
                if sections is not None and not isinstance(sections, list):
                    sections = [str(sections)]
                max_chars = args.get("max_chars", 0)
                try:
                    max_chars = int(max_chars)
                except Exception:
                    max_chars = 0
                return self._tool_read_doc(module=str(module), doc=str(doc), sections=sections, max_chars=max_chars)

            if tool_name == "forkSubAgent":
                module = str(args.get("module") or "")
                task = str(args.get("task") or "").strip()

                async def _run_core_partition_child(child_node: Any, resolved_task: str) -> Dict[str, Any]:
                    child_node_path = (
                        child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                    )
                    child_cache_key = self._build_recursive_cache_key(
                        "partition",
                        child_node_path,
                        resolved_task,
                        1,
                    )
                    report = await self._run_pass3_recursive_agent(
                        top_node=top_node,
                        current_node=child_node,
                        task=resolved_task,
                        level=1,
                        prompt_style="partition",
                    )
                    if len(report) > 16000:
                        report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                    child_token_stats = dict(self._subagent_token_stats.get(child_cache_key) or {})
                    return {
                        "tool_result": (
                            f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), level=1\n\n"
                            f"{report}"
                        ),
                        "report_chars": len(report),
                        "prompt_tokens": int(child_token_stats.get("input_tokens", 0) or 0),
                    }

                return await self._dispatch_direct_child_fork_subagent(
                    scope_node=top_node,
                    module_selector=module,
                    child_task=task,
                    parent_path=top_node.module_name,
                    parent_instance=top_node.instance_name if hasattr(top_node, "instance_name") else "<top>",
                    parent_module=top_node.module_name,
                    parent_level=0,
                    prompt_style="partition",
                    request_event="pass3_2_fork_request",
                    reject_event="pass3_2_fork_rejected",
                    dispatch_event="pass3_2_fork_dispatch",
                    return_event="pass3_2_fork_return",
                    default_task_builder=lambda child_node: (
                        f"请针对 {child_node.instance_name}({child_node.module_name}) 输出 core 相关 top-down 微架构划分，"
                        "并按需继续fork下一级。"
                    ),
                    runner=_run_core_partition_child,
                )

            return f"Error: unknown tool '{tool_name}'"

        log_path = self._project_log_path("pass3_2_core_partition")
        self.owner.project_tracker.mark_running(
            artifact_md,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_2"},
        )

        try:
            content, _token_stats = await self.owner.llm.generate(
                PASS3_2_SYSTEM,
                prompt,
                log_path=log_path,
                tools_enabled=True,
                tools=self._pass3_2_tools(),
                tool_callback=_tool_callback,
                max_tool_rounds=12,
            )
        except Exception as e:
            self.owner.project_tracker.mark_failed(artifact_md, str(e))
            return None

        out_md.write_text(content, encoding="utf-8")
        self.owner.project_tracker.update(
            artifact_md,
            content,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_2"},
        )

        partition_json = self._partition_helpers.extract_pass3_2_partition_json(content, top_node)
        if partition_json:
            json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
            out_json.write_text(json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_json,
                json_text,
                input_hash=input_hash,
                meta={"source": artifact_md, "parser": "core_partition_table_v1"},
            )

        return content

    async def run_pass3_3(self, top_node: Any) -> Optional[str]:
        if not getattr(self.owner, "pass3_3_enabled", True):
            return None

        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)
        instrack_dir = self.owner.chip_dir / "instrack"
        instrack_dir.mkdir(parents=True, exist_ok=True)

        instructions = self._resolve_instrack_instructions()
        if not instructions:
            return None

        top_preview = self.owner.tracker.get_pass1_content(top_node.module_name) or "无可用 preview 文档"
        top_child_preview_list = self._instrack_stages.build_child_overview(top_node)

        pass3_2_path = self.owner.chip_dir / "core_partition.md"
        core_partition_text = "未检测到 pass3.2 输出，可结合 readSource 与 forkSubAgent 补齐证据。"
        if pass3_2_path.exists():
            try:
                core_partition_text = pass3_2_path.read_text(encoding="utf-8")
            except Exception:
                pass

        index_entries: List[Dict[str, Any]] = []
        datasheet_text = self._load_instrack_datasheet()

        for instruction in instructions:
            slug = self._safe_slug(instruction).lower()
            artifact_search_md = f"instrack/{slug}/{slug}.search.md"
            artifact_search_json = f"instrack/{slug}/{slug}.search.json"
            artifact_md = f"instrack/{slug}/{slug}.md"
            artifact_json = f"instrack/{slug}/{slug}.json"
            instruction_dir = instrack_dir / slug
            instruction_dir.mkdir(parents=True, exist_ok=True)
            artifacts_dir = instruction_dir / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            out_search_md = instruction_dir / f"{slug}.search.md"
            out_search_json = instruction_dir / f"{slug}.search.json"
            out_md = instruction_dir / f"{slug}.md"
            out_json = instruction_dir / f"{slug}.json"
            artifact_orchestrate_index_json = f"instrack/{slug}/artifacts/orchestrate_index.json"
            artifact_apv_index_json = f"instrack/{slug}/artifacts/apv_index.json"
            orchestrate_index_path = artifacts_dir / "orchestrate_index.json"
            apv_index_path = artifacts_dir / "apv_index.json"
            instruction_datasheet = self._extract_instruction_datasheet_excerpt(datasheet_text, instruction)

            search_input_hash = self._build_pass3_3_search_input_hash(
                top_module=top_node.module_name,
                instruction=instruction,
                top_preview=top_preview,
                core_partition=core_partition_text,
                instruction_datasheet=instruction_datasheet,
            )

            search_content: Optional[str] = None
            if (
                self._pass3_3_1_resume_enabled()
                and self.owner.project_tracker.is_done(
                    artifact_search_md,
                    str(out_search_md),
                    expected_input_hash=search_input_hash,
                )
            ):
                try:
                    search_content = out_search_md.read_text(encoding="utf-8")
                except Exception:
                    search_content = None

            if search_content is None:
                current_module_topology = self._prompts.build_instrack_current_module_topology(top_node.module_name)
                search_prompt = PASS3_3_1_SEARCH_PROMPT.format(
                    top_module=top_node.module_name,
                    instruction=instruction,
                    module_preview=top_preview,
                    current_module_topology=current_module_topology,
                    child_preview_list=top_child_preview_list,
                    core_partition=core_partition_text,
                    instruction_datasheet=instruction_datasheet,
                )

                async def _search_tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
                    if tool_name == "forkSubAgent":
                        module = str(args.get("module") or "")
                        task = str(args.get("task") or "").strip()
                        top_path = top_node.instance_name if hasattr(top_node, "instance_name") else top_node.module_name

                        async def _run_search_child(child_node: Any, resolved_task: str) -> Dict[str, Any]:
                            child_node_path = (
                                child_node.get_path() if hasattr(child_node, "get_path") else child_node.instance_name
                            )
                            child_cache_key = self._build_recursive_cache_key(
                                "instrack_search",
                                child_node_path,
                                resolved_task,
                                1,
                            )

                            report = await self._run_pass3_recursive_agent(
                                top_node=top_node,
                                current_node=child_node,
                                task=resolved_task,
                                level=1,
                                prompt_style="instrack_search",
                                instruction=instruction,
                                instruction_datasheet=instruction_datasheet,
                                path_records=[],
                            )
                            if len(report) > 16000:
                                report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                            child_token_stats = dict(self._subagent_token_stats.get(child_cache_key) or {})
                            return {
                                "tool_result": report,
                                "report_chars": len(report),
                                "prompt_tokens": int(child_token_stats.get("input_tokens", 0) or 0),
                            }

                        return await self._dispatch_direct_child_fork_subagent(
                            scope_node=top_node,
                            module_selector=module,
                            child_task=task,
                            parent_path=top_path,
                            parent_instance=top_node.instance_name if hasattr(top_node, "instance_name") else "<top>",
                            parent_module=top_node.module_name,
                            parent_level=0,
                            prompt_style="instrack_search",
                            task_error_message=(
                                "Error: forkSubAgent requires a non-empty 'task'. Define a free-form child goal from the parent context."
                            ),
                            runner=_run_search_child,
                        )

                    return f"Error: unknown tool '{tool_name}'"

                search_log_path = self._build_pass3_instrack_agent_log_path(
                    instruction=instruction,
                    node_path=top_node.instance_name if hasattr(top_node, "instance_name") else top_node.module_name,
                    level=0,
                    role="search_agent",
                )
                self.owner.project_tracker.mark_running(
                    artifact_search_md,
                    input_hash=search_input_hash,
                    meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_1"},
                )

                self._append_agent_io_snapshot(
                    search_log_path,
                    stage="Input",
                    system=PASS3_3_1_SEARCH_SYSTEM,
                    prompt=search_prompt,
                    prompt_redaction_policy="instrack_context",
                )

                try:
                    search_content, _token_stats = await self.owner.llm.generate(
                        PASS3_3_1_SEARCH_SYSTEM,
                        search_prompt,
                        log_path=search_log_path,
                        tools_enabled=True,
                        tools=self._pass3_3_1_tools(),
                        tool_callback=_search_tool_callback,
                        max_tool_rounds=12,
                    )
                except Exception as e:
                    self._append_agent_io_snapshot(
                        search_log_path,
                        stage="Output",
                        system="",
                        prompt="",
                        output="",
                        error=str(e),
                    )
                    self.owner.project_tracker.mark_failed(artifact_search_md, str(e))
                    index_entries.append({
                        "instruction": instruction,
                        "slug": slug,
                        "status": "failed",
                        "stage": "search",
                        "error": str(e),
                    })
                    continue

                self._append_agent_io_snapshot(
                    search_log_path,
                    stage="Output",
                    system="",
                    prompt="",
                    output=search_content or "",
                )

                search_content = self._coerce_instrack_search_json_only(search_content or "", top_node, instruction)

                out_search_md.write_text(search_content, encoding="utf-8")
                self.owner.project_tracker.update(
                    artifact_search_md,
                    search_content,
                    input_hash=search_input_hash,
                    meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_1"},
                )

            parsed_search = self._extract_pass3_3_search_json(search_content or "", top_node, instruction)
            search_json_text = json.dumps(parsed_search, ensure_ascii=False, indent=2)
            out_search_json.write_text(search_json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_search_json,
                search_json_text,
                input_hash=search_input_hash,
                meta={"source": artifact_search_md, "parser": "instrack_startpoint_v2"},
            )

            orchestrate_input_hash = self._instrack_stages.build_pass3_3_orchestrate_input_hash(
                top_module=top_node.module_name,
                instruction=instruction,
                instruction_datasheet=instruction_datasheet,
                search_result_json_text=search_json_text,
            )

            orchestrate_items: List[Dict[str, Any]] = []
            if (
                self._pass3_3_2_resume_enabled()
                and self.owner.project_tracker.is_done(
                    artifact_orchestrate_index_json,
                    str(orchestrate_index_path),
                    expected_input_hash=orchestrate_input_hash,
                )
            ):
                try:
                    orchestrate_payload = json.loads(orchestrate_index_path.read_text(encoding="utf-8"))
                    if isinstance(orchestrate_payload, dict):
                        loaded_items = orchestrate_payload.get("items")
                        if isinstance(loaded_items, list):
                            orchestrate_items = loaded_items
                except Exception:
                    orchestrate_items = []

            if not orchestrate_items:
                self.owner.project_tracker.mark_running(
                    artifact_orchestrate_index_json,
                    input_hash=orchestrate_input_hash,
                    meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_2_orchestrate"},
                )
                orchestrate_items = await self._instrack_stages.run_pass3_3_2_orchestrate(
                    top_node=top_node,
                    instruction=instruction,
                    instruction_datasheet=instruction_datasheet,
                    search_result=parsed_search,
                )
                for item in orchestrate_items:
                    module_slug = self._safe_slug(str(item.get("module") or "unknown")).lower()
                    instance_slug = self._safe_slug(str(item.get("instance") or "inst")).lower()
                    path_slug = self._safe_slug(
                        str(item.get("path") or f"{module_slug}__{instance_slug}").replace("/", "__")
                    ).lower()
                    raw_rel = f"instrack/{slug}/artifacts/{path_slug}.orchestrate.raw.md"
                    raw_abs = artifacts_dir / f"{path_slug}.orchestrate.raw.md"
                    raw_text = str(item.get("raw_orchestration_output") or "")
                    if raw_text.strip():
                        raw_abs.write_text(raw_text, encoding="utf-8")
                        item["artifact_raw"] = raw_rel

                orchestrate_index_payload = {
                    "instruction": instruction,
                    "start_module": parsed_search.get("start_module", ""),
                    "schema_version": "pass3_3_2_orchestrate_index_v2",
                    "items": orchestrate_items,
                }
                orchestrate_index_text = json.dumps(orchestrate_index_payload, ensure_ascii=False, indent=2)
                orchestrate_index_path.write_text(orchestrate_index_text, encoding="utf-8")
                self.owner.project_tracker.update(
                    artifact_orchestrate_index_json,
                    orchestrate_index_text,
                    input_hash=orchestrate_input_hash,
                    meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_2_orchestrate"},
                )

            orchestrate_index_text = orchestrate_index_path.read_text(encoding="utf-8")
            apv_enabled = bool(getattr(self.owner, "pass3_3_3_enabled", True))
            apv_items: List[Dict[str, Any]] = []
            apv_status = ""
            apv_error = ""
            apv_index_input_hash = self._hash.build_pass3_3_apv_index_input_hash(
                top_module=top_node.module_name,
                instruction=instruction,
                start_module=str(parsed_search.get("start_module") or "").strip(),
                orchestration_json_text=orchestrate_index_text,
            )
            cached_apv_items: List[Dict[str, Any]] = []
            if apv_enabled and apv_index_path.exists():
                try:
                    apv_payload = json.loads(apv_index_path.read_text(encoding="utf-8"))
                    if isinstance(apv_payload, dict):
                        loaded_items = apv_payload.get("items")
                        if isinstance(loaded_items, list):
                            cached_apv_items = loaded_items
                except Exception:
                    cached_apv_items = []

            if apv_enabled:
                if self.owner.project_tracker.is_done(
                    artifact_apv_index_json,
                    str(apv_index_path),
                    expected_input_hash=apv_index_input_hash,
                ):
                    apv_items = list(cached_apv_items)
                    try:
                        cached_payload = json.loads(apv_index_path.read_text(encoding="utf-8"))
                    except Exception:
                        cached_payload = {}
                    apv_status = str((cached_payload or {}).get("status") or "").strip()
                    apv_error = str((cached_payload or {}).get("error") or "").strip()
                else:
                    self.owner.project_tracker.mark_running(
                        artifact_apv_index_json,
                        input_hash=apv_index_input_hash,
                        meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_3_apv"},
                    )
                    apv_result = await self._instrack_apv.generate_instruction_apv(
                        top_node=top_node,
                        instruction=instruction,
                        instruction_slug=slug,
                        instruction_datasheet=instruction_datasheet,
                        parsed_search=parsed_search,
                        orchestrate_items=orchestrate_items,
                        orchestrate_index_text=orchestrate_index_text,
                        artifacts_dir=artifacts_dir,
                        cached_items=cached_apv_items,
                    )
                    apv_items = list(apv_result.get("items") or [])
                    apv_status = str(apv_result.get("status") or "").strip()
                    apv_error = str(apv_result.get("error") or "").strip()
                    apv_index_text = str(apv_result.get("index_text") or "")
                    apv_index_path.write_text(apv_index_text, encoding="utf-8")
                    self.owner.project_tracker.update(
                        artifact_apv_index_json,
                        apv_index_text,
                        input_hash=apv_index_input_hash,
                        meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_3_apv"},
                    )

            lifecycle_modules: List[str] = []
            for item in orchestrate_items:
                module_name = str(item.get("module") or "").strip()
                if module_name and module_name not in lifecycle_modules:
                    lifecycle_modules.append(module_name)

            md_lines = [
                "```json",
                search_json_text,
                "```",
                "",
                "## Orchestrate Artifact",
                f"- `instrack/{slug}/artifacts/orchestrate_index.json`",
                "",
                "## APV Artifacts",
            ]
            if apv_items:
                for item in apv_items:
                    md_lines.append(
                        f"- `{item.get('artifact_yaml', '')}`"
                        f" (status={item.get('status', 'partial')}, tasks={item.get('task_count', 0)})"
                    )
            else:
                md_lines.append("- (none)")
            if apv_error:
                md_lines.append("")
                md_lines.append("## APV Status")
                md_lines.append(f"- `{apv_status or 'failed'}`")
                md_lines.append(f"- {apv_error}")
            md_text = "\n".join(md_lines).strip() + "\n"

            out_md.write_text(md_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_md,
                md_text,
                input_hash=apv_index_input_hash if apv_enabled else orchestrate_input_hash,
                meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3"},
            )

            json_payload = {
                **parsed_search,
                "schema_version": "pass3_3_instrack_apv_v2",
                "orchestrate_index_artifact_json": artifact_orchestrate_index_json,
                "apv_index_artifact_json": artifact_apv_index_json if apv_enabled else "",
                "apv_status": apv_status,
                "apv_error": apv_error,
                "lifecycle_modules": lifecycle_modules,
            }
            json_text = json.dumps(json_payload, ensure_ascii=False, indent=2)
            out_json.write_text(json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_json,
                json_text,
                input_hash=apv_index_input_hash if apv_enabled else orchestrate_input_hash,
                meta={"source": artifact_md, "parser": "instrack_apv_v2"},
            )

            index_entries.append({
                "instruction": instruction,
                "slug": slug,
                "status": "done",
                "search_artifact_md": artifact_search_md,
                "search_artifact_json": artifact_search_json,
                "artifact_md": artifact_md,
                "artifact_json": artifact_json,
                "start_module": parsed_search.get("start_module", ""),
                "start_block": parsed_search.get("start_block", ""),
                "key_register": parsed_search.get("key_register", ""),
                "key_register_line_range": parsed_search.get("key_register_line_range", {"start_line": 0, "end_line": 0}),
                "search_confidence": parsed_search.get("confidence", "low"),
                "orchestrate_index_artifact_json": artifact_orchestrate_index_json,
                "apv_index_artifact_json": artifact_apv_index_json if apv_enabled else "",
                "lifecycle_modules": lifecycle_modules,
            })

        index_payload = {
            "top_module": top_node.module_name,
            "isa_profile": getattr(self.owner, "isa_profile", "c910"),
            "total": len(index_entries),
            "entries": index_entries,
        }
        index_text = json.dumps(index_payload, ensure_ascii=False, indent=2)
        index_path = instrack_dir / "index.json"
        index_path.write_text(index_text, encoding="utf-8")
        self.owner.project_tracker.update(
            "instrack/index.json",
            index_text,
            input_hash=self._hash_text(index_text),
            meta={"pass": "pass3_3", "type": "instrack_index"},
        )

        return index_text
