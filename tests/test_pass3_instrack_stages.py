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


def test_extract_pass3_3_draw_payload_parses_entry_ports_and_boundary_handoffs():
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
  "instruction_state": "slot1 valid",
  "confidence": "high",
  "unknown": ""
}
```
"""

    payload = stages.extract_pass3_3_draw_payload(content, current)

    assert payload["module"] == "ct_mod"
    assert payload["instance"] == "x_ct_mod"
    assert payload["lifecycle_context"] == "decode handoff"
    assert payload["instruction_state"] == "slot1 valid"
    assert payload["confidence"] == "high"
    assert payload["entry_ports"] == [
        {
            "port": "ifu_in",
            "direction": "input",
            "value_kind": "instruction_valid",
            "semantic": "slot valid from IFU",
            "matched_from": {
                "source_instance": "x_ifu",
                "source_module": "ct_ifu_top",
                "source_port": "ifu_out",
                "parent_wire": "ifu_idu_vld",
            },
        }
    ]
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


def test_build_upstream_context_only_keeps_exact_child_entry_ports():
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

    context = stages._build_upstream_context(
        parent_node=parent,
        source_child_node=source,
        target_child_node=dst0,
        handoffs=handoffs,
        source_payload={
            "lifecycle_context": "parent bridge",
            "instruction_state": "decoded valid",
        },
    )

    assert context == {
        "source_instance": "x_src",
        "source_module": "src_mod",
        "entry_ports": [
            {
                "target_port": "in_a",
                "wire": "shared_wire",
                "source_port": "out1",
                "source_instance": "x_src",
                "source_module": "src_mod",
                "semantic": "to decode",
                "value_kind": "instruction_valid",
            }
        ],
        "exits_parent": [],
        "unresolved": ["dangling"],
        "lifecycle_context": "parent bridge",
        "instruction_state": "decoded valid",
    }


def test_enrich_draw_payload_merges_upstream_entry_port_matches():
    stages = make_stages()
    _, source, _, _ = make_parent_tree()
    payload = {
        "entry_ports": [
            {
                "port": "in_a",
                "direction": "input",
                "value_kind": "instruction_valid",
                "semantic": "existing semantics",
            }
        ],
        "boundary_handoffs": [{"egress_port": "out1", "semantic": "to decode"}],
    }
    upstream_context = {
        "entry_ports": [
            {
                "target_port": "in_a",
                "wire": "shared_wire",
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_port": "out1",
                "semantic": "from parent context",
                "value_kind": "instruction_valid",
            },
            {
                "target_port": "in_b",
                "wire": "shared_wire",
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_port": "out1",
                "semantic": "second entry",
                "value_kind": "instruction_valid",
            },
        ]
    }

    enriched = stages._enrich_draw_payload(source, payload, upstream_context)

    assert enriched["entry_ports"] == [
        {
            "port": "in_a",
            "direction": "input",
            "value_kind": "instruction_valid",
            "semantic": "existing semantics",
            "matched_from": {
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_port": "out1",
                "parent_wire": "shared_wire",
            },
        },
        {
            "port": "in_b",
            "direction": "input",
            "value_kind": "instruction_valid",
            "semantic": "second entry",
            "matched_from": {
                "source_instance": "x_src",
                "source_module": "src_mod",
                "source_port": "out1",
                "parent_wire": "shared_wire",
            },
        },
    ]
    assert enriched["boundary_handoffs"][0]["status"] == "resolved"


def test_enrich_draw_payload_marks_invalid_duplicate_and_unknown_boundary_handoffs():
    stages = make_stages()
    _, source, _, _ = make_parent_tree()
    payload = {
        "entry_ports": [],
        "boundary_handoffs": [
            {"egress_port": "out1", "semantic": "primary handoff"},
            {"egress_port": "out1", "semantic": "duplicate handoff"},
            {"egress_port": "bad", "semantic": "input should be rejected"},
            {"egress_port": "ghost", "semantic": "unknown port should be rejected"},
        ],
    }

    enriched = stages._enrich_draw_payload(source, payload, upstream_context=None)

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
        "entry_ports": [],
        "boundary_handoffs": [
            {"egress_port": "top_out", "semantic": "legal top output"},
            {"egress_port": "top_in", "semantic": "input should be rejected"},
            {"egress_port": "ghost", "semantic": "unknown top port"},
        ],
    }

    enriched = stages._enrich_draw_payload(top, payload, upstream_context=None)

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
        "draw_payload": {
            "entry_ports": [{"port": "in_a", "direction": "input"}],
            "boundary_handoffs": [{"egress_port": "out1"}],
            "instruction_state": "state",
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
        "entry_ports": [{"port": "in_a", "direction": "input"}],
        "boundary_handoffs": [{"egress_port": "out1"}],
        "instruction_state": "state",
        "lifecycle_context": "ctx",
        "confidence": "medium",
        "unknown": "",
        "task": "continue decode",
    }
