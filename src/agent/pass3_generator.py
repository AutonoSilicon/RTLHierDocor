"""Pass3 generation module.

This module isolates chip-level (pass3) orchestration from the main
doc generator to keep responsibilities focused and files maintainable.
"""

import json
import os
import re
import hashlib
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from .prompts import (
    PASS3_1_SYSTEM,
    PASS3_1_PROMPT,
    PASS3_2_SYSTEM,
    PASS3_2_PROMPT,
    PASS3_3_1_SEARCH_SYSTEM,
    PASS3_3_1_SEARCH_PROMPT,
    PASS3_3_2_LIFECYCLE_SYSTEM,
    PASS3_3_2_LIFECYCLE_PROMPT,
)


class Pass3Generator:
    """Encapsulates all pass3 logic and helper tools."""

    def __init__(self, owner: Any):
        self.owner = owner
        self._subagent_cache: Dict[str, str] = {}
        self._agent_log_seq: int = 0

    def clear_progress(self):
        self.owner.project_tracker.clear()

    def _project_log_path(self, name: str) -> str:
        project_debug_dir = self.owner.chip_debug_dir
        project_debug_dir.mkdir(parents=True, exist_ok=True)
        return str(project_debug_dir / f"debug_{name}.md")

    def _next_agent_log_seq(self) -> int:
        self._agent_log_seq += 1
        return self._agent_log_seq

    def _build_pass3_3_1_agent_log_path(
        self,
        *,
        instruction: str,
        node_path: str,
        level: int,
        role: str,
    ) -> str:
        """Create a unique debug log path for one pass3.3.1 agent invocation."""
        seq = self._next_agent_log_seq()
        inst_slug = self._safe_slug(instruction or "unknown")
        node_slug = self._safe_slug((node_path or "top").replace("/", "__"))
        role_slug = self._safe_slug(role or "agent")
        return self._project_log_path(
            f"pass3_3_1_{role_slug}_{inst_slug}_{node_slug}_L{level}_R{seq:04d}"
        )

    def _fork_trace_log_path(self) -> Path:
        log_dir = self.owner.chip_debug_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "debug_pass3_fork_trace.jsonl"

    def _fork_trace_log_path_pass3_3_1(self) -> Path:
        log_dir = self.owner.chip_debug_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "debug_pass3_3_1_fork_trace.jsonl"

    def _append_fork_trace(self, event: str, payload: Dict[str, Any]):
        """Append one fork-related trace record as JSONL."""
        trace_path = self._fork_trace_log_path()
        record = {
            "ts": datetime.datetime.now().isoformat(),
            "event": event,
        }
        record.update(payload or {})
        try:
            with open(trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            # Keep a dedicated pass3.3.1 trace stream for easier triage.
            pass_name = str((payload or {}).get("pass") or "")
            prompt_style = str((payload or {}).get("prompt_style") or "")
            is_pass3_3_1 = pass_name == "pass3_3_1" or prompt_style == "instrack_search"
            if is_pass3_3_1:
                trace_331 = self._fork_trace_log_path_pass3_3_1()
                with open(trace_331, "a", encoding="utf-8") as f331:
                    f331.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # Debug log failure should not block the generation flow.
            pass

    def _append_recursive_context_log(
        self,
        log_path: str,
        *,
        top_node: Any,
        current_node: Any,
        level: int,
        prompt_style: str,
        task: str,
        cache_key: str,
        child_overview: str,
    ):
        """Append deterministic recursive call context to per-call debug file."""
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write("## Recursive Context\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"- top_module: {top_node.module_name}\n")
                f.write(f"- node: {current_node.instance_name} ({current_node.module_name})\n")
                f.write(f"- level: {level}\n")
                f.write(f"- prompt_style: {prompt_style}\n")
                f.write(f"- task: {task}\n")
                f.write(f"- cache_key: {cache_key}\n")
                f.write("\n### Direct children snapshot\n")
                f.write((child_overview or "- 无子模块") + "\n")
        except Exception:
            # Debug log failure should not block generation.
            pass

    def _append_agent_io_snapshot(
        self,
        log_path: str,
        *,
        stage: str,
        system: str,
        prompt: str,
        output: str = "",
        error: str = "",
    ):
        """Append explicit agent input/output snapshot for deterministic debugging."""
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n\n" + "=" * 80 + "\n")
                f.write(f"## Pass3.3.1 Agent {stage}\n")
                f.write("=" * 80 + "\n\n")
                if stage.lower() == "input":
                    f.write("### System\n")
                    f.write((system or "") + "\n\n")
                    f.write("### Prompt\n")
                    f.write((prompt or "") + "\n")
                else:
                    if error:
                        f.write("### Error\n")
                        f.write(error + "\n\n")
                    f.write("### Output\n")
                    f.write((output or "") + "\n")
        except Exception:
            # Debug log failure should not block generation.
            pass

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.md5((text or "").encode("utf-8")).hexdigest()

    def _build_instance_path_index(self, top_node: Any) -> Dict[str, Any]:
        index: Dict[str, Any] = {}

        def walk(node: Any, path: str):
            index[path] = node
            for child in node.children.values():
                child_path = f"{path}/{child.instance_name}" if path else child.instance_name
                walk(child, child_path)

        walk(top_node, "")
        return index

    def _build_pass3_1_input_hash(self, top_module: str, top_description: str) -> str:
        payload = {
            "version": "pass3_1_cache_v2",
            "top_module": top_module,
            "system_prompt": PASS3_1_SYSTEM,
            "prompt_template": PASS3_1_PROMPT,
            "top_description_hash": self._hash_text(top_description),
            "tools_schema": self._pass3_1_tools(),
        }
        return self._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _build_pass3_2_input_hash(self, top_module: str, top_description: str, subsystem_partition: str) -> str:
        payload = {
            "version": "pass3_2_cache_v1",
            "top_module": top_module,
            "system_prompt": PASS3_2_SYSTEM,
            "prompt_template": PASS3_2_PROMPT,
            "top_description_hash": self._hash_text(top_description),
            "subsystem_partition_hash": self._hash_text(subsystem_partition),
            "tools_schema": self._pass3_2_tools(),
        }
        return self._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _build_pass3_3_search_input_hash(
        self,
        top_module: str,
        instruction: str,
        top_description: str,
        core_partition: str,
        instruction_datasheet: str,
    ) -> str:
        payload = {
            "version": "pass3_3_instrack_search_cache_v3",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_1_SEARCH_SYSTEM,
            "prompt_template": PASS3_3_1_SEARCH_PROMPT,
            "top_description_hash": self._hash_text(top_description),
            "core_partition_hash": self._hash_text(core_partition),
            "instruction_datasheet_hash": self._hash_text(instruction_datasheet),
            "tools_schema": self._pass3_3_1_tools(),
        }
        return self._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _build_pass3_3_lifecycle_input_hash(
        self,
        top_module: str,
        instruction: str,
        top_description: str,
        instruction_datasheet: str,
        topology_context: str,
        search_result_json_text: str,
    ) -> str:
        payload = {
            "version": "pass3_3_instrack_lifecycle_cache_v1",
            "top_module": top_module,
            "instruction": instruction,
            "system_prompt": PASS3_3_2_LIFECYCLE_SYSTEM,
            "prompt_template": PASS3_3_2_LIFECYCLE_PROMPT,
            "top_description_hash": self._hash_text(top_description),
            "instruction_datasheet_hash": self._hash_text(instruction_datasheet),
            "topology_context_hash": self._hash_text(topology_context),
            "search_result_json_hash": self._hash_text(search_result_json_text),
            "tools_schema": self._pass3_3_2_tools(),
        }
        return self._hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _instrack_datasheet_path() -> Path:
        return Path.cwd() / "docs" / "rv64gc_instruction_datasheet.md"

    def _load_instrack_datasheet(self) -> str:
        path = self._instrack_datasheet_path()
        if not path.exists():
            return "未找到 datasheet 文件：docs/rv64gc_instruction_datasheet.md"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"读取 datasheet 失败: {e}"

    def _extract_instruction_datasheet_excerpt(self, datasheet_text: str, instruction: str) -> str:
        text = (datasheet_text or "").strip()
        if not text:
            return "datasheet 为空，无法提供指令文段。"

        target = self._normalize_instruction(instruction)
        lines = text.splitlines()

        heading_indices: List[int] = []
        for i, line in enumerate(lines):
            if line.startswith("### "):
                heading_indices.append(i)

        def _heading_to_mnemonic(heading_line: str) -> str:
            # Example: "### ADD rd, rs1, rs2" => "ADD"
            title = heading_line[4:].strip()
            token = title.split(" ", 1)[0]
            token = token.split(",", 1)[0]
            return self._normalize_instruction(token)

        chosen_start = -1
        chosen_end = -1
        for idx, start in enumerate(heading_indices):
            mnemonic = _heading_to_mnemonic(lines[start])
            if mnemonic != target:
                continue
            end = heading_indices[idx + 1] if idx + 1 < len(heading_indices) else len(lines)
            chosen_start = start
            chosen_end = end
            break

        if chosen_start < 0:
            print(
                f"[WARN] InStrack datasheet section not found for instruction '{instruction}' "
                "in docs/rv64gc_instruction_datasheet.md"
            )
            return (
                "Instruction section not found in docs/rv64gc_instruction_datasheet.md. "
                f"instruction={instruction}"
            )

        block = "\n".join(lines[chosen_start:chosen_end]).strip()
        return self.owner._extract_summary(block, max_lines=120, max_chars=12000)

    @staticmethod
    def _instruction_profiles() -> Dict[str, List[str]]:
        rv64i = [
            "LUI", "AUIPC", "JAL", "JALR", "BEQ", "BNE", "BLT", "BGE", "BLTU", "BGEU",
            "LB", "LH", "LW", "LBU", "LHU", "LWU", "LD",
            "SB", "SH", "SW", "SD",
            "ADDI", "SLTI", "SLTIU", "XORI", "ORI", "ANDI", "SLLI", "SRLI", "SRAI",
            "ADDIW", "SLLIW", "SRLIW", "SRAIW",
            "ADD", "SUB", "SLL", "SLT", "SLTU", "XOR", "SRL", "SRA", "OR", "AND",
            "ADDW", "SUBW", "SLLW", "SRLW", "SRAW",
            "FENCE", "ECALL", "EBREAK",
        ]
        zifencei = ["FENCE.I"]
        zicsr = ["CSRRW", "CSRRS", "CSRRC", "CSRRWI", "CSRRSI", "CSRRCI"]
        rv64m = [
            "MUL", "MULH", "MULHSU", "MULHU", "DIV", "DIVU", "REM", "REMU",
            "MULW", "DIVW", "DIVUW", "REMW", "REMUW",
        ]
        rv64a = [
            "LR.W", "SC.W", "AMOSWAP.W", "AMOADD.W", "AMOXOR.W", "AMOAND.W", "AMOOR.W", "AMOMIN.W", "AMOMAX.W", "AMOMINU.W", "AMOMAXU.W",
            "LR.D", "SC.D", "AMOSWAP.D", "AMOADD.D", "AMOXOR.D", "AMOAND.D", "AMOOR.D", "AMOMIN.D", "AMOMAX.D", "AMOMINU.D", "AMOMAXU.D",
        ]
        rv64f = [
            "FLW", "FSW",
            "FMADD.S", "FMSUB.S", "FNMSUB.S", "FNMADD.S",
            "FADD.S", "FSUB.S", "FMUL.S", "FDIV.S", "FSQRT.S",
            "FSGNJ.S", "FSGNJN.S", "FSGNJX.S", "FMIN.S", "FMAX.S",
            "FCVT.W.S", "FCVT.WU.S", "FCVT.L.S", "FCVT.LU.S",
            "FCVT.S.W", "FCVT.S.WU", "FCVT.S.L", "FCVT.S.LU",
            "FMV.X.W", "FMV.W.X",
            "FEQ.S", "FLT.S", "FLE.S", "FCLASS.S",
        ]
        rv64d = [
            "FLD", "FSD",
            "FMADD.D", "FMSUB.D", "FNMSUB.D", "FNMADD.D",
            "FADD.D", "FSUB.D", "FMUL.D", "FDIV.D", "FSQRT.D",
            "FSGNJ.D", "FSGNJN.D", "FSGNJX.D", "FMIN.D", "FMAX.D",
            "FCVT.W.D", "FCVT.WU.D", "FCVT.L.D", "FCVT.LU.D",
            "FCVT.D.W", "FCVT.D.WU", "FCVT.D.L", "FCVT.D.LU",
            "FCVT.S.D", "FCVT.D.S",
            "FMV.X.D", "FMV.D.X",
            "FEQ.D", "FLT.D", "FLE.D", "FCLASS.D",
        ]
        rv64c = [
            "C.ADDI4SPN",
            "C.FLD", "C.LW", "C.LD", "C.FSD", "C.SW", "C.SD",
            "C.NOP", "C.ADDI", "C.ADDIW", "C.LI", "C.ADDI16SP", "C.LUI",
            "C.SRLI", "C.SRAI", "C.ANDI", "C.SUB", "C.XOR", "C.OR", "C.AND", "C.SUBW", "C.ADDW",
            "C.J", "C.BEQZ", "C.BNEZ",
            "C.SLLI", "C.FLDSP", "C.LWSP", "C.LDSP", "C.JR", "C.MV", "C.EBREAK", "C.JALR", "C.ADD",
            "C.FSDSP", "C.SWSP", "C.SDSP",
        ]
        return {
            "rv64i": rv64i + zifencei + zicsr,
            "rv64gc": rv64i + zifencei + zicsr + rv64m + rv64a + rv64f + rv64d + rv64c,
            # c910 default profile keeps common integer/system instructions for phase-1 throughput.
            "c910": rv64i + zifencei + zicsr + rv64m + rv64a + ["MRET", "SRET", "WFI"],
        }

    @staticmethod
    def _normalize_instruction(inst: str) -> str:
        text = (inst or "").strip().upper()
        return re.sub(r"\s+", "", text)

    def _resolve_instrack_instructions(self) -> List[str]:
        single = self._normalize_instruction(getattr(self.owner, "instrack_single_instruction", "") or "")
        if single:
            return [single]

        configured = [
            self._normalize_instruction(i)
            for i in (getattr(self.owner, "isa_instructions", []) or [])
            if self._normalize_instruction(i)
        ]
        if configured:
            # Keep insertion order while de-duplicating.
            seen = set()
            ordered: List[str] = []
            for inst in configured:
                if inst in seen:
                    continue
                seen.add(inst)
                ordered.append(inst)
            return ordered

        profile = str(getattr(self.owner, "isa_profile", "c910") or "c910").strip().lower()
        profiles = self._instruction_profiles()
        base = profiles.get(profile, profiles["c910"])
        return [self._normalize_instruction(i) for i in base if self._normalize_instruction(i)]

    @staticmethod
    def _safe_slug(text: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text or "")
        safe = safe.strip("_")
        return safe or "unknown"

    @staticmethod
    def _encode_rel_path(rel_path: str) -> str:
        segments = rel_path.replace("\\", "/").split("/")
        return "/".join(quote(seg) for seg in segments)

    @staticmethod
    def _parse_roots_cell_tokens(roots_cell: str) -> List[str]:
        text = (roots_cell or "").strip()
        if not text:
            return []

        text = text.replace("`", "")
        text = text.replace("\n", ";")
        for sep in ["，", "、", "；", "|"]:
            text = text.replace(sep, ",")

        raw_tokens: List[str] = []
        for chunk in text.split(","):
            token = chunk.strip()
            if not token:
                continue
            if "(" in token and token.endswith(")"):
                token = token.split("(", 1)[0].strip()
            raw_tokens.append(token)

        seen = set()
        tokens: List[str] = []
        for token in raw_tokens:
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        return tokens

    def _resolve_root_token_to_paths(self, token: str, path_index: Dict[str, Any]) -> List[str]:
        cleaned = (token or "").strip().strip("/")
        if not cleaned:
            return []

        if cleaned in ("<top>", "top", "TOP"):
            return [""]

        if cleaned in path_index:
            return [cleaned]

        by_instance = [
            p for p, n in path_index.items()
            if p and getattr(n, "instance_name", "") == cleaned
        ]
        if by_instance:
            by_instance.sort(key=lambda p: (p.count('/'), p))
            return by_instance[:1]

        by_module = [
            p for p, n in path_index.items()
            if p and getattr(n, "module_name", "") == cleaned
        ]
        if by_module:
            by_module.sort(key=lambda p: (p.count('/'), p))
            return by_module[:2]

        return []

    def _extract_pass3_1_partition_json(self, markdown: str, top_node: Any) -> Optional[Dict[str, Any]]:
        content = (markdown or "").strip()
        if not content:
            return None

        table_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]
        if len(table_lines) < 3:
            return None

        header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]

        def _norm_header(text: str) -> str:
            t = (text or "").strip().lower()
            # Normalize spaces and punctuation style for robust matching.
            t = t.replace("（", "(").replace("）", ")")
            t = re.sub(r"\s+", "", t)
            return t

        header_map = {cell: idx for idx, cell in enumerate(header_cells)}
        header_map_norm = {_norm_header(cell): idx for idx, cell in enumerate(header_cells)}

        idx_name = header_map.get("子系统", 0)
        idx_roots = header_map.get("Roots(root modules)", 1)
        idx_intent = header_map.get("职责/边界(intent)", 2)
        idx_sw_visible = header_map.get("软件可见面(sw visible)", 3)
        idx_evidence = header_map.get("证据(readDoc 摘要)", 4)
        idx_unknown = header_map.get("未知/待确认", 5)

        path_index = self._build_instance_path_index(top_node)
        subsystems: List[Dict[str, Any]] = []

        for row_idx, line in enumerate(table_lines[2:], start=1):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 2:
                continue

            name = cells[idx_name] if idx_name < len(cells) else ""
            roots_cell = cells[idx_roots] if idx_roots < len(cells) else ""
            if not name or not roots_cell:
                continue

            tokens = self._parse_roots_cell_tokens(roots_cell)
            root_paths: List[str] = []
            for token in tokens:
                root_paths.extend(self._resolve_root_token_to_paths(token, path_index))

            dedup_paths: List[str] = []
            seen_paths = set()
            for p in root_paths:
                if p in seen_paths:
                    continue
                seen_paths.add(p)
                dedup_paths.append(p)

            if not dedup_paths:
                continue

            roots = [{"instance_path": p if p else "<top>"} for p in dedup_paths]
            intent = cells[idx_intent] if idx_intent < len(cells) else ""
            sw_visible = cells[idx_sw_visible] if idx_sw_visible < len(cells) else ""
            evidence = cells[idx_evidence] if idx_evidence < len(cells) else ""
            unknown = cells[idx_unknown] if idx_unknown < len(cells) else ""

            subsystems.append({
                "id": f"SS{row_idx}",
                "name": name,
                "roots": roots,
                "intent": intent,
                "sw_visible": sw_visible,
                "evidence": evidence,
                "unknown": unknown,
            })

        if not subsystems:
            return None

        return {
            "top_module": top_node.module_name,
            "subsystems": subsystems,
        }

    def _extract_pass3_2_partition_json(self, markdown: str, top_node: Any) -> Optional[Dict[str, Any]]:
        content = (markdown or "").strip()
        if not content:
            return None

        table_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]
        if len(table_lines) < 3:
            return None

        header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]

        def _norm_header(text: str) -> str:
            t = (text or "").strip().lower()
            t = t.replace("（", "(").replace("）", ")")
            t = re.sub(r"\s+", "", t)
            return t

        header_map = {cell: idx for idx, cell in enumerate(header_cells)}
        header_map_norm = {_norm_header(cell): idx for idx, cell in enumerate(header_cells)}

        idx_domain = header_map.get("微架构域", 0)
        idx_roots = header_map.get("Roots(root modules)", 1)
        idx_intent = header_map.get("职责/边界(intent)", 2)
        idx_iface = header_map.get("关键接口/状态(key interface/state)", 3)
        idx_evidence = header_map.get("证据(readDoc 摘要)", 4)
        idx_unknown = header_map.get("未知/待确认", 5)

        path_index = self._build_instance_path_index(top_node)
        micro_domains: List[Dict[str, Any]] = []

        for row_idx, line in enumerate(table_lines[2:], start=1):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 2:
                continue

            name = cells[idx_domain] if idx_domain < len(cells) else ""
            roots_cell = cells[idx_roots] if idx_roots < len(cells) else ""
            if not name or not roots_cell:
                continue

            tokens = self._parse_roots_cell_tokens(roots_cell)
            root_paths: List[str] = []
            for token in tokens:
                root_paths.extend(self._resolve_root_token_to_paths(token, path_index))

            dedup_paths: List[str] = []
            seen_paths = set()
            for p in root_paths:
                if p in seen_paths:
                    continue
                seen_paths.add(p)
                dedup_paths.append(p)

            if not dedup_paths:
                continue

            roots = [{"instance_path": p if p else "<top>"} for p in dedup_paths]
            intent = cells[idx_intent] if idx_intent < len(cells) else ""
            key_iface = cells[idx_iface] if idx_iface < len(cells) else ""
            evidence = cells[idx_evidence] if idx_evidence < len(cells) else ""
            unknown = cells[idx_unknown] if idx_unknown < len(cells) else ""

            micro_domains.append({
                "id": f"CORE{row_idx}",
                "name": name,
                "roots": roots,
                "intent": intent,
                "key_interface_state": key_iface,
                "evidence": evidence,
                "unknown": unknown,
            })

        if not micro_domains:
            return None

        return {
            "top_module": top_node.module_name,
            "micro_domains": micro_domains,
        }

    def _extract_pass3_3_instrack_json(
        self,
        markdown: str,
        top_node: Any,
        instruction: str,
    ) -> Dict[str, Any]:
        content = (markdown or "").strip()
        out: Dict[str, Any] = {
            "schema_version": "pass3_3_instrack_v2",
            "top_module": top_node.module_name,
            "instruction": instruction,
            "route_blocks": [],
            "route_bridges": [],
            "route_modules_approx": [],
            "evidence": "",
            "unknown": "",
            "verification_mode": "topology_plus_doc_evidence",
        }
        if not content:
            return out

        mermaid_code = self._extract_mermaid_code(content)
        if mermaid_code:
            route_blocks, route_bridges, modules_approx = self._extract_routes_from_mermaid(mermaid_code)
            out["route_blocks"] = route_blocks
            out["route_bridges"] = route_bridges
            out["route_modules_approx"] = modules_approx
            out["raw_summary"] = self.owner._extract_summary(content, max_lines=24, max_chars=3000)
            out["mermaid"] = mermaid_code
            return out

        lines = content.splitlines()

        # Prefer parsing the table under the explicit final-route section.
        section_markers = ["## 最终路线表格", "### 最终路线表格"]
        start_idx = 0
        for i, line in enumerate(lines):
            if line.strip() in section_markers:
                start_idx = i + 1
                break

        scoped_lines = lines[start_idx:] if start_idx < len(lines) else lines
        table_lines = [line.strip() for line in scoped_lines if line.strip().startswith("|")]
        if len(table_lines) < 3:
            out["raw_summary"] = self.owner._extract_summary(content, max_lines=24, max_chars=3000)
            return out

        header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]

        def _norm_header(text: str) -> str:
            t = (text or "").strip().lower()
            t = t.replace("（", "(").replace("）", ")")
            t = re.sub(r"\s+", "", t)
            return t

        header_map = {cell: idx for idx, cell in enumerate(header_cells)}
        header_map_norm = {_norm_header(cell): idx for idx, cell in enumerate(header_cells)}

        idx_inst = header_map.get("指令", header_map_norm.get(_norm_header("指令"), -1))
        idx_route_blocks = header_map.get("路线(Block路径链)")
        if idx_route_blocks is None:
            idx_route_blocks = header_map_norm.get(_norm_header("路线(Block路径链)"))
        if idx_route_blocks is None:
            idx_route_blocks = header_map.get("Block路径链")
        if idx_route_blocks is None:
            idx_route_blocks = header_map_norm.get(_norm_header("Block路径链"))
        if idx_route_blocks is None:
            idx_route_blocks = header_map.get("路线(路径链)")
        if idx_route_blocks is None:
            idx_route_blocks = header_map_norm.get(_norm_header("路线(路径链)"))

        idx_route_bridge = header_map.get("桥接(Bridge链)")
        if idx_route_bridge is None:
            idx_route_bridge = header_map_norm.get(_norm_header("桥接(Bridge链)"))
        if idx_route_bridge is None:
            idx_route_bridge = header_map.get("Bridge链")
        if idx_route_bridge is None:
            idx_route_bridge = header_map_norm.get(_norm_header("Bridge链"))
        if idx_route_bridge is None:
            idx_route_bridge = header_map.get("桥接")
        if idx_route_bridge is None:
            idx_route_bridge = header_map_norm.get(_norm_header("桥接"))

        idx_evidence = header_map.get("证据(readDoc 摘要)")
        if idx_evidence is None:
            idx_evidence = header_map_norm.get(_norm_header("证据(readDoc 摘要)"))
        if idx_evidence is None:
            idx_evidence = header_map.get("证据")
        if idx_evidence is None:
            idx_evidence = header_map_norm.get(_norm_header("证据"))
        if idx_evidence is None:
            idx_evidence = 3

        idx_unknown = header_map.get("未知/待确认")
        if idx_unknown is None:
            idx_unknown = header_map_norm.get(_norm_header("未知/待确认"))
        if idx_unknown is None:
            idx_unknown = 4

        if idx_route_blocks is None:
            out["raw_summary"] = self.owner._extract_summary(content, max_lines=24, max_chars=3000)
            return out

        target_row: Optional[List[str]] = None
        normalized_target = self._normalize_instruction(instruction)

        def _is_separator_row(cells: List[str]) -> bool:
            return all(re.match(r"^:?-{3,}:?$", c.strip()) for c in cells if c.strip())

        candidate_rows: List[List[str]] = []
        for line in table_lines[2:]:
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 3 or _is_separator_row(cells):
                continue

            candidate_rows.append(cells)

            if idx_inst >= 0:
                inst = cells[idx_inst] if idx_inst < len(cells) else ""
                if self._normalize_instruction(inst) == normalized_target:
                    target_row = cells
                    break

        if target_row is None and candidate_rows:
            def _chain_score(cells: List[str]) -> int:
                route_block = cells[idx_route_blocks] if idx_route_blocks < len(cells) else ""
                route_bridge = cells[idx_route_bridge] if (idx_route_bridge is not None and idx_route_bridge < len(cells)) else ""

                # Reject obvious placeholders/header echoes.
                reject_tokens = {"路径链", "route", "path", "chain", "bridge", "block"}
                low_b = route_block.lower()
                low_g = route_bridge.lower()
                if any(tok in low_b for tok in reject_tokens) and "->" not in route_block and "→" not in route_block:
                    return -1
                if route_bridge and any(tok in low_g for tok in reject_tokens) and "->" not in route_bridge and "→" not in route_bridge:
                    return -1

                return max(
                    len(self._split_chain(route_block)),
                    len(self._split_chain(route_bridge)),
                )

            target_row = max(candidate_rows, key=_chain_score)

        if not target_row:
            out["raw_summary"] = self.owner._extract_summary(content, max_lines=24, max_chars=3000)
            return out

        route_block_text = target_row[idx_route_blocks] if idx_route_blocks < len(target_row) else ""
        route_bridge_text = target_row[idx_route_bridge] if (idx_route_bridge is not None and idx_route_bridge < len(target_row)) else ""
        evidence_text = target_row[idx_evidence] if idx_evidence < len(target_row) else ""
        unknown_text = target_row[idx_unknown] if idx_unknown < len(target_row) else ""

        route_blocks = self._split_chain(route_block_text)
        route_bridges = self._split_chain(route_bridge_text)

        # Approximate module chain from module:block tokens.
        modules_approx: List[str] = []
        seen_modules = set()
        for item in route_blocks:
            if ":" not in item:
                continue
            module_name = item.split(":", 1)[0].strip()
            if not module_name or module_name in seen_modules:
                continue
            seen_modules.add(module_name)
            modules_approx.append(module_name)

        out["route_blocks"] = route_blocks
        out["route_bridges"] = route_bridges
        out["route_modules_approx"] = modules_approx
        out["evidence"] = evidence_text
        out["unknown"] = unknown_text
        out["raw_summary"] = self.owner._extract_summary(content, max_lines=24, max_chars=3000)
        return out

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> List[str]:
        t = (text or "").lower()
        return [k for k in keywords if k in t]

    def _discover_core_candidates(self, top_node: Any) -> List[Dict[str, Any]]:
        primary_kw = ["core", "cpu", "ct_top"]
        micro_kw = ["ifu", "idu", "iu", "lsu", "rtu", "biu", "cp0", "had", "fpu", "vpu"]

        candidates: List[Dict[str, Any]] = []
        queue: List[Any] = [top_node]

        while queue:
            node = queue.pop(0)
            for child in node.children.values():
                queue.append(child)

            if node is top_node:
                continue

            path = node.get_path() if hasattr(node, "get_path") else node.instance_name
            name_blob = f"{node.instance_name} {node.module_name}".lower()

            score = 0
            primary_hits = self._contains_any(name_blob, primary_kw)
            score += len(primary_hits) * 60

            child_blob = " ".join(
                f"{c.instance_name} {c.module_name}".lower()
                for c in node.children.values()
            )
            micro_hits = self._contains_any(child_blob, micro_kw)
            score += len(set(micro_hits)) * 10

            if node.depth <= 2:
                score += 10
            if len(node.children) >= 6:
                score += 8
            if "ct_top" in name_blob:
                score += 25

            if score <= 0:
                continue

            key_children = sorted({c.module_name for c in node.children.values()})[:10]
            candidates.append({
                "path": path,
                "instance": node.instance_name,
                "module": node.module_name,
                "depth": node.depth,
                "children": len(node.children),
                "score": score,
                "primary_hits": sorted(set(primary_hits)),
                "micro_hits": sorted(set(micro_hits)),
                "key_children": key_children,
            })

        candidates.sort(key=lambda c: (-c["score"], c["depth"], c["path"]))
        return candidates

    def _tool_explore_core(self, top_node: Any, max_candidates: int = 10) -> str:
        try:
            limit = max(1, min(int(max_candidates), 20))
        except Exception:
            limit = 10

        cands = self._discover_core_candidates(top_node)
        if not cands:
            return "[exploreCore]\n\nNo core-like candidates found."

        chosen = [c for c in cands if c["score"] >= 90][:4]
        if not chosen:
            chosen = cands[: min(2, len(cands))]

        lines = [
            "[exploreCore]",
            "",
            "## Recommended Core Roots",
        ]

        for c in chosen:
            lines.append(
                f"- {c['path']} ({c['instance']} / {c['module']}), score={c['score']}, children={c['children']}"
            )

        lines.extend([
            "",
            "## Candidate Ranking",
            "",
            "| Rank | Instance Path | Instance(Module) | Depth | Children | Score | Name Hits | Micro Hits |",
            "|---:|---|---|---:|---:|---:|---|---|",
        ])

        for idx, c in enumerate(cands[:limit], start=1):
            name_hits = ",".join(c["primary_hits"]) or "-"
            micro_hits = ",".join(c["micro_hits"]) or "-"
            lines.append(
                f"| {idx} | {c['path']} | {c['instance']}({c['module']}) | {c['depth']} | {c['children']} | {c['score']} | {name_hits} | {micro_hits} |"
            )

        lines.append("\n## Top Candidate Summaries")
        for c in chosen:
            summary_src = (
                self.owner.tracker.get_pass2_7_content(c["module"]) or
                self.owner.tracker.get_pass2_content(c["module"]) or
                self.owner.tracker.get_pass1_content(c["module"]) or
                "无可用文档摘要"
            )
            summary = self.owner._extract_summary(summary_src, max_lines=5, max_chars=500)
            lines.append(f"\n### {c['path']} ({c['module']})\n{summary}")

        return "\n".join(lines)

    @staticmethod
    def _classify_instruction(instruction: str) -> str:
        inst = (instruction or "").upper()
        if not inst:
            return "unknown"
        if inst.startswith("C."):
            return "compressed"
        if inst in {"JAL", "JALR", "BEQ", "BNE", "BLT", "BGE", "BLTU", "BGEU"}:
            return "branch_jump"
        if inst.startswith("AMO") or inst.startswith("LR.") or inst.startswith("SC."):
            return "atomic"
        if inst.startswith("CSR") or inst in {"ECALL", "EBREAK", "MRET", "SRET", "WFI", "FENCE", "FENCE.I"}:
            return "system"
        if inst.startswith("L") and inst not in {"LUI"}:
            return "load"
        if inst.startswith("S") and inst not in {"SLT", "SLTI", "SLTU", "SLTIU", "SLL", "SLLI", "SRL", "SRLI", "SRA", "SRAI", "SUB"}:
            return "store"
        if inst.startswith("MUL") or inst.startswith("DIV") or inst.startswith("REM"):
            return "muldiv"
        return "integer"

    @staticmethod
    def _split_chain(text: str) -> List[str]:
        chain = (text or "").replace("`", "").replace("...", "").replace("…", "")
        chain = chain.replace("→", "->").replace("=>", "->")
        return [p.strip() for p in chain.split("->") if p.strip()]
    
    @staticmethod
    def _extract_mermaid_code(markdown: str) -> str:
        text = markdown or ""
        m = re.search(r"```mermaid\s*(.*?)```", text, flags=re.S | re.I)
        if m:
            return (m.group(1) or "").strip()
        return ""

    @staticmethod
    def _extract_json_code(markdown: str) -> str:
        text = markdown or ""
        m = re.search(r"```json\s*(.*?)```", text, flags=re.S | re.I)
        if m:
            return (m.group(1) or "").strip()
        # Fallback: allow raw json body without fenced block.
        return text.strip()
    
    def _extract_routes_from_mermaid(self, mermaid_code: str) -> Tuple[List[str], List[str], List[str]]:
        code = mermaid_code or ""
        if not code:
            return [], [], []
        
        block_matches = re.findall(
            r"([A-Za-z0-9_.$\\]+):((?:PROC|COMB|IN_COMB|OUT_COMB)_\d+)",
            code,
            flags=re.I,
        )
        bridge_matches = re.findall(r"BRIDGE:([A-Za-z0-9_./$\\-]+)", code)
        
        route_blocks: List[str] = []
        seen_blocks = set()
        for module_name, block_id in block_matches:
            item = f"{module_name}:{block_id.upper()}"
            if item in seen_blocks:
                continue
            seen_blocks.add(item)
            route_blocks.append(item)
        
        route_bridges: List[str] = []
        seen_bridges = set()
        for bridge in bridge_matches:
            item = f"BRIDGE:{bridge}"
            if item in seen_bridges:
                continue
            seen_bridges.add(item)
            route_bridges.append(item)
        
        modules_approx: List[str] = []
        seen_modules = set()
        for item in route_blocks:
            if ":" not in item:
                continue
            module_name = item.split(":", 1)[0]
            if module_name in seen_modules:
                continue
            seen_modules.add(module_name)
            modules_approx.append(module_name)
        
        return route_blocks, route_bridges, modules_approx

    def _coerce_instrack_mermaid_only(self, content: str) -> str:
        mermaid = self._extract_mermaid_code(content or "")
        if not mermaid:
            return (content or "").strip()
        return f"```mermaid\n{mermaid}\n```\n"

    def _extract_pass3_3_search_json(self, content: str, top_node: Any, instruction: str) -> Dict[str, Any]:
        out = {
            "schema_version": "pass3_3_1_startpoint_v1",
            "top_module": top_node.module_name,
            "instruction": instruction,
            "start_module": "",
            "start_instance": "",
            "start_block": "",
            "key_register": "",
            "key_register_reason": "",
            "start_reason": "",
            "confidence": "low",
            "candidate_domains": [],
            "unknown": "",
        }
        json_body = self._extract_json_code(content or "")
        if not json_body:
            return out
        try:
            parsed = json.loads(json_body)
        except Exception:
            return out
        if not isinstance(parsed, dict):
            return out

        out["start_module"] = str(parsed.get("start_module") or "").strip()
        out["start_instance"] = str(parsed.get("start_instance") or "").strip()
        out["start_block"] = str(parsed.get("start_block") or "").strip()
        out["key_register"] = str(parsed.get("key_register") or "").strip()
        out["key_register_reason"] = str(parsed.get("key_register_reason") or "").strip()
        out["start_reason"] = str(parsed.get("start_reason") or "").strip()
        conf = str(parsed.get("confidence") or "low").strip().lower()
        out["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        out["unknown"] = str(parsed.get("unknown") or "").strip()

        domains = parsed.get("candidate_domains")
        if isinstance(domains, list):
            normalized: List[str] = []
            for item in domains:
                text = str(item or "").strip()
                if text:
                    normalized.append(text)
            out["candidate_domains"] = normalized

        return out

    def _coerce_instrack_search_json_only(self, content: str, top_node: Any, instruction: str) -> str:
        parsed = self._extract_pass3_3_search_json(content, top_node, instruction)
        return "```json\n" + json.dumps(parsed, ensure_ascii=False, indent=2) + "\n```\n"

    def _record_path_entry(
        self,
        *,
        instruction: str,
        current_node: Any,
        level: int,
        relation: str,
        start_block: str,
        key_path: str,
        evidence: str,
        confidence: str,
        handoff_to: str,
        register_reads: Optional[List[str]] = None,
        register_writes: Optional[List[str]] = None,
        key_conditions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        conf = (confidence or "medium").strip().lower()
        if conf not in {"high", "medium", "low"}:
            conf = "medium"
        return {
            "instruction": instruction,
            "module": getattr(current_node, "module_name", ""),
            "instance": getattr(current_node, "instance_name", ""),
            "level": int(level),
            "relation": (relation or "related").strip(),
            "start_block": (start_block or "").strip(),
            "key_path": (key_path or "").strip(),
            "evidence": (evidence or "").strip(),
            "confidence": conf,
            "handoff_to": (handoff_to or "").strip(),
            "register_reads": self._normalize_str_list(register_reads),
            "register_writes": self._normalize_str_list(register_writes),
            "key_conditions": self._normalize_str_list(key_conditions),
        }

    @staticmethod
    def _normalize_str_list(values: Optional[List[str]]) -> List[str]:
        out: List[str] = []
        for item in values or []:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out

    @staticmethod
    def _coerce_optional_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _is_register_level_record(self, item: Dict[str, Any]) -> bool:
        reads = self._normalize_str_list(item.get("register_reads") or [])
        writes = self._normalize_str_list(item.get("register_writes") or [])
        conds = self._normalize_str_list(item.get("key_conditions") or [])
        return bool(reads and writes and conds)

    def _enforce_register_level_records(self, search_json: Dict[str, Any], records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        strong = [r for r in records if self._is_register_level_record(r)]
        if strong:
            return strong

        unknown = str(search_json.get("unknown") or "").strip()
        extra = "证据不足，当前记录未达到寄存器级粒度（缺少register_reads/register_writes/key_conditions）。"
        search_json["unknown"] = f"{unknown} {extra}".strip()
        search_json["confidence"] = "low"
        return []

    @staticmethod
    def _dedupe_path_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in records or []:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("instruction", ""),
                item.get("instance", ""),
                item.get("start_block", ""),
                item.get("key_path", ""),
                item.get("handoff_to", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _render_instrack_search_markdown(self, search_json: Dict[str, Any], records: List[Dict[str, Any]]) -> str:
        lines: List[str] = [
            "# Pass3.3 Search Report",
            "",
            f"- instruction: {search_json.get('instruction', '')}",
            f"- start_module: {search_json.get('start_module', '')}",
            f"- start_instance: {search_json.get('start_instance', '')}",
            f"- start_block: {search_json.get('start_block', '')}",
            f"- confidence: {search_json.get('confidence', 'low')}",
            "",
            "## Key Path Records",
        ]

        if not records:
            lines.append("- no related module records")
            return "\n".join(lines) + "\n"

        for idx, item in enumerate(records, start=1):
            lines.append(f"### record_{idx}")
            lines.append(f"- module: {item.get('module', '')}")
            lines.append(f"- instance: {item.get('instance', '')}")
            lines.append(f"- level: {item.get('level', 0)}")
            lines.append(f"- relation: {item.get('relation', '')}")
            lines.append(f"- start_block: {item.get('start_block', '')}")
            lines.append(f"- key_path: {item.get('key_path', '')}")
            lines.append(f"- register_reads: {', '.join(item.get('register_reads') or []) or '-'}")
            lines.append(f"- register_writes: {', '.join(item.get('register_writes') or []) or '-'}")
            lines.append(f"- key_conditions: {', '.join(item.get('key_conditions') or []) or '-'}")
            lines.append(f"- handoff_to: {item.get('handoff_to', '')}")
            lines.append(f"- confidence: {item.get('confidence', 'medium')}")
            lines.append(f"- evidence: {item.get('evidence', '')}")
            lines.append("")
        return "\n".join(lines)

    def _build_pass2_style_topology_block(self, module_name: str) -> str:
        """Return pass2-style topology block text for injection.

        Priority:
        1) Reuse pass2 formatter (`_format_graph_description`) on precomputed graph.
        2) Fallback to extracting topology section from pass2.4 markdown.
        """
        graphs = getattr(self.owner, "_graphs", None)
        formatter = getattr(self.owner, "_format_graph_description", None)
        if isinstance(graphs, dict) and callable(formatter):
            graph = graphs.get(module_name)
            if graph is not None:
                try:
                    graph_description = formatter(graph, include_source=True)
                    if graph_description and str(graph_description).strip():
                        return (
                            "# Circuit Topology (PROC/COMB Logic Block Connection Diagram, Topologically Sorted, with Source Code):\n"
                            f"{graph_description}"
                        )
                except Exception:
                    pass

        pass2_4 = self.owner.tracker.get_pass2_4_content(module_name) or ""
        if pass2_4:
            section = ""
            extractor = getattr(self.owner, "_extract_named_section", None)
            if callable(extractor):
                section = extractor(pass2_4, ["Circuit Topology", "电路拓扑"]) or ""
            if section:
                return str(section)

        return "无拓扑结构信息（沿用 pass2 子文档格式时未找到对应拓扑块）"

    def _tool_explore_inst_route(self, top_node: Any, instruction: str, max_domains: int = 12) -> str:
        category = self._classify_instruction(instruction)
        core_json_path = self.owner.chip_dir / "core_partition.json"
        domains: List[Dict[str, Any]] = []
        if core_json_path.exists():
            try:
                data = json.loads(core_json_path.read_text(encoding="utf-8"))
                values = data.get("micro_domains")
                if isinstance(values, list):
                    domains = [d for d in values if isinstance(d, dict)]
            except Exception:
                domains = []

        keyword_map: Dict[str, List[str]] = {
            "integer": ["ifu", "idu", "iu", "rtu"],
            "branch_jump": ["ifu", "idu", "iu", "rtu", "pc"],
            "load": ["ifu", "idu", "lsu", "biu", "rtu"],
            "store": ["ifu", "idu", "lsu", "biu", "rtu"],
            "system": ["ifu", "idu", "cp0", "rtu", "had"],
            "atomic": ["ifu", "idu", "lsu", "biu", "rtu"],
            "muldiv": ["ifu", "idu", "iu", "rtu"],
            "compressed": ["ifu", "idu", "iu", "rtu"],
            "unknown": ["ifu", "idu", "iu", "lsu", "rtu"],
        }
        keywords = keyword_map.get(category, keyword_map["unknown"])

        ranked: List[Tuple[int, Dict[str, Any]]] = []
        for domain in domains:
            name = str(domain.get("name") or "").lower()
            intent = str(domain.get("intent") or "").lower()
            evidence = str(domain.get("evidence") or "").lower()
            text = f"{name} {intent} {evidence}"
            score = 0
            for kw in keywords:
                if kw in text:
                    score += 10
            if score > 0:
                ranked.append((score, domain))

        ranked.sort(key=lambda x: -x[0])
        lines = [
            f"[exploreInstRoute] instruction={instruction}",
            f"- category: {category}",
            f"- keywords: {', '.join(keywords)}",
            "",
        ]
        if not ranked:
            lines.append("No strong domain match from core_partition.json; fallback to exploreCore + readDoc is recommended.")
            return "\n".join(lines)

        lines.append("## Candidate Micro Domains")
        lines.append("| Rank | Score | Domain | Roots | Evidence |")
        lines.append("|---:|---:|---|---|---|")
        for idx, (score, domain) in enumerate(ranked[: max(1, min(max_domains, 24))], start=1):
            roots = domain.get("roots") or []
            root_paths = []
            for root in roots:
                if isinstance(root, dict):
                    path = str(root.get("instance_path") or "")
                    if path:
                        root_paths.append(path)
            lines.append(
                f"| {idx} | {score} | {domain.get('name', '')} | {', '.join(root_paths) or '-'} | {domain.get('evidence', '')} |"
            )

        return "\n".join(lines)

    def _pass3_1_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "readSource",
                    "description": "Read full RTL source code for a module on demand. By default, do not truncate.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "RTL module name.",
                            },
                            "max_lines": {
                                "type": "integer",
                                "description": "Maximum lines to return. 0 means no truncation.",
                                "default": 0,
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "readDoc",
                    "description": "Read generated RTL documentation for a module. Optionally extract specific markdown sections.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string", "description": "RTL module name"},
                            "doc": {
                                "type": "string",
                                "description": "Which doc to read: auto|preview|architecture|description|interface_spec|design_highlights|functional_desc|register_desc|timing_cdc|block_docs|flowchart|metadata",
                                "default": "auto",
                            },
                            "sections": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Heading keywords to extract (e.g. 接口/寄存器/中断/异常/配置). Empty means return full doc.",
                            },
                            "section": {
                                "type": "string",
                                "description": "Convenience alias for one section keyword (equivalent to sections=[section]).",
                            },
                            "max_chars": {"type": "integer", "description": "Max chars to return (0 means no truncation)", "default": 0},
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Delegate deeper analysis to a child-level recursive agent. Only direct children of top are allowed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Target child module selector (instance_name or module_name).",
                            },
                            "task": {
                                "type": "string",
                                "description": "Free-form goal defined by the parent agent for the child agent (not a preset template string).",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
        ]

    def _pass3_2_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "exploreCore",
                    "description": "Agent Explore mode: find likely core roots and rank candidates by hierarchy/data evidence.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "max_candidates": {
                                "type": "integer",
                                "description": "Maximum ranked candidates to return (1-20).",
                                "default": 10,
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "readDoc",
                    "description": "Read generated RTL documentation for a module. Optionally extract specific markdown sections.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string", "description": "RTL module name"},
                            "doc": {
                                "type": "string",
                                "description": "Which doc to read: auto|preview|architecture|description|interface_spec|design_highlights|functional_desc|register_desc|timing_cdc|block_docs|flowchart|metadata",
                                "default": "auto",
                            },
                            "sections": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Heading keywords to extract (e.g. 接口/寄存器/中断/异常/配置). Empty means return full doc.",
                            },
                            "section": {
                                "type": "string",
                                "description": "Convenience alias for one section keyword (equivalent to sections=[section]).",
                            },
                            "max_chars": {"type": "integer", "description": "Max chars to return (0 means no truncation)", "default": 0},
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Delegate deeper analysis to a child-level recursive agent. Only direct children of top are allowed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Target child module selector (instance_name or module_name).",
                            },
                            "task": {
                                "type": "string",
                                "description": "Free-form goal defined by the parent agent for the child agent (not a preset template string).",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
        ]

    def _pass3_3_1_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "readSource",
                    "description": "Read pass2-style topologyized source block for a module (PROC/COMB topology with source annotations).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string", "description": "RTL module name"},
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "readDoc",
                    "description": "Read generated RTL documentation for a module. Optionally extract specific markdown sections.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string", "description": "RTL module name"},
                            "doc": {
                                "type": "string",
                                "description": "Which doc to read: auto|preview|architecture|description|interface_spec|design_highlights|functional_desc|register_desc|timing_cdc|block_docs|flowchart|metadata",
                                "default": "auto",
                            },
                            "sections": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Heading keywords to extract.",
                            },
                            "section": {
                                "type": "string",
                                "description": "Convenience alias for one section keyword.",
                            },
                            "max_chars": {"type": "integer", "description": "Max chars to return (0 means no truncation)", "default": 0},
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Delegate deeper analysis to a child-level recursive agent. Only direct children of top are allowed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Target child module selector (instance_name or module_name).",
                            },
                            "task": {
                                "type": "string",
                                "description": "Free-form goal defined by the parent agent for the child agent (not a preset template string).",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
        ]

    def _pass3_3_2_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "readDoc",
                    "description": "Read generated RTL documentation for a module. Optionally extract specific markdown sections.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string", "description": "RTL module name"},
                            "doc": {
                                "type": "string",
                                "description": "Which doc to read: auto|preview|architecture|description|interface_spec|design_highlights|functional_desc|register_desc|timing_cdc|block_docs|flowchart|metadata",
                                "default": "auto",
                            },
                            "sections": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Heading keywords to extract.",
                            },
                            "section": {
                                "type": "string",
                                "description": "Convenience alias for one section keyword.",
                            },
                            "max_chars": {"type": "integer", "description": "Max chars to return (0 means no truncation)", "default": 0},
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Delegate deeper analysis to a child-level recursive agent. Only direct children of top are allowed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Target child module selector (instance_name or module_name).",
                            },
                            "task": {
                                "type": "string",
                                "description": "Free-form goal defined by the parent agent for the child agent (not a preset template string).",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
        ]

    def _pass3_3_tools(self) -> List[Dict[str, Any]]:
        # Keep the legacy name for call sites not yet migrated.
        return self._pass3_3_2_tools()

    def _pass3_recursive_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "readSource",
                    "description": "Read pass2-style topologyized source block for a module. Scope-limited: current level and direct children only.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Module selector. Prefer instance_name or module_name in current scope.",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "readDoc",
                    "description": "Read one module's one section doc (pass2.1~2.7). Scope-limited: current level and direct children only.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Module selector. Prefer instance_name or module_name in current scope.",
                            },
                            "section": {
                                "type": "string",
                                "description": "Section key. Allowed: pass2.1, pass2.2, pass2.3, pass2.4, pass2.5, pass2.6, pass2.7",
                            },
                        },
                        "required": ["module", "section"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Spawn a child-level agent for deeper analysis. Only direct children can be forked.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Target child module selector (instance_name or module_name).",
                            },
                            "task": {
                                "type": "string",
                                "description": "Free-form goal defined by the parent agent for the child agent (not a preset template string).",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
        ]

    @staticmethod
    def _normalize_pass_section(section: str) -> str:
        key = (section or "").strip().lower().replace(" ", "")
        key = key.replace("_", ".")
        alias = {
            "pass2.1": "pass2_1",
            "2.1": "pass2_1",
            "highlights": "pass2_1",
            "design_highlights": "pass2_1",
            "pass2.2": "pass2_2",
            "2.2": "pass2_2",
            "flowchart": "pass2_2",
            "pass2.3": "pass2_3",
            "2.3": "pass2_3",
            "interface": "pass2_3",
            "interface_spec": "pass2_3",
            "pass2.4": "pass2_4",
            "2.4": "pass2_4",
            "functional": "pass2_4",
            "functional_desc": "pass2_4",
            "pass2.5": "pass2_5",
            "2.5": "pass2_5",
            "register": "pass2_5",
            "register_desc": "pass2_5",
            "pass2.6": "pass2_6",
            "2.6": "pass2_6",
            "timing": "pass2_6",
            "timing_cdc": "pass2_6",
            "pass2.7": "pass2_7",
            "2.7": "pass2_7",
            "architecture": "pass2_7",
        }
        return alias.get(key, "")

    def _get_module_section_content(self, module_name: str, canonical_section: str) -> str:
        tracker = self.owner.tracker
        getters = {
            "pass2_1": tracker.get_pass2_1_content,
            "pass2_2": tracker.get_pass2_2_content,
            "pass2_3": tracker.get_pass2_3_content,
            "pass2_4": tracker.get_pass2_4_content,
            "pass2_5": tracker.get_pass2_5_content,
            "pass2_6": tracker.get_pass2_6_content,
            "pass2_7": tracker.get_pass2_7_content,
        }
        getter = getters.get(canonical_section)
        if getter is None:
            return ""
        return getter(module_name) or ""

    def _resolve_scope_node(self, current_node: Any, module_selector: str, child_only: bool = False) -> Tuple[Optional[Any], str]:
        selector = (module_selector or "").strip()
        if not selector:
            return None, "Error: module is empty"

        children = list(current_node.children.values())
        visible = children if child_only else [current_node] + children

        if not child_only and selector in ["self", "<self>", ".", current_node.instance_name, current_node.module_name]:
            return current_node, ""

        for n in visible:
            if selector == n.instance_name:
                return n, ""

        candidates = [n for n in visible if selector == n.module_name]
        if candidates:
            candidates.sort(key=lambda n: (n.depth, n.instance_name))
            return candidates[0], ""

        child_hints = ", ".join(sorted({f"{c.instance_name}({c.module_name})" for c in children})[:12])
        if child_only:
            return None, (
                "Error: module is not a direct child. "
                "Use forkSubAgent only on direct children. "
                f"Children: {child_hints or 'N/A'}"
            )

        visible_hints = ", ".join(sorted({f"{n.instance_name}({n.module_name})" for n in visible})[:16])
        return None, (
            "Error: module is outside current read scope. "
            "At this level you can read only current module and direct children. "
            "For deeper nodes, call forkSubAgent on the relevant child first. "
            f"Visible: {visible_hints or 'N/A'}"
        )

    def _tool_read_doc_scoped(self, current_node: Any, module: str, section: str) -> str:
        target, error = self._resolve_scope_node(current_node, module, child_only=False)
        if target is None:
            return error

        canonical = self._normalize_pass_section(section)
        if not canonical:
            return (
                "Error: unsupported section. Allowed: "
                "pass2.1, pass2.2, pass2.3, pass2.4, pass2.5, pass2.6, pass2.7"
            )

        content = self._get_module_section_content(target.module_name, canonical)
        if not content:
            return (
                f"[readDoc] module={target.instance_name}({target.module_name}), section={canonical}\n\n"
                "No content found (possibly not generated yet)."
            )

        return (
            f"[readDoc] module={target.instance_name}({target.module_name}), section={canonical}\n\n"
            f"{content}"
        )

    @staticmethod
    def _extract_pass3_1_output_schema() -> str:
        marker = "## 输出结构"
        idx = PASS3_1_PROMPT.find(marker)
        if idx >= 0:
            return PASS3_1_PROMPT[idx:].strip()
        # Fallback keeps behavior stable even if prompt template changes unexpectedly.
        return (
            "## 输出结构（请严格按此结构输出）\n\n"
            "## 子系统划分表格\n\n"
            "| 子系统 | Roots(root modules) | 职责/边界(intent) | 软件可见面(sw visible) | 证据(readDoc 摘要) | 未知/待确认 |\n"
            "|---|---|---|---|---|---|"
        )

    def _build_pass3_recursive_system(self, prompt_style: str = "architecture") -> str:
        if prompt_style == "instrack_search":
            return (
                PASS3_3_1_SEARCH_SYSTEM.strip()
                + "\n\n"
                + "递归子代理附加约束：\n"
                + "- 你处于递归子代理模式：先在当前层判断是否存在生命周期起点/关键寄存器证据。\n"
                + "- 你只能直接读取本级与直接子级证据（readDoc/readSource 受限）。\n"
                + "- 若要读取更深层，必须调用 forkSubAgent(module, task)；task 由你自由定义为下一级目标。"
            )

        if prompt_style == "instrack":
            return (
                "你是指令流向分析子代理。目标是为单条指令补充当前层级的 block-first 路由证据。\n"
                "你只能读取本级和直接子级文档；更深层必须 forkSubAgent。\n"
                "路径节点必须使用结构化拓扑中的 PROC/COMB block，跨模块跳转用 bridge 表达。\n"
                "若当前模块存在子模块，必须逐个 forkSubAgent 探查是否与该指令相关。\n"
                "最终输出只能是一个 mermaid flowchart LR 代码块。\n"
                "禁止臆断，证据不足时明确写待确认。"
            )

        if prompt_style != "partition":
            return (
                "你是芯片架构文档Agent。请以top-down方式输出本层级架构结论和任务拆解。\n"
                "你只能直接读取本级模块与其直接子模块文档（readDoc工具已受限）。\n"
                "若需要再往下读取下下级或更深层，请使用 forkSubAgent(module, task) 交给下一级Agent。\n"
                "禁止臆断，证据不足时明确写待确认。"
            )

        return (
            PASS3_1_SYSTEM.strip()
            + "\n\n"
            + "递归子代理附加约束：\n"
            + "- 你处于递归子代理模式：优先按 Pass3.1 风格输出当前层级划分。\n"
            + "- 你只能直接读取本级与直接子级文档（readDoc 受限）。\n"
            + "- 若要读取更深层，必须调用 forkSubAgent(module, task) 继续下钻。"
        )

    def _build_pass3_recursive_prompt(
        self,
        top_node: Any,
        current_node: Any,
        level: int,
        task: str,
        instruction: str,
        instruction_datasheet: str,
        current_description: str,
        child_overview: str,
        prompt_style: str = "architecture",
    ) -> str:
        if prompt_style == "instrack_search":
            base_prompt = PASS3_3_1_SEARCH_PROMPT.format(
                instruction=instruction or "UNKNOWN",
                instruction_datasheet=(
                    instruction_datasheet
                    if (instruction_datasheet or "").strip()
                    else "Instruction unavailable"
                ),
                module_description=self.owner._extract_summary(current_description, max_lines=20, max_chars=2400),
            ).strip()
            return (
                f"{base_prompt}\n\n"
                "## Recursive context\n"
                f"- Top module: {top_node.module_name}\n"
                f"- Current level: {level}\n"
                f"- Current node: {current_node.instance_name} ({current_node.module_name})\n"
                f"- Parent-assigned task: {task}\n\n"
                "## Direct children snapshot\n"
                f"{child_overview}\n\n"
                "若需要子模块证据，调用 forkSubAgent 并自行编写子任务；需跨兄弟模块请报告上级Agent，由上级调度。\n"
                "输出必须遵循 PASS3.3.1 的 JSON 约束。\n"
            )

        if prompt_style == "instrack":
            return (
                f"# Recursive Pass3.3 InStrack Agent\n\n"
                f"- Top module: {top_node.module_name}\n"
                f"- Current level: {level}\n"
                f"- Current node: {current_node.instance_name} ({current_node.module_name})\n"
                f"- Task: {task}\n\n"
                "## Current module description\n"
                f"{self.owner._extract_summary(current_description, max_lines=20, max_chars=2400)}\n\n"
                "## Direct children snapshot\n"
                f"{child_overview}\n\n"
                "要求：若有子模块，先逐个 forkSubAgent 判断相关性，再绘制最终路径。\n"
                "最终输出只能是一个 mermaid flowchart LR 代码块，不要输出其他内容。\n"
            )

        if prompt_style != "partition":
            return (
                f"# Recursive Pass3 Agent\n\n"
                f"- Top module: {top_node.module_name}\n"
                f"- Current level: {level}\n"
                f"- Current node: {current_node.instance_name} ({current_node.module_name})\n"
                f"- Task: {task}\n\n"
                f"## Current description\n{self.owner._extract_summary(current_description, max_lines=20, max_chars=2400)}\n\n"
                f"## Direct children snapshot\n{child_overview}\n\n"
                "请输出以下结构：\n"
                "## 架构结论\n"
                "## 证据与边界\n"
                "## 任务拆解\n"
                "- 每条任务包含：目标、输入、输出、风险/待确认\n"
                "## 下钻建议\n"
            )

        output_schema = self._extract_pass3_1_output_schema()
        output_schema = output_schema.replace("## 子系统划分表格", "## 当前层级子系统划分表格")

        return (
            f"请为当前层级模块 `{current_node.instance_name}` (`{current_node.module_name}`) "
            "生成递归 Pass3.1《子系统划分》输出。\n\n"
            "## Current module description\n"
            f"{current_description}\n\n"
            "## 当前层级上下文\n"
            f"- Top module: {top_node.module_name}\n"
            f"- Current level: {level}\n"
            f"- Current node: {current_node.instance_name} ({current_node.module_name})\n"
            f"- Task: {task}\n\n"
            "## Direct children snapshot\n"
            f"{child_overview}\n\n"
            "说明：若证据不足，可调用 readDoc；若需要更深层信息，请 forkSubAgent。\n"
            "本任务只服务于 SoC level 子系统划分，不展开微架构域细分。\n\n"
            f"{output_schema}\n"
        )

    async def _run_pass3_recursive_agent(
        self,
        top_node: Any,
        current_node: Any,
        task: str = "",
        level: int = 0,
        max_depth: int = 6,
        prompt_style: str = "architecture",
        instruction: str = "",
        instruction_datasheet: str = "",
        path_records: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        node_path = current_node.get_path() if hasattr(current_node, "get_path") else current_node.instance_name
        norm_task = (task or "").strip() or "生成该层级的top-down架构概览和可执行任务拆解"
        cache_key = f"{prompt_style}||{node_path}||{norm_task}||L{level}"
        if path_records is None:
            path_records = []

        self._append_fork_trace(
            "recursive_enter",
            {
                "top_module": top_node.module_name,
                "node_path": node_path,
                "instance": current_node.instance_name,
                "module": current_node.module_name,
                "level": level,
                "prompt_style": prompt_style,
                "task": norm_task,
                "cache_key": cache_key,
            },
        )

        if prompt_style != "instrack_search" and cache_key in self._subagent_cache:
            self._append_fork_trace(
                "recursive_cache_hit",
                {
                    "node_path": node_path,
                    "instance": current_node.instance_name,
                    "module": current_node.module_name,
                    "level": level,
                    "prompt_style": prompt_style,
                    "cache_key": cache_key,
                },
            )
            return self._subagent_cache[cache_key]

        if level > max_depth:
            self._append_fork_trace(
                "recursive_depth_limit",
                {
                    "node_path": node_path,
                    "instance": current_node.instance_name,
                    "module": current_node.module_name,
                    "level": level,
                    "max_depth": max_depth,
                    "prompt_style": prompt_style,
                },
            )
            return f"[SubAgent depth={level}] Reached max_depth={max_depth}. Stop recursion."

        child_lines = []
        for child in sorted(current_node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
            child_arch = self.owner.tracker.get_pass2_7_content(child.module_name) or self.owner.tracker.get_pass2_content(child.module_name) or "无描述"
            child_lines.append(
                f"- {child.instance_name} ({child.module_name}): {self.owner._extract_summary(child_arch, max_lines=3, max_chars=320)}"
            )
        child_overview = "\n".join(child_lines) if child_lines else "- 无子模块"

        current_description = self.owner.tracker.get_pass2_content(current_node.module_name) or ""
        if not current_description:
            current_description = self.owner.tracker.get_pass2_7_content(current_node.module_name) or ""
        if not current_description:
            current_description = self.owner.tracker.get_pass1_content(current_node.module_name) or "无可用 description 文档"

        system = self._build_pass3_recursive_system(prompt_style=prompt_style)
        prompt = self._build_pass3_recursive_prompt(
            top_node=top_node,
            current_node=current_node,
            level=level,
            task=norm_task,
            instruction=instruction,
            instruction_datasheet=instruction_datasheet,
            current_description=current_description,
            child_overview=child_overview,
            prompt_style=prompt_style,
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "readSource":
                module = str(args.get("module") or "").strip()
                scoped_node, scope_error = self._resolve_scope_node(current_node, module, child_only=False)
                if scoped_node is None:
                    return (
                        "Error: instrack readSource scope violation. "
                        "At this level you can read only current module and direct children. "
                        "For deeper modules, call forkSubAgent(module, task) first. "
                        f"Details: {scope_error}"
                    )
                topology = self._build_pass2_style_topology_block(str(scoped_node.module_name))
                return f"[readSource] module={scoped_node.module_name}\n\n{topology}"

            if tool_name == "readDoc":
                module = str(args.get("module") or "")
                section = str(args.get("section") or "")
                return self._tool_read_doc_scoped(current_node, module, section)

            if tool_name == "forkSubAgent":
                module = str(args.get("module") or "")
                child_task = str(args.get("task") or "").strip()

                self._append_fork_trace(
                    "fork_request",
                    {
                        "parent_path": node_path,
                        "parent_instance": current_node.instance_name,
                        "parent_module": current_node.module_name,
                        "parent_level": level,
                        "module_selector": module,
                        "task": child_task,
                        "prompt_style": prompt_style,
                    },
                )

                child_node, error = self._resolve_scope_node(current_node, module, child_only=True)
                if child_node is None:
                    self._append_fork_trace(
                        "fork_rejected",
                        {
                            "parent_path": node_path,
                            "parent_level": level,
                            "module_selector": module,
                            "reason": error,
                            "prompt_style": prompt_style,
                        },
                    )
                    return error

                if not child_task:
                    return "Error: forkSubAgent requires a non-empty 'task'. The parent agent must define a free-form goal for the child agent."

                self._append_fork_trace(
                    "fork_dispatch",
                    {
                        "parent_path": node_path,
                        "parent_level": level,
                        "child_instance": child_node.instance_name,
                        "child_module": child_node.module_name,
                        "child_level": level + 1,
                        "task": child_task,
                        "prompt_style": prompt_style,
                    },
                )

                report = await self._run_pass3_recursive_agent(
                    top_node=top_node,
                    current_node=child_node,
                    task=child_task,
                    level=level + 1,
                    max_depth=max_depth,
                    prompt_style=prompt_style,
                    instruction=instruction,
                    instruction_datasheet=instruction_datasheet,
                    path_records=path_records,
                )
                if len(report) > 16000:
                    report = report[:16000] + "\n\n[... truncated sub-agent report ...]"

                self._append_fork_trace(
                    "fork_return",
                    {
                        "parent_path": node_path,
                        "parent_level": level,
                        "child_instance": child_node.instance_name,
                        "child_module": child_node.module_name,
                        "child_level": level + 1,
                        "report_chars": len(report),
                        "prompt_style": prompt_style,
                    },
                )

                return (
                    f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), level={level + 1}\n\n"
                    f"{report}"
                )

            return f"Error: unknown tool '{tool_name}'"

        if prompt_style == "instrack_search":
            log_path = self._build_pass3_3_1_agent_log_path(
                instruction=instruction,
                node_path=node_path,
                level=level,
                role="subagent",
            )
        else:
            log_path = self._project_log_path(
                f"pass3_recursive_{self._safe_slug(current_node.instance_name)}_L{level}"
            )

        self._append_recursive_context_log(
            log_path,
            top_node=top_node,
            current_node=current_node,
            level=level,
            prompt_style=prompt_style,
            task=norm_task,
            cache_key=cache_key,
            child_overview=child_overview,
        )

        self._append_fork_trace(
            "recursive_llm_start",
            {
                "node_path": node_path,
                "instance": current_node.instance_name,
                "module": current_node.module_name,
                "level": level,
                "prompt_style": prompt_style,
                "log_path": log_path,
            },
        )

        if prompt_style == "instrack_search":
            self._append_agent_io_snapshot(
                log_path,
                stage="Input",
                system=system,
                prompt=prompt,
            )

        report, _token_stats = await self.owner.llm.generate(
            system,
            prompt,
            log_path=log_path,
            tools_enabled=True,
            tools=self._pass3_recursive_tools(),
            tool_callback=_tool_callback,
            max_tool_rounds=14,
        )

        if prompt_style == "instrack_search":
            self._append_agent_io_snapshot(
                log_path,
                stage="Output",
                system="",
                prompt="",
                output=report or "",
            )

        self._append_fork_trace(
            "recursive_llm_done",
            {
                "node_path": node_path,
                "instance": current_node.instance_name,
                "module": current_node.module_name,
                "level": level,
                "prompt_style": prompt_style,
                "report_chars": len(report or ""),
            },
        )

        self._subagent_cache[cache_key] = report
        return report

    def _tool_tree_codebase(
        self,
        root: str = "src",
        max_depth: int = 4,
        max_entries: int = 400,
        include_files: bool = True,
        ignore: Optional[List[str]] = None,
    ) -> str:
        ignore_set = set(ignore or [
            ".git",
            ".venv",
            "__pycache__",
            ".rtl_cache",
            "oss-cad-suite",
            "rtl_docs",
            "target",
        ])

        repo_root = Path.cwd()
        root_path = (repo_root / (root or "src")).resolve()
        try:
            root_path.relative_to(repo_root.resolve())
        except Exception:
            return "Error: root must be within repo"

        if not root_path.exists() or not root_path.is_dir():
            return f"Error: root not found or not a directory: {root}"

        lines: List[str] = []
        entries = 0

        def walk(dir_path: Path, depth: int):
            nonlocal entries
            if entries >= max_entries:
                return
            if depth > max_depth:
                return

            try:
                children = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except Exception:
                return

            for child in children:
                if entries >= max_entries:
                    return
                if child.name in ignore_set:
                    continue
                if child.is_dir():
                    prefix = "  " * depth
                    rel = child.relative_to(repo_root).as_posix()
                    lines.append(f"{prefix}- {rel}/")
                    entries += 1
                    walk(child, depth + 1)
                else:
                    if not include_files:
                        continue
                    prefix = "  " * depth
                    rel = child.relative_to(repo_root).as_posix()
                    lines.append(f"{prefix}- {rel}")
                    entries += 1

        lines.append(f"- {root_path.relative_to(repo_root).as_posix()}/")
        walk(root_path, 1)

        if entries >= max_entries:
            lines.append(f"\n[... truncated: reached max_entries={max_entries} ...]")

        return "[treeCodebase]\n\n" + "\n".join(lines)

    def _tool_read_doc(
        self,
        module: str,
        doc: str = "auto",
        sections: Optional[List[str]] = None,
        max_chars: int = 0,
    ) -> str:
        module_name = (module or "").strip()
        if not module_name:
            return "Error: module is empty"

        doc_key = (doc or "auto").strip().lower()
        name_map = {
            "preview": "preview.md",
            "architecture": "architecture.md",
            "description": "description.md",
            "interface_spec": "interface_spec.md",
            "design_highlights": "design_highlights.md",
            "functional_desc": "functional_desc.md",
            "register_desc": "register_desc.md",
            "timing_cdc": "timing_cdc.md",
            "block_docs": "block_docs.md",
            "flowchart": "flowchart.mmd",
            "metadata": "metadata.json",
        }

        module_dir = self.owner.modules_dir / module_name
        if not module_dir.exists():
            return f"Error: module docs not found for '{module_name}'"

        if doc_key == "auto":
            candidates = ["architecture.md", "description.md", "preview.md", "interface_spec.md"]
            chosen = None
            for fn in candidates:
                p = module_dir / fn
                if p.exists():
                    chosen = p
                    break
            if chosen is None:
                return f"Error: no doc files found for '{module_name}'"
            path = chosen
        else:
            fn = name_map.get(doc_key)
            if not fn:
                return f"Error: unknown doc type '{doc_key}'"
            path = module_dir / fn

        if not path.exists():
            return f"Error: doc file not found: {path.name}"

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error: failed to read {path.name}: {e}"

        if path.suffix == ".json":
            try:
                obj = json.loads(text)
                text = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                pass

        extracted_parts: List[str] = []
        if sections:
            for sec in sections:
                sec = (sec or "").strip()
                if not sec:
                    continue
                block = self.owner._extract_named_section(text, [sec])
                if block:
                    extracted_parts.append(block)
            if extracted_parts:
                text = "\n\n---\n\n".join(extracted_parts)

        if max_chars and len(text) > int(max_chars):
            text = text[: int(max_chars)] + "\n\n[... truncated ...]"

        return f"[readDoc] module={module_name}, file={path.name}\n\n{text}"

    async def run_pass3_1(self, top_node: Any) -> Optional[str]:
        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)

        artifact_md = "subsystem_partition.md"
        artifact_json = "subsystem_partition.json"
        out_md = self.owner.chip_dir / artifact_md
        out_json = self.owner.chip_dir / artifact_json

        top_description = self.owner.tracker.get_pass2_content(top_node.module_name) or ""
        if not top_description:
            top_description = self.owner.tracker.get_pass2_7_content(top_node.module_name) or ""
        if not top_description:
            top_description = self.owner.tracker.get_pass1_content(top_node.module_name) or "无可用 description 文档"

        input_hash = self._build_pass3_1_input_hash(
            top_module=top_node.module_name,
            top_description=top_description,
        )

        if self.owner.project_tracker.is_done(artifact_md, str(out_md), expected_input_hash=input_hash):
            try:
                content = out_md.read_text(encoding="utf-8")
                if (not out_json.exists()) or (
                    not self.owner.project_tracker.is_done(artifact_json, str(out_json), expected_input_hash=input_hash)
                ):
                    partition_json = self._extract_pass3_1_partition_json(content, top_node)
                    if partition_json:
                        json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
                        out_json.write_text(json_text, encoding="utf-8")
                        self.owner.project_tracker.update(
                            artifact_json,
                            json_text,
                            input_hash=input_hash,
                            meta={"source": artifact_md, "parser": "partition_table_v1"},
                        )
                return content
            except Exception:
                return None

        prompt = PASS3_1_PROMPT.format(
            top_module=top_node.module_name,
            top_description=top_description,
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "readDoc":
                module = args.get("module") or args.get("module_name") or ""
                doc = args.get("doc") or "auto"
                sections = args.get("sections")
                section = args.get("section")
                if section and sections is None:
                    sections = [str(section)]
                if sections is not None and not isinstance(sections, list):
                    sections = [str(sections)]
                max_chars = args.get("max_chars", 0)
                try:
                    max_chars = int(max_chars)
                except Exception:
                    max_chars = 0
                return self._tool_read_doc(module=str(module), doc=str(doc), sections=sections, max_chars=max_chars)

            if tool_name == "forkSubAgent":
                module = str(args.get("module") or "")
                task = str(args.get("task") or "").strip()
                self._append_fork_trace(
                    "pass3_1_fork_request",
                    {
                        "top_module": top_node.module_name,
                        "module_selector": module,
                        "task": task,
                        "prompt_style": "partition",
                    },
                )
                child_node, error = self._resolve_scope_node(top_node, module, child_only=True)
                if child_node is None:
                    self._append_fork_trace(
                        "pass3_1_fork_rejected",
                        {
                            "top_module": top_node.module_name,
                            "module_selector": module,
                            "reason": error,
                            "prompt_style": "partition",
                        },
                    )
                    return error
                if not task:
                    task = (
                        f"请针对 {child_node.instance_name}({child_node.module_name}) 补充 SoC 子系统边界证据，"
                        "输出可归属的 SoC 子系统、边界依据与待确认项，并按需继续 fork。"
                    )
                self._append_fork_trace(
                    "pass3_1_fork_dispatch",
                    {
                        "top_module": top_node.module_name,
                        "child_instance": child_node.instance_name,
                        "child_module": child_node.module_name,
                        "child_level": 1,
                        "task": task,
                        "prompt_style": "partition",
                    },
                )
                report = await self._run_pass3_recursive_agent(
                    top_node=top_node,
                    current_node=child_node,
                    task=task,
                    level=1,
                    prompt_style="partition",
                )
                if len(report) > 16000:
                    report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                self._append_fork_trace(
                    "pass3_1_fork_return",
                    {
                        "top_module": top_node.module_name,
                        "child_instance": child_node.instance_name,
                        "child_module": child_node.module_name,
                        "child_level": 1,
                        "report_chars": len(report),
                        "prompt_style": "partition",
                    },
                )
                return (
                    f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), level=1\n\n"
                    f"{report}"
                )

            return f"Error: unknown tool '{tool_name}'"

        log_path = self._project_log_path("pass3_1_partition")
        self.owner.project_tracker.mark_running(
            artifact_md,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_1"},
        )
        try:
            content, _token_stats = await self.owner.llm.generate(
                PASS3_1_SYSTEM,
                prompt,
                log_path=log_path,
                tools_enabled=True,
                tools=self._pass3_1_tools(),
                tool_callback=_tool_callback,
                max_tool_rounds=10,
            )
        except Exception as e:
            self.owner.project_tracker.mark_failed(artifact_md, str(e))
            return None

        out_md.write_text(content, encoding="utf-8")
        self.owner.project_tracker.update(
            artifact_md,
            content,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_1"},
        )

        partition_json = self._extract_pass3_1_partition_json(content, top_node)
        if partition_json:
            json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
            out_json.write_text(json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_json,
                json_text,
                input_hash=input_hash,
                meta={"source": artifact_md, "parser": "partition_table_v1"},
            )

        return content

    async def run_pass3_2(self, top_node: Any) -> Optional[str]:
        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)

        artifact_md = "core_partition.md"
        artifact_json = "core_partition.json"
        out_md = self.owner.chip_dir / artifact_md
        out_json = self.owner.chip_dir / artifact_json

        top_description = self.owner.tracker.get_pass2_content(top_node.module_name) or ""
        if not top_description:
            top_description = self.owner.tracker.get_pass2_7_content(top_node.module_name) or ""
        if not top_description:
            top_description = self.owner.tracker.get_pass1_content(top_node.module_name) or "无可用 description 文档"

        pass3_1_path = self.owner.chip_dir / "subsystem_partition.md"
        subsystem_partition_text = "未检测到 pass3.1 输出，可基于工具证据自行定位 core。"
        if pass3_1_path.exists():
            try:
                subsystem_partition_text = pass3_1_path.read_text(encoding="utf-8")
            except Exception:
                pass

        input_hash = self._build_pass3_2_input_hash(
            top_module=top_node.module_name,
            top_description=top_description,
            subsystem_partition=subsystem_partition_text,
        )

        if self.owner.project_tracker.is_done(artifact_md, str(out_md), expected_input_hash=input_hash):
            try:
                content = out_md.read_text(encoding="utf-8")
                if (not out_json.exists()) or (
                    not self.owner.project_tracker.is_done(artifact_json, str(out_json), expected_input_hash=input_hash)
                ):
                    partition_json = self._extract_pass3_2_partition_json(content, top_node)
                    if partition_json:
                        json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
                        out_json.write_text(json_text, encoding="utf-8")
                        self.owner.project_tracker.update(
                            artifact_json,
                            json_text,
                            input_hash=input_hash,
                            meta={"source": artifact_md, "parser": "core_partition_table_v1"},
                        )
                return content
            except Exception:
                return None

        prompt = PASS3_2_PROMPT.format(
            top_module=top_node.module_name,
            top_description=top_description,
            subsystem_partition=self.owner._extract_summary(subsystem_partition_text, max_lines=80, max_chars=10000),
        )

        async def _tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
            if tool_name == "exploreCore":
                max_candidates = args.get("max_candidates", 10)
                try:
                    max_candidates = int(max_candidates)
                except Exception:
                    max_candidates = 10
                return self._tool_explore_core(top_node, max_candidates=max_candidates)

            if tool_name == "readDoc":
                module = args.get("module") or args.get("module_name") or ""
                doc = args.get("doc") or "auto"
                sections = args.get("sections")
                section = args.get("section")
                if section and sections is None:
                    sections = [str(section)]
                if sections is not None and not isinstance(sections, list):
                    sections = [str(sections)]
                max_chars = args.get("max_chars", 0)
                try:
                    max_chars = int(max_chars)
                except Exception:
                    max_chars = 0
                return self._tool_read_doc(module=str(module), doc=str(doc), sections=sections, max_chars=max_chars)

            if tool_name == "forkSubAgent":
                module = str(args.get("module") or "")
                task = str(args.get("task") or "").strip()
                self._append_fork_trace(
                    "pass3_2_fork_request",
                    {
                        "top_module": top_node.module_name,
                        "module_selector": module,
                        "task": task,
                        "prompt_style": "partition",
                    },
                )
                child_node, error = self._resolve_scope_node(top_node, module, child_only=True)
                if child_node is None:
                    self._append_fork_trace(
                        "pass3_2_fork_rejected",
                        {
                            "top_module": top_node.module_name,
                            "module_selector": module,
                            "reason": error,
                            "prompt_style": "partition",
                        },
                    )
                    return error
                if not task:
                    task = (
                        f"请针对 {child_node.instance_name}({child_node.module_name}) 输出 core 相关 top-down 微架构划分，"
                        "并按需继续fork下一级。"
                    )
                self._append_fork_trace(
                    "pass3_2_fork_dispatch",
                    {
                        "top_module": top_node.module_name,
                        "child_instance": child_node.instance_name,
                        "child_module": child_node.module_name,
                        "child_level": 1,
                        "task": task,
                        "prompt_style": "partition",
                    },
                )
                report = await self._run_pass3_recursive_agent(
                    top_node=top_node,
                    current_node=child_node,
                    task=task,
                    level=1,
                    prompt_style="partition",
                )
                if len(report) > 16000:
                    report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                self._append_fork_trace(
                    "pass3_2_fork_return",
                    {
                        "top_module": top_node.module_name,
                        "child_instance": child_node.instance_name,
                        "child_module": child_node.module_name,
                        "child_level": 1,
                        "report_chars": len(report),
                        "prompt_style": "partition",
                    },
                )
                return (
                    f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), level=1\n\n"
                    f"{report}"
                )

            return f"Error: unknown tool '{tool_name}'"

        log_path = self._project_log_path("pass3_2_core_partition")
        self.owner.project_tracker.mark_running(
            artifact_md,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_2"},
        )

        try:
            content, _token_stats = await self.owner.llm.generate(
                PASS3_2_SYSTEM,
                prompt,
                log_path=log_path,
                tools_enabled=True,
                tools=self._pass3_2_tools(),
                tool_callback=_tool_callback,
                max_tool_rounds=12,
            )
        except Exception as e:
            self.owner.project_tracker.mark_failed(artifact_md, str(e))
            return None

        out_md.write_text(content, encoding="utf-8")
        self.owner.project_tracker.update(
            artifact_md,
            content,
            input_hash=input_hash,
            meta={"top_module": top_node.module_name, "pass": "pass3_2"},
        )

        partition_json = self._extract_pass3_2_partition_json(content, top_node)
        if partition_json:
            json_text = json.dumps(partition_json, ensure_ascii=False, indent=2)
            out_json.write_text(json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_json,
                json_text,
                input_hash=input_hash,
                meta={"source": artifact_md, "parser": "core_partition_table_v1"},
            )

        return content

    async def run_pass3_3(self, top_node: Any) -> Optional[str]:
        if not getattr(self.owner, "pass3_3_enabled", True):
            return None

        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)
        instrack_dir = self.owner.chip_dir / "instrack"
        instrack_dir.mkdir(parents=True, exist_ok=True)

        instructions = self._resolve_instrack_instructions()
        if not instructions:
            return None

        top_description = self.owner.tracker.get_pass2_content(top_node.module_name) or ""
        if not top_description:
            top_description = self.owner.tracker.get_pass2_7_content(top_node.module_name) or ""
        if not top_description:
            top_description = self.owner.tracker.get_pass1_content(top_node.module_name) or "无可用 description 文档"

        pass3_2_path = self.owner.chip_dir / "core_partition.md"
        core_partition_text = "未检测到 pass3.2 输出，可结合 readDoc 工具补齐证据。"
        if pass3_2_path.exists():
            try:
                core_partition_text = pass3_2_path.read_text(encoding="utf-8")
            except Exception:
                pass

        index_entries: List[Dict[str, Any]] = []
        datasheet_text = self._load_instrack_datasheet()

        for instruction in instructions:
            slug = self._safe_slug(instruction)
            artifact_search_md = f"instrack/{slug}.search.md"
            artifact_search_json = f"instrack/{slug}.search.json"
            artifact_md = f"instrack/{slug}.md"
            artifact_json = f"instrack/{slug}.json"
            out_search_md = instrack_dir / f"{slug}.search.md"
            out_search_json = instrack_dir / f"{slug}.search.json"
            out_md = instrack_dir / f"{slug}.md"
            out_json = instrack_dir / f"{slug}.json"
            instruction_datasheet = self._extract_instruction_datasheet_excerpt(datasheet_text, instruction)

            search_input_hash = self._build_pass3_3_search_input_hash(
                top_module=top_node.module_name,
                instruction=instruction,
                top_description=top_description,
                core_partition=core_partition_text,
                instruction_datasheet=instruction_datasheet,
            )

            search_content: Optional[str] = None
            if self.owner.project_tracker.is_done(artifact_search_md, str(out_search_md), expected_input_hash=search_input_hash):
                try:
                    search_content = out_search_md.read_text(encoding="utf-8")
                except Exception:
                    search_content = None

            if search_content is None:
                search_prompt = PASS3_3_1_SEARCH_PROMPT.format(
                    top_module=top_node.module_name,
                    instruction=instruction,
                    module_description=top_description,
                    core_partition=self.owner._extract_summary(core_partition_text, max_lines=80, max_chars=12000),
                    instruction_datasheet=instruction_datasheet,
                )

                async def _search_tool_callback(tool_name: str, args: Dict[str, Any]) -> str:
                    if tool_name == "readSource":
                        module = str(args.get("module") or "").strip()
                        if not module:
                            return "Error: module is required"
                        scoped_node, scope_error = self._resolve_scope_node(top_node, module, child_only=False)
                        if scoped_node is None:
                            return (
                                "Error: instrack readSource scope violation. "
                                "At this level you can read only current module and direct children. "
                                "For deeper modules, call forkSubAgent(module, task) first. "
                                f"Details: {scope_error}"
                            )
                        topology = self._build_pass2_style_topology_block(str(scoped_node.module_name))
                        return f"[readSource] module={scoped_node.module_name}\n\n{topology}"

                    if tool_name == "readDoc":
                        module = args.get("module") or args.get("module_name") or ""
                        doc = args.get("doc") or "auto"
                        sections = args.get("sections")
                        section = args.get("section")
                        if section and sections is None:
                            sections = [str(section)]
                        if sections is not None and not isinstance(sections, list):
                            sections = [str(sections)]
                        max_chars = args.get("max_chars", 0)
                        try:
                            max_chars = int(max_chars)
                        except Exception:
                            max_chars = 0
                        scoped_node, scope_error = self._resolve_scope_node(top_node, str(module), child_only=False)
                        if scoped_node is None:
                            return (
                                "Error: instrack readDoc scope violation. "
                                "At this level you can read only current module and direct children. "
                                "For deeper modules, call forkSubAgent(module, task) first. "
                                f"Details: {scope_error}"
                            )
                        return self._tool_read_doc(
                            module=str(scoped_node.module_name),
                            doc=str(doc),
                            sections=sections,
                            max_chars=max_chars,
                        )

                    if tool_name == "forkSubAgent":
                        module = str(args.get("module") or "")
                        task = str(args.get("task") or "").strip()
                        child_node, error = self._resolve_scope_node(top_node, module, child_only=True)
                        if child_node is None:
                            return error

                        if not task:
                            return "Error: forkSubAgent requires a non-empty 'task'. Define a free-form child goal from the parent context."
                        report = await self._run_pass3_recursive_agent(
                            top_node=top_node,
                            current_node=child_node,
                            task=task,
                            level=1,
                            prompt_style="instrack_search",
                            instruction=instruction,
                            instruction_datasheet=instruction_datasheet,
                            path_records=[],
                        )
                        if len(report) > 16000:
                            report = report[:16000] + "\n\n[... truncated sub-agent report ...]"
                        return (
                            f"[forkSubAgent] child={child_node.instance_name}({child_node.module_name}), level=1\n\n"
                            f"{report}"
                        )

                    return f"Error: unknown tool '{tool_name}'"

                search_log_path = self._build_pass3_3_1_agent_log_path(
                    instruction=instruction,
                    node_path=top_node.instance_name if hasattr(top_node, "instance_name") else top_node.module_name,
                    level=0,
                    role="search_agent",
                )
                self.owner.project_tracker.mark_running(
                    artifact_search_md,
                    input_hash=search_input_hash,
                    meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_1"},
                )

                self._append_agent_io_snapshot(
                    search_log_path,
                    stage="Input",
                    system=PASS3_3_1_SEARCH_SYSTEM,
                    prompt=search_prompt,
                )

                try:
                    search_content, _token_stats = await self.owner.llm.generate(
                        PASS3_3_1_SEARCH_SYSTEM,
                        search_prompt,
                        log_path=search_log_path,
                        tools_enabled=True,
                        tools=self._pass3_3_1_tools(),
                        tool_callback=_search_tool_callback,
                        max_tool_rounds=12,
                    )
                except Exception as e:
                    self._append_agent_io_snapshot(
                        search_log_path,
                        stage="Output",
                        system="",
                        prompt="",
                        output="",
                        error=str(e),
                    )
                    self.owner.project_tracker.mark_failed(artifact_search_md, str(e))
                    index_entries.append({
                        "instruction": instruction,
                        "slug": slug,
                        "status": "failed",
                        "stage": "search",
                        "error": str(e),
                    })
                    continue

                self._append_agent_io_snapshot(
                    search_log_path,
                    stage="Output",
                    system="",
                    prompt="",
                    output=search_content or "",
                )

                search_content = self._coerce_instrack_search_json_only(search_content or "", top_node, instruction)

                out_search_md.write_text(search_content, encoding="utf-8")
                self.owner.project_tracker.update(
                    artifact_search_md,
                    search_content,
                    input_hash=search_input_hash,
                    meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_1"},
                )

            parsed_search = self._extract_pass3_3_search_json(search_content or "", top_node, instruction)
            search_json_text = json.dumps(parsed_search, ensure_ascii=False, indent=2)
            out_search_json.write_text(search_json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_search_json,
                search_json_text,
                input_hash=search_input_hash,
                meta={"source": artifact_search_md, "parser": "instrack_startpoint_v1"},
            )

            # Pass3.3.2 lifecycle mermaid generation is temporarily disabled.
            # Keep .md as an exact JSON-codeblock mirror for easy human inspection.
            out_md.write_text(search_content or "", encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_md,
                search_content or "",
                input_hash=search_input_hash,
                meta={"top_module": top_node.module_name, "instruction": instruction, "pass": "pass3_3_search_only"},
            )

            json_text = json.dumps(parsed_search, ensure_ascii=False, indent=2)
            out_json.write_text(json_text, encoding="utf-8")
            self.owner.project_tracker.update(
                artifact_json,
                json_text,
                input_hash=search_input_hash,
                meta={"source": artifact_md, "parser": "instrack_startpoint_only_v1"},
            )

            index_entries.append({
                "instruction": instruction,
                "slug": slug,
                "status": "done",
                "search_artifact_md": artifact_search_md,
                "search_artifact_json": artifact_search_json,
                "artifact_md": artifact_md,
                "artifact_json": artifact_json,
                "start_module": parsed_search.get("start_module", ""),
                "start_block": parsed_search.get("start_block", ""),
                "key_register": parsed_search.get("key_register", ""),
                "search_confidence": parsed_search.get("confidence", "low"),
            })

        index_payload = {
            "top_module": top_node.module_name,
            "isa_profile": getattr(self.owner, "isa_profile", "c910"),
            "total": len(index_entries),
            "entries": index_entries,
        }
        index_text = json.dumps(index_payload, ensure_ascii=False, indent=2)
        index_path = instrack_dir / "index.json"
        index_path.write_text(index_text, encoding="utf-8")
        self.owner.project_tracker.update(
            "instrack/index.json",
            index_text,
            input_hash=self._hash_text(index_text),
            meta={"pass": "pass3_3", "type": "instrack_index"},
        )

        return index_text

    def _resolve_module_doc_rel_path(self, module_name: str, from_dir: Path) -> str:
        module_dir = self.owner.modules_dir / module_name
        candidates = ["architecture.md", "description.md", "preview.md"]

        target = module_dir / "preview.md"
        for name in candidates:
            candidate = module_dir / name
            if candidate.exists():
                target = candidate
                break

        rel_path = os.path.relpath(str(target), str(from_dir))
        return self._encode_rel_path(rel_path)

    def _collect_subtree_nodes(self, node: Any) -> List[Any]:
        result: List[Any] = []
        queue = [node]
        while queue:
            current = queue.pop(0)
            result.append(current)
            for child in current.children.values():
                queue.append(child)
        return result

    def _select_key_nodes(self, subsystem_root: Any) -> List[Any]:
        nodes = self._collect_subtree_nodes(subsystem_root)

        def score(n: Any):
            return (
                n.depth,
                -len(n.children),
                n.module_name,
                n.instance_name,
            )

        nodes = sorted(nodes, key=score)
        limit = max(1, self.owner.pass3_key_modules_per_subsystem)
        return nodes[:limit]

    def _render_hierarchy_tree_with_links(self, root: Any, from_dir: Path, max_depth: Optional[int] = None) -> str:
        lines: List[str] = []

        def walk(node: Any, depth: int):
            if max_depth is not None and depth > max_depth:
                return
            rel_doc = self._resolve_module_doc_rel_path(node.module_name, from_dir)
            line = (
                f"{'  ' * depth}- `{node.instance_name}` ({node.module_name}) "
                f"-> [module doc]({rel_doc})"
            )
            lines.append(line)
            for child in sorted(node.children.values(), key=lambda c: (c.module_name, c.instance_name)):
                walk(child, depth + 1)

        walk(root, 0)
        return "\n".join(lines)

    def _build_module_card(self, node: Any, from_dir: Path) -> str:
        module_name = node.module_name
        preview = self.owner.tracker.get_pass1_content(module_name) or "无预览"
        architecture = self.owner.tracker.get_pass2_7_content(module_name) or ""
        description = self.owner.tracker.get_pass2_content(module_name) or ""
        summary_source = architecture or description or preview

        preview_short = self.owner._extract_summary(preview, max_lines=3, max_chars=450)
        summary_short = self.owner._extract_summary(
            summary_source,
            max_lines=max(4, self.owner.pass3_max_card_lines),
            max_chars=1200,
        )
        port_short = self.owner._extract_summary(self.owner.resolver.get_port_summary(module_name), max_lines=8, max_chars=800)
        rel_doc = self._resolve_module_doc_rel_path(module_name, from_dir)

        return (
            f"### {node.instance_name} ({module_name})\n"
            f"- 文档: [module doc]({rel_doc})\n"
            f"- 子模块数量: {len(node.children)}\n"
            f"- 预览摘要:\n{preview_short}\n\n"
            f"- 关键说明:\n{summary_short}\n\n"
            f"- 端口概览:\n{port_short}\n"
        )

    def _partition_to_groups(self, partition: Optional[Dict[str, Any]], root: Any) -> Optional[List[Dict[str, Any]]]:
        if not partition or not isinstance(partition, dict):
            return None
        subsystems = partition.get("subsystems")
        if not isinstance(subsystems, list) or not subsystems:
            return None

        path_index = self._build_instance_path_index(root)
        groups: List[Dict[str, Any]] = []
        for ss in subsystems:
            if not isinstance(ss, dict):
                continue
            ss_id = str(ss.get("id") or "")
            name = str(ss.get("name") or ss_id or "subsystem")
            roots = ss.get("roots")
            if not isinstance(roots, list) or not roots:
                continue
            root_nodes: List[Tuple[str, Any]] = []
            for r in roots:
                if not isinstance(r, dict):
                    continue
                path = str(r.get("instance_path") or "").strip().strip("/")
                if path == "<top>":
                    path = ""
                if path not in path_index:
                    continue
                node = path_index[path]
                if self.owner._should_skip(node.module_name):
                    continue
                root_nodes.append((path, node))
            if not root_nodes:
                continue
            groups.append({
                "id": ss_id,
                "name": name,
                "root_nodes": root_nodes,
                "intent": ss.get("intent", ""),
            })
        return groups or None

    async def run_pass3(self, root: Any):
        self.owner.chip_dir.mkdir(parents=True, exist_ok=True)
        self.owner.chip_subsystems_dir.mkdir(parents=True, exist_ok=True)

        partition = None
        try:
            partition_path = self.owner.chip_dir / "subsystem_partition.json"
            if partition_path.exists():
                partition = json.loads(partition_path.read_text(encoding="utf-8"))
        except Exception:
            partition = None

        groups = self._partition_to_groups(partition, root)

        subsystem_entries: List[Dict[str, str]] = []
        if groups:
            for g in groups:
                entry = await self._run_pass3_subsystem_group(root, g)
                if entry:
                    subsystem_entries.append(entry)
        else:
            for child in sorted(root.children.values(), key=lambda c: (c.module_name, c.instance_name)):
                if self.owner._should_skip(child.module_name):
                    continue
                entry = await self._run_pass3_subsystem(root, child)
                if entry:
                    subsystem_entries.append(entry)

        await self._generate_chip_overview(root, subsystem_entries)

    async def _run_pass3_subsystem_group(self, top_node: Any, group: Dict[str, Any]) -> Optional[Dict[str, str]]:
        group_name = str(group.get("name") or "subsystem")
        group_id = str(group.get("id") or "")
        root_nodes: List[Tuple[str, Any]] = group.get("root_nodes") or []
        if not root_nodes:
            return None

        file_name = f"{self._safe_slug(group_id)}__{self._safe_slug(group_name)}.md" if group_id else f"{self._safe_slug(group_name)}.md"
        artifact = f"subsystems/{file_name}"
        output_path = self.owner.chip_subsystems_dir / file_name

        if self.owner.project_tracker.is_done(artifact, str(output_path)):
            content = output_path.read_text(encoding="utf-8")
            return {
                "instance": group_name,
                "module": "multi-root",
                "artifact": artifact,
                "summary": self.owner._extract_summary(content, max_lines=12, max_chars=1200),
            }

        all_nodes: List[Any] = []
        seen_ids: set = set()
        for _path, node in root_nodes:
            for n in self._collect_subtree_nodes(node):
                nid = id(n)
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                all_nodes.append(n)

        def score(n: Any):
            return (n.depth, -len(n.children), n.module_name, n.instance_name)

        key_nodes = sorted(all_nodes, key=score)[: max(1, self.owner.pass3_key_modules_per_subsystem)]

        tree_parts: List[str] = []
        for path, node in root_nodes:
            tree_parts.append(f"### Root: {path} ({node.module_name})")
            tree_parts.append(
                self._render_hierarchy_tree_with_links(
                    node,
                    from_dir=self.owner.chip_subsystems_dir,
                    max_depth=self.owner.pass3_tree_max_depth_in_doc,
                )
            )
        subsystem_tree = "\n".join(tree_parts)

        module_cards = []
        for key_node in key_nodes:
            module_cards.append(self._build_module_card(key_node, from_dir=self.owner.chip_subsystems_dir))

        table_lines = [
            "| Instance | Module | Children | Link |",
            "|---|---|---:|---|",
        ]
        for n in sorted(all_nodes, key=lambda x: (x.module_name, x.instance_name)):
            rel_doc = self._resolve_module_doc_rel_path(n.module_name, self.owner.chip_subsystems_dir)
            table_lines.append(
                f"| {n.instance_name} | {n.module_name} | {len(n.children)} | [doc]({rel_doc}) |"
            )
        module_table = "\n".join(table_lines)

        sub_reports: List[str] = []
        for path, node in root_nodes:
            report = await self._run_pass3_recursive_agent(
                top_node=top_node,
                current_node=node,
                task=(
                    f"为分组子系统 `{group_name}` 下根 `{path}` 生成 top-down 架构文档与任务拆解；"
                    "若需更深信息请递归 forkSubAgent。"
                ),
                level=1,
            )
            sub_reports.append(f"### Root: {path} ({node.module_name})\n\n{report}")
        subsystem_body = "\n\n".join(sub_reports) if sub_reports else "无可用子报告"

        index_block = [
            f"# Subsystem Overview: {group_name}",
            "",
            "## 内嵌索引",
            "",
            "### 子系统层次树",
            subsystem_tree,
            "",
            "### 模块链接表",
            module_table,
            "",
            "## 架构叙述",
            "",
            subsystem_body,
            "",
        ]
        final_content = "\n".join(index_block)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_content, encoding="utf-8")
        self.owner.project_tracker.update(artifact, final_content)

        return {
            "instance": group_name,
            "module": "multi-root",
            "artifact": artifact,
            "summary": self.owner._extract_summary(subsystem_body, max_lines=12, max_chars=1200),
        }

    async def _run_pass3_subsystem(self, top_node: Any, subsystem_node: Any) -> Optional[Dict[str, str]]:
        file_name = f"{self._safe_slug(subsystem_node.instance_name)}__{self._safe_slug(subsystem_node.module_name)}.md"
        artifact = f"subsystems/{file_name}"
        output_path = self.owner.chip_subsystems_dir / file_name

        if self.owner.project_tracker.is_done(artifact, str(output_path)):
            content = output_path.read_text(encoding="utf-8")
            return {
                "instance": subsystem_node.instance_name,
                "module": subsystem_node.module_name,
                "artifact": artifact,
                "summary": self.owner._extract_summary(content, max_lines=12, max_chars=1200),
            }

        all_nodes = self._collect_subtree_nodes(subsystem_node)
        key_nodes = self._select_key_nodes(subsystem_node)

        subsystem_tree = self._render_hierarchy_tree_with_links(
            subsystem_node,
            from_dir=self.owner.chip_subsystems_dir,
            max_depth=self.owner.pass3_tree_max_depth_in_doc,
        )

        module_cards = []
        for key_node in key_nodes:
            module_cards.append(self._build_module_card(key_node, from_dir=self.owner.chip_subsystems_dir))

        table_lines = [
            "| Instance | Module | Children | Link |",
            "|---|---|---:|---|",
        ]
        for n in all_nodes:
            rel_doc = self._resolve_module_doc_rel_path(n.module_name, self.owner.chip_subsystems_dir)
            table_lines.append(
                f"| {n.instance_name} | {n.module_name} | {len(n.children)} | [doc]({rel_doc}) |"
            )
        module_table = "\n".join(table_lines)

        subsystem_body = await self._run_pass3_recursive_agent(
            top_node=top_node,
            current_node=subsystem_node,
            task=(
                f"为子系统 {subsystem_node.instance_name}({subsystem_node.module_name}) 生成 top-down 架构文档与任务拆解；"
                "需要更深层信息时请递归 forkSubAgent。"
            ),
            level=1,
        )

        index_block = [
            f"# Subsystem Overview: {subsystem_node.instance_name} ({subsystem_node.module_name})",
            "",
            "## 内嵌索引",
            "",
            "### 子系统层次树",
            subsystem_tree,
            "",
            "### 模块链接表",
            module_table,
            "",
            "## 架构叙述",
            "",
            subsystem_body,
            "",
        ]
        final_content = "\n".join(index_block)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_content, encoding="utf-8")
        self.owner.project_tracker.update(artifact, final_content)

        return {
            "instance": subsystem_node.instance_name,
            "module": subsystem_node.module_name,
            "artifact": artifact,
            "summary": self.owner._extract_summary(subsystem_body, max_lines=12, max_chars=1200),
        }

    async def _generate_chip_overview(self, top_node: Any, subsystem_entries: List[Dict[str, str]]):
        artifact = "overview.md"
        output_path = self.owner.chip_dir / "overview.md"

        if self.owner.project_tracker.is_done(artifact, str(output_path)):
            return

        table_lines = [
            "| Subsystem Instance | Subsystem Module | Subsystem Page | Module Doc |",
            "|---|---|---|---|",
        ]
        for entry in subsystem_entries:
            subsystem_page_rel = self._encode_rel_path(entry["artifact"])
            module_doc_rel = self._resolve_module_doc_rel_path(entry["module"], self.owner.chip_dir)
            table_lines.append(
                f"| {entry['instance']} | {entry['module']} | [overview]({subsystem_page_rel}) | [module doc]({module_doc_rel}) |"
            )

        subsystem_table = "\n".join(table_lines) if subsystem_entries else "无子系统"
        hierarchy_index = self._render_hierarchy_tree_with_links(
            top_node,
            from_dir=self.owner.chip_dir,
            max_depth=self.owner.pass3_tree_max_depth_in_doc,
        )

        overview_body = await self._run_pass3_recursive_agent(
            top_node=top_node,
            current_node=top_node,
            task=(
                "生成芯片级 top-down 架构总览与任务拆解；"
                "对于 deeper 子系统信息请通过 forkSubAgent 下钻。"
            ),
            level=0,
        )

        final_parts = [
            "# CHIP 架构概览",
            "",
            "## 内嵌索引",
            "",
            "### 子系统划分（Pass 3.1）",
            "- 子系统划分输出: [subsystem_partition.md](subsystem_partition.md)",
            "- Core 微架构划分: [core_partition.md](core_partition.md)",
            "",
            "### 子系统总览",
            subsystem_table,
            "",
            "### 层次结构索引",
            hierarchy_index,
            "",
            "## 架构叙述",
            "",
            overview_body,
            "",
        ]
        final_content = "\n".join(final_parts)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_content, encoding="utf-8")
        self.owner.project_tracker.update(artifact, final_content)
