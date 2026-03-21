# 3.3.3 接入 AgenticPipeViewer Tasks Fragment 生成

**摘要**
- 3.3.3 当前代码仍是 Mermaid render 阶段，职责是把 `pass3_3_2_orchestrate_index_v2` 渲染成 `.mmd` artifact。本计划的目标是在不重写 3.3.2 orchestration 语义的前提下，用 APV YAML 片段生成替换这一步。
- 3.3.2 已经落地的 continuation 语义应视为稳定前提，而不是待补假设：`boundary_handoffs` 对外只保留 `output_port / value_condition / behavior`；Python 负责补 `boundary_takeover` 与 `_boundary_handoff_routes`；child-to-child continuation、parent bridge、parent-port ingress projection、non-direct-child advisory override 已经存在。
- 3.3.3 v1 是最小版本：不启用任何 tool，不允许 fork，也不在 3.3.3 重新执行路线搜索。它直接复用 3.3.2 已落盘的 module item 列表；每个 module item 只做一次独立的 LLM 调用，并生成该 item 对应的 YAML 文件。为了支持最小版 `$dep`，item 生成顺序按 3.3.2 主链串行执行；`fsdbFile`、`globalClock`、根 `scope`、`globalFlush` 继续由外层模板或人工包装补齐。

**抽象层次**
- 第 0 层是 3.3.2 orchestration：它负责把指令相关的模块路径、入口 takeover、出口 handoff 和 route 证据整理成 item 列表；3.3.3 不再重做这一层。
- 第 1 层是 3.3.3 APV 生成：处理粒度严格按 3.3.2 item 走，每个 module item 只做一次独立的 LLM 调用，只生成该 item 自己的 APV 内容。
- 第 2 层是 Python 中间转换与装配：Python 不负责搜索路线，只负责把已有 item 上下文整理进 prompt、校验模型返回结构、完成 `scope` / 信号名归一化、分配稳定 task id、回填最终 `$dep` 引用，并写出 `<path_slug>.apv.yaml` 和索引文件。
- 第 3 层是外层包装：后续如果需要完整 APV 配置，再由外层模板或人工把多个 item 的 YAML 片段与 `fsdbFile`、`globalClock`、根 `scope`、`globalFlush` 拼起来。

**当前基线**
- 3.3.1 search 产物保持不变，继续作为 3.3.3 的全局上下文。
- 3.3.2 orchestration index 当前 schema 为 `pass3_3_2_orchestrate_index_v2`。每个 item 至少包含 `module`、`instance`、`path`、`orchestrator_role`、`orchestration`，并在有原始输出时带 `artifact_raw`。
- 3.3.3 的处理粒度按 3.3.2 item 走，而不是只按 `module_name` 去重。也就是说，同一 `module` 如果对应不同 `path` / `instance`，仍然分别生成各自的 YAML 文件。
- `orchestration.boundary_handoffs` 是公开给下游阶段消费的精简出口摘要；`orchestration._boundary_handoff_routes` 保存 Python 已解析好的 route 证据。
- `orchestration.boundary_takeover` 是 Python 注入的当前模块 ingress contract，不是模型输出字段；它已经会在 child continuation 和 parent-port ingress 投影场景下持久化到 item。
- 现有代码中的 `$dep` 解析是显式 `"$dep.<task_id>.<signal>"` 形式；但 3.3.3 v1 的生成策略会主动收紧成线性可见链，而不是开放任意 DAG 引用。
- 3.3.2 当前仍强制 rerun，不复用旧 orchestrate index；3.3.3 可以拥有独立 hash/cache。
- `pass3_3_3_enabled` 当前代码语义还是“启用 render (.mmd generation)”。实现 APV 后，需要把这个开关语义迁移为“启用 APV YAML 生成”。

