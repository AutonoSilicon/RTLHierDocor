import json

from agent.pass3_instrack_stages import Pass3InStrackStages


class FakeName:
    def __init__(self, text):
        self._text = text

    def str(self):
        return self._text


class FakeWire:
    def __init__(self, name, *, port_input=False, port_output=False):
        self.name = FakeName(name)
        self.port_input = port_input
        self.port_output = port_output


class FakeModule:
    def __init__(self, wires):
        self._wires = list(wires)
        self.wires_ = list(range(len(self._wires)))

    def wire(self, wire_id):
        return self._wires[wire_id]


class FakeBackend:
    def __init__(self, modules):
        self._modules = dict(modules)

    def get_module(self, module_name):
        return self._modules.get(module_name)


class FakeResolver:
    def __init__(self, backend):
        self.backend = backend


class FakeTracker:
    def get_pass2_content(self, module_name):
        return ""

    def get_pass2_7_content(self, module_name):
        return ""

    def get_pass1_content(self, module_name):
        return ""


class FakeGraph:
    def __init__(self, io_ports):
        self.io_ports = dict(io_ports)


class FakeOwner:
    def __init__(self, backend, graphs=None):
        self.resolver = FakeResolver(backend)
        self._graphs = graphs or {}
        self.tracker = FakeTracker()


class FakeGenerator:
    def __init__(self, owner):
        self.owner = owner


class FakeNode:
    def __init__(self, module_name, instance_name, *, parent=None, port_connections=None):
        self.module_name = module_name
        self.instance_name = instance_name
        self.parent = parent
        self.children = {}
        self.port_connections = dict(port_connections or {})
        self.depth = parent.depth + 1 if parent is not None else 0
        if parent is not None:
            parent.children[instance_name] = self

    def get_path(self):
        if self.parent is None:
            return self.instance_name
        return f"{self.parent.get_path()}/{self.instance_name}"


def make_stages():
    backend = FakeBackend(
        {
            "parent_mod": FakeModule(
                [
                    FakeWire("\\top_out", port_output=True),
                    FakeWire("\\top_in", port_input=True),
                ]
            ),
            "src_mod": FakeModule(
                [
                    FakeWire("\\out1", port_output=True),
                    FakeWire("\\out2", port_output=True),
                    FakeWire("\\bad", port_input=True),
                    FakeWire("\\dangling", port_output=True),
                ]
            ),
            "dst_mod": FakeModule(
                [
                    FakeWire("\\in_a", port_input=True),
                    FakeWire("\\in_b", port_input=True),
                ]
            ),
        }
    )
    owner = FakeOwner(backend, graphs={"parent_mod": FakeGraph({"top_out": "\\top_out"})})
    return Pass3InStrackStages(FakeGenerator(owner))


def make_parent_tree():
    parent = FakeNode("parent_mod", "top")
    source = FakeNode(
        "src_mod",
        "x_src",
        parent=parent,
        port_connections={
            "out1": "\\shared_wire",
            "out2": "\\top_out",
            "bad": "\\shared_wire",
            "dangling": "\\dangling_wire",
        },
    )
    dst0 = FakeNode(
        "dst_mod",
        "x_dst0",
        parent=parent,
        port_connections={"in_a": "\\shared_wire"},
    )
    dst1 = FakeNode(
        "dst_mod",
        "x_dst1",
        parent=parent,
        port_connections={"in_b": "\\shared_wire"},
    )
    return parent, source, dst0, dst1


