import asyncio
import json
import re

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

    def __init__(self, source_slices=None):
        self._source_slices = dict(source_slices or {})

    def get_module_source_slice(self, module_name):
        payload = self._source_slices.get(module_name)
        return dict(payload) if isinstance(payload, dict) else None


class FakeLLM:
    def __init__(self, responses, auto_anchorize=True):
        self.responses = list(responses)
        self.calls = []
        self.auto_anchorize = auto_anchorize

    async def generate(self, system, prompt, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        if self.auto_anchorize:
            response = _maybe_anchorize_response(response)
        return response, {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


class FakeToolLLM(FakeLLM):
    def __init__(self, responses, tool_rounds, auto_anchorize=True):
        super().__init__(responses, auto_anchorize=auto_anchorize)
        self.tool_rounds = list(tool_rounds)

    async def generate(self, system, prompt, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, "kwargs": kwargs})
        if kwargs.get("tools_enabled"):
            tool_callback = kwargs.get("tool_callback")
            assert tool_callback is not None
            rounds = self.tool_rounds.pop(0) if self.tool_rounds else []
            log_path = kwargs.get("log_path", "")
            for round_idx, step in enumerate(rounds, start=1):
                tool_result = tool_callback(step["name"], dict(step.get("args") or {}))
                if asyncio.iscoroutine(tool_result):
                    tool_result = await tool_result
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write("\n\n" + "=" * 80 + "\n")
                        f.write(f"## Tool Trace Round {round_idx}\n")
                        f.write("=" * 80 + "\n\n")
                        f.write("### Calls\n")
                        f.write(
                            f"- call `{step['name']}` args: `{json.dumps(step.get('args') or {}, ensure_ascii=False)}`\n\n"
                        )
                        f.write("### Results\n")
                        f.write(f"- result `{step['name']}_{round_idx}`:\n```text\n{tool_result}\n```\n")
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        if self.auto_anchorize:
            response = _maybe_anchorize_response(response)
        return response, {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


def _extract_json_code_block(text):
    raw_text = str(text or "")
    start = raw_text.find("```json")
    if start < 0:
        return None
    start = raw_text.find("\n", start)
    if start < 0:
        return None
    end = raw_text.rfind("```")
    if end <= start:
        return None
    return raw_text[start + 1 : end].strip()


def _default_anchor_reason(kind, task_name, dep_name, local_signals):
    signal_preview = ", ".join(list(local_signals or [])[:2]) or "the captured local signals"
    normalized_task_name = str(task_name or "task").strip() or "task"
    if kind == "identity":
        if dep_name:
            return (
                f"This identity anchor ties {dep_name} to {signal_preview} so {normalized_task_name} continues the same instruction."
            )
        return f"This identity anchor keeps {signal_preview} as the local instruction identifier for {normalized_task_name}."
    if kind == "path":
        return f"This path anchor uses {signal_preview} to distinguish the active route for {normalized_task_name}."
    return f"This gating anchor uses {signal_preview} to show when {normalized_task_name} is enabled at the sampled cycle."


def _extract_local_condition_signals(condition_lines):
    reserved = {"and", "or", "not", "true", "false", "none"}
    local_signals = []
    for line in list(condition_lines or []):
        stripped = re.sub(r"\$dep\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?", " ", str(line or ""))
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
            lowered = token.lower()
            if lowered in reserved:
                continue
            if token in {"b", "h", "d"}:
                continue
            if token not in local_signals:
                local_signals.append(token)
    return local_signals


def _default_anchor_for_task(task):
    capture_signals = list(task.get("capture_signals") or task.get("capture") or [])
    local_signals = capture_signals[:1]
    dep_name = str(task.get("dep_name") or "").strip()
    condition_lines = list(task.get("condition_lines") or task.get("condition") or [])
    joined_conditions = " ".join(str(line) for line in condition_lines)
    local_condition_signals = _extract_local_condition_signals(condition_lines)
    dep_signals = []
    if dep_name:
        dep_signals = sorted(set(re.findall(rf"\$dep\.{re.escape(dep_name)}\.[A-Za-z_][A-Za-z0-9_]*", joined_conditions)))
    local_anchor_text = " ".join(local_condition_signals + capture_signals).lower()
    if not dep_name:
        kind = "identity"
    elif any(token in local_anchor_text for token in ("pc", "inst", "data", "iid", "dispatch")):
        kind = "identity"
    elif any(token in local_anchor_text for token in ("slot", "pipe", "channel", "buf", "bypass", "route", "mux")):
        kind = "path"
    else:
        kind = "gating"
    if kind == "identity" and dep_name and local_condition_signals:
        local_signals = [local_condition_signals[0]]
        if local_signals[0] not in capture_signals:
            capture_signals.append(local_signals[0])
            task["capture_signals"] = capture_signals
    elif kind == "identity" and not local_signals and local_condition_signals:
        local_signals = [local_condition_signals[0]]
        if local_signals[0] not in capture_signals:
            capture_signals.append(local_signals[0])
            task["capture_signals"] = capture_signals
    elif not local_signals and local_condition_signals:
        local_signals = [local_condition_signals[0]]
        if local_signals[0] not in capture_signals:
            capture_signals.append(local_signals[0])
            task["capture_signals"] = capture_signals
    elif not local_signals and capture_signals:
        local_signals = capture_signals[:1]

    if any(token in local_anchor_text for token in ("slot", "pipe", "channel", "buf", "bypass", "route", "mux")) and not dep_name:
        kind = "identity"
    elif any(token in local_anchor_text for token in ("slot", "pipe", "channel", "buf", "bypass", "route", "mux")):
        kind = "path"
    if kind == "identity" and not local_signals and capture_signals:
        local_signals = capture_signals[:1]
    if kind == "identity" and dep_name and not dep_signals:
        kind = "gating"
    return [
        {
            "kind": kind,
            "dep_signals": dep_signals,
            "local_signals": local_signals,
            "reason": _default_anchor_reason(kind, task.get("task_name") or task.get("name") or "", dep_name, local_signals),
        }
    ]


def _maybe_anchorize_response(text):
    json_block = _extract_json_code_block(text)
    if not json_block:
        return text
    try:
        payload = json.loads(json_block)
    except Exception:
        return text
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return text
    changed = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("anchors"):
            continue
        task["anchors"] = _default_anchor_for_task(task)
        changed = True
    if not changed:
        return text
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


class FakeOwner:
    def __init__(self, tmp_path, llm, source_slices=None, source_access_mode="embedded_topology", apv_max_tool_rounds=32):
        self.llm = llm
        self.instrack_orchestrate_llm = llm
        self.tracker = FakeTracker()
        self.resolver = FakeResolver(source_slices=source_slices)
        self._graphs = {}
        self.chip_dir = tmp_path / "chip"
        self.chip_debug_dir = self.chip_dir / "debug"
        self.project_tracker = ProjectProgressTracker(str(self.chip_dir))
        self.pass3_3_3_enabled = True
        self.isa_profile = "c910"
        self.isa_instructions = []
        self.instrack_single_instruction = None
        self.instrack_apv_source_access_mode = source_access_mode
        self.instrack_apv_max_tool_rounds = apv_max_tool_rounds


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


def _build_source_slice(source_path, lines, file_line_start=1):
    rendered_lines = []
    line_map = []
    for idx, text in enumerate(lines, start=1):
        line_text = str(text)
        if not line_text.endswith("\n"):
            line_text += "\n"
        rendered_lines.append(line_text)
        line_map.append(
            {
                "module_line": idx,
                "file_line": file_line_start + idx - 1,
                "text": line_text.rstrip("\n"),
            }
        )
    return {
        "source_path": source_path,
        "module_line_count": len(rendered_lines),
        "module_file_line_start": file_line_start,
        "module_file_line_end": file_line_start + len(rendered_lines) - 1 if rendered_lines else 0,
        "text": "".join(rendered_lines),
        "lines": rendered_lines,
        "line_map": line_map,
    }


def _build_generator(
    tmp_path,
    llm,
    topology_blocks=None,
    source_slices=None,
    source_access_mode="embedded_topology",
    apv_max_tool_rounds=32,
):
    owner = FakeOwner(
        tmp_path,
        llm,
        source_slices=source_slices,
        source_access_mode=source_access_mode,
        apv_max_tool_rounds=apv_max_tool_rounds,
    )
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
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld && !flush"],"capture_signals":["fetch_vld"],"logging_lines":["ifu accepts instruction"],"match_mode":"single","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","dep_name":"s00_t00_x_ifu","task_name":"decode accept","condition_lines":["$dep.s00_t00_x_ifu.fetch_vld == dispatch_vld_i","dispatch_vld_i && decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["decode accepted"],"match_mode":"first","max_match":1},{"ref_name":"issue_fire","dep_name":"decode_accept","task_name":"issue fire","condition_lines":["$dep.decode_accept.dispatch_vld == issue_en"],"capture_signals":["issue_pkt_vld"],"logging_lines":["issue fired"],"match_mode":"first","max_match":1}],"unknown":[]}
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
    assert '"ref_name": "s00_t00_x_ifu"' in llm.calls[1]["prompt"]
    assert '"local_ref_name": "ifu_entry"' in llm.calls[1]["prompt"]
    assert '"task_id": "s00_t00_x_ifu"' in llm.calls[1]["prompt"]
    assert '"capture_names": [' in llm.calls[1]["prompt"]
    assert '"branch_lineage": [' in llm.calls[1]["prompt"]
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
    assert apv_index["schema_version"] == "pass3_3_3_apv_index_v8"
    assert len(apv_index["items"][0]["leaf_contexts"]) == 1
    first_leaf = apv_index["items"][0]["leaf_contexts"][0]
    assert first_leaf["task_id"] == "s00_t00_x_ifu"
    assert first_leaf["ref_name"] == "ifu_entry"
    assert first_leaf["capture_names"] == ["fetch_vld"]
    assert first_leaf["branch_lineage"] == ["ifu_entry"]
    assert first_leaf["upstream_hint"] == ""
    assert first_leaf["module"] == "ifu_mod"
    assert first_leaf["instance"] == "x_ifu"
    assert first_leaf["path"] == "top/x_ifu"
    assert first_leaf["anchor_kinds"] == ["identity"]
    assert first_leaf["anchor_capture_names"] == ["fetch_vld"]
    assert first_leaf["anchor_reasons"]
    assert first_leaf["anchors"][0]["reason"] == first_leaf["anchor_reasons"][0]
    assert apv_index["items"][0]["leaf_task_id"] == "s00_t00_x_ifu"
    assert apv_index["items"][0]["leaf_ref_name"] == "ifu_entry"
    assert len(apv_index["items"][1]["leaf_contexts"]) == 1
    second_leaf = apv_index["items"][1]["leaf_contexts"][0]
    assert second_leaf["task_id"] == "s01_t01_x_idu"
    assert second_leaf["ref_name"] == "issue_fire"
    assert second_leaf["capture_names"] == ["issue_pkt_vld"]
    assert second_leaf["branch_lineage"] == ["ifu_entry", "decode_accept", "issue_fire"]
    assert second_leaf["upstream_hint"] == ""
    assert second_leaf["module"] == "idu_mod"
    assert second_leaf["instance"] == "x_idu"
    assert second_leaf["path"] == "top/x_idu"
    assert second_leaf["anchor_kinds"] == ["gating"]
    assert second_leaf["anchor_capture_names"] == ["issue_pkt_vld"]
    assert second_leaf["anchor_reasons"]
    assert apv_index["items"][1]["leaf_task_id"] == "s01_t01_x_idu"
    assert apv_index["items"][1]["leaf_ref_name"] == "issue_fire"
    assert apv_index["items"][1]["status"] == "complete"
    assert summary_json["schema_version"] == "pass3_3_instrack_apv_v8"
    assert summary_json["apv_index_artifact_json"] == f"instrack/{slug}/artifacts/apv_index.json"
    assert summary_json["apv_status"] == ""
    assert summary_json["apv_error"] == ""
    assert "top__x_ifu.apv.yaml" in summary_md
    assert "status=complete, tasks=1" in summary_md
    assert "top__x_idu.apv.yaml" in summary_md