**实现改动**
- 新增独立模块 `src/agent/pass3_instrack_apv.py`，职责只做 3.3.3：读取 search/orchestrate artifact，按既定 item 顺序串行遍历 3.3.2 的 module items，为每个 item 组织上下文、发起一次 LLM 调用、解析与校验中间 JSON、分配 task id、回填 `$dep`、写出该 item 的 YAML artifact。
- 新增 prompt 文件 `src/agent/prompts/pass3_3_apv.py`。prompt 只允许模型输出当前 module item 的 JSON 中间协议，不直接产出最终 YAML，也不直接写最终 `$dep.<task_id>.<signal>`。
- 在 `src/agent/pass3_generator.py` 中用 `apv` 分支替换现有 `render` 分支；`.mmd` 不再作为 3.3.3 主产物。
- 将现有 `pass3_3_3_render` hash / index 迁移为 APV 专用版本。缓存粒度按 module item 切分，至少包含：instruction、search JSON、当前 item 的 orchestration JSON、APV prompt 模板版本。
- 3.3.3 不新增独立的 model / thinking 配置，直接复用主 LLM 配置。
- 3.3.3 不修改 3.3.2 的公开 schema。APV 只消费既有 `boundary_handoffs`、`boundary_takeover`、`_boundary_handoff_routes`、`path`、`orchestrator_role` 等已落盘字段。

**3.3.3 调用方式**
- 3.3.3 对每个 3.3.2 module item 只进行一次独立的 LLM 调用。
- 每次调用的输入至少包含：3.3.1 search JSON、当前 item 的 orchestration、Python 预整理的少量邻接路线信息，以及当前可见的上游 `$dep` 句柄摘要。
- 这一轮调用不启用任何 tool，不允许 fork，不做多轮 agent 交互。
- 3.3.3 不负责路线搜索，也不在运行时重新选择 child / parent 路径；它只复用 3.3.2 已经产出的模块顺序和 route 证据。
- 虽然每个 item 只有一次 LLM 调用，但 item 之间的执行顺序必须串行，因为当前 item 的可见 `$dep` 句柄依赖前一个 item 的最终 leaf task。

**路线复用与装配规则**
- Python 先把 orchestrate index 建成按 `path` 索引的 item map，并保留 3.3.2 当前已经确定好的 item 顺序。
- 3.3.3 直接遍历这份既有 item 列表，不在这一阶段重新做 route search、child selection、parent backtrack 或 fork 展开。
- 对每个 item，Python 只整理该 item 自身的 `boundary_takeover`、`boundary_handoffs`、`_boundary_handoff_routes`，以及必要的前后相邻 item 摘要，作为 prompt 上下文。
- 如果 3.3.2 的现有路线图本身存在缺口、歧义、缺失 item、或 route 不完整，3.3.3 只把这些问题反映到当前 item 的 `partial` / `unknown`，不尝试在这一阶段补搜路径。
- v1 默认不展开多出口 fork；未展开支路、unresolved route、或非主链信息只作为当前 module item 的上下文注释进入 prompt。
- v1 的 `$dep` 可见性严格按线性链收紧：当前 module 的第一个 task 只能引用上一个 module 的 leaf task；当前 module 内后续 task 只能引用本 module 中前一个 task。
- 不允许引用上一个 module 的非 leaf task、不允许引用更早 module、不允许引用 sibling / fork 支路，也不允许前向引用未生成的 task。

**APV 输出约束**
- 每个 module item 的一次 LLM 调用输出一个 JSON 中间协议，至少包含：`status`（`complete|partial`）、`tasks`、`unknown`。其中 `tasks` 只描述当前 item 对应的 APV 内容。
- 每个 task item 至少包含：`ref_name`、`task_name`、`condition_lines`、`capture_signals`、`logging_lines`、`match_mode`、`max_match`。
- 每个 YAML 文件由 Python 根据当前 item 的 JSON 中间协议写出；文件内允许包含一个或多个属于该 item 的 task。
- task id 由 Python 统一生成为稳定格式，如 `s00_<instance_slug>`、`s01_<instance_slug>`。Python 同时维护线性的全局 task 注册表，并把每个 module item 的最后一个 task 记录为该 item 的 `leaf_task_id`。
- `scope` 与信号名的最终形态由 Python 中间转换层统一决定。模型只需要表达当前 module item 的信号语义；Python 负责把这些引用规范化为相对未来 APV 根 `scope` 的层级路径，例如去掉顶层 RTL module 名后的 `x_ct_top_0.x_ct_core...signal`。v1 仍不使用 task-level `scope`。
- 模型不直接输出最终 `$dep.<task_id>.<signal>`，而是通过 `$dep.<ref_name>.<signal>` 引用上游 task 暴露出来的 capture 信号。
- 每个 task 都显式声明一个稳定的 `ref_name`；后续 task 可以在同一个 raw JSON 中用 `$dep.<ref_name>.<signal>` 回指更早声明的 task。
- 对跨 module 的依赖，Python 在 prompt 的 `Visible Upstream Dep Handles` 中提供候选列表，至少包含 `ref_name`、`task_id`、`capture_names`、`module`、`instance`、`path`。
- Python 根据 `ref_name` 校验依赖引用、回填 `dependsOn`，并在 task id 分配完成后把 raw `$dep.<ref_name>.<signal>` 统一改写成最终 `$dep.<task_id>.<signal>`。
- `leaf task` 在 v1 中定义为当前 module item 归一化后 task 列表中的最后一个 task。
- `condition` 与 `logging` 最终由 Python 写成 YAML list 形式；不把大段自然语言直接塞进 `condition`。
- `ref_name`、`task_name`、`condition_lines`、`capture_signals` 非空；`match_mode` 必须属于 Python 允许集合；`max_match >= 0`；若使用 `$dep.<ref_name>.<signal>`，则同一个 task 中只能引用一个唯一 `ref_name`，且该 `ref_name` 与 `signal` 必须能在可见 dep 符号表中解析。