def test_extract_pass3_3_draw_payload_ignores_takeover_fields_and_parses_boundary_handoffs():
    stages = make_stages()
    current = FakeNode("ct_mod", "x_ct_mod")
    content = """
```mermaid
flowchart LR
```
```json
{
  "module": "ct_mod",
  "instance": "x_ct_mod",
  "boundary_takeover": [
    {
      "ingress_port": "ifu_in",
      "value_kind": "instruction_valid",
      "semantic": "slot valid from IFU",
      "taken_from": {
        "source_instance": "x_ifu",
        "source_module": "ct_ifu_top",
        "source_instance_path": "top/x_ifu",
        "source_port": "ifu_out",
        "source_handoff_id": "top/x_ifu::ifu_out",
        "parent_wire": "ifu_idu_vld"
      }
    }
  ],
  "entry_ports": [
    {
      "port": "ifu_in",
      "direction": "input",
      "value_kind": "instruction_valid",
      "semantic": "slot valid from IFU",
      "matched_from": {
        "source_instance": "x_ifu",
        "source_module": "ct_ifu_top",
        "source_port": "ifu_out",
        "wire": "ifu_idu_vld"
      }
    }
  ],
  "boundary_handoffs": [
    {
      "source_block": "PROC_11",
      "source_state": "ctrl_id_pipedown_inst1_vld",
      "egress_port": "id_out",
      "value_kind": "instruction_valid",
      "semantic": "decoded slot valid",
      "behavior": "assert on pipedown"
    }
  ],
  "lifecycle_context": "decode handoff",
  "confidence": "high",
  "unknown": ""
}
```
"""

    payload = stages.extract_pass3_3_draw_payload(content, current)

    assert payload["module"] == "ct_mod"
    assert payload["instance"] == "x_ct_mod"
    assert payload["lifecycle_context"] == "decode handoff"
    assert "instruction_state" not in payload
    assert payload["confidence"] == "high"
    assert payload["boundary_takeover"] == []
    assert "entry_ports" not in payload
    assert payload["boundary_handoffs"] == [
        {
            "source_block": "PROC_11",
            "source_state": "ctrl_id_pipedown_inst1_vld",
            "egress_port": "id_out",
            "value_kind": "instruction_valid",
            "semantic": "decoded slot valid",
            "behavior": "assert on pipedown",
            "resolutions": [],
            "status": "",
            "confidence": "high",
            "unknown": "",
        }
    ]


def test_resolve_boundary_handoff_destinations_enriches_resolution_status_and_identity():
    stages = make_stages()
    parent, source, _, _ = make_parent_tree()
    handoffs = [
        {"egress_port": "out1", "semantic": "to decode", "unknown": ""},
        {"egress_port": "out2", "semantic": "leave parent", "unknown": ""},
        {"egress_port": "bad", "semantic": "wrong direction", "unknown": ""},
        {"egress_port": "dangling", "semantic": "no consumer", "unknown": ""},
    ]

    enriched = stages._resolve_boundary_handoff_destinations(parent, source, handoffs)

    out1, out2, bad, dangling = enriched
    assert out1["handoff_id"] == "top/x_src::out1"
    assert out1["source_instance_path"] == "top/x_src"
    assert out1["source_module"] == "src_mod"
    assert out1["source_instance"] == "x_src"
    assert out1["parent_wire"] == "shared_wire"
    assert out1["status"] == "resolved"
    assert out1["resolutions"] == [
        {
            "resolution_kind": "sibling_child",
            "parent_wire": "shared_wire",
            "target_instance": "x_dst0",
            "target_module": "dst_mod",
            "target_port": "in_a",
            "match_policy": "exact_port",
        },
        {
            "resolution_kind": "sibling_child",
            "parent_wire": "shared_wire",
            "target_instance": "x_dst1",
            "target_module": "dst_mod",
            "target_port": "in_b",
            "match_policy": "exact_port",
        },
    ]

    assert out2["status"] == "exit_parent"
    assert out2["parent_wire"] == "top_out"
    assert out2["resolutions"] == [
        {
            "resolution_kind": "exit_parent",
            "parent_wire": "top_out",
            "parent_port": "top_out",
            "match_policy": "exact_port",
        }
    ]

    assert bad["status"] == "invalid"
    assert "is not an output port" in bad["unknown"]

    assert dangling["status"] == "unresolved"
    assert dangling["parent_wire"] == "dangling_wire"
    assert "no strict boundary match" in dangling["unknown"]


def test_build_boundary_takeover_only_keeps_exact_child_matches():
    stages = make_stages()
    parent, source, dst0, _ = make_parent_tree()
    handoffs = [
        {
            "egress_port": "out1",
            "parent_wire": "shared_wire",
            "semantic": "to decode",
            "value_kind": "instruction_valid",
            "source_instance": "x_src",
            "source_module": "src_mod",
            "resolutions": [
                {
                    "resolution_kind": "sibling_child",
                    "parent_wire": "shared_wire",
                    "target_instance": "x_dst0",
                    "target_module": "dst_mod",
                    "target_port": "in_a",
                },
                {
                    "resolution_kind": "sibling_child",
                    "parent_wire": "shared_wire",
                    "target_instance": "x_dst1",
                    "target_module": "dst_mod",
                    "target_port": "in_b",
                },
            ],
        },
        {
            "egress_port": "dangling",
            "status": "unresolved",
            "resolutions": [],
        },
    ]

    takeover = stages._build_boundary_takeover(
        parent_node=parent,
        source_child_node=source,
        target_child_node=dst0,
        handoffs=handoffs,
    )

    assert takeover == [
        {
            "ingress_port": "in_a",
            "value_kind": "instruction_valid",
            "semantic": "to decode",
            "taken_from": {
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_instance_path": "top/x_src",
                "source_port": "out1",
                "source_handoff_id": "top/x_src::out1",
                "parent_wire": "shared_wire",
            },
        }
    ]


