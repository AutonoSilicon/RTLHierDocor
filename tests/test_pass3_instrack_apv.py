import asyncio
import json

from agent.pass3_generator import Pass3Generator
from agent.project_progress_tracker import ProjectProgressTracker


class FakeTracker:
    def get_pass1_content(self, module_name):
        return f"preview for {module_name}"

    def get_pass2_content(self, module_name):
        return ""

    def get_pass2_7_content(self, module_name):
        return ""


class FakeResolver:
    backend = None


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, system, prompt, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0), {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


class FakeOwner:
    def __init__(self, tmp_path, llm):
        self.llm = llm
        self.instrack_orchestrate_llm = llm
        self.tracker = FakeTracker()
        self.resolver = FakeResolver()
        self._graphs = {}
        self.chip_dir = tmp_path / "chip"
        self.chip_debug_dir = self.chip_dir / "debug"
        self.project_tracker = ProjectProgressTracker(str(self.chip_dir))
        self.pass3_3_3_enabled = True
        self.isa_profile = "c910"
        self.isa_instructions = []
        self.instrack_single_instruction = None


class FakeNode:
    def __init__(self, module_name, instance_name):
        self.module_name = module_name
        self.instance_name = instance_name
        self.children = {}
        self.depth = 0

    def get_path(self):
        return self.instance_name


def _prime_cached_search_and_orchestrate(generator, top_node, instruction, search_payload, orchestrate_items):
    slug = generator._safe_slug(instruction).lower()
    instruction_dir = generator.owner.chip_dir / "instrack" / slug
    artifacts_dir = instruction_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    search_content = "```json\n" + json.dumps(search_payload, ensure_ascii=False, indent=2) + "\n```\n"
    search_path = instruction_dir / f"{slug}.search.md"
    search_path.write_text(search_content, encoding="utf-8")
    parsed_search = generator._extract_pass3_3_search_json(search_content, top_node, instruction)
    parsed_search_text = json.dumps(parsed_search, ensure_ascii=False, indent=2)

    top_preview = generator.owner.tracker.get_pass1_content(top_node.module_name)
    core_partition_text = "未检测到 pass3.2 输出，可结合 readSource 与 forkSubAgent 补齐证据。"
    datasheet = generator._extract_instruction_datasheet_excerpt(generator._load_instrack_datasheet(), instruction)
    search_input_hash = generator._build_pass3_3_search_input_hash(
        top_module=top_node.module_name,
        instruction=instruction,
        top_preview=top_preview,
        core_partition=core_partition_text,
        instruction_datasheet=datasheet,
    )
    generator.owner.project_tracker.update(
        f"instrack/{slug}/{slug}.search.md",
        search_content,
        input_hash=search_input_hash,
        meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_1"},
    )

    orchestrate_index_text = json.dumps(
        {
            "instruction": instruction,
            "start_module": parsed_search.get("start_module", ""),
            "schema_version": "pass3_3_2_orchestrate_index_v2",
            "items": orchestrate_items,
        },
        ensure_ascii=False,
        indent=2,
    )
    orchestrate_index_path = artifacts_dir / "orchestrate_index.json"
    orchestrate_index_path.write_text(orchestrate_index_text, encoding="utf-8")
    orchestrate_input_hash = generator._instrack_stages.build_pass3_3_orchestrate_input_hash(
        top_module=top_node.module_name,
        instruction=instruction,
        instruction_datasheet=datasheet,
        search_result_json_text=parsed_search_text,
    )
    generator.owner.project_tracker.update(
        f"instrack/{slug}/artifacts/orchestrate_index.json",
        orchestrate_index_text,
        input_hash=orchestrate_input_hash,
        meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_2_orchestrate"},
    )


def _structured_topology_block(*signals):
    joined = ", ".join(f"{signal}(port)" for signal in signals)
    return f"# Topology\n\nInputs from blocks: {joined}\n"


def _build_generator(tmp_path, llm, topology_blocks=None):
    owner = FakeOwner(tmp_path, llm)
    generator = Pass3Generator(owner)
    generator._resolve_instrack_instructions = lambda: ["ADD"]
    generator._load_instrack_datasheet = lambda: "ADD datasheet excerpt"
    generator._extract_instruction_datasheet_excerpt = lambda text, instruction: text
    topology_blocks = dict(topology_blocks or {})
    generator._build_pass2_style_topology_block = lambda module_name: topology_blocks.get(
        module_name,
        f"topology for {module_name}",
    )
    return generator


def test_run_pass3_3_generates_apv_yaml_with_serial_dep_backfill(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","task_name":"ifu entry","condition_lines":["fetch_vld && !flush"],"capture_signals":["fetch_vld"],"logging_lines":["ifu accepts instruction"],"match_mode":"single","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","task_name":"decode accept","condition_lines":["$dep.ifu_entry.fetch_vld == dispatch_vld_i","dispatch_vld_i && decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["decode accepted"],"match_mode":"first","max_match":1},{"ref_name":"issue_fire","task_name":"issue fire","condition_lines":["$dep.decode_accept.dispatch_vld == issue_en"],"capture_signals":["issue_pkt_vld"],"logging_lines":["issue fired"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("fetch_vld", "flush"),
            "idu_mod": _structured_topology_block("dispatch_vld_i", "decode_ready", "dispatch_vld", "issue_en", "issue_pkt_vld"),
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "ifu_mod",
        "start_instance": "x_ifu",
        "start_block": "IFU_ENTRY",
        "key_register": "ifu_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "ifu_mod",
            "instance": "x_ifu",
            "path": "top/x_ifu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [{"output_port": "dispatch_vld_o", "value_condition": "dispatch_vld_o = fetch_vld", "behavior": "forward to IDU"}],
                "_boundary_handoff_routes": [{"status": "resolved", "output_port": "dispatch_vld_o"}],
                "lifecycle_context": "accepts fetch-valid and forwards decode work",
                "confidence": "high",
                "unknown": "",
            },
        },
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "parent_module",
            "orchestration": {
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept IFU handoff"}],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "accepts decode handoff and prepares issue packet",
                "confidence": "high",
                "unknown": "",
            },
        },
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    assert len(llm.calls) == 2
    assert all(call["kwargs"].get("tools_enabled", False) is False for call in llm.calls)
    assert '"ref_name": "ifu_entry"' in llm.calls[1]["prompt"]
    assert '"task_id": "s00_t00_x_ifu"' in llm.calls[1]["prompt"]
    assert '"capture_names": [' in llm.calls[1]["prompt"]
    assert "## Search Result JSON" not in llm.calls[1]["prompt"]
    assert "## Current Module Preview" not in llm.calls[1]["prompt"]
    assert "## Previous Item Summary" not in llm.calls[1]["prompt"]
    assert "## Next Item Summary" not in llm.calls[1]["prompt"]
    assert "## Current Module Context" in llm.calls[1]["prompt"]
    assert "## Local Signal Guidance" not in llm.calls[1]["prompt"]
    assert '"instruction_state": {' in llm.calls[1]["prompt"]
    assert '"lifecycle_context": "accepts decode handoff and prepares issue packet"' in llm.calls[1]["prompt"]
    assert '"boundary_takeover": [' in llm.calls[1]["prompt"]
    assert '"boundary_handoffs": []' in llm.calls[1]["prompt"]
    assert '"preferred_local_anchor_names": [' not in llm.calls[1]["prompt"]
    assert '"dispatch_vld_i"' in llm.calls[1]["prompt"]

    slug = generator._safe_slug("ADD").lower()
    ifu_yaml = (tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_ifu.apv.yaml").read_text(encoding="utf-8")
    idu_yaml = (tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_idu.apv.yaml").read_text(encoding="utf-8")
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))
    summary_json = json.loads((tmp_path / "chip" / "instrack" / slug / "add.json").read_text(encoding="utf-8"))
    summary_md = (tmp_path / "chip" / "instrack" / slug / "add.md").read_text(encoding="utf-8")

    assert 'id: "s00_t00_x_ifu"' in ifu_yaml
    assert 'name: "ifu entry"' in ifu_yaml
    assert 'matchMode: "first"' in ifu_yaml
    assert '"x_ifu.fetch_vld && !x_ifu.flush"' in ifu_yaml
    assert 'dependsOn: "s00_t00_x_ifu"' in idu_yaml
    assert '"$dep.s00_t00_x_ifu.fetch_vld == x_idu.dispatch_vld_i"' in idu_yaml
    assert '"$dep.s01_t00_x_idu.dispatch_vld == x_idu.issue_en"' in idu_yaml
    assert apv_index["schema_version"] == "pass3_3_3_apv_index_v1"
    assert apv_index["items"][0]["leaf_task_id"] == "s00_t00_x_ifu"
    assert apv_index["items"][0]["leaf_ref_name"] == "ifu_entry"
    assert apv_index["items"][1]["leaf_task_id"] == "s01_t01_x_idu"
    assert apv_index["items"][1]["leaf_ref_name"] == "issue_fire"
    assert apv_index["items"][1]["status"] == "complete"
    assert summary_json["schema_version"] == "pass3_3_instrack_apv_v1"
    assert summary_json["apv_index_artifact_json"] == f"instrack/{slug}/artifacts/apv_index.json"
    assert "top__x_ifu.apv.yaml" in summary_md
    assert "status=complete, tasks=1" in summary_md
    assert "top__x_idu.apv.yaml" in summary_md


