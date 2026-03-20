from agent.pass3_instrack_takeover import Pass3InStrackTakeoverResolver


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
    def __init__(self, modules, dot_map=None):
        self._modules = dict(modules)
        self._dot_map = dict(dot_map or {})

    def get_module(self, module_name):
        return self._modules.get(module_name)

    def generate_dot(self, module_name):
        return self._dot_map.get(module_name)


class FakeResolver:
    def __init__(self, backend):
        self.backend = backend


class FakeOwner:
    def __init__(self, backend):
        self.resolver = FakeResolver(backend)


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
        if parent is not None:
            parent.children[instance_name] = self

    def get_path(self):
        if self.parent is None:
            return self.instance_name
        return f"{self.parent.get_path()}/{self.instance_name}"


def make_modules():
    return {
        "parent_mod": FakeModule(
            [
                FakeWire("\\top_out", port_output=True),
                FakeWire("\\top_in", port_input=True),
            ]
        ),
        "parent_seq_mod": FakeModule(
            [
                FakeWire("\\top_clk", port_input=True),
            ]
        ),
        "src_mod": FakeModule(
            [
                FakeWire("\\out1", port_output=True),
                FakeWire("\\out2", port_output=True),
                FakeWire("\\dangling", port_output=True),
                FakeWire("\\bad", port_input=True),
            ]
        ),
        "dst_mod": FakeModule(
            [
                FakeWire("\\in_a", port_input=True),
                FakeWire("\\in_b", port_input=True),
                FakeWire("\\out", port_output=True),
            ]
        ),
        "mid_mod": FakeModule(
            [
                FakeWire("\\in_a", port_input=True),
                FakeWire("\\out", port_output=True),
            ]
        ),
    }


def make_generator(dot_map=None):
    backend = FakeBackend(make_modules(), dot_map=dot_map)
    return FakeGenerator(FakeOwner(backend))


def make_comb_dot():
    return r'''
digraph "parent_mod" {
label="parent_mod";
rankdir="LR";
n_out [ shape=octagon, label="top_out", color="black", fontcolor="black"];
n_dang [ shape=diamond, label="dang_buf", color="black", fontcolor="black"];
c_src [ shape=record, label="{{<p1> bad}|x_src\nsrc_mod|{<p2> out1|<p3> out2|<p4> dangling}}",  ];
c_dst [ shape=record, label="{{<p5> in_a}|x_dst0\ndst_mod|{<p6> out}}",  ];
c_buf [ shape=record, label="{{<p10> A}|$1\n$buf|{<p11> Y}}",  ];
c_src:p2:e -> c_buf:p10:w [color="black", fontcolor="black", label=""];
c_buf:p11:e -> c_dst:p5:w [color="black", fontcolor="black", label=""];
c_src:p3:e -> n_out:w [color="black", fontcolor="black", label=""];
c_src:p4:e -> n_dang:w [color="black", fontcolor="black", label=""];
}
'''.strip()


def make_seq_multiport_dot():
    return r'''
digraph "parent_seq_mod" {
label="parent_seq_mod";
rankdir="LR";
n_clk [ shape=octagon, label="top_clk", color="black", fontcolor="black"];
c_src [ shape=record, label="{{<p1> bad}|x_src\nsrc_mod|{<p2> out1}}",  ];
c_dst [ shape=record, label="{{<p5> in_a|<p6> in_b}|x_dst\ndst_mod|{<p7> out}}",  ];
c_seq [ shape=record, label="{{<p10> CLK|<p11> D}|$2\n$dff|{<p12> Q}}",  ];
x0 [shape=point, ];
n_clk:e -> c_seq:p10:w [color="black", fontcolor="black", label=""];
c_src:p2:e -> c_seq:p11:w [color="black", fontcolor="black", label=""];
c_seq:p12:e -> x0:w [color="black", fontcolor="black", label=""];
x0:e -> c_dst:p5:w [color="black", fontcolor="black", label=""];
x0:e -> c_dst:p6:w [color="black", fontcolor="black", label=""];
}
'''.strip()


def make_sibling_barrier_dot():
    return r'''
digraph "parent_mod" {
label="parent_mod";
rankdir="LR";
c_src [ shape=record, label="{{<p1> bad}|x_src\nsrc_mod|{<p2> out1}}",  ];
x0 [shape=point, ];
c_mid [ shape=record, label="{{<p5> in_a}|x_mid\nmid_mod|{<p6> out}}",  ];
c_dst [ shape=record, label="{{<p7> in_a}|x_target\ndst_mod|{<p8> out}}",  ];
c_src:p2:e -> x0:w [color="black", fontcolor="black", label=""];
x0:e -> c_mid:p5:w [color="black", fontcolor="black", label=""];
c_mid:p6:e -> c_dst:p7:w [color="black", fontcolor="black", label=""];
}
'''.strip()


