# 3.3.3 Multi-Leaf v2（无 Relay）

## Summary
- 3.3.3 继续完全按 `orchestrate_index.items` 顺序消费，不引入 relay / passthrough 推断。
- 3.3.3 的跨 item 状态从单个 `previous_leaf_context` 升级为 `previous_leaf_contexts[]`。
- 多 terminal leaf 不再自动降级为 `partial`；只要每个 terminal leaf 都可导出且 branch identity 明确，就允许 `complete`。
- 本版不改 3.3.2 的输出顺序和 route 语义，只改 3.3.3 的状态协议、prompt 和质量门。
- 本版显式不支持同一路径在同一条 route 中重复出现；检测到重复 `item.path` 时，本 instruction 的 3.3.3 直接失败并写明原因，避免 artifact/cache 静默碰撞。

## Key Changes
### State and Schema
- 在 [pass3_instrack_apv.py](/home/ling/RTLHierDocor/src/agent/pass3_instrack_apv.py) 中，将 `previous_leaf_context` 改为 `previous_leaf_contexts: List[Dict[str, Any]]`。
- `apv_index.json` schema 升级为 `pass3_3_3_apv_index_v2`；每个 item 新增 `leaf_contexts`。
- `leaf_contexts` 元素字段固定为 `task_id`, `ref_name`, `capture_names`, `branch_tags`, `module`, `instance`, `path`。
- 兼容字段 `leaf_task_id`, `leaf_ref_name`, `leaf_capture_names` 继续保留；仅当 `leaf_contexts` 长度为 1 时填充，否则置空。
- pass3.3 summary JSON schema 升级为 `pass3_3_instrack_apv_v2`。
- APV 相关 hash/cache version 全部 bump，防止旧 single-leaf 缓存误命中。

### Raw Prompt and Task Protocol
- 在 [pass3_3_apv.py](/home/ling/RTLHierDocor/src/agent/prompts/pass3_3_apv.py) 的 raw task schema 中新增可选 `branch_tags`，类型固定为扁平 string map，例如 `{"slot":"0","channel":"bypass"}`。
- 单个 task 仍只允许依赖一个 dep `ref_name`；不允许在一个 task 中混多个 upstream source。
- 当当前 item 的 terminal tasks 数量大于 1 时，每个 terminal task 必须提供非空 `branch_tags`。
- 同一组 sibling terminal tasks 的 `branch_tags` 必须唯一。
- 当上游 visible dep handles 多于 1 个时，任何依赖 upstream 的 task 都必须声明 `branch_tags`，并与其选中的 candidate 的 `branch_tags` 精确匹配。
- `Visible Upstream Dep Handles` 改为输出全部 `previous_leaf_contexts`，每个 candidate 都携带 `branch_tags`。
- 最多向 prompt 暴露 16 个 upstream candidates；若上游 `leaf_contexts` 超过 8，当前 item 直接记 `partial`，`unknown` 写明 candidate overflow。

### Python Materialization and Quality Gate
- `_build_visible_dep_payload()` 从单 candidate 改为多 candidate list。
- `_materialize_tasks()` 允许 task 依赖任意一个可见 candidate，但继续禁止一个 task 混用多个 dep source。
- terminal leaf 的导出逻辑改为导出全部 terminal tasks，不再只取第一个 terminal task。
- 删除“`complete` 且 multiple terminal tasks 必须降级”的旧逻辑，替换为以下规则：
- terminal tasks 全部有有效 `capture_names`。
- terminal tasks 全部有唯一 `branch_tags`。
- 若 task 依赖 upstream，则 `branch_tags` 与所选 upstream candidate 精确匹配。
- 满足以上条件即可保持 `complete`。
- same-line continuity 检查从单 `previous_task_id` 改为“按 task 实际绑定的 upstream candidate 检查”；存在多个 candidates 时，只检查当前 task 所选择的那个 candidate。
- 最终 YAML 不新增 `branchTags` 字段，保持 APV runtime 输入不变；`branch_tags` 仅保留在 `apv_index.json` 和 prompt payload 中。

### Order and Route Constraints
- 3.3.3 完全按 `orchestrate_index.items` 顺序执行，不增删 item，不重排，不推断结构。
- 3.3.3 启动前先扫描 `orchestrate_items`；若同一 instruction route 内 `item.path` 重复出现，直接停止本 instruction 的 APV 生成并写出明确错误。
- 本版不改 3.3.2 的 `orchestrator_role` 使用方式；它仅作为已有上下文字段保留。

## Test Plan
- 更新 [test_pass3_instrack_apv.py](/home/ling/RTLHierDocor/tests/test_pass3_instrack_apv.py)：
- 现有“multiple terminal leaves 自动 partial”的测试改为两类：有唯一 `branch_tags` 时应 `complete`；无 `branch_tags` 时仍应 `partial`。
- 新增测试：上游 item 导出 `slot0/slot1/slot2` 三个 `leaf_contexts`，下游 prompt 中能看到三个 visible dep handles。
- 新增测试：下游 task 只能依赖一个 candidate；若 `branch_tags` 与所选 candidate 不匹配，当前 item 记 `partial`。
- 新增测试：multi-leaf item 的 `apv_index.json` 正确写出 `leaf_contexts`，旧兼容字段在多 leaf 时为空。
- 新增测试：同一路径在同一条 route 中重复出现时，3.3.3 显式失败而不是覆盖 artifact/cache。
- 更新 [test_pass3_instrack_prompts.py](/home/ling/RTLHierDocor/tests/test_pass3_instrack_prompts.py)：
- prompt 文案必须出现 multi-candidate、`branch_tags`、multi-leaf export 规则。
- prompt 必须继续强调“单 task 只能依赖一个 dep source”。
- 端到端验收使用 `ADD`：
- `ct_ifu_ibdp` 应保持 `complete` 并导出 3 个 `leaf_contexts`。
- `ct_ifu_top` 至少应看到 3 个 upstream candidates；若模型成功选择分支，应继续导出对应 leaf，而不再因 multi-leaf 自动断链。
- `ct_idu_top` 不允许退化成“完全失去 upstream dep 的本地 opcode-only 观察”，除非明确因 overflow 或 branch mismatch 被标 `partial`。

## Assumptions
- 本版明确不做 relay / passthrough 机制。
- 本版不做 full DAG reconverge；只支持按 `orchestrate_index` 顺序传播 bounded multi-leaf 集合。
- 本版要求一个 instruction route 内 `item.path` 唯一；若未来 3.3.2 需要重复进入同一路径，再单独设计 occurrence-aware artifact/cache。
- `branch_tags` 只接受扁平字符串键值；默认键优先使用 `slot`, `channel`, `pipe`, `buffer`。
