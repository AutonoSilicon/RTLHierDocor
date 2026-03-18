1. Continuation State
这是每次 3.3.2 agent 输入里最关键的状态消息，嵌在 prompt 的 `## Continuation State` 下面。

设计目标：

- 只向 LLM 暴露当前 trace 真正需要的 continuation 事实
- 当前模块的合法入口只由 Python 生成的 `boundary_takeover` 提供
- sibling child 继续推进时，LLM 只消费 Python 已经闭合好的 takeover / child 候选
- parent bridge 场景允许 `boundary_takeover` 为空，但必须保留来源 provenance

完整示例：`openC910/x_ct_top_0/x_ct_core/x_ct_idu_top`

```json
{
  "module": "ct_idu_top",
  "instance": "x_ct_idu_top",
  "boundary_takeover": [
    {
      "ingress_port": "ifu_idu_ib_inst0_data",
      "value_kind": "dispatch_bundle",
      "semantic": "Complete dispatch bundle for slot 0 instruction, including ADD encoding, PC, exception bits, and vector configuration",
      "taken_from": {
        "source_instance": "x_ct_ifu_top",
        "source_module": "ct_ifu_top",
        "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top",
        "source_port": "ifu_idu_ib_inst0_data[WIDTH-1:0]",
        "source_handoff_id": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top::ifu_idu_ib_inst0_data[WIDTH-1:0]",
        "parent_wire": "ifu_idu_ib_inst0_data"
      }
    },
    {
      "ingress_port": "ifu_idu_ib_inst0_vld",
      "value_kind": "instruction_valid",
      "semantic": "Dispatch-valid qualifier for slot 0 instruction",
      "taken_from": {
        "source_instance": "x_ct_ifu_top",
        "source_module": "ct_ifu_top",
        "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top",
        "source_port": "ifu_idu_ib_inst0_vld",
        "source_handoff_id": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top::ifu_idu_ib_inst0_vld",
        "parent_wire": "ifu_idu_ib_inst0_vld"
      }
    }
  ],
  "lifecycle_context": [
    {
      "accessed_submodule": "x_ct_ifu_top(ct_ifu_top)",
      "behavior_description": "Receive ADD-related dispatch bundle from IFU and continue tracing it through decode entry."
    }
  ],
  "continuation_source": {
    "source_instance": "x_ct_ifu_top",
    "source_module": "ct_ifu_top",
    "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top"
  }
}
```

字段清单：

- `module`
- `instance`
- `boundary_takeover`
- `lifecycle_context`
- `continuation_source`

要点：

- `boundary_takeover` 是当前模块的唯一 authoritative entry contract
- `boundary_takeover` 只接受严格端口匹配生成的结果，不允许语义猜测补洞
- parent bridge 场景可以出现 `boundary_takeover=[]`
- 当 `boundary_takeover=[]` 时，LLM 只能依赖 `continuation_source` 保留来源，不得虚构 ingress port

2. Active Continuation
这是 Python 内部维护的“当前 parent session 正在追哪一段”的状态，不直接作为最终产物落盘，但决定后续 `drawChild` 怎么走。

内部结构：

```json
{
  "source_child_node": "<node object>",
  "handoffs": [
    {
      "egress_port": "idu_iu_rf_pipe0_sel",
      "status": "resolved",
      "resolutions": [
        {
          "resolution_kind": "exit_parent",
          "parent_port": "idu_iu_rf_pipe0_sel",
          "parent_wire": "idu_iu_rf_pipe0_sel"
        }
      ]
    }
  ],
  "payload": {
    "module": "ct_idu_top",
    "instance": "x_ct_idu_top",
    "boundary_takeover": [],
    "boundary_handoffs": [
      {
        "egress_port": "idu_iu_rf_pipe0_sel",
        "status": "resolved"
      }
    ],
    "lifecycle_context": "ADD has left AIQ/RF arbitration and is now selecting IU pipe0 for execution"
  },
  "lifecycle_context": [
    {
      "accessed_submodule": "x_ct_idu_top(ct_idu_top)",
      "behavior_description": "ADD has left AIQ/RF arbitration and is now selecting IU pipe0 for execution"
    }
  ],
  "bridge_context": {
    "source_instance": "x_ct_idu_top",
    "source_module": "ct_idu_top",
    "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top",
    "parent_module": "ct_core",
    "candidate_children": [
      {
        "target_instance": "x_ct_iu_top",
        "target_module": "ct_iu_top"
      }
    ],
    "takeover_bundles": [
      {
        "target_instance": "x_ct_iu_top",
        "target_module": "ct_iu_top",
        "boundary_takeover": [
          {
            "ingress_port": "idu_iu_rf_pipe0_sel",
            "value_kind": "instruction_valid",
            "semantic": "pipe0 issue valid",
            "taken_from": {
              "source_instance": "x_ct_idu_top",
              "source_module": "ct_idu_top",
              "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top",
              "source_port": "idu_iu_rf_pipe0_sel",
              "source_handoff_id": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top::idu_iu_rf_pipe0_sel",
              "parent_wire": "idu_iu_rf_pipe0_sel"
            }
          }
        ]
      }
    ],
    "resolved_handoffs": [
      {
        "egress_port": "idu_iu_rf_pipe0_sel",
        "status": "resolved"
      }
    ],
    "exits_parent": [],
    "unresolved": []
  },
  "boundary_takeover": [],
  "continuation_source": {
    "source_instance": "x_ct_idu_top",
    "source_module": "ct_idu_top",
    "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top"
  }
}
```