def make_seed_unresolved_dot():
    return r'''
digraph "parent_mod" {
label="parent_mod";
rankdir="LR";
c_src [ shape=record, label="{{<p1> bad}|x_src\nsrc_mod|{<p2> out1|<p3> out2}}",  ];
n_out [ shape=octagon, label="top_out", color="black", fontcolor="black"];
c_src:p3:e -> n_out:w [color="black", fontcolor="black", label=""];
}
'''.strip()


def make_multi_path_dot():
    return r'''
digraph "parent_mod" {
label="parent_mod";
rankdir="LR";
c_src [ shape=record, label="{{<p1> bad}|x_src\nsrc_mod|{<p2> out1}}",  ];
x0 [shape=point, ];
c_fast [ shape=record, label="{{<p5> in_a}|x_fast\nmid_mod|{<p6> out}}",  ];
c_slow1 [ shape=record, label="{{<p7> in_a}|x_slow1\nmid_mod|{<p8> out}}",  ];
c_slow2 [ shape=record, label="{{<p9> in_a}|x_slow2\nmid_mod|{<p10> out}}",  ];
c_dst [ shape=record, label="{{<p11> in_a|<p12> in_b}|x_target\ndst_mod|{<p13> out}}",  ];
c_src:p2:e -> x0:w [color="black", fontcolor="black", label=""];
x0:e -> c_fast:p5:w [color="black", fontcolor="black", label=""];
x0:e -> c_slow1:p7:w [color="black", fontcolor="black", label=""];
c_fast:p6:e -> c_dst:p11:w [color="black", fontcolor="black", label=""];
c_slow1:p8:e -> c_slow2:p9:w [color="black", fontcolor="black", label=""];
c_slow2:p10:e -> c_dst:p12:w [color="black", fontcolor="black", label=""];
}
'''.strip()


def test_takeover_resolver_reaches_target_through_comb_and_reports_debug():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_comb_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode(
        "src_mod",
        "x_src",
        parent=parent,
        port_connections={"out1": "\\shared_wire", "out2": "\\top_out", "dangling": "\\dangling_wire"},
    )
    target = FakeNode("dst_mod", "x_dst0", parent=parent, port_connections={"in_a": "\\shared_wire"})

    result = resolver.resolve(
        parent_node=parent,
        source_child_node=source,
        boundary_handoffs=[{"output_port": "out1", "behavior": "to decode"}],
        target_child_node=target,
    )

    assert result.resolved_handoffs[0]["status"] == "resolved"
    assert result.resolved_handoffs[0]["resolutions"] == [
        {
            "resolution_kind": "sibling_child",
            "parent_wire": "shared_wire",
            "target_instance": "x_dst0",
            "target_module": "dst_mod",
            "target_port": "in_a",
            "match_policy": "graph_forward",
        }
    ]
    assert result.boundary_takeover_for_target == [
        {
            "input_port": "in_a",
            "value_condition": "to decode",
            "behavior": "to decode",
        }
    ]
    assert result.debug_report["graph_status"] == "ready"
    assert result.debug_report["handoffs"][0]["seed"]["port_name"] == "out1"
    assert result.debug_report["handoffs"][0]["visited_count"] >= 2
    assert result.debug_report["handoffs"][0]["target_hits"] == [
        {
            "target_instance": "x_dst0",
            "target_module": "dst_mod",
            "target_port": "in_a",
            "node_id": "c_dst",
        }
    ]