def test_build_bridge_context_collects_candidate_children_and_parent_exits():
    stages = make_stages()
    parent, source, _, _ = make_parent_tree()
    raw_handoffs = [
        {"egress_port": "out1", "semantic": "to decode", "value_kind": "instruction_valid"},
        {"egress_port": "out2", "semantic": "leave parent", "value_kind": "instruction_bundle"},
        {"egress_port": "dangling", "semantic": "stuck", "value_kind": "instruction_valid"},
    ]
    resolved_handoffs = stages._resolve_boundary_handoff_destinations(parent, source, raw_handoffs)

    context = stages._build_bridge_context(
        parent_node=parent,
        source_child_node=source,
        handoffs=resolved_handoffs,
        source_payload={
            "lifecycle_context": "ifu bridge",
        },
    )

    assert context["source_instance"] == "x_src"
    assert context["source_module"] == "src_mod"
    assert context["source_instance_path"] == "top/x_src"
    assert context["parent_module"] == "parent_mod"
    assert context["candidate_children"] == [
        {"target_instance": "x_dst0", "target_module": "dst_mod"},
        {"target_instance": "x_dst1", "target_module": "dst_mod"},
    ]
    assert context["takeover_bundles"][0]["boundary_takeover"] == [
        {
            "ingress_port": "in_a",
            "value_kind": "instruction_valid",
            "semantic": "to decode",
            "taken_from": {
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_instance_path": "top/x_src",
                "source_port": "out1",
                "source_handoff_id": "top/x_src::out1",
                "parent_wire": "shared_wire",
            },
        }
    ]
    assert context["exits_parent"] == [
        {
            "parent_port": "top_out",
            "parent_wire": "top_out",
            "source_port": "out2",
        }
    ]
    assert context["unresolved"] == ["dangling"]
    assert context["resolved_handoffs"][0]["egress_port"] == "out1"
    assert context["resolved_handoffs"][0]["targets"] == [
        {
            "target_instance": "x_dst0",
            "target_module": "dst_mod",
            "target_port": "in_a",
            "parent_wire": "shared_wire",
        },
        {
            "target_instance": "x_dst1",
            "target_module": "dst_mod",
            "target_port": "in_b",
            "parent_wire": "shared_wire",
        },
    ]
    assert context["resolved_handoffs"][1]["egress_port"] == "out2"
    assert context["resolved_handoffs"][1]["status"] == "exit_parent"


