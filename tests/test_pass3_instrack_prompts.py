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

    assert "Define the smallest set of local observation and sampling points" in prompt_text
    assert "trace one instruction through this item's local pipeline behavior" in prompt_text
    assert "form one coherent local event chain" in prompt_text
    assert "Make each task and capture serve downstream tracing" in prompt_text
    assert "captured signals should preserve the instruction evidence that later tasks may need through `$dep`" in prompt_text
    assert "Preserve single-instruction identity across the chain" in prompt_text
    assert "Keep the chain short, high-confidence, and temporally consistent" in prompt_text
    assert "How APV matches a task chain:" in prompt_text
    assert "under one global clock supplied by the outer wrapper" in prompt_text
    assert "A task is a match rule for one local observation point" in prompt_text
    assert "condition_lines` are evaluated together at one candidate time point" in prompt_text
    assert "capture_signals` are not extra hints" in prompt_text
    assert "sampled at that matched time point and persisted" in prompt_text
    assert "use the signal value captured by the dependency task" in prompt_text
    assert "task without any dependency is a trigger-style task" in prompt_text
    assert "task with a dependency is a trace-style task" in prompt_text
    assert "starts from that dependency row's matched time point and searches forward in time" in prompt_text
    assert "local non-`$dep` signals are evaluated at the current candidate time point" in prompt_text
    assert "`match_mode` controls how many matches APV keeps" in prompt_text
    assert "`first`: keep the first later-or-same-cycle match" in prompt_text
    assert "`all`: keep all matches in the forward window" in prompt_text
    assert "`unique_per_var`: keep one match for each unique pattern-variable binding" in prompt_text
    assert "`max_match` limits how many matches one upstream row may produce" in prompt_text
    assert "must never mix multiple different dependency `ref_name` values" in prompt_text
    assert "may depend only on an earlier declared local task by its `ref_name`" in prompt_text
    assert "The task chain must be monotonic in time" in prompt_text
    assert "Keep one task when one observation point is enough" in prompt_text
    assert "final `$dep.<task_id>.<signal>`" in prompt_text
    assert "`ref_name`" in prompt_text
    assert "$dep.<ref_name>.<signal>" in prompt_text
    assert "$dep.<leaf_name>" not in prompt_text
    assert "$dep_leaf.<leaf_name>" not in prompt_text
    assert "## Search Result JSON" not in prompt_text
    assert "## Current Module Preview" not in prompt_text
    assert "## Previous Item Summary" not in prompt_text
    assert "## Next Item Summary" not in prompt_text
    assert "## Current Module Context" in prompt_text
    assert "`Current Module Context.instruction_state`" in prompt_text
    assert "`lifecycle_context`, `boundary_takeover`, `boundary_handoffs`" in prompt_text
    assert "Visible Upstream Dep Handles" in prompt_text
    assert "`capture_names`" in prompt_text
    assert "provided upstream `ref_name`" in prompt_text
    assert "reference that earlier task's declared `ref_name`" in prompt_text
    assert "dep_source = \"prev_module_leaf\"" not in prompt_text
    assert "dep_source = \"prev_task_in_item\"" not in prompt_text
    assert '"dep_source"' not in prompt_text
    assert '"dep_leaf_name"' not in prompt_text
    assert '"dependsOn"' not in prompt_text
    assert "`match_mode`: must be `first`, `all`, or `unique_per_var`" in prompt_text
    assert "Prefer conditions that prove instruction continuity" in prompt_text
    assert "## APV Authoring Goal" in prompt_text
    assert "## Example Task Shape" in prompt_text
    assert "## Example Walkthrough" in prompt_text
    assert "Interpret each task's `condition_lines` as one local observation point" in prompt_text
    assert "`ibctrl_accept` is a dependent trace-style task" in prompt_text
    assert "searches later-or-same-cycle candidate time points" in prompt_text
    assert "captured and persisted when `pcgen_leaf` matched" in prompt_text
    assert "additional predicates that must be true at that same candidate time point" in prompt_text
    assert "Those captured values become the only values later tasks may read through `$dep.ibctrl_accept.<signal>`" in prompt_text
    assert "For each matched row of `ibctrl_accept`, APV searches forward" in prompt_text
    assert "compare historical captured values from `ibctrl_accept` against local signals at the current `ibctrl_issue` candidate time point" in prompt_text
    assert 'Both example tasks use `match_mode = "first"`' in prompt_text
    assert 'The example is split into two tasks because "accept into ibctrl" and "drive local issue enable"' in prompt_text
    assert "it must not mix multiple different dependency sources in the same task" in prompt_text
    assert '"ref_name": "ibctrl_accept"' in prompt_text
    assert '"task_name": "ibctrl_pc_match_and_bypass_activate"' in prompt_text
    assert '"$dep.pcgen_leaf.pcgen_ifctrl_pc == ib_vpc"' in prompt_text
    assert '"ref_name": "ibctrl_issue"' in prompt_text
    assert '"$dep.ibctrl_accept.bypass_inst_vld == issue_en"' in prompt_text