def test_takeover_resolver_crosses_seq_and_collects_all_target_ports():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_seq_mod": make_seq_multiport_dot()}))
    parent = FakeNode("parent_seq_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"out1": "\\stage_wire"})
    target = FakeNode(
        "dst_mod",
        "x_dst",
        parent=parent,
        port_connections={"in_a": "\\stage_wire_a", "in_b": "\\stage_wire_b"},
    )

    result = resolver.resolve(
        parent_node=parent,
        source_child_node=source,
        boundary_handoffs=[{"output_port": "out1", "behavior": "through seq"}],
        target_child_node=target,
    )

    assert result.resolved_handoffs[0]["status"] == "resolved"
    assert result.boundary_takeover_for_target == [
        {
            "input_port": "in_a",
            "value_condition": "through seq",
            "behavior": "through seq",
        },
        {
            "input_port": "in_b",
            "value_condition": "through seq",
            "behavior": "through seq",
        },
    ]
    assert result.debug_report["handoffs"][0]["target_hits"] == [
        {
            "target_instance": "x_dst",
            "target_module": "dst_mod",
            "target_port": "in_a",
            "node_id": "c_dst",
        },
        {
            "target_instance": "x_dst",
            "target_module": "dst_mod",
            "target_port": "in_b",
            "node_id": "c_dst",
        },
    ]


def test_takeover_resolver_stops_at_non_target_sibling_without_traversing_through_it():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_sibling_barrier_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"out1": "\\shared_wire"})
    FakeNode("mid_mod", "x_mid", parent=parent, port_connections={"in_a": "\\shared_wire", "out": "\\after_mid"})
    target = FakeNode("dst_mod", "x_target", parent=parent, port_connections={"in_a": "\\after_mid"})

    result = resolver.resolve(
        parent_node=parent,
        source_child_node=source,
        boundary_handoffs=[{"output_port": "out1", "behavior": "toward target"}],
        target_child_node=target,
    )

    assert result.resolved_handoffs[0]["status"] == "resolved"
    assert result.boundary_takeover_for_target == []
    assert result.resolved_handoffs[0]["resolutions"] == [
        {
            "resolution_kind": "sibling_child",
            "parent_wire": "shared_wire",
            "target_instance": "x_mid",
            "target_module": "mid_mod",
            "target_port": "in_a",
            "match_policy": "graph_forward",
        }
    ]
    assert result.debug_report["handoffs"][0]["target_hits"] == []
    assert result.debug_report["handoffs"][0]["sibling_hits"] == [
        {
            "target_instance": "x_mid",
            "target_module": "mid_mod",
            "target_port": "in_a",
            "node_id": "c_mid",
        }
    ]


def test_takeover_resolver_reports_exit_parent_and_graph_unavailable_cases():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_comb_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"out2": "\\top_out"})
    target = FakeNode("dst_mod", "x_dst0", parent=parent, port_connections={"in_a": "\\shared_wire"})

    exit_result = resolver.resolve(
        parent_node=parent,
        source_child_node=source,
        boundary_handoffs=[{"output_port": "out2", "behavior": "leave parent"}],
        target_child_node=target,
    )

    assert exit_result.resolved_handoffs[0]["status"] == "exit_parent"
    assert exit_result.exits_parent == [
        {
            "parent_port": "top_out",
            "parent_wire": "top_out",
            "source_port": "out2",
        }
    ]
    assert exit_result.boundary_takeover_for_target == []

    unavailable_resolver = Pass3InStrackTakeoverResolver(make_generator({}))
    unavailable_result = unavailable_resolver.resolve(
        parent_node=parent,
        source_child_node=source,
        boundary_handoffs=[{"output_port": "out2", "behavior": "leave parent"}],
        target_child_node=target,
    )

    assert unavailable_result.resolved_handoffs[0]["status"] == "unresolved"
    assert "graph_unavailable" in unavailable_result.resolved_handoffs[0]["unknown"]
    assert unavailable_result.debug_report["graph_status"] == "graph_unavailable"


def test_takeover_resolver_reports_seed_unresolved_when_dot_omits_source_port():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_seed_unresolved_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"dangling": "\\dangling_wire"})
    target = FakeNode("dst_mod", "x_dst0", parent=parent, port_connections={"in_a": "\\shared_wire"})

    result = resolver.resolve(
        parent_node=parent,
        source_child_node=source,
        boundary_handoffs=[{"output_port": "dangling", "behavior": "missing in dot"}],
        target_child_node=target,
    )

    assert result.resolved_handoffs[0]["status"] == "unresolved"
    assert "seed_unresolved" in result.resolved_handoffs[0]["unknown"]
    assert result.debug_report["handoffs"][0]["stop_reasons"] == ["seed_unresolved"]


def test_non_direct_child_advisory_returns_shortest_relay_path():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_sibling_barrier_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"out1": "\\shared_wire"})
    mid = FakeNode("mid_mod", "x_mid", parent=parent, port_connections={"in_a": "\\shared_wire", "out": "\\after_mid"})
    target = FakeNode("dst_mod", "x_target", parent=parent, port_connections={"in_a": "\\after_mid"})

    advisory = resolver.resolve_non_direct_child_advisory(
        parent_node=parent,
        source_child_node=source,
        requested_child_node=target,
    )

    assert advisory.graph_status == "ready"
    assert advisory.recommended_child == {
        "instance": "x_mid",
        "module": "mid_mod",
        "path": "top/x_mid",
    }
    assert advisory.advisory_path == [
        {"instance": "x_mid", "module": "mid_mod", "path": "top/x_mid"},
        {"instance": "x_target", "module": "dst_mod", "path": "top/x_target"},
    ]
    assert advisory.skipped_children == [
        {"instance": "x_mid", "module": "mid_mod", "path": "top/x_mid"},
    ]
    assert advisory.reachable_children == [
        {"instance": "x_mid", "module": "mid_mod", "path": "top/x_mid"},
    ]


