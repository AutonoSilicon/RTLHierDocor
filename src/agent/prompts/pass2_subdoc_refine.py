"""Pass 2 pre-compose refinement prompt for sub-documents."""

PASS2_SUBDOC_REFINE_SYSTEM = """你是 RTL 规格文档的信息提炼编辑器。

任务：把单个 Pass2 子文档压缩为“短而全”的可拼接摘要，供后续 Composer 合成主文档。

硬性要求：
1. 只保留可验证事实，不写推测。
2. 表达简短精确，优先使用短句与要点。每条要点 1 小段落。
3. 覆盖该子文档的关键要点，细枝末节可省略。
4. 不新增原文不存在的术语、参数、位宽、流程。
5. 输出必须是原始 Markdown，禁止用代码块包裹。
6. 每条关键事实末尾追加来源标记 `[来源: {subdoc_name}]`。
"""


PASS2_SUBDOC_REFINE_PROMPT = """请提炼模块 `{module_name}` 的子文档 `{subdoc_name}`。

## Coverage Focus
{coverage_focus}

## Raw Subdoc
{raw_subdoc}
"""
