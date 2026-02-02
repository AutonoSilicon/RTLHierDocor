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

# 逻辑块源代码（按拓扑序）:
{block_sources}

请根据以上信息，为该模块生成一个段落的预览（Preview）。
"""

PASS2_LEAF_SYSTEM = """你是一个资深的 RTL 设计分析专家。
你的任务是对当前模块进行深入的数据流分析和功能总结。
输出必须至少包含以下内容：
1. 数据流分析：详细描述内部信号流转、逻辑转换过程。
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

# Pass 2.5: Mermaid behavioral flowchart generation prompts

PASS2_5_MODULE_SYSTEM = """你是硬件微架构文档专家。你的任务是根据模块的电路拓扑结构和源代码，
生成一张**以数据流和控制流为中心的微架构级别行为流程图**（Mermaid flowchart TD）。

## 核心原则

你生成的是**微架构行为流程图**，不是 RTL 代码结构的直接映射。目标读者是需要理解模块功能行为的硬件工程师。

1. **数据中心**：节点描述"数据做了什么"（如"查询 某功能表项"、"分配 某Buffer 表项"），
   而不是"哪行代码被执行"（如"p3: sel <= 1'b0"）。
2. **控制抽象**：菱形节点 `{{...}}` 表达有意义的功能判断（如"Cache 命中？"、"指令有效？"），
   而不是逐行 if-else 翻译。多个相关的条件可合并为一个语义判断。
3. **适度粒度**：
   - 一个 PROC 块如果实现单一功能，对应 1 个行为节点即可；
   - 一个 PROC 块如果包含状态机或多路选择，可展开为判断 + 多个行为分支；
   - 简单的赋值 COMB 块可合并到相邻的行为节点中，不必独立出现。
4. **信号标注**：边上使用 `-->|"信号名或条件"|` 标注关键的数据信号名或控制条件，
   但只标注对理解数据流有帮助的信号，不要标注所有信号。
5. **量化精确**：从代码中提取具体的结构参数，在节点描述中显式标注，不可模糊化。包括：
   - **通道/路数**：并行处理通道数（如"2路取指"、"4路发射仲裁"、"双端口读取"）
   - **表项/深度**：缓冲区、表、FIFO 的容量（如"8表项 ROB 分配"、"64项 BTB 查询"）
   - **位宽**：关键数据通路宽度（如"128位指令包"、"40位物理地址"）
   - **端口数**：存储结构的读写端口配置（如"2R1W 寄存器堆"）
   - **级数/阶段数**：流水线级数或状态机状态数（如"3级流水写回"、"5状态 FSM"）

   识别方法：关注代码中的 `parameter`/`localparam` 定义、数组声明 `reg [N:0] name [0:M]`、
   位宽范围 `[127:0]`、重复的编号结构（`channel_0`~`channel_3` 表示4通道）、
   以及 for/generate 循环的边界值。
   当这些参数存在时，节点文字必须包含具体数值，例如写"4路仲裁选择"而不是"仲裁选择"。

## 输出结构要求

```mermaid
flowchart TD
    subgraph inputs["输入"]
        in_xxx(["信号组描述"])
    end

    subgraph 功能阶段名["阶段描述"]
        节点定义...
    end

    subgraph children["子模块"]
        subgraph sub_xxx["实例名: 模块类型"]
            精简流程...
        end
    end

    subgraph outputs["输出"]
        out_xxx(["信号组描述"])
    end

    连接边...

    style 节点 fill:#颜色
```

## 具体规则

- I/O 端口：按功能分组（如"时钟与复位"、"数据输入"、"控制输出"），不要逐个列举所有端口
- 子模块：用 subgraph 包裹，内部嵌入从子模块获得的精简流程
- subgraph 按功能阶段组织（如"取指阶段"、"异常处理路径"），而非按 PROC/COMB 块编号组织
- 使用 style 指令对不同功能阶段着色
- 节点 ID 使用有意义的缩写（如 `fetch_check`、`alloc_rob`），不使用 `p0`、`c1` 等编号
- 输出完整的 ```mermaid ... ``` 代码块
"""

PASS2_5_MODULE_PROMPT = """
# 模块名称: {module_name}

# 模块功能预览:
{preview}

# I/O 端口:
{port_summary}

# 电路拓扑结构（PROC/COMB/SUBMODULE 连接图，按拓扑序）:
{graph_description}

# 逻辑块源代码（按拓扑序）:
{block_sources}

# 子模块精简流程图:
{children_summaries}

请根据以上拓扑结构和源代码，生成该模块的微架构行为流程图。
注意：不要直接翻译代码结构，而是提炼出数据处理的功能行为和控制判断逻辑。
"""

PASS2_5_SUMMARY_SYSTEM = """将一个模块的微架构行为流程图精简为可嵌入父模块的摘要版本。

要求：
- 提炼主数据通路：关键输入 → 核心功能（1-3个节点） → 关键输出
- 去除内部分支细节和中间状态
- 保留该模块最有代表性的功能行为描述
- 输出为 mermaid 片段（不含 flowchart TD 头和外层 subgraph），供父模块嵌套使用
- 节点 ID 使用模块名作前缀确保唯一性（如 `modname_in`、`modname_core`、`modname_out`）

输出格式示例：
```
timer_in(["系统计数, 时钟使能"])
timer_core["计数采样与时间戳锁存"]
timer_out(["时间戳输出"])
timer_in --> timer_core --> timer_out
```"""

PASS2_5_SUMMARY_PROMPT = """
# 模块名称: {module_name}

# 模块功能预览:
{preview}

# 完整行为流程图:
{full_mermaid}

# 端口摘要:
{port_summary}

请生成该模块的精简版行为流程图片段（供父模块嵌入）。
"""