语义：

- `bridge_context` 是 Python 内部 continuation/调度状态
- `bridge_context` 不进入 prompt，不进入 artifact，不作为 LLM 输出 schema
- `boundary_takeover` 是从 `bridge_context.takeover_bundles` 里投影给“当前目标模块”的那一小段
- child 返回后，active continuation 会前移到新 source child，并重算 bridge context

3. Child Tool Result
这是 `drawChild(...)` 返回给 LLM 的中间消息格式。它不是 child 的完整 module trace，而是 parent 继续决策时消费的 continuation digest。

设计目标：

- 全量 child trace 仍然保存到 artifact/debug
- parent-facing result 只保留继续追路径所必需的信息
- `next_children` 必须来自 Python 已闭合好的 takeover bundles，而不是松散文本猜测

完整示例：

```json
{
  "child": {
    "module": "ct_idu_is_aiq0",
    "instance": "x_ct_idu_is_aiq0",
    "path": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top/x_ct_idu_is_aiq0"
  },
  "cached": false,
  "boundary_handoffs": [
    {
      "egress_port": "aiq0_xx_issue_en",
      "status": "resolved",
      "value_kind": "control_flag",
      "semantic": "Issue enable indicating ADD is selected and ready to leave AIQ0",
      "behavior": "Asserted when bypass issue path wins or oldest-ready AIQ0 entry is selected",
      "resolutions": [
        {
          "resolution_kind": "sibling_child",
          "target_instance": "x_ct_idu_rf_dp",
          "target_module": "ct_idu_rf_dp",
          "target_port": "aiq0_xx_issue_en",
          "parent_wire": "aiq0_xx_issue_en"
        }
      ]
    }
  ],
  "next_children": [
    {
      "target_instance": "x_ct_idu_rf_dp",
      "target_module": "ct_idu_rf_dp"
    }
  ],
  "confidence": "medium",
  "unknown": [],
  "task": "continue issue"
}
```

字段清单：

- `child`
- `cached`
- `boundary_handoffs`
- `next_children`
- `confidence`
- `unknown`
- `task`

4. Orchestration Payload
这是每个模块最终保存到 orchestrate index 里的标准结果，也是 downstream render 消费的核心格式。

格式：

```json
{
  "module": "ct_core",
  "instance": "x_ct_core",
  "boundary_takeover": [],
  "boundary_handoffs": [
    {
      "source_block": "x_ct_idu_top",
      "source_state": "rf_pipe0_inst_vld",
      "egress_port": "idu_iu_rf_pipe0_sel",
      "value_kind": "boolean",
      "semantic": "pipe0_issue_valid",
      "behavior": "Asserted when ADD is selected for IU pipe0 and launch does not fail",
      "resolutions": [
        {
          "resolution_kind": "child",
          "target_instance": "x_ct_iu_top",
          "target_module": "ct_iu_top",
          "target_port": "idu_iu_rf_pipe0_sel",
          "parent_wire": "idu_iu_rf_pipe0_sel"
        }
      ],
      "status": "resolved",
      "confidence": "medium",
      "unknown": "",
      "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top",
      "source_module": "ct_idu_top",
      "source_instance": "x_ct_idu_top",
      "handoff_id": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top::idu_iu_rf_pipe0_sel",
      "source_direction": "output",
      "parent_wire": "idu_iu_rf_pipe0_sel"
    }
  ],
  "lifecycle_context": "ADD leaves IDU and enters IU pipe0 execution path",
  "confidence": "medium",
  "unknown": []
}
```

必备字段：

- `module`
- `instance`
- `boundary_takeover`
- `boundary_handoffs`
- `lifecycle_context`
- `confidence`
- `unknown`

增强字段：

- `boundary_takeover[].taken_from`
- `boundary_handoffs[].resolutions`
- `boundary_handoffs[].status`
- `boundary_handoffs[].source_instance_path`
- `boundary_handoffs[].source_module`
- `boundary_handoffs[].source_instance`
- `boundary_handoffs[].handoff_id`
- `boundary_handoffs[].source_direction`
- `boundary_handoffs[].parent_wire`
