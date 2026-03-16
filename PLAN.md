# Boundary Handoff v1 设计方案

## Summary
将现有 `handoff_signals` 彻底替换为 `boundary_handoffs`，把 `drawChild` 从“返回子图文本”改为“返回子模块边界契约”。  
核心目标是让 agent 之间基于**严格端口匹配**完成接力，并由 Python 确定性地把各模块局部图拼接成一个总 Mermaid。

本方案采用这些已锁定的默认决策：
- 严格端口匹配，不允许仅靠语义继续接力
- 一个 `boundary_handoff` 对应当前模块的一个输出端口
- 直接替换旧 `handoff_signals`
- 边界覆盖所有 instruction-relevant 的模块出口，包括 sibling child、parent exit、暂未闭合出口
- 最终跨模块拼图由 Python 确定性完成，不由 LLM 主导

## Key Changes

### 1. 新的边界契约：`boundary_handoffs`
Pass3 draw 的 JSON 输出改为以 `boundary_handoffs` 为核心，不再要求 `handoff_signals`。

每个 item 只代表**当前模块的一个输出端口**，字段固定为：

```json
{
  "handoff_id": "stable-per-instance-path-and-port",
  "source": {
    "instance_path": "openC910/.../x_ct_idu_id_ctrl",
    "module": "ct_idu_id_ctrl",
    "instance": "x_ct_idu_id_ctrl",
    "block": "PROC_11",
    "state": "ctrl_id_pipedown_inst1_vld"
  },
  "egress": {
    "port": "ctrl_id_pipedown_inst1_vld",
    "direction": "output",
    "value_kind": "instruction_valid",
    "semantic": "decoded slot1 valid toward next stage",
    "behavior": "assert when slot1 can pipedown; clear on flush/cancel; hold on stall"
  },
  "resolutions": [
    {
      "resolution_kind": "sibling_child",
      "parent_wire": "ctrl_id_pipedown_inst1_vld",
      "target_instance": "x_ct_idu_ir_dp",
      "target_module": "ct_idu_ir_dp",
      "target_port": "ctrl_id_pipedown_inst1_vld",
      "match_policy": "exact_port"
    }
  ],
  "status": "resolved",
  "confidence": "high",
  "unknown": ""
}
```

约束：
- `source.block` 必须是当前模块图中的 block id
- `egress.port` 必须是当前模块的真实输出端口
- `behavior` 必须描述端口上的值在当前模块里何时产生、保持、清除或更新
- 一个端口如扇出到多个目标，仍保留一个 item，但 `resolutions` 可包含多个目标
- 无法闭合到子模块时，仍保留 item，`status` 标记为 `exit_parent` 或 `unresolved`

### 2. 新的入口契约：`entry_ports`
每个 draw 结果都显式返回本模块在当前 trace 中真正用到的输入边界，作为下一跳接力锚点。

建议字段：

```json
{
  "entry_ports": [
    {
      "port": "ifu_idu_ib_inst1_vld",
      "direction": "input",
      "value_kind": "instruction_valid",
      "semantic": "slot1 valid from upstream",
      "matched_from": {
        "source_instance": "x_ct_ifu_top",
        "source_port": "ifu_idu_ib_inst1_vld",
        "parent_wire": "ifu_idu_ib_inst1_vld"
      }
    }
  ]
}
```

约束：
- `entry_ports` 只列当前 trace 真正进入模块主通路的输入端口
- 若上游 `upstream_context.entry_ports` 已给出，则当前模块必须优先围绕这些端口续画
- 入口必须能映射到当前模块 Mermaid 中的端口节点或入口 block

### 3. LLM 与 Python 的职责分离
LLM 只负责**当前模块内部语义**，Python 负责**跨模块严格配对**。

LLM 负责输出：
- `entry_ports`
- `boundary_handoffs` 的 `source` / `egress` / `behavior`
- `instruction_state`
- `lifecycle_context`
- `confidence`
- `unknown`

Python 后处理负责：
- 基于 hierarchy `port_connections` 和 parent wiring，为每个 `boundary_handoff` 填充 `resolutions`
- 严格校验 `egress.port` 是否确为当前模块输出端口
- 识别三类 resolution：
  - `sibling_child`
  - `exit_parent`
  - `unresolved`
- 拒绝 LLM 猜 target module / target instance；目标解析以 Python 为准
- 将 enriched payload 写入 cache / debug / draw index