def test_non_direct_child_advisory_falls_back_to_first_reachable_child_when_target_unreachable():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_comb_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode(
        "src_mod",
        "x_src",
        parent=parent,
        port_connections={"out1": "\\shared_wire", "out2": "\\top_out", "dangling": "\\dangling_wire"},
    )
    FakeNode("dst_mod", "x_dst0", parent=parent, port_connections={"in_a": "\\shared_wire"})
    requested = FakeNode("dst_mod", "x_unreachable", parent=parent, port_connections={"in_a": "\\other_wire"})

    advisory = resolver.resolve_non_direct_child_advisory(
        parent_node=parent,
        source_child_node=source,
        requested_child_node=requested,
    )

    assert advisory.graph_status == "ready"
    assert advisory.advisory_path == []
    assert advisory.recommended_child == {
        "instance": "x_dst0",
        "module": "dst_mod",
        "path": "top/x_dst0",
    }
    assert advisory.reachable_children == [
        {"instance": "x_dst0", "module": "dst_mod", "path": "top/x_dst0"},
    ]


def test_non_direct_child_advisory_can_recommend_requested_child_directly():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_comb_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode(
        "src_mod",
        "x_src",
        parent=parent,
        port_connections={"out1": "\\shared_wire", "out2": "\\top_out", "dangling": "\\dangling_wire"},
    )
    target = FakeNode("dst_mod", "x_dst0", parent=parent, port_connections={"in_a": "\\shared_wire"})

    advisory = resolver.resolve_non_direct_child_advisory(
        parent_node=parent,
        source_child_node=source,
        requested_child_node=target,
    )

    assert advisory.graph_status == "ready"
    assert advisory.recommended_child == {
        "instance": "x_dst0",
        "module": "dst_mod",
        "path": "top/x_dst0",
    }
    assert advisory.advisory_path == [
        {"instance": "x_dst0", "module": "dst_mod", "path": "top/x_dst0"},
    ]
    assert advisory.skipped_children == []


def test_non_direct_child_advisory_prefers_shortest_relay_path():
    resolver = Pass3InStrackTakeoverResolver(make_generator({"parent_mod": make_multi_path_dot()}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"out1": "\\shared_wire"})
    FakeNode("mid_mod", "x_fast", parent=parent, port_connections={"in_a": "\\fast_in", "out": "\\fast_out"})
    FakeNode("mid_mod", "x_slow1", parent=parent, port_connections={"in_a": "\\slow1_in", "out": "\\slow1_out"})
    FakeNode("mid_mod", "x_slow2", parent=parent, port_connections={"in_a": "\\slow2_in", "out": "\\slow2_out"})
    target = FakeNode("dst_mod", "x_target", parent=parent, port_connections={"in_a": "\\dst_a", "in_b": "\\dst_b"})

    advisory = resolver.resolve_non_direct_child_advisory(
        parent_node=parent,
        source_child_node=source,
        requested_child_node=target,
    )

    assert advisory.recommended_child == {
        "instance": "x_fast",
        "module": "mid_mod",
        "path": "top/x_fast",
    }
    assert advisory.advisory_path == [
        {"instance": "x_fast", "module": "mid_mod", "path": "top/x_fast"},
        {"instance": "x_target", "module": "dst_mod", "path": "top/x_target"},
    ]


def test_non_direct_child_advisory_reports_graph_unavailable():
    resolver = Pass3InStrackTakeoverResolver(make_generator({}))
    parent = FakeNode("parent_mod", "top")
    source = FakeNode("src_mod", "x_src", parent=parent, port_connections={"out1": "\\shared_wire"})
    target = FakeNode("dst_mod", "x_target", parent=parent, port_connections={"in_a": "\\other_wire"})

    advisory = resolver.resolve_non_direct_child_advisory(
        parent_node=parent,
        source_child_node=source,
        requested_child_node=target,
    )

    assert advisory.graph_status == "graph_unavailable"
    assert advisory.recommended_child == {}
    assert advisory.advisory_path == []
    assert "graph could not be built" in advisory.reason
