"""Pass3 prompt builders.

This module encapsulates prompt building logic for pass3 agents.
"""

import json
from typing import Any, Dict, List, Optional

from .prompts import (
    PASS3_1_SYSTEM,
    PASS3_3_1_SEARCH_SYSTEM,
    PASS3_3_1_SEARCH_PROMPT,
    PASS3_3_2_ORCHESTRATE_SYSTEM,
    PASS3_3_2_ORCHESTRATE_PROMPT,
    PASS3_RECURSIVE_ARCHITECTURE_SYSTEM,
    PASS3_RECURSIVE_PARTITION_APPENDIX,
    PASS3_RECURSIVE_ARCHITECTURE_PROMPT,
    PASS3_RECURSIVE_PARTITION_PROMPT,
    PASS3_1_RECURSIVE_OUTPUT_SCHEMA,
)


class Pass3Prompts:
    """Helper object encapsulating pass3 prompt builders."""

    def __init__(self, generator: Any):
        self.g = generator

    def build_instrack_draw_state_json(
        self,
        *,
        current_module: str,
        current_instance: str,
        upstream_handoff: Optional[List[str]] = None,
        upstream_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build a compact continuation-state JSON string for prompts."""
        handoff = [str(item).strip() for item in (upstream_handoff or []) if str(item).strip()]
        payload: Dict[str, Any] = {
            "module": str(current_module or "").strip(),
            "instance": str(current_instance or "").strip(),
        }
        if handoff:
            payload["upstream_handoff"] = handoff[:4]
            if len(handoff) > 4:
                payload["upstream_handoff_truncated"] = len(handoff) - 4
        if isinstance(upstream_context, dict) and upstream_context:
            payload["upstream_context"] = upstream_context
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def build_instrack_current_module_topology(self, module_name: str) -> str:
        """Return the current-module topology/source block for implicit prompt injection."""
        text = self.g._build_pass2_style_topology_block(str(module_name or ""))
        text = (text or "").strip()
        if not text:
            return "无当前模块拓扑证据"
        return text

    def build_pass3_recursive_system(self, prompt_style: str = "architecture") -> str:
        """Build the system prompt for recursive pass3 agents."""
        if prompt_style == "instrack_search":
            return PASS3_3_1_SEARCH_SYSTEM.strip()

        if prompt_style == "instrack_draw":
            return PASS3_3_2_ORCHESTRATE_SYSTEM.strip()

        if prompt_style != "partition":
            return PASS3_RECURSIVE_ARCHITECTURE_SYSTEM.strip()

        return self._join_prompt_parts(PASS3_1_SYSTEM, PASS3_RECURSIVE_PARTITION_APPENDIX)

    def build_pass3_recursive_prompt(
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
        """Build the user prompt for recursive pass3 agents."""
        if prompt_style == "instrack_search":
            current_module_topology = self.build_instrack_current_module_topology(current_node.module_name)
            return PASS3_3_1_SEARCH_PROMPT.format(
                instruction=instruction or "UNKNOWN",
                instruction_datasheet=(
                    instruction_datasheet
                    if (instruction_datasheet or "").strip()
                    else "Instruction unavailable"
                ),
                module_preview=current_description,
                current_module_topology=current_module_topology,
                child_preview_list=child_overview,
            ).strip()

        if prompt_style == "instrack_draw":
            current_module_topology = self.build_instrack_current_module_topology(current_node.module_name)
            return PASS3_3_2_ORCHESTRATE_PROMPT.format(
                instruction=instruction or "UNKNOWN",
                instruction_datasheet=(
                    instruction_datasheet
                    if (instruction_datasheet or "").strip()
                    else "Instruction unavailable"
                ),
                draw_state_json=self.build_instrack_draw_state_json(
                    current_module=current_node.module_name,
                    current_instance=current_node.instance_name,
                    upstream_handoff=[],
                ),
                module_preview=current_description,
                current_module_topology=current_module_topology,
                child_preview_list=child_overview,
            ).strip()

        if prompt_style != "partition":
            return PASS3_RECURSIVE_ARCHITECTURE_PROMPT.format(
                top_module=top_node.module_name,
                level=level,
                current_instance=current_node.instance_name,
                current_module=current_node.module_name,
                task=task,
                current_description=current_description,
                child_overview=child_overview,
            )

        output_schema = self.extract_pass3_1_output_schema()
        output_schema = output_schema.replace("## 子系统划分表格", "## 当前层级子系统划分表格")

        return PASS3_RECURSIVE_PARTITION_PROMPT.format(
            current_instance=current_node.instance_name,
            current_module=current_node.module_name,
            current_description=current_description,
            top_module=top_node.module_name,
            level=level,
            task=task,
            child_overview=child_overview,
            output_schema=output_schema,
        )

    @staticmethod
    def extract_pass3_1_output_schema() -> str:
        """Return the output schema for pass3.1."""
        return PASS3_1_RECURSIVE_OUTPUT_SCHEMA

    @staticmethod
    def _join_prompt_parts(*parts: str) -> str:
        """Join non-empty prompt fragments with a blank line."""
        return "\n\n".join((part or "").strip() for part in parts if (part or "").strip())