def test_run_pass3_3_marks_partial_when_dep_ref_name_is_not_visible(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_dep","task_name":"bad dep","condition_lines":["$dep.missing.fetch_vld && local_vld"],"capture_signals":["local_vld"],"logging_lines":["should fail"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first local decode module",
                "confidence": "medium",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    yaml_text = (tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_idu.apv.yaml").read_text(encoding="utf-8")
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert "tasks: []" in yaml_text
    assert apv_index["items"][0]["status"] == "partial"
    assert any("unknown dep `ref_name=missing`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_keeps_valid_tasks_when_one_task_is_invalid(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_first","task_name":"bad first","condition_lines":["local_vld"],"capture_signals":["local_vld"],"logging_lines":["bad"],"match_mode":"bogus","max_match":1},{"ref_name":"good_second","task_name":"good second","condition_lines":["local_vld && ready"],"capture_signals":["done_vld"],"logging_lines":["good"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first local decode module",
                "confidence": "medium",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    yaml_text = (tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_idu.apv.yaml").read_text(encoding="utf-8")
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert 'name: "good second"' in yaml_text
    assert 'tasks: []' not in yaml_text
    assert apv_index["items"][0]["status"] == "partial"
    assert apv_index["items"][0]["task_count"] == 1
    assert any("unsupported `match_mode=bogus`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_normalizes_assignment_conditions_and_sv_literals(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"valid_flag","task_name":"valid flag","condition_lines":["flag = 1'b1"],"capture_signals":["flag"],"logging_lines":["flag asserted"],"match_mode":"condition","max_match":1},{"ref_name":"consume_flag","task_name":"consume flag","condition_lines":["$dep.valid_flag.flag = local_vld && ready"],"capture_signals":["done_vld"],"logging_lines":["flag consumed"],"match_mode":"condition","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "ifu_mod",
        "start_instance": "x_ifu",
        "start_block": "IFU_ENTRY",
        "key_register": "ifu_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "ifu_mod",
            "instance": "x_ifu",
            "path": "top/x_ifu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "tracks local flag propagation",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    yaml_text = (tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_ifu.apv.yaml").read_text(encoding="utf-8")
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert 'matchMode: "first"' in yaml_text
    assert '"x_ifu.flag == (1\'b1)"' in yaml_text
    assert '"$dep.s00_t00_x_ifu.flag == (x_ifu.local_vld && x_ifu.ready)"' in yaml_text
    assert apv_index["items"][0]["status"] == "complete"


def test_run_pass3_3_rejects_multiple_dep_ref_names(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_multi_dep","task_name":"bad multi dep","condition_lines":["$dep.left.fetch_vld && $dep.right.dispatch_vld"],"capture_signals":["done_vld"],"logging_lines":["bad"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first local decode module",
                "confidence": "medium",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["status"] == "partial"
    assert any("at most one unique dep `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_resolved_dep_reference_in_raw_json(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_resolved_dep","task_name":"bad resolved dep","condition_lines":["$dep.s00_t00_x_ifu.fetch_vld && local_vld"],"capture_signals":["done_vld"],"logging_lines":["bad"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first local decode module",
                "confidence": "medium",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["status"] == "partial"
    assert any(
        "must not emit final `$dep.<task_id>.<signal>` references" in reason
        for reason in apv_index["items"][0]["unknown"]
    )


def test_run_pass3_3_rejects_unknown_dep_capture_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","task_name":"start ok","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_capture","task_name":"bad capture","condition_lines":["$dep.ifu_entry.missing_sig && ready"],"capture_signals":["dispatch_vld"],"logging_lines":["bad capture"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "ifu_mod",
        "start_instance": "x_ifu",
        "start_block": "IFU_ENTRY",
        "key_register": "ifu_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "ifu_mod",
            "instance": "x_ifu",
            "path": "top/x_ifu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [{"output_port": "dispatch_vld_o", "value_condition": "dispatch_vld_o = fetch_vld", "behavior": "forward to IDU"}],
                "_boundary_handoff_routes": [{"status": "resolved", "output_port": "dispatch_vld_o"}],
                "lifecycle_context": "accepts fetch-valid and forwards decode work",
                "confidence": "high",
                "unknown": "",
            },
        },
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "parent_module",
            "orchestration": {
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept IFU handoff"}],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "accepts decode handoff",
                "confidence": "high",
                "unknown": "",
            },
        },
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))
    assert apv_index["items"][0]["status"] == "complete"
    assert apv_index["items"][1]["status"] == "partial"
    assert any("unknown dep capture name(s)" in reason for reason in apv_index["items"][1]["unknown"])


def test_run_pass3_3_rejects_duplicate_ref_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"dup_ref","task_name":"first task","condition_lines":["local_vld"],"capture_signals":["local_vld"],"logging_lines":["first"],"match_mode":"first","max_match":1},{"ref_name":"dup_ref","task_name":"second task","condition_lines":["ready"],"capture_signals":["done_vld"],"logging_lines":["second"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first local decode module",
                "confidence": "medium",
                "unknown": "",
            },
        },
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))
    assert apv_index["items"][0]["status"] == "partial"
    assert apv_index["items"][0]["task_count"] == 1
    assert any("duplicate `ref_name=dup_ref`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_forward_dep_ref_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","task_name":"decode accept","condition_lines":["$dep.issue_fire.issue_pkt_vld && decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["bad forward ref"],"match_mode":"first","max_match":1},{"ref_name":"issue_fire","task_name":"issue fire","condition_lines":["issue_en"],"capture_signals":["issue_pkt_vld"],"logging_lines":["later task"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(tmp_path, llm)
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first local decode module",
                "confidence": "medium",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))
    assert apv_index["items"][0]["status"] == "partial"
    assert any("forward-references `ref_name=issue_fire`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_unknown_local_signal_when_topology_catalog_is_trusted(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_local","task_name":"bad local","condition_lines":["ghost_sig == real_vld"],"capture_signals":["real_vld"],"logging_lines":["bad local"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("real_vld", "real_data"),
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "ifu_mod",
        "start_instance": "x_ifu",
        "start_block": "IFU_ENTRY",
        "key_register": "ifu_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "ifu_mod",
            "instance": "x_ifu",
            "path": "top/x_ifu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [{"output_port": "real_vld", "value_condition": "real_vld", "behavior": "forward valid"}],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "simple local signal check",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["status"] == "partial"
    assert any("unknown local signal(s)" in reason and "ghost_sig" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_marks_partial_when_complete_lacks_same_line_anchor_relation(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["ifu accepts instruction"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","task_name":"decode accept","condition_lines":["$dep.ifu_entry.fetch_vld","dispatch_vld_i","decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["weak continuity"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("fetch_vld"),
            "idu_mod": _structured_topology_block("dispatch_vld_i", "decode_ready", "dispatch_vld"),
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "ifu_mod",
        "start_instance": "x_ifu",
        "start_block": "IFU_ENTRY",
        "key_register": "ifu_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "ifu_mod",
            "instance": "x_ifu",
            "path": "top/x_ifu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [{"output_port": "fetch_vld", "value_condition": "fetch_vld", "behavior": "forward valid"}],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "fetch stage",
                "confidence": "high",
                "unknown": "",
            },
        },
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "parent_module",
            "orchestration": {
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept upstream valid"}],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "decode stage",
                "confidence": "high",
                "unknown": "",
            },
        },
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["status"] == "complete"
    assert apv_index["items"][1]["status"] == "partial"
    assert any("same-line continuity-preserving relation" in reason for reason in apv_index["items"][1]["unknown"])


def test_run_pass3_3_allows_sibling_branch_tasks_sharing_same_parent_ref_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"partial","tasks":[{"ref_name":"dispatch_entry","task_name":"dispatch entry","condition_lines":["entry_vld"],"capture_signals":["entry_vld"],"logging_lines":["dispatch entry"],"match_mode":"first","max_match":1},{"ref_name":"to_buf0","task_name":"route to buf0","condition_lines":["$dep.dispatch_entry.entry_vld == buf0_fire","buf_sel == 2'b00"],"capture_signals":["buf0_fire"],"logging_lines":["buf0 path"],"match_mode":"first","max_match":1},{"ref_name":"to_buf1","task_name":"route to buf1","condition_lines":["$dep.dispatch_entry.entry_vld == buf1_fire","buf_sel == 2'b01"],"capture_signals":["buf1_fire"],"logging_lines":["buf1 path"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "idu_mod": _structured_topology_block("entry_vld", "buf0_fire", "buf1_fire", "buf_sel"),
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept upstream valid"}],
                "boundary_handoffs": [
                    {"output_port": "buf0_fire_o", "value_condition": "buf0_fire_o", "behavior": "route to buffer0"},
                    {"output_port": "buf1_fire_o", "value_condition": "buf1_fire_o", "behavior": "route to buffer1"},
                ],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "branches dispatch toward two local buffers",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    yaml_text = (tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_idu.apv.yaml").read_text(encoding="utf-8")
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert 'name: "dispatch entry"' in yaml_text
    assert 'name: "route to buf0"' in yaml_text
    assert 'name: "route to buf1"' in yaml_text
    assert yaml_text.count('dependsOn: "s00_t00_x_idu"') == 2
    assert apv_index["items"][0]["status"] == "partial"
    assert apv_index["items"][0]["task_count"] == 3
    assert apv_index["items"][0]["leaf_task_id"] == ""
    assert apv_index["items"][0]["leaf_ref_name"] == ""
    assert apv_index["items"][0]["leaf_capture_names"] == []
    assert not any("duplicate `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])
    assert not any("forward-references" in reason for reason in apv_index["items"][0]["unknown"])
    assert not any("unknown dep `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])
    assert not any("at most one unique dep `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_marks_complete_branching_item_partial_when_multiple_terminal_leaves(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"dispatch_entry","task_name":"dispatch entry","condition_lines":["entry_vld"],"capture_signals":["entry_vld"],"logging_lines":["dispatch entry"],"match_mode":"first","max_match":1},{"ref_name":"to_buf0","task_name":"route to buf0","condition_lines":["$dep.dispatch_entry.entry_vld == buf0_fire","buf_sel == 2'b00"],"capture_signals":["buf0_fire"],"logging_lines":["buf0 path"],"match_mode":"first","max_match":1},{"ref_name":"to_buf1","task_name":"route to buf1","condition_lines":["$dep.dispatch_entry.entry_vld == buf1_fire","buf_sel == 2'b01"],"capture_signals":["buf1_fire"],"logging_lines":["buf1 path"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "idu_mod": _structured_topology_block("entry_vld", "buf0_fire", "buf1_fire", "buf_sel"),
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "idu_mod",
        "start_instance": "x_idu",
        "start_block": "IDU_ENTRY",
        "key_register": "idu_reg",
        "key_register_line_range": {"start_line": 3, "end_line": 4},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_idu",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept upstream valid"}],
                "boundary_handoffs": [
                    {"output_port": "buf0_fire_o", "value_condition": "buf0_fire_o", "behavior": "route to buffer0"},
                    {"output_port": "buf1_fire_o", "value_condition": "buf1_fire_o", "behavior": "route to buffer1"},
                ],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "branches dispatch toward two local buffers",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["status"] == "partial"
    assert apv_index["items"][0]["task_count"] == 3
    assert apv_index["items"][0]["leaf_task_id"] == ""
    assert apv_index["items"][0]["leaf_ref_name"] == ""
    assert apv_index["items"][0]["leaf_capture_names"] == []
    assert any("multiple terminal task branches" in reason for reason in apv_index["items"][0]["unknown"])
    assert any("exports only one downstream leaf" in reason for reason in apv_index["items"][0]["unknown"])