def test_run_pass3_3_toolized_source_uses_tools_and_omits_embedded_source(tmp_path):
    llm = FakeToolLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"pcgen_seq_fetch","dep_name":"","task_name":"sequential fetch","condition_lines":["pcgen_icache_if_seq_data_req == 1'b1","!pcgen_chgflw"],"capture_signals":["pcgen_ifctrl_pc"],"logging_lines":["pcgen fetch observed"],"match_mode":"first","max_match":1}],"unknown":[]}
```"""
        ],
        tool_rounds=[
            [
                {"name": "grepSource", "args": {"pattern": "pcgen_ifctrl_pc", "ignore_case": False, "max_matches": 5}},
                {"name": "readLine", "args": {"start_line": 3, "end_line": 7}},
            ]
        ],
    )
    generator = _build_generator(
        tmp_path,
        llm,
        source_access_mode="toolized_source",
        source_slices={
            "pcgen_mod": _build_source_slice(
                "/tmp/pcgen_mod.v",
                [
                    "module pcgen_mod(",
                    "  input wire pcgen_icache_if_seq_data_req,",
                    "  input wire pcgen_chgflw,",
                    "  output wire [14:0] pcgen_ifctrl_pc",
                    ");",
                    "assign pcgen_ifctrl_pc = 15'h1234;",
                    "endmodule",
                ],
                file_line_start=101,
            )
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "pcgen_mod",
        "start_instance": "x_pcgen",
        "start_block": "PCGEN_ENTRY",
        "key_register": "pcgen_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "pcgen_mod",
            "instance": "x_pcgen",
            "path": "top/x_pcgen",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "tracks pcgen sequential fetch",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    call = llm.calls[0]
    prompt_text = call["prompt"]
    slug = generator._safe_slug("ADD").lower()
    debug_text = (
        tmp_path / "chip" / "debug" / "debug_pass3_instrack_apv_agent_ADD_top__x_pcgen_L1.md"
    ).read_text(encoding="utf-8")
    yaml_text = (
        tmp_path / "chip" / "instrack" / slug / "artifacts" / "top__x_pcgen.apv.yaml"
    ).read_text(encoding="utf-8")

    assert call["kwargs"].get("tools_enabled") is True
    assert call["kwargs"].get("tools")
    assert call["kwargs"].get("max_tool_rounds") == 32
    assert "## Current Module Source Metadata" in prompt_text
    assert "## Optional Source Tools" in prompt_text
    assert "## Current Module Verilog Source" not in prompt_text
    assert "module pcgen_mod(" not in prompt_text
    assert '"source_path": "/tmp/pcgen_mod.v"' in prompt_text
    assert "## Tool Trace Round 1" in debug_text
    assert "grepSource" in debug_text
    assert "readLine" in debug_text
    assert 'name: "sequential fetch"' in yaml_text


def test_run_pass3_3_writes_llm_error_artifact_for_empty_response(tmp_path):
    llm = FakeLLM(
        [
            "Error: LLM returned empty response. rounds_used=32; tool_mode=True; saw_tool_calls=True; configured_max_tool_rounds=32; input_tokens=101; output_tokens=0; total_tokens=101."
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        source_access_mode="toolized_source",
        source_slices={
            "pcgen_mod": _build_source_slice(
                "/tmp/pcgen_mod.v",
                [
                    "module pcgen_mod(",
                    "  input wire pcgen_icache_if_seq_data_req,",
                    "  output wire pcgen_ifctrl_pc",
                    ");",
                    "endmodule",
                ],
                file_line_start=1,
            )
        },
    )
    top_node = FakeNode("top_mod", "top")

    search_payload = {
        "schema_version": "pass3_3_1_startpoint_v3",
        "top_module": "top_mod",
        "instruction": "ADD",
        "start_module": "pcgen_mod",
        "start_instance": "x_pcgen",
        "start_block": "PCGEN_ENTRY",
        "key_register": "pcgen_reg",
        "key_register_line_range": {"start_line": 1, "end_line": 2},
        "confidence": "high",
        "unknown": "",
    }
    orchestrate_items = [
        {
            "module": "pcgen_mod",
            "instance": "x_pcgen",
            "path": "top/x_pcgen",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "tracks pcgen sequential fetch",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    artifacts_dir = tmp_path / "chip" / "instrack" / slug / "artifacts"
    error_log = artifacts_dir / "top__x_pcgen.apv.llm_error.md"
    index_payload = json.loads((artifacts_dir / "apv_index.json").read_text(encoding="utf-8"))

    assert error_log.exists()
    error_text = error_log.read_text(encoding="utf-8")
    assert "## Raw Error" in error_text
    assert "configured_max_tool_rounds=32" in error_text
    assert index_payload["items"][0]["artifact_llm_error"].endswith(".apv.llm_error.md")


def test_run_pass3_3_marks_partial_when_dep_ref_name_is_not_visible(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_dep","dep_name":"missing","task_name":"bad dep","condition_lines":["$dep.missing.fetch_vld && local_vld"],"capture_signals":["local_vld"],"logging_lines":["should fail"],"match_mode":"first","max_match":1}],"unknown":[]}
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

    assert 'name: "bad dep"' in yaml_text
    assert apv_index["items"][0]["status"] == "partial"
    assert apv_index["items"][0]["task_count"] == 1
    assert any("unknown dep `ref_name=missing`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_propagates_handoff_hint_into_visible_upstream_candidates(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","behavior_hint":"IFU entry remains on the fetch family, but downstream should keep slot ambiguity in mind.","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld && !flush"],"capture_signals":["fetch_vld"],"logging_lines":["ifu accepts instruction"],"handoff_hint":"This leaf confirms fetch-family progress only; downstream must not assume slot identity yet.","match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","dep_name":"s00_t00_x_ifu","task_name":"decode accept","condition_lines":["$dep.s00_t00_x_ifu.fetch_vld == dispatch_vld_i","dispatch_vld_i && decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["decode accepted"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("fetch_vld", "flush"),
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

    assert apv_index["items"][0]["behavior_hint"] == "IFU entry remains on the fetch family, but downstream should keep slot ambiguity in mind."
    assert len(apv_index["items"][0]["leaf_contexts"]) == 1
    leaf_context = apv_index["items"][0]["leaf_contexts"][0]
    assert leaf_context["task_id"] == "s00_t00_x_ifu"
    assert leaf_context["ref_name"] == "ifu_entry"
    assert leaf_context["capture_names"] == ["fetch_vld"]
    assert leaf_context["branch_lineage"] == ["ifu_entry"]
    assert leaf_context["upstream_hint"] == "This leaf confirms fetch-family progress only; downstream must not assume slot identity yet."
    assert leaf_context["anchor_kinds"] == ["identity"]
    assert leaf_context["anchor_capture_names"] == ["fetch_vld"]
    assert '"upstream_hint": "This leaf confirms fetch-family progress only; downstream must not assume slot identity yet."' in llm.calls[1]["prompt"]


def test_run_pass3_3_leaf_hint_falls_back_to_behavior_hint(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","behavior_hint":"Bypass family is resolved here, but downstream still needs local evidence before selecting a concrete slot.","tasks":[{"ref_name":"bypass_leaf","dep_name":"","task_name":"bypass leaf","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["bypass observed"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={"ifu_mod": _structured_topology_block("fetch_vld")},
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
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "tracks bypass family",
                "confidence": "high",
                "unknown": "",
            },
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["behavior_hint"] == "Bypass family is resolved here, but downstream still needs local evidence before selecting a concrete slot."
    assert apv_index["items"][0]["leaf_contexts"][0]["upstream_hint"] == (
        "Bypass family is resolved here, but downstream still needs local evidence before selecting a concrete slot."
    )


def test_run_pass3_3_keeps_valid_tasks_when_one_task_is_invalid(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_first","dep_name":"","task_name":"bad first","condition_lines":["local_vld"],"capture_signals":["local_vld"],"logging_lines":["bad"],"match_mode":"bogus","max_match":1},{"ref_name":"good_second","dep_name":"","task_name":"good second","condition_lines":["local_vld && ready"],"capture_signals":["done_vld"],"logging_lines":["good"],"match_mode":"first","max_match":1}],"unknown":[]}
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
    assert apv_index["items"][0]["task_count"] == 2
    assert any("unsupported `match_mode=bogus`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_normalizes_assignment_conditions_and_sv_literals(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"valid_flag","dep_name":"","task_name":"valid flag","condition_lines":["flag = 1'b1"],"capture_signals":["flag"],"logging_lines":["flag asserted"],"match_mode":"condition","max_match":1},{"ref_name":"consume_flag","dep_name":"valid_flag","task_name":"consume flag","condition_lines":["$dep.valid_flag.flag = local_vld && ready"],"capture_signals":["done_vld"],"logging_lines":["flag consumed"],"match_mode":"condition","max_match":1}],"unknown":[]}
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
{"status":"complete","tasks":[{"ref_name":"bad_multi_dep","dep_name":"left","task_name":"bad multi dep","condition_lines":["$dep.left.fetch_vld && $dep.right.dispatch_vld"],"capture_signals":["done_vld"],"logging_lines":["bad"],"match_mode":"first","max_match":1}],"unknown":[]}
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


def test_run_pass3_3_accepts_visible_task_id_dep_reference(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"task_id_dep","dep_name":"s00_t00_x_ifu","task_name":"task id dep","condition_lines":["$dep.s00_t00_x_ifu.fetch_vld == dispatch_vld_i","dispatch_vld_i && decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["task id dep is valid"],"match_mode":"first","max_match":1}],"unknown":[]}
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
                "boundary_handoffs": [{"output_port": "dispatch_vld_o", "value_condition": "dispatch_vld_o = fetch_vld", "behavior": "forward"}],
                "_boundary_handoff_routes": [{"status": "resolved", "output_port": "dispatch_vld_o"}],
                "lifecycle_context": "first local fetch module",
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
        }
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))

    assert apv_index["items"][0]["status"] == "complete"
    assert apv_index["items"][1]["status"] == "complete"


def test_run_pass3_3_rejects_unknown_dep_capture_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"start ok","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"bad_capture","dep_name":"s00_t00_x_ifu","task_name":"bad capture","condition_lines":["$dep.s00_t00_x_ifu.missing_sig && ready"],"capture_signals":["dispatch_vld"],"logging_lines":["bad capture"],"match_mode":"first","max_match":1}],"unknown":[]}
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
{"status":"complete","tasks":[{"ref_name":"dup_ref","dep_name":"","task_name":"first task","condition_lines":["local_vld"],"capture_signals":["local_vld"],"logging_lines":["first"],"match_mode":"first","max_match":1},{"ref_name":"dup_ref","dep_name":"","task_name":"second task","condition_lines":["ready"],"capture_signals":["done_vld"],"logging_lines":["second"],"match_mode":"first","max_match":1}],"unknown":[]}
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
    assert apv_index["items"][0]["task_count"] == 2
    assert any("duplicate `ref_name=dup_ref`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_forward_dep_ref_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","dep_name":"issue_fire","task_name":"decode accept","condition_lines":["$dep.issue_fire.issue_pkt_vld && decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["bad forward ref"],"match_mode":"first","max_match":1},{"ref_name":"issue_fire","dep_name":"","task_name":"issue fire","condition_lines":["issue_en"],"capture_signals":["issue_pkt_vld"],"logging_lines":["later task"],"match_mode":"first","max_match":1}],"unknown":[]}
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
{"status":"complete","tasks":[{"ref_name":"bad_local","dep_name":"","task_name":"bad local","condition_lines":["ghost_sig == real_vld"],"capture_signals":["real_vld"],"logging_lines":["bad local"],"match_mode":"first","max_match":1}],"unknown":[]}
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
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["ifu accepts instruction"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","dep_name":"s00_t00_x_ifu","task_name":"decode accept","condition_lines":["$dep.s00_t00_x_ifu.fetch_vld","dispatch_vld_i","decode_ready"],"capture_signals":["dispatch_vld"],"logging_lines":["weak continuity"],"match_mode":"first","max_match":1}],"unknown":[]}
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
    assert any("same-line `identity` relation" in reason for reason in apv_index["items"][1]["unknown"])


def test_run_pass3_3_allows_sibling_branch_tasks_sharing_same_parent_ref_name(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"partial","tasks":[{"ref_name":"dispatch_entry","dep_name":"","task_name":"dispatch entry","condition_lines":["entry_vld"],"capture_signals":["entry_vld"],"logging_lines":["dispatch entry"],"match_mode":"first","max_match":1},{"ref_name":"to_buf0","dep_name":"dispatch_entry","task_name":"route to buf0","condition_lines":["$dep.dispatch_entry.entry_vld == buf0_fire","buf_sel == 2'b00"],"capture_signals":["buf0_fire"],"logging_lines":["buf0 path"],"match_mode":"first","max_match":1},{"ref_name":"to_buf1","dep_name":"dispatch_entry","task_name":"route to buf1","condition_lines":["$dep.dispatch_entry.entry_vld == buf1_fire","buf_sel == 2'b01"],"capture_signals":["buf1_fire"],"logging_lines":["buf1 path"],"match_mode":"first","max_match":1}],"unknown":[]}
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
    assert len(apv_index["items"][0]["leaf_contexts"]) == 2
    assert apv_index["items"][0]["leaf_task_id"] == ""
    assert apv_index["items"][0]["leaf_ref_name"] == ""
    assert apv_index["items"][0]["leaf_capture_names"] == []
    assert not any("duplicate `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])
    assert not any("forward-references" in reason for reason in apv_index["items"][0]["unknown"])
    assert not any("unknown dep `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])
    assert not any("at most one unique dep `ref_name`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_keeps_complete_multi_leaf_item_when_terminal_branch_lineages_are_auto_generated(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"dispatch_entry","dep_name":"","task_name":"dispatch entry","condition_lines":["entry_vld"],"capture_signals":["entry_vld"],"logging_lines":["dispatch entry"],"match_mode":"first","max_match":1},{"ref_name":"to_buf0","dep_name":"dispatch_entry","task_name":"route to buf0","condition_lines":["$dep.dispatch_entry.entry_vld == buf0_fire","buf_sel == 2'b00"],"capture_signals":["buf0_fire"],"logging_lines":["buf0 path"],"match_mode":"first","max_match":1},{"ref_name":"to_buf1","dep_name":"dispatch_entry","task_name":"route to buf1","condition_lines":["$dep.dispatch_entry.entry_vld == buf1_fire","buf_sel == 2'b01"],"capture_signals":["buf1_fire"],"logging_lines":["buf1 path"],"match_mode":"first","max_match":1}],"unknown":[]}
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

    assert apv_index["items"][0]["status"] == "complete"
    assert apv_index["items"][0]["task_count"] == 3
    assert apv_index["items"][0]["leaf_task_id"] == ""
    assert apv_index["items"][0]["leaf_ref_name"] == ""
    assert apv_index["items"][0]["leaf_capture_names"] == []
    assert len(apv_index["items"][0]["leaf_contexts"]) == 2
    buf0_leaf, buf1_leaf = apv_index["items"][0]["leaf_contexts"]
    assert buf0_leaf["task_id"] == "s00_t01_x_idu"
    assert buf0_leaf["ref_name"] == "to_buf0"
    assert buf0_leaf["capture_names"] == ["buf0_fire"]
    assert buf0_leaf["branch_lineage"] == ["dispatch_entry", "to_buf0"]
    assert buf0_leaf["anchor_kinds"] == ["path"]
    assert buf0_leaf["anchor_capture_names"] == ["buf0_fire"]
    assert buf1_leaf["task_id"] == "s00_t02_x_idu"
    assert buf1_leaf["ref_name"] == "to_buf1"
    assert buf1_leaf["capture_names"] == ["buf1_fire"]
    assert buf1_leaf["branch_lineage"] == ["dispatch_entry", "to_buf1"]
    assert buf1_leaf["anchor_kinds"] == ["path"]
    assert buf1_leaf["anchor_capture_names"] == ["buf1_fire"]
    assert apv_index["items"][0]["unknown"] == []


def test_run_pass3_3_keeps_complete_multi_leaf_item_without_raw_branch_metadata(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"dispatch_entry","dep_name":"","task_name":"dispatch entry","condition_lines":["entry_vld"],"capture_signals":["entry_vld"],"logging_lines":["dispatch entry"],"match_mode":"first","max_match":1},{"ref_name":"to_buf0","dep_name":"dispatch_entry","task_name":"route to buf0","condition_lines":["$dep.dispatch_entry.entry_vld == buf0_fire","buf_sel == 2'b00"],"capture_signals":["buf0_fire"],"logging_lines":["buf0 path"],"match_mode":"first","max_match":1},{"ref_name":"to_buf1","dep_name":"dispatch_entry","task_name":"route to buf1","condition_lines":["$dep.dispatch_entry.entry_vld == buf1_fire","buf_sel == 2'b01"],"capture_signals":["buf1_fire"],"logging_lines":["buf1 path"],"match_mode":"first","max_match":1}],"unknown":[]}
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

    assert apv_index["items"][0]["status"] == "complete"
    assert len(apv_index["items"][0]["leaf_contexts"]) == 2
    assert apv_index["items"][0]["leaf_task_id"] == ""
    assert apv_index["items"][0]["leaf_ref_name"] == ""
    assert apv_index["items"][0]["leaf_capture_names"] == []
    assert apv_index["items"][0]["unknown"] == []


