# Boundary Handoff / Boundary Takeover v2

## Summary
- `boundary_handoffs` 继续表示“当前模块的真实输出边界”。
- `boundary_takeover` 取代旧 `entry_ports`，表示“上一模块 handoff 在当前模块上的合法入口投影”。
- Python 是跨模块 continuation 的唯一权威；LLM 不再生成入口字段。
- `bridge_context` 只在 Python 内部存在，用于 sibling routing、debug 和 continuation 更新。

## Key Contracts

### 1. `boundary_handoffs`
- 一个 item 对应当前模块的一个真实输出端口。
- Python 负责做端口合法性校验、resolution enrichment 和 handoff identity 补全。
- `resolutions` 继续只允许：
  - `sibling_child`
  - `exit_parent`
  - `unresolved`

### 2. `boundary_takeover`
- 一个 item 对应当前模块的一个真实入口接管点。
- 固定字段：
  - `ingress_port`
  - `value_kind`
  - `semantic`
  - `taken_from.source_instance`
  - `taken_from.source_module`
  - `taken_from.source_instance_path`
  - `taken_from.source_port`
  - `taken_from.source_handoff_id`
  - `taken_from.parent_wire`
- 只由 Python 根据上一跳 `boundary_handoffs + resolutions` 生成。
- LLM 不输出 `boundary_takeover`，parser 也不信任 raw model output 里的 takeover 字段。

### 3. `bridge_context`
- Python 内部 continuation 状态。
- 固定包含：
  - `source_instance`
  - `source_module`
  - `source_instance_path`
  - `parent_module`
  - `candidate_children`
  - `takeover_bundles`
  - `resolved_handoffs`
  - `exits_parent`
  - `unresolved`
- `takeover_bundles` 是 sibling routing 的唯一候选来源。
- `forkSubAgent` 只有在存在对应 takeover bundle 时，才把该 child 当作正常 continuation 目标。

## LLM / Python Responsibility Split
- LLM 只负责：
  - 当前模块 `boundary_handoffs`
  - 当前模块 `lifecycle_context`
  - `confidence`
  - `unknown`
- Python 负责：
  - 入口接管 `boundary_takeover`
  - child routing 候选
  - 所有 resolution / provenance / identity enrichment
  - 最终 orchestration artifact 和 render 输入

## Render
- Mermaid 入口节点改为从 `boundary_takeover` 生成。
- 模块内 continuation 仍然只画当前模块自己的 `lifecycle_context`。
- 模块出口节点继续从 `boundary_handoffs` 生成。

## Testing Focus
- parser 必须忽略 raw `entry_ports` / raw `boundary_takeover`
- `boundary_takeover` 只能来自严格端口匹配
- `next_children` 只能来自非空 takeover bundles
- parent bridge 允许 `boundary_takeover=[]`
