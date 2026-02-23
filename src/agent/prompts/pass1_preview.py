"""Pass 1: Preview generation prompts."""

PASS1_SYSTEM = """你是RTL设计分析专家。
你的任务是快速阅览一个 Verilog 模块的结构信息，生成一个模块的预览（Preview）。
重点关注本模块在整个硬件架构层次结构中的角色、核心功能以及与其他模块的主要接口关系。
语言专业严谨，内容精炼，表达清晰，不要过多形容, 确保列举完备不要使用“等”字样省略信息。
输出必须严格限定在1个段落以内。
"""

PASS1_PROMPT = """
# Module Name: 
{module_name}

# Parent Module Preview:
{ancestor_context}

# Port List:
{port_summary}

# Submodule List:
{children_summary}

# Structured Circuit Description:
{graph_description}

Please generate a 1-paragraph preview for this module based on the information above.
"""
