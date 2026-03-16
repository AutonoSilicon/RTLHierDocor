"""Pass3 ISA (Instruction Set Architecture) data.

This module encapsulates instruction set profiles and datasheet handling
for instruction tracking functionality.
"""

from pathlib import Path
from typing import Any, Dict, List

from .pass3_utils import normalize_instruction


def instruction_profiles() -> Dict[str, List[str]]:
    """Get instruction profiles for different ISA configurations.

    Returns a dict mapping profile names to lists of instruction mnemonics.
    Profiles: rv64i, rv64gc, c910
    """
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


def instrack_datasheet_path() -> Path:
    """Get the default path to the instruction datasheet."""
    return Path.cwd() / "docs" / "rv64gc_instruction_datasheet.md"


class Pass3ISA:
    """Helper object encapsulating ISA/instruction data handling."""

    def __init__(self, generator: Any):
        self.g = generator

    def load_instrack_datasheet(self) -> str:
        """Load the instruction datasheet from disk."""
        path = instrack_datasheet_path()
        if not path.exists():
            return "未找到 datasheet 文件：docs/rv64gc_instruction_datasheet.md"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"读取 datasheet 失败: {e}"

    def extract_instruction_datasheet_excerpt(self, datasheet_text: str, instruction: str) -> str:
        """Extract the datasheet section for a specific instruction.

        Args:
            datasheet_text: Full text of the datasheet
            instruction: Instruction mnemonic to find

        Returns:
            The excerpt for the instruction, or an error message
        """
        text = (datasheet_text or "").strip()
        if not text:
            return "datasheet 为空，无法提供指令文段。"

        target = normalize_instruction(instruction)
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
            return normalize_instruction(token)

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
        return block

    def resolve_instrack_instructions(self) -> List[str]:
        """Resolve the list of instructions to track.

        Priority:
        1. Single instruction override (instrack_single_instruction)
        2. Configured list (isa_instructions)
        3. Profile-based (isa_profile)
        """
        single = normalize_instruction(
            getattr(self.g.owner, "instrack_single_instruction", "") or ""
        )
        if single:
            return [single]

        configured = [
            normalize_instruction(i)
            for i in (getattr(self.g.owner, "isa_instructions", []) or [])
            if normalize_instruction(i)
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

        profile = str(getattr(self.g.owner, "isa_profile", "c910") or "c910").strip().lower()
        profiles = instruction_profiles()
        base = profiles.get(profile, profiles["c910"])
        return [normalize_instruction(i) for i in base if normalize_instruction(i)]

    def get_profile_names(self) -> List[str]:
        """Get list of available ISA profile names."""
        return list(instruction_profiles().keys())

    def get_profile_instructions(self, profile: str) -> List[str]:
        """Get instructions for a specific profile."""
        profiles = instruction_profiles()
        return profiles.get(profile, profiles["c910"])
