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
    assert "starting from this module item's `boundary_takeover` and ending at its `boundary_handoffs`" in prompt_text
    assert "trace one instruction through this item's local pipeline behavior" in prompt_text
    assert "form one coherent local event chain" in prompt_text
    assert "Fill in the key signal points along that route" in prompt_text
    assert "remove instruction-identity ambiguity rather than generic valid-bit snapshots" in prompt_text
    assert "branch, buffer, out-of-order, crossbar, arbitration, or FSM-controlled ambiguity point" in prompt_text
    assert "Make each task and capture serve downstream tracing" in prompt_text
    assert "captured signals should preserve the instruction evidence that later tasks may need through `$dep`" in prompt_text
    assert "Preserve single-instruction identity across the chain" in prompt_text
    assert "Keep the chain short, high-confidence, and temporally consistent" in prompt_text
    assert "How APV matches a task chain:" in prompt_text
    assert "instruction-relevant route from `boundary_takeover` ingress to `boundary_handoffs` egress" in prompt_text
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
    assert "task graph inside one module item is allowed to be tree-shaped, not only linear" in prompt_text
    assert "`match_mode` controls how many matches APV keeps" in prompt_text
    assert "`first`: keep the first later-or-same-cycle match" in prompt_text
    assert "`all`: keep all matches in the forward window" in prompt_text
    assert "`unique_per_var`: keep one match for each unique pattern-variable binding" in prompt_text
    assert "`max_match` limits how many matches one upstream row may produce" in prompt_text
    assert "must never mix multiple different dependency `ref_name` values" in prompt_text
    assert "exported `leaf_contexts`" in prompt_text
    assert "may depend only on an earlier declared local task by its `ref_name`" in prompt_text
    assert "Multiple later tasks may depend on the same earlier local `ref_name`" in prompt_text
    assert "Such sibling branch tasks must use different conditions" in prompt_text
    assert "The task chain must be monotonic in time" in prompt_text
    assert "Keep one task when one observation point is enough" in prompt_text
    assert "`branch_tags`" in prompt_text
    assert "flat string map" in prompt_text
    assert "every terminal task must declare non-empty `branch_tags`" in prompt_text
    assert "every task that directly depends on one upstream candidate must declare `branch_tags`" in prompt_text
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
    assert "pick exactly one candidate per task and copy its `branch_tags` exactly onto that task" in prompt_text
    assert "give each terminal leaf unique non-empty `branch_tags`" in prompt_text
    assert "reference that earlier task's declared `ref_name`" in prompt_text
    assert "dep_source = \"prev_module_leaf\"" not in prompt_text
    assert "dep_source = \"prev_task_in_item\"" not in prompt_text
    assert '"dep_source"' not in prompt_text
    assert '"dep_leaf_name"' not in prompt_text
    assert '"dependsOn"' not in prompt_text
    assert "`match_mode`: must be `first`, `all`, or `unique_per_var`" in prompt_text
    assert "Prefer conditions that prove instruction continuity" in prompt_text
    assert "## APV Authoring Goal" in prompt_text
    assert "Start from `boundary_takeover` as the authoritative ingress of this item and end at this item's instruction-relevant `boundary_handoffs`." in prompt_text
    assert "Prefer event points that remove instruction-identity ambiguity over generic valid-only snapshots." in prompt_text
    assert "The local event chain is not necessarily a single linear chain." in prompt_text
    assert "each sibling task must use conditions that explicitly distinguish that path" in prompt_text
    assert "`complete` means all resolved instruction-relevant terminal paths in this item's current context are represented and transferable downstream" in prompt_text
    assert "Multi-leaf v2 cross-item propagation can export multiple downstream leaves." in prompt_text
    assert "must keep branch identity exact by matching that candidate's `branch_tags`" in prompt_text
    assert "If you cannot make branch identity explicit for every exported terminal leaf, return `partial`." in prompt_text
    assert "## Example Task Shape" in prompt_text
    assert "## Example Walkthrough" in prompt_text
    assert "## Branching Walkthrough" in prompt_text
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
    assert "both example tasks carry the same `branch_tags`" in prompt_text
    assert "The local event chain is not necessarily a single linear chain. If the current module can route the same instruction through multiple local channels or selector-controlled paths" in prompt_text
    assert "a parent `dispatch_entry` task may branch into `to_buf0` and `to_buf1`" in prompt_text
    assert "Both depend on `dispatch_entry`, but one uses `buf_sel == 2'b00` and the other uses `buf_sel == 2'b01`." in prompt_text
    assert "If both sibling branches remain terminal exits of the current item, keep both leaves and give each one unique non-empty `branch_tags`" in prompt_text
    assert '"ref_name": "ibctrl_accept"' in prompt_text
    assert '"task_name": "ibctrl_pc_match_and_bypass_activate"' in prompt_text
    assert '"$dep.pcgen_leaf.pcgen_ifctrl_pc == ib_vpc"' in prompt_text
    assert '"branch_tags": {' in prompt_text
    assert '"ref_name": "ibctrl_issue"' in prompt_text
    assert '"$dep.ibctrl_accept.bypass_inst_vld == issue_en"' in prompt_text


