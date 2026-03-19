import json

from agent.prompts.pass3_3_instrack import (
    PASS3_3_2_ORCHESTRATE_PROMPT,
    PASS3_3_2_ORCHESTRATE_SYSTEM,
)
from agent.pass3_tools import Pass3Tools


def test_orchestrate_prompt_requires_continuing_same_module_child_to_child_handoffs():
    prompt_text = PASS3_3_2_ORCHESTRATE_SYSTEM + "\n" + PASS3_3_2_ORCHESTRATE_PROMPT

    assert "`drawChild(module, task)`" in prompt_text
    assert "`forkSubAgent(module, task)`" not in prompt_text
    assert "Same-module child-to-child continuation is not a stop condition." in prompt_text
    assert "including via `next_children` or sibling-child handoffs" in prompt_text
    assert "continue into that child instead of stopping at the interconnect" in prompt_text
    assert "`boundary_takeover`" in prompt_text
    assert "authoritative continuation entry" in prompt_text
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
