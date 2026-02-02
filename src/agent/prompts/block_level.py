"""Block-level documentation prompts."""

# Block-level documentation prompts
BLOCK_SYSTEM = """你是RTL设计分析专家。分析一个逻辑块的源代码片段，结合其电路中的上下游连接关系，
生成该逻辑块的功能描述。输出限定1-2段落，重点描述数据变换逻辑和信号流向。
语言专业严谨，内容精炼，表达清晰。"""

BLOCK_PROMPT = """
# 模块: {module_name}
# 逻辑块: {block_id} ({block_type})

# 上游连接 (数据来源):
{upstream}

# 下游连接 (数据去向):
{downstream}

# 源代码片段:
```verilog
{source_snippet}
```

请分析该逻辑块的功能。
"""
