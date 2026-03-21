"""Pass3 hash builders for cache key generation.

This module encapsulates all input hash builders used for caching pass3 results.
"""

import json
from typing import Any, Dict, List

from .pass3_utils import hash_text
from .prompts import (
    PASS3_1_SYSTEM,
    PASS3_1_PROMPT,
    PASS3_2_SYSTEM,
    PASS3_2_PROMPT,
    PASS3_3_1_SEARCH_SYSTEM,
    PASS3_3_1_SEARCH_PROMPT,
    PASS3_3_2_ORCHESTRATE_SYSTEM,
    PASS3_3_2_ORCHESTRATE_PROMPT,
    PASS3_3_3_APV_SYSTEM,
    PASS3_3_3_APV_PROMPT,
)


class Pass3Hash:
    """Helper object encapsulating pass3 hash builders."""

    def __init__(self, generator: Any):
        self.g = generator

    # Pass 3.1 hash builders

    def build_pass3_1_input_hash(self, top_module: str, top_description: str) -> str:
        """Build cache key for pass3.1 subsystem partition."""
        payload = {
            "version": "pass3_1_cache_v2",
            "top_module": top_module,
            "system_prompt": PASS3_1_SYSTEM,
            "prompt_template": PASS3_1_PROMPT,
            "top_description_hash": hash_text(top_description),
            "tools_schema": self.g._tools.pass3_1_tools(),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    # Pass 3.2 hash builders

    def build_pass3_2_input_hash(
        self, top_module: str, top_description: str, subsystem_partition: str
    ) -> str:
        """Build cache key for pass3.2 core partition."""
        payload = {
            "version": "pass3_2_cache_v1",
            "top_module": top_module,
            "system_prompt": PASS3_2_SYSTEM,
            "prompt_template": PASS3_2_PROMPT,
            "top_description_hash": hash_text(top_description),
            "subsystem_partition_hash": hash_text(subsystem_partition),
            "tools_schema": self.g._tools.pass3_2_tools(),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    # Pass 3.3 hash builders

    def build_pass3_3_search_input_hash(
        self,
        top_module: str,
        instruction: str,
        top_preview: str,
        core_partition: str,
        instruction_datasheet: str,
    ) -> str:
        """Build cache key for pass3.3.1 search stage."""
        payload = {
            "version": "pass3_3_instrack_search_cache_v5",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_1_SEARCH_SYSTEM,
            "prompt_template": PASS3_3_1_SEARCH_PROMPT,
            "top_preview_hash": hash_text(top_preview),
            "core_partition_hash": hash_text(core_partition),
            "instruction_datasheet_hash": hash_text(instruction_datasheet),
            "tools_schema": self.g._tools.pass3_3_1_tools(),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def build_pass3_3_orchestrate_input_hash(
        self,
        top_module: str,
        instruction: str,
        instruction_datasheet: str,
        search_result_json_text: str,
    ) -> str:
        """Build cache key for pass3.3.2 orchestration stage."""
        payload = {
            "version": "pass3_3_instrack_orchestrate_cache_v2",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_2_ORCHESTRATE_SYSTEM,
            "prompt_template": PASS3_3_2_ORCHESTRATE_PROMPT,
            "instruction_datasheet_hash": hash_text(instruction_datasheet),
            "search_result_json_hash": hash_text(search_result_json_text),
            "tools_schema": self.g._tools.pass3_3_2_tools(),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def build_pass3_3_render_input_hash(
        self,
        top_module: str,
        instruction: str,
        orchestration_json_text: str,
    ) -> str:
        """Build cache key for pass3.3.3 render stage."""
        payload = {
            "version": "pass3_3_instrack_render_cache_v1",
            "top_module": top_module,
            "instruction": instruction,
            "orchestration_json_hash": hash_text(orchestration_json_text),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def build_pass3_3_apv_index_input_hash(
        self,
        top_module: str,
        instruction: str,
        start_module: str,
        orchestration_json_text: str,
    ) -> str:
        """Build cache key for pass3.3.3 APV instruction index."""
        payload = {
            "version": "pass3_3_instrack_apv_index_cache_v7",
            "top_module": top_module,
            "instruction": instruction,
            "start_module": start_module,
            "system_prompt": PASS3_3_3_APV_SYSTEM,
            "prompt_template": PASS3_3_3_APV_PROMPT,
            "orchestration_json_hash": hash_text(orchestration_json_text),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def build_pass3_3_apv_item_input_hash(
        self,
        top_module: str,
        instruction: str,
        item_json_text: str,
        previous_leaf_json_text: str,
        current_module_topology_text: str = "",
        local_signal_guidance_json_text: str = "",
    ) -> str:
        """Build cache key for one pass3.3.3 APV module item."""
        payload = {
            "version": "pass3_3_instrack_apv_item_cache_v8",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_3_APV_SYSTEM,
            "prompt_template": PASS3_3_3_APV_PROMPT,
            "item_json_hash": hash_text(item_json_text),
            "previous_leaf_json_hash": hash_text(previous_leaf_json_text),
            "current_module_topology_hash": hash_text(current_module_topology_text),
            "local_signal_guidance_hash": hash_text(local_signal_guidance_json_text),
        }
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    # Generic artifact hash

    def build_artifact_hash(
        self,
        version: str,
        top_module: str,
        instruction: str = "",
        **kwargs: Any,
    ) -> str:
        """Build a generic artifact hash with version and module info."""
        payload: Dict[str, Any] = {
            "version": version,
            "top_module": top_module,
        }
        if instruction:
            payload["instruction"] = instruction
        for key, value in kwargs.items():
            if isinstance(value, str):
                payload[f"{key}_hash"] = hash_text(value)
            else:
                payload[key] = value
        return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
