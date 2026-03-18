1. Continuation State
这是每次 3.3.2 agent 输入里最关键的状态消息，嵌在 prompt 的 `## Continuation State` 下面。

设计目标：

- 这是 parent 继续决策时给 LLM 的上游延续状态
- 这份状态应当覆盖“当前路径为什么会进入本模块、当前仍在追什么、下一跳候选 child 是谁”
- 这份状态允许完整，但应避免塞入与当前路径无关的长段叙事

完整示例：`openC910/x_ct_top_0/x_ct_core/x_ct_idu_top`

```json
{
  "module": "ct_idu_top",
  "instance": "x_ct_idu_top",
  "upstream_handoff": [
    "ifu_idu_ib_inst0_data[WIDTH-1:0](dispatch_bundle) -> x_ct_idu_top(ct_idu_top):ifu_idu_ib_inst0_data",
    "ifu_idu_ib_inst0_vld(instruction_valid) -> x_ct_idu_top(ct_idu_top):ifu_idu_ib_inst0_vld"
  ],
  "upstream_context": {
    "source_instance": "x_ct_ifu_top",
    "source_module": "ct_ifu_top",
    "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top",
    "parent_module": "ct_core",
    "entry_ports": [
      {
        "port": "ifu_idu_ib_inst0_data",
        "direction": "input",
        "value_kind": "dispatch_bundle",
        "semantic": "Complete dispatch bundle for slot 0 instruction, including ADD encoding, PC, exception bits, and vector configuration",
        "matched_from": {
          "source_instance": "x_ct_ifu_top",
          "source_module": "ct_ifu_top",
          "source_port": "ifu_idu_ib_inst0_data[WIDTH-1:0]",
          "parent_wire": "ifu_idu_ib_inst0_data"
        }
      },
      {
        "port": "ifu_idu_ib_inst0_vld",
        "direction": "input",
        "value_kind": "instruction_valid",
        "semantic": "Dispatch-valid qualifier for slot 0 instruction",
        "matched_from": {
          "source_instance": "x_ct_ifu_top",
          "source_module": "ct_ifu_top",
          "source_port": "ifu_idu_ib_inst0_vld",
          "parent_wire": "ifu_idu_ib_inst0_vld"
        }
      }
    ],
    "candidate_children": [
      {
        "target_instance": "x_ct_idu_id_dp",
        "target_module": "ct_idu_id_dp"
      },
      {
        "target_instance": "x_ct_idu_id_ctrl",
        "target_module": "ct_idu_id_ctrl"
      }
    ],
    "resolved_handoffs": [
      {
        "egress_port": "ifu_idu_ib_inst0_data[WIDTH-1:0]",
        "semantic": "Complete dispatch bundle for slot 0 instruction",
        "value_kind": "dispatch_bundle",
        "behavior": "Combinational mux output from IFU bypass/ibuf/lbuf selection; stable while source registers stay stable; cleared on reset or exception injection",
        "source_block": "x_ct_ifu_ibdp.p13",
        "source_state": "inst0_data[WIDTH-1:0]",
        "status": "resolved",
        "parent_wire": "ifu_idu_ib_inst0_data",
        "targets": [
          {
            "target_instance": "x_ct_idu_top",
            "target_module": "ct_idu_top",
            "target_port": "ifu_idu_ib_inst0_data",
            "parent_wire": "ifu_idu_ib_inst0_data"
          }
        ]
      },
      {
        "egress_port": "ifu_idu_ib_inst0_vld",
        "semantic": "Dispatch-valid qualifier for slot 0 instruction",
        "value_kind": "instruction_valid",
        "behavior": "Asserted when IFU slot 0 holds a valid dispatchable instruction and no flush/reset masks it",
        "source_block": "x_ct_ifu_ibctrl.p8",
        "source_state": "inst0_vld",
        "status": "resolved",
        "parent_wire": "ifu_idu_ib_inst0_vld",
        "targets": [
          {
            "target_instance": "x_ct_idu_top",
            "target_module": "ct_idu_top",
            "target_port": "ifu_idu_ib_inst0_vld",
            "parent_wire": "ifu_idu_ib_inst0_vld"
          }
        ]
      }
    ],
    "exits_parent": [],
    "unresolved": [],
    "lifecycle_context": {
      "stage": "decode_entry",
      "focus": "Receive ADD from IFU and propagate it into ID stage decode and rename pipeline",
      "next_expected_observations": [
        "Decoded instruction bundle for IR stage",
        "Source and destination register indices for rs1/rs2/rd",
        "Candidate child modules that continue ADD through decode"
      ]
    },
    "instruction_state": [
      {
        "state": "inst_word",
        "semantic": "32-bit ADD instruction encoding",
        "carrier": "ifu_idu_ib_inst0_data[31:0]"
      },
      {
        "state": "pc",
        "semantic": "Program counter associated with dispatched ADD",
        "carrier": "ifu_idu_ib_inst0_data[PC_FIELD]"
      },
      {
        "state": "valid",
        "semantic": "Dispatch valid for slot 0",
        "carrier": "ifu_idu_ib_inst0_vld"
      },
      {
        "state": "exception_status",
        "semantic": "Any IFU-side exception bits carried with the instruction",
        "carrier": "ifu_idu_ib_inst0_data[EXPT_FIELD]"
      }
    ]
  }
}
```

