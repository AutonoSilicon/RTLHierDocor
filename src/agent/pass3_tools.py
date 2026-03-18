"""Pass3 tool definitions for LLM agent interactions.

This module encapsulates all tool schema definitions used by pass3 agents.
"""

from typing import Any, Dict, List


class Pass3Tools:
    """Helper object encapsulating pass3 tool definitions."""

    def __init__(self, generator: Any):
        self.g = generator

    def pass3_1_tools(self) -> List[Dict[str, Any]]:
        """Tools for pass3.1 subsystem partition."""
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
                    "description": "Delegate deeper analysis to a child-level recursive agent only when needed. Only direct children of top are allowed. If no child expansion is needed, output the result directly.",
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

    def pass3_2_tools(self) -> List[Dict[str, Any]]:
        """Tools for pass3.2 core partition."""
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
                    "description": "Delegate deeper analysis to a child-level recursive agent only when needed. Only direct children of top are allowed. If no child expansion is needed, output the result directly.",
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

    def pass3_3_1_tools(self) -> List[Dict[str, Any]]:
        """Tools for pass3.3.1 instruction search."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Delegate deeper analysis to a child-level recursive agent only when needed. Only direct children of top are allowed. If no child expansion is needed, output the result directly.",
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

    def pass3_3_2_tools(self) -> List[Dict[str, Any]]:
        """Tools for pass3.3.2 instruction draw."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "drawChild",
                    "description": "Trigger draw generation for one direct child module only when needed, and return its structured boundary summary (boundary handoffs, next-child candidates, confidence, unknowns). If that child was already drawn earlier in this run, return the cached orchestration result instead of redrawing it. If no child expansion is needed, output the result directly.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Target direct child selector (instance_name or module_name).",
                            },
                            "task": {
                                "type": "string",
                                "description": "Optional free-form child draw goal in current context.",
                            },
                        },
                        "required": ["module"],
                    },
                },
            },
        ]

    def pass3_3_2_parent_tools(self) -> List[Dict[str, Any]]:
        """Alias for pass3.3.2 tools (for parent-level calls)."""
        return self.pass3_3_2_tools()

    def pass3_3_tools(self) -> List[Dict[str, Any]]:
        """Legacy alias for pass3.3 tools."""
        return self.pass3_3_2_tools()

    def pass3_recursive_tools(self, prompt_style: str = "architecture") -> List[Dict[str, Any]]:
        """Tools for recursive pass3 agent execution."""
        tools: List[Dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "forkSubAgent",
                    "description": "Spawn a child-level agent for deeper analysis only when needed. Only direct children can be forked. If no child expansion is needed, output the result directly.",
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

        if prompt_style not in {"instrack_search", "instrack_draw"}:
            tools.insert(
                0,
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
            )
            tools.insert(
                1,
                {
                    "type": "function",
                    "function": {
                        "name": "readPreview",
                        "description": "Read lightweight pass1 preview for one module. Scope-limited: current level and direct children only.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "module": {
                                    "type": "string",
                                    "description": "Module selector. Prefer instance_name or module_name in current scope.",
                                },
                                "max_chars": {
                                    "type": "integer",
                                    "description": "Max chars to return (0 means default cap).",
                                },
                            },
                            "required": ["module"],
                        },
                    },
                },
            )

        if prompt_style not in {"instrack_search", "instrack_draw"}:
            tools.insert(
                2,
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
            )

        return tools

    @staticmethod
    def normalize_pass_section(section: str) -> str:
        """Normalize pass section aliases to canonical form.

        Examples:
        - "pass2.1", "2.1", "highlights" -> "pass2_1"
        - "pass2.4", "2.4", "functional" -> "pass2_4"
        """
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