def test_advance_active_continuation_from_child_promotes_child_outputs():
    stages = make_stages()
    parent, source, _, _ = make_parent_tree()
    mid = FakeNode(
        "src_mod",
        "x_mid",
        parent=parent,
        port_connections={
            "out1": "\\mid_wire",
            "out2": "\\top_out",
        },
    )
    target0 = FakeNode(
        "dst_mod",
        "x_mid_dst0",
        parent=parent,
        port_connections={"in_a": "\\mid_wire"},
    )
    target1 = FakeNode(
        "dst_mod",
        "x_mid_dst1",
        parent=parent,
        port_connections={"in_b": "\\mid_wire"},
    )

    initial_state = stages._build_active_continuation(
        parent_node=parent,
        source_child_node=source,
        handoffs=[{"egress_port": "out1", "semantic": "old source"}],
        source_payload={"lifecycle_context": "old"},
    )
    assert initial_state["bridge_context"]["source_instance"] == "x_src"
    assert initial_state["continuation_source"] == {
        "source_instance": "x_src",
        "source_module": "src_mod",
        "source_instance_path": "top/x_src",
    }
    assert initial_state["boundary_takeover"] == []

    child_handoffs = stages._resolve_boundary_handoff_destinations(
        parent,
        mid,
        [
            {"egress_port": "out1", "semantic": "to next child", "value_kind": "instruction_valid"},
            {"egress_port": "out2", "semantic": "leave parent", "value_kind": "instruction_bundle"},
        ],
    )
    next_state = stages._advance_active_continuation_from_child(
        parent_node=parent,
        child_node=mid,
        child_item={
            "orchestration": {
                "boundary_handoffs": child_handoffs,
                "lifecycle_context": "mid stage",
            }
        },
        inherited_bridge_context=initial_state["bridge_context"],
        inherited_lifecycle_context=initial_state["lifecycle_context"],
    )

    assert next_state["source_child_node"] is mid
    assert next_state["payload"]["lifecycle_context"] == "mid stage"
    assert next_state["lifecycle_context"] == [
        {
            "accessed_submodule": "x_src(src_mod)",
            "behavior_description": "old",
        },
        {
            "accessed_submodule": "x_mid(src_mod)",
            "behavior_description": "mid stage",
        },
    ]
    assert next_state["boundary_takeover"] == []
    assert next_state["bridge_context"]["source_instance"] == "x_mid"
    assert next_state["bridge_context"]["candidate_children"] == [
        {"target_instance": target0.instance_name, "target_module": target0.module_name},
        {"target_instance": target1.instance_name, "target_module": target1.module_name},
    ]
    assert next_state["bridge_context"]["exits_parent"] == [
        {
            "parent_port": "top_out",
            "parent_wire": "top_out",
            "source_port": "out2",
        }
    ]
    assert next_state["continuation_source"] == {
        "source_instance": "x_mid",
        "source_module": "src_mod",
        "source_instance_path": "top/x_mid",
    }


def test_enrich_draw_payload_injects_python_boundary_takeover():
    stages = make_stages()
    _, source, _, _ = make_parent_tree()
    payload = {
        "boundary_handoffs": [{"egress_port": "out1", "semantic": "to decode"}],
    }
    boundary_takeover = [
        {
            "ingress_port": "in_a",
            "value_kind": "instruction_valid",
            "semantic": "from parent context",
            "taken_from": {
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_instance_path": "top/x_src",
                "source_port": "out1",
                "source_handoff_id": "top/x_src::out1",
                "parent_wire": "shared_wire",
            },
        },
        {
            "ingress_port": "in_b",
            "value_kind": "instruction_valid",
            "semantic": "second entry",
            "taken_from": {
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_instance_path": "top/x_src",
                "source_port": "out1",
                "source_handoff_id": "top/x_src::out1",
                "parent_wire": "shared_wire",
            },
        },
    ]

    enriched = stages._enrich_draw_payload(source, payload, boundary_takeover)

    assert enriched["boundary_takeover"] == boundary_takeover
    assert enriched["boundary_handoffs"][0]["status"] == "resolved"


def test_enrich_draw_payload_marks_invalid_duplicate_and_unknown_boundary_handoffs():
    stages = make_stages()
    _, source, _, _ = make_parent_tree()
    payload = {
        "boundary_handoffs": [
            {"egress_port": "out1", "semantic": "primary handoff"},
            {"egress_port": "out1", "semantic": "duplicate handoff"},
            {"egress_port": "bad", "semantic": "input should be rejected"},
            {"egress_port": "ghost", "semantic": "unknown port should be rejected"},
        ],
    }

    enriched = stages._enrich_draw_payload(source, payload, boundary_takeover=None)

    assert [item["egress_port"] for item in enriched["boundary_handoffs"]] == ["out1", "out1", "bad", "ghost"]
    assert enriched["boundary_handoffs"][0]["status"] == "resolved"
    assert enriched["boundary_handoffs"][1]["status"] == "invalid"
    assert "duplicate boundary_handoff" in enriched["boundary_handoffs"][1]["unknown"]
    assert enriched["boundary_handoffs"][2]["status"] == "invalid"
    assert "is not an output port" in enriched["boundary_handoffs"][2]["unknown"]
    assert enriched["boundary_handoffs"][3]["status"] == "invalid"
    assert "is not a declared port" in enriched["boundary_handoffs"][3]["unknown"]