def test_run_pass3_3_exposes_all_visible_upstream_leaf_context_candidates_in_prompt(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"slot0_leaf","dep_name":"","task_name":"slot0 leaf","condition_lines":["slot0_vld"],"capture_signals":["slot0_vld"],"logging_lines":["slot0"],"match_mode":"first","max_match":1},{"ref_name":"slot1_leaf","dep_name":"","task_name":"slot1 leaf","condition_lines":["slot1_vld"],"capture_signals":["slot1_vld"],"logging_lines":["slot1"],"match_mode":"first","max_match":1},{"ref_name":"slot2_leaf","dep_name":"","task_name":"slot2 leaf","condition_lines":["slot2_vld"],"capture_signals":["slot2_vld"],"logging_lines":["slot2"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"partial","tasks":[],"unknown":["no local continuation yet"]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("slot0_vld", "slot1_vld", "slot2_vld"),
            "idu_mod": _structured_topology_block("dispatch_vld_i", "issue_vld"),
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
                "boundary_handoffs": [
                    {"output_port": "slot0_vld_o", "value_condition": "slot0_vld_o", "behavior": "route slot0"},
                    {"output_port": "slot1_vld_o", "value_condition": "slot1_vld_o", "behavior": "route slot1"},
                    {"output_port": "slot2_vld_o", "value_condition": "slot2_vld_o", "behavior": "route slot2"},
                ],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "fanout to three terminal slots",
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
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept visible leaf"}],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "consumes one selected slot",
                "confidence": "medium",
                "unknown": "",
            },
        },
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    assert len(llm.calls) == 2
    prompt_text = llm.calls[1]["prompt"]
    assert prompt_text.count('"task_id": "') == 3
    assert '"ref_name": "s00_t00_x_ifu"' in prompt_text
    assert '"ref_name": "s00_t01_x_ifu"' in prompt_text
    assert '"ref_name": "s00_t02_x_ifu"' in prompt_text
    assert '"local_ref_name": "slot0_leaf"' in prompt_text
    assert '"local_ref_name": "slot1_leaf"' in prompt_text
    assert '"local_ref_name": "slot2_leaf"' in prompt_text
    assert '"branch_lineage": [' in prompt_text
    assert '"total_candidates": 3' in prompt_text


