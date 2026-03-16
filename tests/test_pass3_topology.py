from agent.pass3_topology import Pass3Topology
from models.source_loc import SourceLocation
from schematic.simplifier import CombLocationInfo, CombNodeInfo, ProcNodeInfo, SimplifiedGraph


class FakeTracker:
    def get_pass2_4_content(self, module_name):
        return ""

    def get_pass2_content(self, module_name):
        return ""

    def get_pass2_7_content(self, module_name):
        return ""

    def get_pass1_content(self, module_name):
        return ""


class FakeResolver:
    def get_topology_content(self, module_name):
        if module_name == "fallback_mod":
            return "# Topology for fallback_mod\n\nfallback raw topology"
        return ""

    def read_block_source(self, source_locations):
        loc = source_locations[0]
        return f"// {loc.file_path}:{loc.start_line}\nalways @(posedge clk) q <= d;"


class FakeSchematicGenerator:
    def __init__(self, graph=None):
        self.graph = graph

    def generate_simplified_graph(self, module_name):
        return self.graph


class FakeOwner:
    def __init__(self, graph=None):
        self.tracker = FakeTracker()
        self.resolver = FakeResolver()
        self.schematic_gen = FakeSchematicGenerator(graph)
        self._graphs = {}


class FakeGenerator:
    def __init__(self, owner):
        self.owner = owner


def make_graph():
    graph = SimplifiedGraph(module_name="demo_mod")
    graph.proc_nodes["PROC_1"] = ProcNodeInfo(
        node_id="PROC_1",
        label="PROC_1",
        source_location=SourceLocation(
            file_path="/tmp/demo.v",
            start_line=12,
            end_line=18,
        ),
    )
    loc_info = CombLocationInfo()
    loc_info.add_location("/tmp/demo.v", 22)
    graph.comb_nodes["COMB_1"] = CombNodeInfo(
        node_id="COMB_1",
        comb_type="COMB",
        node_count=3,
        location_info=loc_info,
        source_locations=[
            SourceLocation(
                file_path="/tmp/demo.v",
                start_line=22,
                end_line=24,
            )
        ],
    )
    graph.io_ports["in0"] = "req_vld"
    graph.io_ports["out0"] = "grant_vld"
    graph.submodules["sub0"] = "x_child"
    graph.edges.extend(
        [
            ("in0", "PROC_1"),
            ("PROC_1", "COMB_1"),
            ("COMB_1", "out0"),
            ("COMB_1", "sub0"),
        ]
    )
    return graph


def test_build_topology_block_prefers_core_graph_description():
    topology = Pass3Topology(FakeGenerator(FakeOwner(make_graph())))

    text = topology.build_topology_block("demo_mod")

    assert text.startswith("# Topology for demo_mod")
    assert "## Circuit Blocks (in topological order, block-to-block view):" in text
    assert "- **PROC_1** [PROC] (demo.v:12-18)" in text
    assert "- **COMB_1** [COMB, 3 nodes] (demo.v:22)" in text
    assert "Inputs from blocks: req_vld(port)" in text
    assert "Outputs to blocks: COMB_1(COMB)" in text
    assert "## Boundary Connections (including direct links):" in text
    assert "COMB_1(COMB) -> x_child(submodule)" in text
    assert "```verilog" in text


def test_build_topology_block_falls_back_to_resolver_when_no_graph():
    topology = Pass3Topology(FakeGenerator(FakeOwner(graph=None)))

    text = topology.build_topology_block("fallback_mod")

    assert text == "# Topology for fallback_mod\n\nfallback raw topology"
