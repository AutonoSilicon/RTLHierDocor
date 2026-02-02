"""Prompt templates for Docor Agent."""

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

# Pass 1: Preview generation (renamed from Overview)
PASS1_SYSTEM = """
你的任务是快速阅览一个 Verilog 模块的结构信息，生成一个模块的预览（Preview）。
输出必须严格限定在1个段落以内。
重点关注本模块在硬件架构层次结构中的角色、核心功能以及与其他模块的主要接口关系。
语言专业严谨，内容精炼，表达清晰，不要过多形容词。
"""

PASS1_PROMPT = """
# 模块名称: {module_name}

# 父层级背景上下文:
{ancestor_context}

# 端口摘要:
{port_summary}

# 子模块列表:
{children_summary}

# 简化电路结构:
{graph_description}

请根据以上信息，为该模块生成一个段落的预览（Preview）。
"""

PASS2_LEAF_SYSTEM = """你是一个资深的 RTL 设计分析专家。
你的任务是对当前模块进行深入的数据流分析和功能总结。
输出必须遵循以下结构：
1. 数据流分析：详细描述内部信号流转、逻辑转换过程。预算：3个段落。
2. 功能描述与关键信号：总结模块的最终功能实现，并列举说明关键控制信号。预算：2个段落。
请保持专业、准确，并采用标准的硬件文档风格。"""

PASS2_LEAF_PROMPT = """
# 模块名称: {module_name}

# 背景上下文:
{ancestor_context}

# 模块预览:
{preview}

# 端口定义:
{port_summary}

# 逻辑块功能文档:
{block_descriptions}

请对该模块进行详细解析，按照 3 段数据流分析 + 2 段功能描述的格式进行输出。
结合上述逻辑块的功能描述，综合分析模块的整体数据流和功能实现。
"""

PASS2_NONLEAF_SYSTEM = """
你的任务是结合子模块的功能描述和本模块的数据流逻辑，生成本模块的综合文档。
详细描述本模块如何协同各个子模块工作，以及模块内部的数据流逻辑，指出关键信号、条件域、状态机、时序。

保持技术报告风格：
完备性：   不能使用“等”省略列举，所有的功能特性及关键信号说明均被描述。
客观中性： 严禁使用主观情感词汇，采用无人称陈述或被动语态。
量化驱动： 优先使用具体数据和事实，将模糊的形容词（如“很快”、“大幅”）替换为精准描述。
逻辑分级： 使用标准化的分级标题（1.1, 1.2），确保论证链条清晰。
专业术语： 术语使用必须前后一致，表达需简洁、无歧义。
结论先行： 在段落或章节开头直接陈述核心结论。
"""

PASS2_NONLEAF_PROMPT = """
# 模块名称: {module_name}

# 背景上下文:
{ancestor_context}

# 模块预览:
{preview}

# 子模块功能描述:
{children_descriptions}

# 逻辑块功能文档:
{block_descriptions}

请结合子模块功能和本模块的逻辑块功能，生成本模块的综合文档。
"""

# Pass 2.5: Mermaid flowchart generation prompts

PASS2_5_BLOCK_SYSTEM = """你是RTL微架构分析专家。根据一个逻辑块的源代码和上下游信号连接，
生成该逻辑块的 Mermaid flowchart 片段。

要求：
- 用 flowchart 节点表达该块的行为逻辑（条件判断、赋值、状态转移）
- 每个节点用 block_id 作为前缀命名（如 p4_check, p4_assign），确保节点 ID 唯一
- 边上用 |"信号名"| 标注精确的信号名称
- 菱形节点 {...} 表示条件判断，矩形节点 [...] 表示赋值/操作
- 仅输出 mermaid 节点和边的定义（不含 flowchart TD 头部），用于后续整合
- 如果逻辑过于简单（如单个 assign），可以用一个节点表达

输出格式示例：
```
p4_check{{"p4: cpurst_b?"}}
p4_reset["p4: flush_done <= 0"]
p4_update["p4: flush_done <= l2c_done"]
p4_check -->|"cpurst_b=0"| p4_reset
p4_check -->|"cpurst_b=1 & clk_en"| p4_update
```"""

PASS2_5_BLOCK_PROMPT = """
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

请生成该逻辑块的 Mermaid flowchart 片段（仅节点和边定义）。
"""

PASS2_5_MODULE_SYSTEM = """你是RTL微架构分析专家。将各个逻辑块的流程图片段和子模块精简流程图
整合为一个完整的 Mermaid flowchart TD 图。

要求：
- 使用 flowchart TD（top-down）布局
- 输入端口放在顶部 subgraph，输出端口放在底部 subgraph
- 子模块用 subgraph 表达，内部嵌入精简流程
- 各 block 的片段直接嵌入，确保节点 ID 唯一
- 补充 block 之间、I/O 端口与 block 之间的连接边（标注信号名）
- 输出完整的 ```mermaid ... ``` 代码块

输出格式：
```mermaid
flowchart TD
    subgraph inputs["Input Ports"]
        in_clk(["clk"])
    end
    ... 各 block 片段 ...
    subgraph sub_x["x_instance: module_type"]
        ... 精简流程 ...
    end
    subgraph outputs["Output Ports"]
        out_data(["data_out"])
    end
    ... 连接边 ...
```"""

PASS2_5_MODULE_PROMPT = """
# 模块名称: {module_name}

# I/O 端口:
{port_list}

# Block 流程图片段:
{block_fragments}

# 子模块精简流程图:
{children_summaries}

# SimplifiedGraph 连接关系 (拓扑参考):
{edges_summary}

请整合以上信息，生成本模块的完整 Mermaid flowchart TD 流程图。
"""

PASS2_5_SUMMARY_SYSTEM = """将一个模块的完整 Mermaid 流程图精简为父模块可嵌入的摘要版。

要求：
- 保留：关键输入信号→核心功能块→关键输出信号 的主路径
- 去除：内部条件分支细节、中间临时信号
- 输出为 mermaid 片段（不含 subgraph 外层，供父模块嵌套）
- 节点 ID 保持原有前缀，确保在父模块中唯一

输出格式示例：
```
p7_in(["输入: sys_cnt, clk_en"])
p7_func["系统计数采样与锁存"]
p7_out(["输出: time"])
p7_in --> p7_func --> p7_out
```"""

PASS2_5_SUMMARY_PROMPT = """
# 模块名称: {module_name}

# 完整流程图:
{full_mermaid}

# 端口摘要:
{port_summary}

请生成该模块的精简版流程图片段（供父模块嵌入）。
"""
