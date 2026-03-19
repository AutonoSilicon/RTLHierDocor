# 3.3.3 接入 AgenticPipeViewer Tasks Fragment 生成

**摘要**
- 将 3.3.3 从当前 Mermaid render 完全替换为“APV `tasks:` 片段生成”阶段，输入固定为 3.3.2 的 `orchestrate_index.json` 与 3.3.1 的 search 结果，输出固定为单条指令的一份线性主路径 APV tasks YAML 片段。
- 3.3.3 不再直接产出完整 APV 配置；`fsdbFile`、`globalClock`、根 `scope`、`globalFlush` 由外层模板或人工包装补齐。3.3.3 只负责生成可拼接的 `tasks:` 块。
- 单主路径的“哪一步开始当 trigger、哪一个 handoff 继续往下走”不做 Python 硬编码规则，交给 3.3.3 的 step-agent 在当前模块上下文中判断；Python 只做路径解析、校验、装配与缓存。

**模块设计**
- 新增独立模块 [pass3_instrack_apv.py](/home/ling/RTLHierDocor/src/agent/pass3_instrack_apv.py)，职责只做 3.3.3：读取 orchestration、驱动 step-agent、解析/校验中间 JSON、解析下一跳、装配 YAML、落盘 artifact。
- 新增 prompt 文件 [pass3_3_apv.py](/home/ling/RTLHierDocor/src/agent/prompts/pass3_3_apv.py)，把 APV 规则压缩成“JSON-only 中间协议”，不要让模型直接输出最终 YAML。
- 在 [pass3_generator.py](/home/ling/RTLHierDocor/src/agent/pass3_generator.py) 中把现有 `render` 分支替换成 `apv` 分支；`pass3_3_3_enabled` 语义改为“启用 APV tasks 生成”。
- 3.3.3 的 step-agent 输出协议固定为：`step_action`（`emit|skip|stop`）、`task_name`、`condition_lines`、`capture_signals`、`logging_lines`、`match_mode`、`max_match`、`selected_output_port`、`confidence`、`unknown`。
- `emit` 表示当前模块生成一个 APV task；`skip` 表示当前模块仅选择下一跳、不产 task；`stop` 表示路径在此闭合。这样 trigger 是否从 search 起点开始，由 agent 自行决定，不在 Python 层强约束。
- Python 只允许 `selected_output_port` 取自当前 item 的 `_boundary_handoff_routes`/`boundary_handoffs`；解析到 `sibling_child` 时进入对应 child item，解析到 `exit_parent` 时进入 parent bridge item；发现歧义、回环或无下一跳时结束并标记 `partial`。
- 任务 ID 由 Python 统一生成为稳定格式，如 `s00_<instance_slug>`、`s01_<instance_slug>`；`dependsOn` 仅链接到上一个实际 `emit` 的 task，v1 不展开多分支。
- 任务里的本地信号统一写成“相对未来 APV 根 `scope` 的层级路径”，例如去掉顶层 RTL module 名后的 `x_ct_top_0.x_ct_core...signal`；不使用 task-level `scope`，避免和外层包装 scope 冲突。
- `$dep` 引用只允许引用上一个已发出 task 的 capture 叶子名；Python 先检查 capture 叶子名唯一性，再把可用 `$dep.<task_id>.<leaf_name>` 清单喂给下一个 step-agent，避免模型随意猜引用名。

**实现改动**
- 3.3.3 输入 hash 改为 `apv` 专用版本，包含：instruction、search JSON、orchestrate index JSON、APV prompt 模板版本；新增 `pass3_3_3_apv_index_v1` artifact 索引。
- 产物改为：
- `instrack/<inst>/artifacts/apv_tasks.yaml`：仅含 `tasks:` 顶层块和注释头。
- `instrack/<inst>/artifacts/apv_index.json`：记录每个 step 的 `emit/skip/stop`、选中的 `output_port`、对应 item path、最终 task_id、状态与 unknown。
- `instrack/<inst>/artifacts/<step_slug>.apv.raw.md`：每一步 agent 原始输出，便于 debug。
- Markdown 汇总页不再列 `.mmd`，改为列 search JSON、orchestrate index、APV tasks artifact、以及 `partial/complete` 状态。
- `ProjectConfig`/CLI 增加可选 `instrack_apv_model`、`instrack_apv_thinking`；未配置时回退 `instrack_orchestrate_llm`，再回退默认 `llm`。
- 校验规则固定为：`emit` 时 `condition_lines` 与 `capture_signals` 非空；`match_mode` 必须属于 APV 支持集合；`max_match >= 0`；`selected_output_port` 必须真实存在；capture 叶子名不可冲突；`$dep` 只能引用前一 task 的已捕获叶子名。
- APV task 的 `condition` 与 `logging` 最终由 Python 写成 YAML list 形式，保持和 APV 示例一致；不把大段自然语言直接塞进 condition。

**测试计划**
- 用构造好的 orchestration mock 覆盖 3 类路径：`emit -> emit -> stop`、`skip -> emit -> stop`、`emit -> unresolved/partial`，确认路径推进、visited 防环和 `dependsOn` 装配正确。
- 对 step-agent JSON 做严格解析测试：缺字段、非法 `step_action`、非法 `selected_output_port`、capture 冲突、非法 `$dep` 引用都必须被拒绝并转成 `partial`。
- 对最终 `apv_tasks.yaml` 生成一个最小包装 YAML，调用本地 APV 的 `YamlValidator` 校验结构合法；再用 `--deps-only` 跑一次依赖图生成，确认 `dependsOn` DAG 正常。
- 选一条真实指令（先用 `ADD`）做端到端冒烟：search -> orchestrate -> apv，检查产物包含稳定 task id、线性依赖链、可读 logging，以及至少一个有效 `$dep` 条件。
- 回归验证 `pass3_3_3_enabled=false` 时完全跳过 3.3.3；旧缓存不会误命中新 APV hash；旧 `.mmd` 相关输出不再被引用。

**假设与默认**
- 3.3.2 会继续收敛到你当前正在改的严格 schema：`boundary_handoffs` 只保留 instruction-relevant 的 `output_port/value_condition/behavior`，这样 3.3.3 不需要再从大量无关端口里重筛。
- v1 只生成单主路径，不展开多出口 fork；遇到真实分支时，优先保留主链并在 `apv_index.json`/注释中记录未展开分支。
- 3.3.3 只负责 tasks 片段，不负责自动推导最终 APV 根 `scope`、FSDB 文件路径和 flush 策略；但会在 YAML 注释头写出“需要外层补齐”的字段清单与推荐拼装方式。