### 4. `drawChild` 工具返回值改造
`drawChild` 不再把完整 Mermaid 作为主返回值返回给父 agent。  
它改为返回一个**紧凑的子模块边界契约摘要**，供父 agent 继续推理。

返回内容采用单个 JSON block，包含：
- `child`
- `entry_ports`
- `boundary_handoffs`
- `instruction_state`
- `terminal_status`
- `confidence`
- `unknown`
- 可选 `draw_ref`，指向缓存中的完整子模块 draw 结果

完整 Mermaid 仍保留在子模块自己的 draw 结果和 debug artifact 中，但不作为 agent-to-agent 主载荷。

### 5. 总 Mermaid 的确定性拼接
新增一个 Python 侧 assembler，以 `entry_ports + boundary_handoffs.resolutions` 为唯一跨模块拼图依据。

拼接原则：
- 每个模块局部 Mermaid 中的内部功能节点统一加 instance-path 前缀，避免冲突
- 每个模块边界端口使用稳定节点 id：
  - `IN::<instance_path>::<port>`
  - `OUT::<instance_path>::<port>`
- 模块内图由局部 Mermaid 提供
- 模块间连线只通过 `boundary_handoffs.resolutions` 生成：
  - `OUT::<src>::<port> --> IN::<dst>::<port>`
- 若 `status=exit_parent`，连接到 parent-scope 边界节点
- 若 `status=unresolved`，保留悬挂边界节点并打标，不能擅自闭合

## Implementation Changes

### Prompt / schema
在 `pass3_3_instrack` draw prompt 中把输出契约改成：
- 必须输出 `entry_ports`
- 必须输出 `boundary_handoffs`
- 不再要求 `handoff_signals`
- 明确要求 handoff 只能使用当前模块真实输出端口
- 明确要求描述该端口的值语义与更新行为
- 明确禁止猜测目标模块；目标只由 Python 解析

### Parse / normalize / enrich
在 Pass3 draw payload 解析层：
- 新增 `boundary_handoffs` 解析与字段校验
- 用 full instance path 作为 handoff identity，不再以 module 名做 dedupe key
- 保留 `instruction_state` / `lifecycle_context` 现有字段，但边界契约改为结构化字段优先
- 在 post-process 阶段 enrichment `resolutions`
- 旧 `handoff_signals` 相关逻辑全部替换为 `boundary_handoffs`

### Upstream continuation
`_build_upstream_context` 的输入不再是薄的 `signals[]`，而是前一模块 enriched 后的 `boundary_handoffs`。
对子模块续画时：
- 仅把严格匹配到该 child 的 `resolutions` 转成 `entry_ports`
- 不再靠字符串 signal summary 做主要接力
- `parent_logic_on_path` 仍保留为辅助证据，不参与端口闭合判定

## Test Plan

### Schema / validation
- draw JSON 缺少 `boundary_handoffs` 时应判失败或低置信度回退
- `egress.port` 不是当前模块输出端口时应被标记无效
- 同一 instance path 下同一输出端口只允许一个 handoff item

### Resolution cases
- 单一 source output 精确连到 sibling child input
- 一个 source output 扇出到多个 sibling child inputs
- source output 只能离开当前 parent，形成 `exit_parent`
- source output 无法闭合时形成 `unresolved`
- 相同 module 的不同 instance 必须正确区分，不允许按 module 名串线

### End-to-end assembly
- 用 `ct_idu_id_ctrl -> ct_idu_ir_dp` 这类直接边界验证严格端口拼接
- 用带 parent exit 的链路验证跨父层继续接力
- 用 infrastructure-only child 验证空 handoff 或 closure reason
- 最终总 Mermaid 中边界端口节点必须全可追溯到具体 instance path + port

## Assumptions
- 只输出 instruction-relevant 的边界端口，不追求穷举所有 RTL 输出
- `value_kind` 采用固定小枚举，例如：`instruction_valid`、`instruction_payload`、`decode_class`、`stall`、`flush_cancel`、`wakeup`、`data`、`other`
- 允许一个输出端口在 `behavior` 中描述多分支行为，但 handoff item 仍保持单端口单项
- Python 是跨模块 target 解析和总图拼接的唯一权威，LLM 不是
- v1 不要求一次性解决所有语义类型，只要求端口级接力闭环稳定可用
