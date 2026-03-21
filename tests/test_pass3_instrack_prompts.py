import json

from agent.pass3_prompts import Pass3Prompts
from agent.prompts.pass3_3_instrack import (
    PASS3_3_2_ORCHESTRATE_PROMPT,
    PASS3_3_2_ORCHESTRATE_SYSTEM,
)
from agent.prompts.pass3_3_apv import (
    PASS3_3_3_APV_PROMPT,
    PASS3_3_3_APV_SYSTEM,
)
from agent.pass3_tools import Pass3Tools


class _DummyGenerator:
    pass


def test_orchestrate_prompt_requires_continuing_same_module_child_to_child_handoffs():
    prompt_text = PASS3_3_2_ORCHESTRATE_SYSTEM + "\n" + PASS3_3_2_ORCHESTRATE_PROMPT

    assert "`drawChild(module, task)`" in prompt_text
    assert "`forkSubAgent(module, task)`" not in prompt_text
    assert "Same-module child-to-child continuation is not a stop condition." in prompt_text
    assert "including via `next_children` or sibling-child handoffs" in prompt_text
    assert "continue into that child instead of stopping at the interconnect" in prompt_text
    assert "advisory_non_direct_child" in prompt_text
    assert "repeat the same child once with a non-empty `task` to force the override" in prompt_text
    assert "Every `drawChild` call must include a non-empty free-form `task`." in prompt_text
    assert "`Continuation State.override_hint`" in prompt_text
    assert "`boundary_takeover`" in prompt_text
    assert "authoritative continuation entry" in prompt_text
    assert "parent-port `boundary_takeover`" in prompt_text
    assert "Same-module facts must be verified from the current prompt context." in prompt_text
    assert "`gated_clk_cell`" in prompt_text
    assert "`lifecycle_context` must be one concise string" in prompt_text
    assert "Do not return structured objects or arrays under `lifecycle_context`" in prompt_text
    assert "`instruction_state`" not in prompt_text
    assert "`entry_ports`" not in prompt_text
    assert "`upstream_context`" not in prompt_text
    assert "`upstream_handoff`" not in prompt_text


def test_orchestrate_tools_expose_draw_child_only():
    tools = Pass3Tools(generator=None).pass3_3_2_tools()

    assert [tool["function"]["name"] for tool in tools] == ["drawChild"]
    assert "forkSubAgent" not in json.dumps(tools, ensure_ascii=False)
    assert tools[0]["function"]["parameters"]["required"] == ["module", "task"]


def test_orchestrate_state_json_includes_override_hint_when_present():
    prompts = Pass3Prompts(_DummyGenerator())

    raw = prompts.build_instrack_orchestrate_state_json(
        current_module="ct_ifu_ibdp",
        current_instance="x_ct_ifu_ibdp",
        boundary_takeover=[],
        lifecycle_context=[{"accessed_submodule": "x_prev(prev_mod)", "behavior_description": "came from relay"}],
        continuation_source={"source_instance": "x_ct_ifu_pcgen"},
        override_hint={
            "mode": "guided_relay_override",
            "source_child": {"instance": "x_ct_ifu_pcgen", "module": "ct_ifu_pcgen"},
            "requested_child": {"instance": "x_ct_ifu_ibdp", "module": "ct_ifu_ibdp"},
            "recommended_child": {"instance": "x_ct_ifu_ibctrl", "module": "ct_ifu_ibctrl"},
        },
    )

    payload = json.loads(raw)

    assert payload["module"] == "ct_ifu_ibdp"
    assert payload["instance"] == "x_ct_ifu_ibdp"
    assert payload["override_hint"]["mode"] == "guided_relay_override"
    assert payload["override_hint"]["recommended_child"]["instance"] == "x_ct_ifu_ibctrl"


def test_apv_prompt_is_single_shot_and_uses_dep_placeholders_only():
    prompt_text = PASS3_3_3_APV_SYSTEM + "\n" + PASS3_3_3_APV_PROMPT

    assert "tool-free" in prompt_text
    assert "Do not request tools, sub-agents" in prompt_text
    assert "AgenticPipeViewer (APV) consumes a linear sequence of small task fragments" in prompt_text
    assert "APV evaluates this task chain under one global clock" in prompt_text
    assert "A task is a local observation point in time" in prompt_text
    assert "Preserve instruction identity across the route whenever evidence allows" in prompt_text
    assert "`condition_lines`: boolean predicates" in prompt_text
    assert "`capture_signals`: the local signals to record from this same observation point" in prompt_text
    assert "all `condition_lines` in one task describe the same local observation point" in prompt_text
    assert "all `capture_signals` in that task are sampled from that same local observation point" in prompt_text
    assert "Never emit final task ids" in prompt_text
    assert "final `$dep.<task_id>.<signal>`" in prompt_text
    assert "`ref_name`" in prompt_text
    assert "$dep.<ref_name>.<signal>" in prompt_text
    assert "$dep.<leaf_name>" not in prompt_text
    assert "$dep_leaf.<leaf_name>" not in prompt_text
    assert "Do not emit separate `dep_source` or `dep_leaf_name` fields." in prompt_text
    assert "## Search Result JSON" not in prompt_text
    assert "## Current Module Preview" not in prompt_text
    assert "## Previous Item Summary" not in prompt_text
    assert "## Next Item Summary" not in prompt_text
    assert "## Current Module Context" in prompt_text
    assert "`Current Module Context.instruction_state`" in prompt_text
    assert "`lifecycle_context`, `boundary_takeover`, `boundary_handoffs`" in prompt_text
    assert "Visible Upstream Dep Handles" in prompt_text
    assert "`capture_names`" in prompt_text
    assert "upstream candidate `ref_name` values" in prompt_text
    assert "reference that earlier task's declared `ref_name`" in prompt_text
    assert "dep_source = \"prev_module_leaf\"" not in prompt_text
    assert "dep_source = \"prev_task_in_item\"" not in prompt_text
    assert '"dep_source"' not in prompt_text
    assert '"dep_leaf_name"' not in prompt_text
    assert '"dependsOn"' not in prompt_text
    assert "Do not search for new routes" in prompt_text
    assert "`match_mode` must be one of `first`, `all`, or `unique_per_var`" in prompt_text
    assert "Do not use aliases like `single` or `once`" in prompt_text
    assert "The task chain must be monotonic in time" in prompt_text
    assert "may match in the same cycle as that upstream handle or in a later cycle, but never in an earlier cycle" in prompt_text
    assert "Same-cycle combinational chaining is allowed" in prompt_text
    assert "If a single task can clearly express both the triggering relationship and the updated result, keeping them together is allowed" in prompt_text
    assert "Prefer conditions that prove instruction continuity" in prompt_text
    assert "Treat one task as one local observation point by default" in prompt_text
    assert "If the behavior really needs multiple distinct local steps to be understandable, split it into multiple tasks" in prompt_text
    assert "## APV Authoring Goal" in prompt_text
    assert "## Example Task Shape" in prompt_text
    assert "Interpret each task's `condition_lines` as one local observation point" in prompt_text
    assert '"ref_name": "ibctrl_accept"' in prompt_text
    assert '"task_name": "ibctrl_pc_match_and_bypass_activate"' in prompt_text
    assert '"$dep.pcgen_leaf.pcgen_ifctrl_pc == ib_vpc"' in prompt_text
    assert '"ref_name": "ibctrl_issue"' in prompt_text
    assert '"$dep.ibctrl_accept.bypass_inst_vld == issue_en"' in prompt_text
