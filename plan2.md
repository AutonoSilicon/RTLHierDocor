# Pass3.3.3 Branch-Tree 语义重构与 Python+Prompt 联调方案

## Summary
本轮把 APV 的 `branch_tags` 从“当前 task 的扁平标签”正式升级为“沿依赖链单调增长的路径身份”，让 3.3.3 明确支持决策树式分支传播，而不是伪线性链。

目标是同时解决两类不稳定：
- `ibctrl` 这类正常“继承后细化”的 raw 输出被 Python 质量门误判为错误。
- `ibdp`/`ct_ifu_top` 这类链路里，prompt 已经鼓励分支与 continuity，但 Python 侧协议没有给出稳定、可累积的路径语义。

本轮采用的决策：
- `branch_tags` 可继承并细化。
- 继承来源是 task 的直接 `dependsOn` 目标。
- 对有依赖的 task，raw `branch_tags` 改为“本地增量”语义。
- 允许直接继承，不要求每一步都显式再写 tags。
- 对旧的“有依赖 task 直接写完整路径 tags”采用硬切，不再兼容。
- continuity 继续由 prompt 强化与 debug 观察驱动，不作为 `complete -> partial` 的硬降级条件。

## 协议与接口变更
### APV Raw JSON 语义
- 保持字段名仍为 `branch_tags`，但重新定义语义：
  - 无 `dependsOn` 的 root/source task：`branch_tags` 表示该 task 产生的初始完整路径身份。
  - 有 `dependsOn` 的 task：`branch_tags` 仅表示相对直接依赖的“本地新增细化键值”。
  - 若该 task 没有新增分支判别，可省略 `branch_tags` 或写空对象，表示“直接继承”。
- 对有依赖的 task，禁止在 raw `branch_tags` 中重复祖先已有 key。
  - 例如上游是 `{"channel":"ifctrl"}`，下游只能写 `{"path":"bypass"}`，不能再写 `{"channel":"ifctrl","path":"bypass"}`。
- canonical key 约定固定为：
  - 源头分支优先用 `channel`
  - 下游细化优先用 `path`
  - 进一步细化再用 `slot` / `pipe` / `buffer`
- `leaf_contexts[*].branch_tags`、Visible Upstream Dep Handles 中的 `branch_tags` 一律保存“已合并后的完整路径身份”。
- runtime `.apv.yaml` 继续不落 `branch_tags`。

### Python Materialization 与质量门
- 在 [pass3_instrack_apv.py](/home/ling/RTLHierDocor/src/agent/pass3_instrack_apv.py) 中引入明确的 tag 合并逻辑：
  - `effective_branch_tags = direct_dep_effective_tags + local_delta_branch_tags`
  - 若 local delta 试图复用或覆盖祖先 key，直接报错。
- `dep_symbols` 存储的 `branch_tags` 改为“effective merged tags”，不再是 raw 值。
- 删除当前“有多个 upstream candidates 时 raw tags 必须与 candidate tags 全等”的规则，替换为：
  - 直接依赖哪个 candidate，由 `$dep.<ref_name>` 决定
  - effective tags 必须包含该直接依赖的全部 branch identity
  - local delta 只能追加，不能覆盖
- terminal leaf 校验改为基于 effective tags：
  - multi-leaf item 的 terminal leaves 必须具有非空 effective tags
  - sibling terminal leaves 的 effective tags 必须唯一
- 允许“空增量直接继承”：
  - task 直接依赖 upstream/local task 但无新增分支时，不再要求 raw `branch_tags` 非空
  - 该 task 的导出 leaf_contexts 自动继承父节点 effective tags
- continuity 处理调整：
  - 去掉当前 same-line continuity 的硬降级 gate
  - continuity 分析保留为 prompt 约束和 debug/诊断信息
  - 不新增 repo artifact schema 字段；若要记录只写 debug 日志，不写 `unknown`

## Prompt 调优
在 [pass3_3_apv.py](/home/ling/RTLHierDocor/src/agent/prompts/pass3_3_apv.py) 中整体改成“决策树继承语义”。

### 关键文案重写
- 把 “copy upstream candidate branch_tags exactly onto that task” 改成：
  - task 继承其直接依赖的 branch identity
  - raw `branch_tags` 只写本地新增细化
  - 若无新增细化，可不写 `branch_tags`
  - 不得重写祖先 key