**产物调整**
- `instrack/<inst>/artifacts/<path_slug>.apv.yaml`：当前 module item 对应的 APV YAML 文件。
- `instrack/<inst>/artifacts/<path_slug>.apv.raw.md`：当前 module item 这一次 LLM 调用的原始输出，便于 debug。
- `instrack/<inst>/artifacts/apv_index.json`：记录所有 module item 的输入摘要、artifact 路径、complete / partial 状态与原因，以及每个 item 的 `first_task_id` / `leaf_task_id`。
- Markdown 汇总页不再列 `.mmd`，改为列 search JSON、orchestrate index，以及每个 module item 对应的 APV YAML artifact 状态。
- pass3.3 总输出 JSON 改为新的 schema version，并将 `render_index_artifact_json` 替换为 `apv_index_artifact_json`。

**测试计划**
- 保留现有 3.3.2 stages / takeover 测试作为基线，新增 3.3.3 APV 纯消费层测试，不改写既有 continuation 语义。
- 覆盖“每个 module item 只发起一次 LLM 调用”的约束测试，确认 3.3.3 不注册 tool、不进入多轮交互、不触发 fork。
- 用构造好的 search + orchestrate mock 覆盖至少 3 类场景：module item 正常生成 YAML、module item 因路线图缺口生成 `partial`、模型输出结构非法被拒绝。
- 覆盖“直接复用既有路线图”的测试：3.3.3 只消费 3.3.2 item 列表和 item-level route context，不在运行时重新选择 child / parent 路径。
- 覆盖串行 `$dep` 规则：当前 module 的第一个 task 只能回指 `Visible Upstream Dep Handles` 中给出的上游 candidate；同 module 内后续 task 只能回指已声明的前序 `ref_name`；非法引用、前向引用、重复 `ref_name` 都必须被拒绝。
- 覆盖 Python `$dep` 回填：模型只输出 raw `$dep.<ref_name>.<signal>`，Python 在 task id 分配后写成最终 `$dep.<task_id>.<signal>`，并正确更新 `apv_index.json` 中的 `leaf_task_id` / `leaf_ref_name`。
- 对每个生成的 `<path_slug>.apv.yaml` 做最小包装 YAML 校验；如果本地 APV validator 可用，再额外跑一次 validator 校验。
- 选一条真实指令先做 `ADD` 端到端冒烟：search -> orchestrate -> apv，检查多个 module item 都各自产生 YAML artifact，且状态可解释。
- 回归验证 `pass3_3_3_enabled=false` 时完全跳过 3.3.3；旧 render hash 不会误命中新 APV hash；旧 `.mmd` 输出不再被 Markdown/JSON 索引引用。

**假设与默认**
- 3.3.2 当前公开 schema 视为稳定基线：`boundary_handoffs` 继续只保留 `output_port / value_condition / behavior`，route 证据继续放在 `_boundary_handoff_routes`。
- 3.3.3 v1 是纯后处理阶段，不会反向触发新的 orchestrate 调用，也不会在本阶段补做路线搜索。
- v1 按 module item 分文件生成 APV YAML，不自动展开 fork；`$dep` 只支持线性链，不支持通用 DAG；也不自动推导最终 APV 根 `scope`、FSDB 文件路径和 flush 策略。
- `artifact_raw` 继续只作为 debug 产物存在，不作为 3.3.3 的强依赖输入；3.3.3 的权威输入仍是 search JSON 与 orchestrate index JSON。
