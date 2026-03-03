"""Reusable incremental document composition workflow.

This module provides a generic staged composition abstraction for
root-draft generation, section-level expansion, and final consistency polish.
"""

from dataclasses import dataclass, field
import time
from typing import Dict, List, Optional, Tuple

from .llm_backend import LLMBackend


@dataclass
class EvidenceItem:
    """Traceable evidence reference used by generated claims."""

    source: str
    claim: str


@dataclass
class ConflictItem:
    """Conflict between two or more claims."""

    topic: str
    details: str
    resolution: str = "待确认"


@dataclass
class SectionPatch:
    """Section-level patch for reusable post-processing."""

    section_title: str
    replacement_markdown: str


@dataclass
class ComposeResult:
    """Result for one composition stage."""

    content: str
    token_stats: Dict[str, int]
    evidence: List[EvidenceItem] = field(default_factory=list)
    conflicts: List[ConflictItem] = field(default_factory=list)


class IncrementalDocComposer:
    """Generic staged document composer backed by LLM calls."""

    def __init__(self, llm: LLMBackend):
        self.llm = llm

    async def _run_stage(
        self,
        system_prompt: str,
        user_prompt: str,
        log_path: str,
        stage_name: str,
        module_name: str,
    ) -> ComposeResult:
        """Execute one composition stage with timing/progress logs."""
        start_time = time.perf_counter()
        print(
            f"    [Pass2][{module_name}] [{stage_name}] START "
            f"(prompt_chars={len(user_prompt)}, system_chars={len(system_prompt)})"
        )

        content, token_stats = await self.llm.generate(
            system_prompt,
            user_prompt,
            log_path=log_path,
        )

        elapsed = time.perf_counter() - start_time
        print(
            f"    [Pass2][{module_name}] [{stage_name}] DONE "
            f"({elapsed:.2f}s, output_chars={len(content)}, tokens={token_stats.get('total_tokens', 0)})"
        )
        return ComposeResult(content=content, token_stats=token_stats)

    async def compose_root(
        self,
        system_prompt: str,
        user_prompt: str,
        log_path: str,
        module_name: str,
    ) -> ComposeResult:
        """Generate initial root document."""
        return await self._run_stage(
            system_prompt,
            user_prompt,
            log_path,
            stage_name="root",
            module_name=module_name,
        )

    async def expand_with_target(
        self,
        system_prompt: str,
        user_prompt: str,
        log_path: str,
        module_name: str,
        target_name: str,
    ) -> ComposeResult:
        """Expand an existing document with one target fragment."""
        return await self._run_stage(
            system_prompt,
            user_prompt,
            log_path,
            stage_name=f"expand:{target_name}",
            module_name=module_name,
        )

    async def polish(
        self,
        system_prompt: str,
        user_prompt: str,
        log_path: str,
        module_name: str,
    ) -> ComposeResult:
        """Polish and de-duplicate a composed document."""
        return await self._run_stage(
            system_prompt,
            user_prompt,
            log_path,
            stage_name="polish",
            module_name=module_name,
        )

    async def refine_subdoc(
        self,
        system_prompt: str,
        user_prompt: str,
        log_path: str,
        module_name: str,
        subdoc_name: str,
    ) -> ComposeResult:
        """Refine one sub-document into concise, high-coverage bullet summary."""
        return await self._run_stage(
            system_prompt,
            user_prompt,
            log_path,
            stage_name=f"refine:{subdoc_name}",
            module_name=module_name,
        )

    @staticmethod
    def apply_section_patches(content: str, patches: List[SectionPatch]) -> str:
        """Apply simple title-based section replacements.

        This utility is intentionally lightweight and can be used by future
        workflows if a deterministic section patch pass is needed.
        """
        updated = content
        for patch in patches:
            marker = f"## {patch.section_title}"
            idx = updated.find(marker)
            if idx < 0:
                continue
            next_idx = updated.find("\n## ", idx + len(marker))
            if next_idx < 0:
                next_idx = len(updated)
            updated = updated[:idx] + marker + "\n" + patch.replacement_markdown.strip() + "\n" + updated[next_idx:]
        return updated

    @staticmethod
    def split_summary_and_conflicts(content: str) -> Tuple[str, str]:
        """Split main content and conflict appendix if present."""
        marker = "# 附录: 冲突与待确认项"
        idx = content.find(marker)
        if idx < 0:
            return content, ""
        return content[:idx].rstrip(), content[idx:].strip()
