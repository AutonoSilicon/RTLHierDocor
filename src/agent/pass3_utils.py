"""Pass3 utility functions.

Static utility functions that don't require access to the generator instance.
These are pure functions that can be imported and used independently.
"""

import hashlib
import re
from typing import Any, List, Optional
from urllib.parse import quote


def hash_text(text: str) -> str:
    """Compute MD5 hash of text for cache key generation."""
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def safe_slug(text: str) -> str:
    """Convert text to a safe filename slug."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text or "")
    safe = safe.strip("_")
    return safe or "unknown"


def encode_rel_path(rel_path: str) -> str:
    """URL-encode path segments for use in markdown links."""
    segments = rel_path.replace("\\", "/").split("/")
    return "/".join(quote(seg) for seg in segments)


def normalize_instruction(inst: str) -> str:
    """Normalize instruction name for comparison (uppercase, no spaces)."""
    text = (inst or "").strip().upper()
    return re.sub(r"\s+", "", text)


def contains_any(text: str, keywords: List[str]) -> List[str]:
    """Return keywords found in text (case-insensitive)."""
    t = (text or "").lower()
    return [k for k in keywords if k in t]


def split_chain(text: str) -> List[str]:
    """Split a chain text by arrows (->) into components."""
    chain = (text or "").replace("`", "").replace("...", "").replace("…", "")
    chain = chain.replace("→", "->").replace("=>", "->")
    return [p.strip() for p in chain.split("->") if p.strip()]


def extract_mermaid_code(markdown: str) -> str:
    """Extract mermaid code block content from markdown."""
    text = markdown or ""
    m = re.search(r"```mermaid\s*(.*?)```", text, flags=re.S | re.I)
    if m:
        return (m.group(1) or "").strip()
    return ""


def extract_json_code(markdown: str) -> str:
    """Extract JSON code block content from markdown."""
    text = markdown or ""
    m = re.search(r"```json\s*(.*?)```", text, flags=re.S | re.I)
    if m:
        return (m.group(1) or "").strip()
    # Fallback: allow raw json body without fenced block.
    return text.strip()


def normalize_str_list(values: Optional[List[str]]) -> List[str]:
    """Normalize a list of strings (strip, filter empty)."""
    out: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def coerce_optional_list(value: Any) -> List[str]:
    """Coerce a value to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def classify_instruction(instruction: str) -> str:
    """Classify an instruction by type."""
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


def parse_roots_cell_tokens(roots_cell: str) -> List[str]:
    """Parse a roots cell string into individual tokens.

    Handles various separators (comma, Chinese punctuation) and
    strips parentheses and extra content.
    """
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


def norm_header(text: str) -> str:
    """Normalize header text for table column matching.

    Converts to lowercase, replaces Chinese brackets with ASCII,
    and removes whitespace.
    """
    t = (text or "").strip().lower()
    t = t.replace("（", "(").replace("）", ")")
    t = re.sub(r"\s+", "", t)
    return t


def is_separator_row(cells: List[str]) -> bool:
    """Check if a table row is a separator (e.g., |---|---|)."""
    return all(re.match(r"^:?-{3,}:?$", c.strip()) for c in cells if c.strip())
