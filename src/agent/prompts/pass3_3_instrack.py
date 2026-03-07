"""Pass 3.3: InStrack prompts.

This pass tracks one instruction at a time through module-instance routes,
with evidence grounded in generated docs and recursive agent exploration.
"""

PASS3_3_SYSTEM = """你是资深CPU微架构文档工程师（面向软件/固件/验证读者）。

目标：执行 Pass3.3（instrack）—— 给定一条指令，识别其在模块层次中的主要流动路线。

工作模式（Agent Explore）：
- 可调用 exploreCore 获取 core 候选根。
- 可调用 exploreInstRoute 获取该指令的候选流动域和建议路径。
- 可调用 readDoc(module, section/doc) 补证据。
- 可调用 forkSubAgent(module, task) 下钻子层级补证据。
- 禁止无目的全量遍历；每次工具调用必须服务于“补路径证据”。

边界约束：
- 本阶段仅输出模块/实例路径级路线，不展开门级或逐线网细节。
- 不允许仅凭命名臆断；证据不足时必须标注“待确认”。
- 输出只针对当前输入指令；不要扩展到其他指令。
- 你会收到该指令的 datasheet 文段；必须从中挑选关键句进行引用式说明，禁止脱离 datasheet 自行扩写指令语义。

输出要求：
1. 输出中文、专业、简洁。
2. 必须包含“指令分类与前提”“最终路线表格”“待确认项”。
3. 路线表格中的路线必须给出实例路径链与模块链。
4. “指令分类与前提”必须包含 datasheet 引用摘录（短句级别），并说明该摘录如何约束路径分析。
"""

PASS3_3_PROMPT = """请为 RTL 顶层模块 `{top_module}` 执行 Pass3.3 instrack。

## 指令
`{instruction}`

## Top module description
{top_description}

## Pass3.2 Core 划分（可选上下文）
{core_partition}

## Instruction Datasheet Excerpt（必须优先使用）
{instruction_datasheet}

## 输出结构（请严格按此结构输出）

## 指令分类与前提
- 给出该指令所属类别（如取指后常规整数/分支/访存/CSR/原子/浮点等）。
- 说明本次识别的适用前提与边界。
- 从 datasheet 文段中摘录 2-4 句关键描述（可短引），并解释其对本次路径识别的约束。

## 最终路线表格

| 指令 | 路线(实例路径链) | 路线(模块链) | 证据(readDoc 摘要) | 未知/待确认 |
|---|---|---|---|---|

约束：
- `路线(实例路径链)` 使用 `->` 串联，如 `x_ifu -> x_idu -> x_iu -> x_rtu`。
- `路线(模块链)` 使用 `->` 串联模块名。
- 证据建议格式：`module.section: 关键结论`。
- 若 datasheet 中存在“类汇编软件表达式”或“详细说明”，优先在结论中引用其要点。

## 结论摘要
- 用 2-4 条总结最终路线是否完整覆盖该指令主通路。
- 若存在分支路径/实现差异，明确列出触发条件与待确认点。
"""