def test_run_pass3_3_marks_partial_when_dep_name_does_not_match_selected_candidate(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"slot0_leaf","dep_name":"","task_name":"slot0 leaf","condition_lines":["slot0_vld"],"capture_signals":["slot0_vld"],"logging_lines":["slot0"],"match_mode":"first","max_match":1},{"ref_name":"slot1_leaf","dep_name":"","task_name":"slot1 leaf","condition_lines":["slot1_vld"],"capture_signals":["slot1_vld"],"logging_lines":["slot1"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","dep_name":"slot1_leaf","task_name":"decode accept","condition_lines":["$dep.slot0_leaf.slot0_vld == dispatch_vld_i","dispatch_vld_i"],"capture_signals":["dispatch_vld_i"],"logging_lines":["bad dep pick"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ]
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("slot0_vld", "slot1_vld"),
            "idu_mod": _structured_topology_block("dispatch_vld_i"),
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
                "boundary_handoffs": [
                    {"output_port": "slot0_vld_o", "value_condition": "slot0_vld_o", "behavior": "route slot0"},
                    {"output_port": "slot1_vld_o", "value_condition": "slot1_vld_o", "behavior": "route slot1"},
                ],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "fanout to two slots",
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
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept selected slot"}],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "selects one upstream slot",
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
    assert any("declares `dep_name=slot1_leaf` but its `condition_lines` reference `$dep.slot0_leaf.*`" in reason for reason in apv_index["items"][1]["unknown"])


def test_run_pass3_3_rejects_missing_anchors(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ],
        auto_anchorize=False,
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={"ifu_mod": _structured_topology_block("fetch_vld")},
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
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "fetch stage",
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
    assert any("missing non-empty `anchors`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_empty_anchor_reason(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"anchors":[{"kind":"identity","dep_signals":[],"local_signals":["fetch_vld"],"reason":""}],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ],
        auto_anchorize=False,
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={"ifu_mod": _structured_topology_block("fetch_vld")},
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
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "fetch stage",
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
    assert any("missing a non-empty `reason`" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_root_anchor_dep_signals(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"anchors":[{"kind":"identity","dep_signals":["$dep.prev.fetch_vld"],"local_signals":["fetch_vld"],"reason":"This identity anchor tries to use upstream history to keep the same instruction."}],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ],
        auto_anchorize=False,
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={"ifu_mod": _structured_topology_block("fetch_vld")},
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
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "fetch stage",
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
    assert any("must not declare `dep_signals` on a root task" in reason for reason in apv_index["items"][0]["unknown"])


def test_run_pass3_3_rejects_dependent_task_without_anchor_dep_signals(tmp_path):
    llm = FakeLLM(
        [
            """```json
{"status":"complete","tasks":[{"ref_name":"ifu_entry","dep_name":"","task_name":"ifu entry","condition_lines":["fetch_vld"],"capture_signals":["fetch_vld"],"anchors":[{"kind":"identity","dep_signals":[],"local_signals":["fetch_vld"],"reason":"This identity anchor keeps fetch_vld as the local instruction identifier for ifu entry."}],"logging_lines":["start"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
            """```json
{"status":"complete","tasks":[{"ref_name":"decode_accept","dep_name":"s00_t00_x_ifu","task_name":"decode accept","condition_lines":["$dep.s00_t00_x_ifu.fetch_vld == dispatch_vld_i","dispatch_vld_i"],"capture_signals":["dispatch_vld_i"],"anchors":[{"kind":"identity","dep_signals":[],"local_signals":["dispatch_vld_i"],"reason":"This identity anchor claims decode_accept keeps the same instruction at dispatch_vld_i."}],"logging_lines":["decode"],"match_mode":"first","max_match":1}],"unknown":[]}
```""",
        ],
        auto_anchorize=False,
    )
    generator = _build_generator(
        tmp_path,
        llm,
        topology_blocks={
            "ifu_mod": _structured_topology_block("fetch_vld"),
            "idu_mod": _structured_topology_block("dispatch_vld_i"),
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
                "boundary_handoffs": [{"output_port": "dispatch_vld_o", "value_condition": "dispatch_vld_o = fetch_vld", "behavior": "forward"}],
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
                "boundary_takeover": [{"input_port": "dispatch_vld_i", "value_condition": "dispatch_vld_i", "behavior": "accept"}],
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
    assert any(
        "dependent tasks must include both `dep_signals` and `local_signals`" in reason
        or "none of its anchors declare any `dep_signals`" in reason
        for reason in apv_index["items"][1]["unknown"]
    )


def test_run_pass3_3_fails_instruction_when_route_contains_duplicate_item_path(tmp_path):
    llm = FakeLLM([])
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
            "path": "top/x_dup",
            "orchestrator_role": "start_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "first duplicate path",
                "confidence": "high",
                "unknown": "",
            },
        },
        {
            "module": "idu_mod",
            "instance": "x_idu",
            "path": "top/x_dup",
            "orchestrator_role": "parent_module",
            "orchestration": {
                "boundary_takeover": [],
                "boundary_handoffs": [],
                "_boundary_handoff_routes": [],
                "lifecycle_context": "second duplicate path",
                "confidence": "high",
                "unknown": "",
            },
        },
    ]
    _prime_cached_search_and_orchestrate(generator, top_node, "ADD", search_payload, orchestrate_items)

    asyncio.run(generator.run_pass3_3(top_node))

    slug = generator._safe_slug("ADD").lower()
    apv_index = json.loads((tmp_path / "chip" / "instrack" / slug / "artifacts" / "apv_index.json").read_text(encoding="utf-8"))
    summary_json = json.loads((tmp_path / "chip" / "instrack" / slug / "add.json").read_text(encoding="utf-8"))
    summary_md = (tmp_path / "chip" / "instrack" / slug / "add.md").read_text(encoding="utf-8")

    assert llm.calls == []
    assert apv_index["status"] == "failed"
    assert apv_index["items"] == []
    assert "Duplicate `item.path` detected" in apv_index["error"]
    assert summary_json["apv_status"] == "failed"
    assert "Duplicate `item.path` detected" in summary_json["apv_error"]
    assert "## APV Status" in summary_md