def test_apv_prompt_plan2_guardrails_are_present():
    prompt_text = PASS3_3_3_APV_SYSTEM + "\n" + PASS3_3_3_APV_PROMPT

    assert "## Pre-output Self-check" in prompt_text
    assert "Before final JSON, verify each of the following." in prompt_text
    assert "If the current item exports multiple terminal leaves, every terminal task has unique non-empty `branch_tags`." in prompt_text
    assert "Each task depends on at most one `ref_name`." in prompt_text
    assert "Every `$dep.<ref>.<signal>` comes from a visible upstream candidate or an earlier declared local task." in prompt_text
    assert "Every referenced dependency signal appears in that dependency's `capture_names` or declared `capture_signals`." in prompt_text
    assert "If you output `complete`, at least one same-line continuity condition directly ties an upstream anchor to a local anchor." in prompt_text
    assert "If any step lacks enough evidence, delete that speculative task and return `partial` instead of keeping a guessed chain." in prompt_text
    assert "A local task must be declared before any later task may reference it." in prompt_text
    assert "Do not reference an undeclared local `ref_name`." in prompt_text
    assert "branch_tags` must be authored in raw JSON task objects, later persisted in `apv_index` `leaf_contexts`, and must not be materialized into runtime YAML." in prompt_text
    assert "complete` means every resolved terminal path in the current item is covered by an exported terminal task that can continue downstream" in prompt_text
    assert "## Multi-leaf Completeness Examples" in prompt_text
    assert "A `complete` answer must cover all resolved terminal paths, not just the visually strongest path." in prompt_text
    assert "Bad `pcgen` pattern: if any exported terminal leaf is missing `branch_tags`, the item must return `partial`" in prompt_text
    assert "Prefer `channel` first in `branch_tags`; add `slot`, `pipe`, or `buffer` only when needed" in prompt_text


def test_apv_prompt_plan2_dependency_and_anchor_examples_are_present():
    prompt_text = PASS3_3_3_APV_SYSTEM + "\n" + PASS3_3_3_APV_PROMPT

    assert "## Local Dependency Discipline" in prompt_text
    assert "Good `ibctrl` pattern: declare `ibctrl_ingress` first" in prompt_text
    assert "Bad `ibctrl` pattern: if `bypass_leaf` references `$dep.ibctrl_ingress.accept_vld` before `ibctrl_ingress` is declared" in prompt_text
    assert "Do not mix multiple dependency sources in one task." in prompt_text
    assert "If the visible upstream candidate you need is missing `branch_tags`, do not force a `complete` tree; return `partial`." in prompt_text
    assert "## Continuity Anchor Guidance" in prompt_text
    assert "Continuity anchor priority is `pc`, then `inst`, then packed data bus or bit slice, then `valid` or `vld`." in prompt_text
    assert 'Good: `$dep.prev.inst0 == ifu_idu_ib_inst0_data[31:0]`' in prompt_text
    assert 'Good: `$dep.prev.inst0_pc == ibuf_ibdp_inst0_pc`' in prompt_text
    assert 'Weak-only example: `$dep.prev.inst0_vld == ifu_idu_ib_inst0_vld`' in prompt_text
    assert "If local `pc`, `inst`, or data anchors are visible, a valid-only relation cannot by itself support `complete`." in prompt_text
    assert "For `ct_ifu_top` or `ct_idu_top` style relay items, a `complete` answer must include at least one same-line upstream-to-local identity relation." in prompt_text
