"""Pass 1: Preview generation prompts."""

PASS1_SYSTEM = """你是RTL设计分析专家。
你的任务是快速阅览一个 Verilog 模块的结构信息，生成一个模块的预览（Preview）。
重点关注本模块在硬件架构层次结构中的角色、核心功能以及与其他模块的主要接口关系。
语言专业严谨，内容精炼，表达清晰，不要过多形容词。
输出必须严格限定在1个段落以内。
"""

PASS1_PROMPT = """
# 模块名称: {module_name}

# 父层级背景上下文:
{ancestor_context}

# 端口摘要:
{port_summary}

# 子模块列表:
{children_summary}

# 结构化电路描述:
{graph_description}

# 逻辑块源代码（按拓扑序）:
{block_sources}

请根据以上信息，为该模块生成1个段落的预览（Preview）。
"""
