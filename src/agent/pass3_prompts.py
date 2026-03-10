"""Pass3 prompt builders.

This module encapsulates prompt building logic for pass3 agents.
"""

import json
from typing import Any, Dict, List, Optional

from .prompts import (
    PASS3_1_SYSTEM,
    PASS3_3_1_SEARCH_SYSTEM,
    PASS3_3_1_SEARCH_PROMPT,
    PASS3_3_2_DRAW_SYSTEM,
    PASS3_3_2_DRAW_PROMPT,
)


class Pass3Prompts:
    """Helper object encapsulating pass3 prompt builders."""

    def __init__(self, generator: Any):
        self.g = generator

    def build_instrack_draw_state_json(
        self,
        *,
        current_module: str,
        current_instance: str,
        current_path: str = "",
        current_level: Optional[int] = None,
        upstream_handoff: Optional[List[str]] = None,
        required_children: Optional[List[str]] = None,
        draw_phase: str = "draw",
        is_top_module: Optional[bool] = None,
        has_children: Optional[bool] = None,
        upstream_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build a compact, bounded Draw State JSON string for prompts."""
        handoff = [str(item).strip() for item in (upstream_handoff or []) if str(item).strip()]
        payload: Dict[str, Any] = {
            "module": str(current_module or "").strip(),
            "instance": str(current_instance or "").strip(),
            "phase": str(draw_phase or "draw").strip(),
        }
        if current_level is not None:
            try:
                payload["level"] = int(current_level)
            except Exception:
                pass
        if current_path:
            payload["path_tail"] = str(current_path).strip().split("/")[-3:]
        if is_top_module is not None:
            payload["is_top"] = bool(is_top_module)
        if has_children is not None:
            payload["has_children"] = bool(has_children)
        if handoff:
            payload["upstream_handoff"] = handoff[:4]
            if len(handoff) > 4:
                payload["upstream_handoff_truncated"] = len(handoff) - 4
        children = [str(item).strip() for item in (required_children or []) if str(item).strip()]
        if children:
            payload["required_children"] = children[:6]
            if len(children) > 6:
                payload["required_children_truncated"] = len(children) - 6
        if isinstance(upstream_context, dict) and upstream_context:
            payload["upstream_context"] = upstream_context
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def build_pass3_recursive_system(self, prompt_style: str = "architecture") -> str:
        """Build the system prompt for recursive pass3 agents."""
        if prompt_style == "instrack_search":
            return (
                PASS3_3_1_SEARCH_SYSTEM.strip()
                + "\n\n"
                + "递归子代理附加约束：\n"
                + "- 你处于递归子代理模式：先在当前层判断是否存在生命周期起点/关键寄存器证据。\n"
                + "- 你只能直接读取本级拓扑证据（readSource 仅可读取本级）。\n"
                + "- 若要读取更深层，必须调用 forkSubAgent(module, task)；task 由你自由定义为下一级目标。"
            )

        if prompt_style == "instrack_draw":
            return (
                PASS3_3_2_DRAW_SYSTEM.strip()
                + "\n\n"
                + "递归子代理附加约束：\n"
                + "- 你处于 draw 递归子代理模式：仅绘制当前模块主通路，并返回 handoff_signals。\n"
                + "- 你只能直接读取本级与直接子级拓扑证据（readSource 受限）。\n"
                + "- 若需要子模块图，调用 drawChild(module, task)；禁止跨层调用。\n"
                + "- 是否调用 drawChild 由你自主决策，Python 不再预设 required_children 强制列表。"
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

    def build_pass3_recursive_prompt(
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
        path_records: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build the user prompt for recursive pass3 agents."""
        if prompt_style == "instrack_search":
            base_prompt = PASS3_3_1_SEARCH_PROMPT.format(
                instruction=instruction or "UNKNOWN",
                instruction_datasheet=(
                    instruction_datasheet
                    if (instruction_datasheet or "").strip()
                    else "Instruction unavailable"
                ),
                module_description=self.g.owner._extract_summary(current_description, max_lines=20, max_chars=2400),
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

        if prompt_style == "instrack_draw":
            return PASS3_3_2_DRAW_PROMPT.format(
                instruction=instruction or "UNKNOWN",
                instruction_datasheet=(
                    instruction_datasheet
                    if (instruction_datasheet or "").strip()
                    else "Instruction unavailable"
                ),
                draw_state_json=self.build_instrack_draw_state_json(
                    current_module=current_node.module_name,
                    current_instance=current_node.instance_name,
                    current_path=current_node.get_path() if hasattr(current_node, "get_path") else current_node.instance_name,
                    current_level=level,
                    upstream_handoff=[],
                    required_children=[],
                    draw_phase="recursive_draw",
                    is_top_module=current_node.parent is None,
                    has_children=bool(current_node.children),
                ),
                module_description=self.g.owner._extract_summary(current_description, max_lines=20, max_chars=2400),
                functional_description="无可用功能描述文档",
                child_overview=child_overview,
                existing_child_draws="- 无已知子模块 draw 结果",
                upstream_context_section="",
            ).strip()

        if prompt_style == "instrack":
            return (
                f"# Recursive Pass3.3 InStrack Agent\n\n"
                f"- Top module: {top_node.module_name}\n"
                f"- Current level: {level}\n"
                f"- Current node: {current_node.instance_name} ({current_node.module_name})\n"
                f"- Task: {task}\n\n"
                "## Current module description\n"
                f"{self.g.owner._extract_summary(current_description, max_lines=20, max_chars=2400)}\n\n"
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
                f"## Current description\n{self.g.owner._extract_summary(current_description, max_lines=20, max_chars=2400)}\n\n"
                f"## Direct children snapshot\n{child_overview}\n\n"
                "请输出以下结构：\n"
                "## 架构结论\n"
                "## 证据与边界\n"
                "## 任务拆解\n"
                "- 每条任务包含：目标、输入、输出、风险/待确认\n"
                "## 下钻建议\n"
            )

        output_schema = self.extract_pass3_1_output_schema()
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
            f"{output_schema}"
        )

    @staticmethod
    def extract_pass3_1_output_schema() -> str:
        """Return the output schema for pass3.1."""
        return """## Output Schema

输出必须包含一个 markdown 表格（子系统划分），示例如下：

| 子系统 | Roots(root modules) | 职责/边界(intent) | 软件可见面(sw visible) | 证据(readDoc 摘要) | 未知/待确认 |
|---|---|---|---|---|---|
| CPU Core Cluster | core* | 执行主程序 | 寄存器/中断 | 含IFU/IDU/EXU... | 无 |
| Debug Subsystem | had* | 调试接口 | 调试寄存器 | ... | ... |

规则：
- 子系统名称要简洁
- Roots 必须是当前层级的直接子模块
- 证据字段必须引用 readDoc 读取的文档片段
- 未知字段列出证据不足的部分
"""
