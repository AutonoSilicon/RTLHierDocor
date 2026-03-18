## `lifecycle_context` 收紧方案（配合 `boundary_takeover/bridge_context`）

### Summary
- orchestrate payload 中：
  - `lifecycle_context` 固定为字符串
  - 只描述“当前模块自身对目标指令做了什么”
- `Continuation State` 中：
  - `lifecycle_context` 固定为数组
  - 只累计当前模块已经访问过的 direct child 行为
- `bridge_context` 仅为 Python 内部状态，不承载 `lifecycle_context`

### Rules
- `Continuation State.lifecycle_context` item 固定字段：
  - `accessed_submodule`
  - `behavior_description`
- `accessed_submodule` 固定格式：
  - `instance(module)`
- child 的 descendant 行为不能上浮到 parent 的 `lifecycle_context`
- parent-facing `Child Tool Result` 不返回 `lifecycle_context`

### Implementation Notes
- active continuation 在 child 返回后，只追加当前 child 的一条行为摘要
- sibling child 继续时，新的 child prompt 继承已有 `lifecycle_context` 数组
- prompt 中的 `Continuation State` 同时带：
  - `boundary_takeover`
  - `lifecycle_context`
  - `continuation_source`
- `bridge_context` 继续用于 child routing，但不进入 prompt

### Acceptance
- payload `lifecycle_context` 始终是字符串
- prompt `Continuation State.lifecycle_context` 始终是 direct-child array
- `bridge_context` 中不再出现 `lifecycle_context`