- 把 multi-leaf 规则改成基于 exported/effective leaf identity，而不是 raw task body 必须显式携带完整 tags。
- continuity 规则改成：
  - 强烈优先 `pc/inst/data` anchor
  - valid-only 是弱证据
  - 但不再宣称 Python 会因 valid-only 自动把 `complete` 降为 `partial`

### 例子与反例
- `pcgen` 正例：
  - root task 扇出 `channel=ifctrl/icache/btb/bht`
- `ibctrl` 正例：
  - 依赖 `channel=ifctrl`
  - raw 只写 `{"path":"bypass"}` / `{"path":"ibuf"}` / `{"path":"lbuf"}`
- `ibdp` 正例：
  - 若只是继承 bypass 路径并新增 slot 区分，只写 `{"slot":"0"}`
  - 若没有新的分叉，可直接省略 `branch_tags`
- 反例：
  - 上游 `{"channel":"ifctrl"}`，下游重复写 `{"channel":"ifctrl","path":"bypass"}`，应判错
  - 祖先 key 被改值也应判错

### Prompt Payload 增强
- 在可见 upstream handles 中继续提供完整 effective tags
- 增加一小段“Branch Identity Guidance”，明确：
  - 你看到的是完整祖先路径
  - 你只需要写当前 step 新增的细化键
  - 如果当前 step 无新分支，直接继承即可

## Test Plan
### 单元与协议测试
更新 [test_pass3_instrack_apv.py](/home/ling/RTLHierDocor/tests/test_pass3_instrack_apv.py)：
- root multi-leaf task 直接写完整 `channel` tags，`complete` 保持通过
- 有依赖 task 省略 `branch_tags` 时，自动继承 direct dep effective tags
- 有依赖 task 写 delta，例如 `path=bypass`，最终 leaf_contexts 合并成完整路径
- 有依赖 task 重复祖先 key，即使值相同，也报错
- 有依赖 task 覆盖祖先 key 值，报错
- 三层链 `pcgen(channel) -> ibctrl(path) -> ibdp(slot)` 的 `leaf_contexts[*].branch_tags` 必须是完整合并结果
- terminal uniqueness 基于 merged/effective tags，而不是 raw delta
- runtime YAML 继续不出现 `branch_tags`
- continuity 不再因为缺少 same-line anchor 自动降级 `complete`

更新 [test_pass3_instrack_prompts.py](/home/ling/RTLHierDocor/tests/test_pass3_instrack_prompts.py)：
- 断言 prompt 明确说明“有依赖 task 的 `branch_tags` 是本地增量”
- 断言 prompt 明确说明“无新分支可直接继承，不必重写 tags”
- 断言 prompt 明确说明“不得重写祖先 key”
- 新增 `pcgen -> ibctrl -> ibdp` 的 delta 示例文案
- 移除或替换“exactly copy selected candidate branch_tags”这类旧断言

### 快速实测验收
以 `ADD` 前 5 个 item 为基线：
- `ct_ifu_pcgen`
  - 保持 `complete`
  - 4 个 leaf_contexts 均有完整唯一 branch identity
- `ct_ifu_ibctrl`
  - raw 改为 delta 写法，如 `path=bypass`
  - 不再因“branch_tags 全等校验”而变 `partial`
- `ct_ifu_ibdp`
  - 可只写 `slot=0` 或直接继承
  - 最终导出的 leaf_contexts 具备完整 merged tags
- `ct_ifu_top` / `ct_idu_top`
  - 若仍为 `partial`，原因应来自真实语义证据不足，而不是 branch identity 协议冲突
  - 不再出现由 branch tag 规则引发的连锁 unknown dep 错误

## Assumptions
- 本轮只调 3.3.3 APV 协议与 prompt，不改 3.3.2 route 顺序语义。
- `branch_tags` 的“硬切增量”仅针对有依赖的 task；root/source task 仍写完整初始 tags。
- continuity 只做提示性增强，不新增 artifact schema，也不做硬降级。
- runtime `.apv.yaml` 结构保持兼容，`branch_tags` 继续只存在于 raw JSON、prompt payload、`apv_index.json` 的 `leaf_contexts` 中。