字段清单：

- `module`
- `instance`
- `upstream_handoff`
- `upstream_context.source_instance`
- `upstream_context.source_module`
- `upstream_context.source_instance_path`
- `upstream_context.parent_module`
- `upstream_context.entry_ports`
- `upstream_context.candidate_children`
- `upstream_context.resolved_handoffs`
- `upstream_context.exits_parent`
- `upstream_context.unresolved`
- `upstream_context.lifecycle_context`
- `upstream_context.instruction_state`

`x_ct_idu_top` 实测占比：

- `Continuation State` 约 `6882 chars`
- 这一轮完整 `User Prompt` 约 `143626 chars`
- `Continuation State` 占整轮 prompt 约 `4.8%`
- `Continuation State` 内部最大子项是 `upstream_context.instruction_state`，约 `3220 chars`
- 第二大子项是 `upstream_context.entry_ports`，约 `2148 chars`

结论：

- `Continuation State` 在 `idu_top` 不是整轮 prompt 的最大块
- 但它仍是“路径延续信息”里最重的一段，尤其是 `instruction_state`
- 如果要裁这段，优先从 `instruction_state` 的表达粒度下手，而不是先裁 `lifecycle_context`

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
    "boundary_handoffs": [
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
    "lifecycle_context": {
      "stage": "rf_to_iu_boundary",
      "focus": "ADD has left AIQ/RF arbitration and is now selecting IU pipe0 for execution"
    },
    "instruction_state": [
      {
        "state": "src0_data",
        "carrier": "rf_pipe0_src0_data"
      },
      {
        "state": "src1_data",
        "carrier": "rf_pipe0_src1_data"
      },
      {
        "state": "dst_preg",
        "carrier": "rf_pipe0_dst_preg"
      }
    ]
  },
  "upstream_context": {
    "...": "same shape as Continuation State.upstream_context"
  }
}
```

语义：

- 初始值来自 parent round 输入
- 每次 child 返回后整体前移
- cached child 复用时也一样前移
- 这层可以完整，但不应该直接原样灌给 LLM

3. Child Tool Result
这是 `drawChild(...)` 返回给 LLM 的中间消息格式。它不是 child 的完整 module trace，而是 parent 继续决策时消费的 continuation digest。

设计目标：

- 全量 child trace 可以持久化保存到 artifact/debug
- parent-facing `Child Tool Result` 只保留继续追路径所必需的信息，不回传 child `entry_ports`
- 如果 child 已经解析出下一跳，应显式暴露 `next_children`

完整示例：`x_ct_idu_is_aiq0 -> x_ct_idu_rf_dp / x_ct_idu_rf_ctrl`

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
        },
        {
          "resolution_kind": "sibling_child",
          "target_instance": "x_ct_idu_rf_ctrl",
          "target_module": "ct_idu_rf_ctrl",
          "target_port": "aiq0_xx_issue_en",
          "parent_wire": "aiq0_xx_issue_en"
        }
      ]
    },
    {
      "egress_port": "aiq0_dp_issue_read_data",
      "status": "resolved",
      "value_kind": "packed_queue_entry",
      "semantic": "Selected ADD issue payload containing rs1/rs2 physical registers, destination preg, IID, and ALU control fields",
      "behavior": "Combinational mux output selecting the issued AIQ0 entry or bypass-created entry",
      "resolutions": [
        {
          "resolution_kind": "sibling_child",
          "target_instance": "x_ct_idu_rf_dp",
          "target_module": "ct_idu_rf_dp",
          "target_port": "aiq0_dp_issue_read_data",
          "parent_wire": "aiq0_dp_issue_read_data"
        }
      ]
    }
  ],
  "instruction_state": [
    {
      "state": "issue_ready",
      "semantic": "ADD has passed AIQ0 readiness selection and is ready to issue"
    },
    {
      "state": "src0_preg",
      "semantic": "Physical register index for rs1 carried in issue payload"
    },
    {
      "state": "src1_preg",
      "semantic": "Physical register index for rs2 carried in issue payload"
    },
    {
      "state": "dst_preg",
      "semantic": "Destination physical register allocated for rd"
    }
  ],
  "lifecycle_context": {
    "stage": "issue_queue",
    "focus": "AIQ0 has selected ADD for issue and is handing it to RF datapath/control logic"
  },
  "confidence": "medium",
  "unknown": [],
  "next_children": [
    {
      "target_instance": "x_ct_idu_rf_dp",
      "target_module": "ct_idu_rf_dp"
    },
    {
      "target_instance": "x_ct_idu_rf_ctrl",
      "target_module": "ct_idu_rf_ctrl"
    }
  ],
  "task": "Trace ADD instruction through AIQ0: entry allocation, source operand readiness monitoring, age-based arbitration, and issue to RF datapath/control"
}
```