def test_enrich_draw_payload_validates_top_module_boundary_ports_without_parent_resolution():
    stages = make_stages()
    top = FakeNode("parent_mod", "top")
    payload = {
        "boundary_handoffs": [
            {"egress_port": "top_out", "semantic": "legal top output"},
            {"egress_port": "top_in", "semantic": "input should be rejected"},
            {"egress_port": "ghost", "semantic": "unknown top port"},
        ],
    }

    enriched = stages._enrich_draw_payload(top, payload, boundary_takeover=None)

    assert enriched["boundary_handoffs"][0]["egress_port"] == "top_out"
    assert enriched["boundary_handoffs"][0].get("status", "") != "invalid"
    assert enriched["boundary_handoffs"][1]["status"] == "invalid"
    assert "is not an output port" in enriched["boundary_handoffs"][1]["unknown"]
    assert enriched["boundary_handoffs"][2]["status"] == "invalid"
    assert "is not a declared port" in enriched["boundary_handoffs"][2]["unknown"]


def test_build_draw_child_tool_result_returns_structured_boundary_summary_only():
    stages = make_stages()
    child_item = {
        "module": "dst_mod",
        "instance": "x_dst0",
        "path": "top/x_dst0",
        "raw_draw_output": "```mermaid\nflowchart LR\nA-->B\n```",
        "orchestration": {
            "boundary_takeover": [{"ingress_port": "in_a"}],
            "boundary_handoffs": [{"egress_port": "out1"}],
            "lifecycle_context": "ctx",
            "confidence": "medium",
            "unknown": "",
        },
    }

    result = stages._build_draw_child_tool_result(child_item, "continue decode")

    assert result.startswith("```json")
    assert "flowchart LR" not in result
    payload = json.loads(result.removeprefix("```json\n").removesuffix("\n```"))
    assert payload == {
        "child": {
            "module": "dst_mod",
            "instance": "x_dst0",
            "path": "top/x_dst0",
        },
        "boundary_handoffs": [{"egress_port": "out1"}],
        "next_children": [],
        "confidence": "medium",
        "unknown": "",
        "cached": False,
        "task": "continue decode",
    }


def test_build_draw_child_tool_result_surfaces_next_children_and_compacts_large_fields():
    stages = make_stages()
    child_item = {
        "module": "dst_mod",
        "instance": "x_dst0",
        "path": "top/x_dst0",
        "orchestration": {
            "boundary_takeover": [
                {"ingress_port": "in0", "semantic": "slot0"},
                {"ingress_port": "in1", "semantic": "slot1"},
                {"ingress_port": "in2", "semantic": "slot2"},
                {"ingress_port": "in3", "semantic": "slot3"},
                {"ingress_port": "in4", "semantic": "slot4"},
            ],
            "boundary_handoffs": [
                {
                    "egress_port": "out1",
                    "semantic": "issue payload to RF stage",
                    "status": "resolved",
                    "behavior": " ".join(["payload"] * 60),
                    "resolutions": [
                        {
                            "resolution_kind": "sibling_child",
                            "target_instance": "x_rf_dp",
                            "target_module": "ct_idu_rf_dp",
                            "target_port": "in_issue",
                            "parent_wire": "issue_wire",
                        },
                        {
                            "resolution_kind": "sibling_child",
                            "target_instance": "x_rf_ctrl",
                            "target_module": "ct_idu_rf_ctrl",
                            "target_port": "in_issue_en",
                            "parent_wire": "issue_wire",
                        },
                        {
                            "resolution_kind": "exit_parent",
                            "parent_port": "top_out",
                            "parent_wire": "top_out",
                        },
                        {
                            "resolution_kind": "sibling_child",
                            "target_instance": "x_extra",
                            "target_module": "ct_extra",
                            "target_port": "in_extra",
                            "parent_wire": "extra_wire",
                        },
                    ],
                }
            ],
            "lifecycle_context": "issue queue selects ADD and hands the issued payload to RF-facing logic",
            "confidence": "high",
            "unknown": [
                {
                    "aspect": "rf detail",
                    "reason": "Need deeper RF expansion to see operand read ports and bypass picks",
                },
                {
                    "aspect": "iu pipe selection",
                    "reason": "Need RF child to confirm pipe0 versus pipe1 dispatch",
                },
                {
                    "aspect": "retire detail",
                    "reason": "Need RTU expansion later",
                },
                {
                    "aspect": "extra",
                    "reason": "should be truncated",
                },
            ],
        },
    }

    result = stages._build_draw_child_tool_result(child_item, "continue issue")
    payload = json.loads(result.removeprefix("```json\n").removesuffix("\n```"))

    assert "entry_ports" not in payload
    assert payload["next_children"] == [
        {"target_instance": "x_rf_dp", "target_module": "ct_idu_rf_dp"},
        {"target_instance": "x_rf_ctrl", "target_module": "ct_idu_rf_ctrl"},
        {"target_instance": "x_extra", "target_module": "ct_extra"},
    ]
    assert payload["boundary_handoffs"][0]["resolutions"] == [
        {
            "resolution_kind": "sibling_child",
            "target_instance": "x_rf_dp",
            "target_module": "ct_idu_rf_dp",
            "target_port": "in_issue",
            "parent_wire": "issue_wire",
        },
        {
            "resolution_kind": "sibling_child",
            "target_instance": "x_rf_ctrl",
            "target_module": "ct_idu_rf_ctrl",
            "target_port": "in_issue_en",
            "parent_wire": "issue_wire",
        },
        {
            "resolution_kind": "exit_parent",
            "parent_port": "top_out",
            "parent_wire": "top_out",
        },
    ]
    assert payload["boundary_handoffs"][0]["resolutions_truncated"] == 1
    assert "instruction_state" not in payload
    assert "lifecycle_context" not in payload
    assert payload["unknown"][-1] == "+1 more unknowns"


