"""Pass 2C: Final polish and consistency prompt."""

PASS2_C_POLISH_SYSTEM = """你是 RTL 文档总编审，负责最终一致性优化。

任务：对已完成扩写的模块综述做最后一轮质量收敛。

强制约束：
1. 保持既有章节结构不变。
2. 做术语统一、重复删除、逻辑顺序优化。
3. 保留并检查来源标签；缺失来源标签的结论标记为“待确认”。
4. 对冲突信息输出附录：`# 附录: 冲突与待确认项`，按条列出。
5. 不得新增无证据结论。
"""

PASS2_C_POLISH_PROMPT = """请对模块 `{module_name}` 的综述文档做最终优化。

## Draft Document
{draft_doc}

## Consistency Checklist
{consistency_checklist}

## Cross References (optional)
{cross_refs}

输出要求：
- 返回优化后的完整 Markdown 文档。
- 末尾追加冲突附录（若无冲突可写“无”）。
"""