字段清单：

- `child`
- `cached`
- `boundary_handoffs`
- `instruction_state`
- `lifecycle_context`
- `confidence`
- `unknown`
- `next_children`
- `task`

`x_ct_idu_top` 实测占比：

- 这轮一共累计了 6 轮 `Tool Trace Results`
- `Tool Trace Results` 合计约 `22887 chars`
- 同轮 `Continuation State` 约 `6882 chars`
- 在“只看这两段”的情况下，`Child Tool Result/history` 占约 `76.9%`，`Continuation State` 占约 `23.1%`

结论：

- 对 `idu_top` 这种多轮 sibling-child 推进的模块，第一优化对象是 `Child Tool Result/history`
- `Child Tool Result` 不应等同于 full child trace
- 推荐做法是：full trace 全量落盘，parent-facing result 只保留当前路径相关 `boundary_handoffs`、`instruction_state` 摘要、`lifecycle_context` 摘要、`next_children`

4. Orchestration Payload
这是每个模块最终保存到 orchestrate index 里的标准结果，也是 downstream render 消费的核心格式。

格式：

```json
{
  "module": "ct_core",
  "instance": "x_ct_core",
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
  "lifecycle_context": {
    "stage": "rf_to_iu_boundary",
    "focus": "ADD leaves IDU and enters IU pipe0 execution path"
  },
  "instruction_state": [
    {
      "state": "src0_data",
      "semantic": "Operand value for rs1 after RF read/forwarding"
    },
    {
      "state": "src1_data",
      "semantic": "Operand value for rs2 after RF read/forwarding"
    }
  ],
  "confidence": "medium",
  "unknown": []
}
```

必备字段：

- `module`
- `instance`
- `entry_ports`
- `boundary_handoffs`
- `lifecycle_context`
- `instruction_state`
- `confidence`
- `unknown`

增强字段：

- `entry_ports[].matched_from`
- `boundary_handoffs[].resolutions`
- `boundary_handoffs[].status`
- `boundary_handoffs[].source_instance_path`
- `boundary_handoffs[].source_module`
- `boundary_handoffs[].source_instance`
- `boundary_handoffs[].handoff_id`
- `boundary_handoffs[].source_direction`
- `boundary_handoffs[].parent_wire`

5. Debug Trace Event
为了看状态是否真的在推进，现在还额外打了两类中间 trace 事件。

`draw_parent_context`

```json
{
  "event": "draw_parent_context",
  "pass": "pass3_3_2_orchestrate",
  "prompt_style": "instrack_draw",
  "stage_label": "parent_module",
  "instance": "x_ct_core",
  "module": "ct_core",
  "level": 2,
  "node_path": "openC910/x_ct_top_0/x_ct_core",
  "source_instance": "x_ct_ifu_top",
  "source_module": "ct_ifu_top",
  "candidate_children": [
    {
      "target_instance": "x_ct_idu_top",
      "target_module": "ct_idu_top"
    }
  ],
  "resolved_handoffs": [
    {
      "egress_port": "ifu_idu_ib_inst0_data[WIDTH-1:0]",
      "status": "resolved"
    }
  ],
  "exits_parent": [],
  "unresolved": []
}
```

`draw_continuation_update`

```json
{
  "event": "draw_continuation_update",
  "pass": "pass3_3_2_orchestrate",
  "prompt_style": "instrack_draw",
  "parent_path": "openC910/x_ct_top_0/x_ct_core",
  "parent_level": 2,
  "child_instance": "x_ct_idu_top",
  "child_module": "ct_idu_top",
  "cached": false,
  "source_instance": "x_ct_idu_top",
  "source_module": "ct_idu_top",
  "candidate_children": [
    {
      "target_instance": "x_ct_iu_top",
      "target_module": "ct_iu_top"
    }
  ],
  "resolved_handoffs": [
    {
      "egress_port": "idu_iu_rf_pipe0_sel",
      "status": "resolved"
    },
    {
      "egress_port": "idu_iu_rf_pipe0_gateclk_sel",
      "status": "resolved"
    }
  ],
  "exits_parent": [],
  "unresolved": []
}
```
