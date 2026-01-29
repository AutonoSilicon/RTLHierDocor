"""Prompt templates for Docor Agent."""

PASS1_SYSTEM = """你是一个硬件设计专家和资深文档工程师。
你的任务是快速阅览一个 Verilog 模块的代码和结构，生成一个精炼的模块概览（Overview）。
输出必须严格限制在一个段落以内，字数在 150-200 字左右。
重点关注模块在层次结构中的角色、核心功能以及与其他模块的主要接口关系。"""

PASS1_PROMPT = """
# 模块名称: {module_name}

# 父层级背景上下文:
{ancestor_context}

# 端口摘要:
{port_summary}

# 子模块列表:
{children_summary}

# 源代码 (前 {max_lines} 行):
```verilog
{source_code}
```

请根据以上信息，为该模块生成一个段落的概览（Overview）。
"""

PASS2_LEAF_SYSTEM = """你是一个资深的 RTL 设计分析专家。
你的任务是对叶子模块（没有子模块的模块）进行深入的数据流分析和功能总结。
输出必须遵循以下结构：
1. 数据流分析：详细描述内部信号流转、逻辑转换过程。预算：3个段落。
2. 功能描述与关键信号：总结模块的最终功能实现，并列举说明关键控制信号。预算：2个段落。
请保持专业、准确，并采用标准的硬件文档风格。"""

PASS2_LEAF_PROMPT = """
# 模块名称: {module_name} (叶子模块)

# 背景上下文:
{ancestor_context}

# 模块概览:
{overview}

# 端口定义:
{port_summary}

# 完整源代码:
```verilog
{source_code}
```

请对该模块进行详细解析，按照 3 段数据流分析 + 2 段功能描述的格式进行输出。
"""

PASS2_NONLEAF_SYSTEM = """你是一个资深的硬件系统架构师。
你的任务是结合子模块的功能描述和本模块的数据流逻辑，生成本模块的综合文档。
输出必须严格控制在 2 个段落。
第 1 段：描述本模块如何协同各个子模块工作，以及模块内部的数据路由逻辑。
第 2 段：从系统层面总结本模块的功能价值和时序/接口特性。"""

PASS2_NONLEAF_PROMPT = """
# 模块名称: {module_name}

# 背景上下文:
{ancestor_context}

# 模块概览:
{overview}

# 子模块功能描述:
{children_descriptions}

# 本模块数据流逻辑 (代码参考):
```verilog
{source_code}
```

请结合子模块信息和本模块逻辑，总结出 2 个段落的 RTL 硬件文档。
"""
