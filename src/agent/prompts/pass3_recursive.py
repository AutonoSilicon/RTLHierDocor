"""Pass 3 recursive prompt templates.

These templates centralize static prompt text used by pass3 recursive agents.
Dynamic runtime assembly stays in ``pass3_prompts.py``.
"""

PASS3_RECURSIVE_ARCHITECTURE_SYSTEM = """你是芯片架构文档Agent。请以top-down方式输出本层级架构结论和任务拆解。
若需要再往下读取下下级或更深层，请使用 forkSubAgent(module, task) 交给下一级Agent。
禁止臆断，证据不足时明确写待确认。"""

PASS3_RECURSIVE_PARTITION_APPENDIX = """递归子代理附加约束：
- 你处于递归子代理模式：优先按 Pass3.1 风格输出当前层级划分。
- 若要读取更深层，必须调用 forkSubAgent(module, task) 继续下钻。"""

PASS3_RECURSIVE_ARCHITECTURE_PROMPT = """# Recursive Pass3 Agent

- Top module: {top_module}
- Current level: {level}
- Current node: {current_instance} ({current_module})
- Task: {task}

## Current description
{current_description}

## Direct children snapshot
{child_overview}

请输出以下结构：
## 架构结论
## 证据与边界
## 任务拆解
- 每条任务包含：目标、输入、输出、风险/待确认
## 下钻建议
"""

PASS3_RECURSIVE_PARTITION_PROMPT = """请为当前层级模块 `{current_instance}` (`{current_module}`) 生成递归 Pass3.1《子系统划分》输出。

## Current module description
{current_description}

## 当前层级上下文
- Top module: {top_module}
- Current level: {level}
- Current node: {current_instance} ({current_module})
- Task: {task}

## Direct children snapshot
{child_overview}

说明：若证据不足，可调用 readDoc；若需要更深层信息，请 forkSubAgent。
本任务只服务于 SoC level 子系统划分，不展开微架构域细分。

{output_schema}
"""

PASS3_1_RECURSIVE_OUTPUT_SCHEMA = """## Output Schema

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