def test_build_active_continuation_keeps_bridge_context_internal_and_takeover_explicit():
    stages = make_stages()
    parent, source, dst0, _ = make_parent_tree()
    boundary_takeover = stages._build_boundary_takeover(
        parent_node=parent,
        source_child_node=source,
        target_child_node=dst0,
        handoffs=[
            {
                "egress_port": "out1",
                "parent_wire": "shared_wire",
                "resolutions": [
                    {
                        "resolution_kind": "sibling_child",
                        "target_instance": "x_dst0",
                        "target_module": "dst_mod",
                        "target_port": "in_a",
                    }
                ],
            }
        ],
    )
    state = stages._build_active_continuation(
        parent_node=parent,
        source_child_node=source,
        handoffs=[
            {
                "egress_port": "out1",
                "parent_wire": "shared_wire",
                "resolutions": [
                    {
                        "resolution_kind": "sibling_child",
                        "target_instance": "x_dst0",
                        "target_module": "dst_mod",
                        "target_port": "in_a",
                    }
                ],
            }
        ],
        source_payload={"lifecycle_context": "current child should not be appended yet"},
        lifecycle_context=[
            {
                "accessed_submodule": "x_prev(prev_mod)",
                "behavior_description": "previous sibling accepted the dispatch bundle",
            }
        ],
        bridge_context=None,
        boundary_takeover=boundary_takeover,
        continuation_source=None,
    )

    assert state["bridge_context"]["candidate_children"] == [
        {"target_instance": "x_dst0", "target_module": "dst_mod"},
    ]
    assert state["boundary_takeover"] == boundary_takeover
    assert state["continuation_source"] == {
        "source_instance": "x_src",
        "source_module": "src_mod",
        "source_instance_path": "top/x_src",
    }
    assert state["lifecycle_context"] == [
        {
            "accessed_submodule": "x_prev(prev_mod)",
            "behavior_description": "previous sibling accepted the dispatch bundle",
        },
        {
            "accessed_submodule": "x_src(src_mod)",
            "behavior_description": "current child should not be appended yet",
        },
    ]


def test_render_orchestration_item_mermaid_uses_boundary_takeover_entries():
    stages = make_stages()
    item = {
        "module": "dst_mod",
        "instance": "x_dst0",
        "orchestration": {
            "boundary_takeover": [
                {
                    "ingress_port": "in_a",
                    "semantic": "decoded slot valid",
                    "taken_from": {
                        "source_instance": "x_src",
                        "source_module": "src_mod",
                        "source_instance_path": "top/x_src",
                        "source_port": "out1",
                        "source_handoff_id": "top/x_src::out1",
                        "parent_wire": "shared_wire",
                    },
                }
            ],
            "boundary_handoffs": [{"egress_port": "out1", "semantic": "handoff to next stage"}],
            "lifecycle_context": "consume the decoded slot and prepare the next stage handoff",
        },
    }

    mermaid = stages.render_orchestration_item_mermaid(item)

    assert "ENTRY in_a | decoded slot valid" in mermaid
    assert "EXIT out1 | handoff to next stage" in mermaid
