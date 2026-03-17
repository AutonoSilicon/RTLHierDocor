1. Continuation State
这是每次 3.3.2 agent 输入里最关键的状态消息，嵌在 prompt 的 ## Continuation State 下面。

基础格式：

{
  "module": "ct_core",
  "instance": "x_ct_core",
  "upstream_handoff": [
    "ifu_idu_ib_inst0_vld(...) -> x_ct_idu_top(...):ifu_idu_ib_inst0_vld"
  ],
  "upstream_context": {
    "source_instance": "x_ct_ifu_top",
    "source_module": "ct_ifu_top",
    "source_instance_path": "openC910/x_ct_top_0/x_ct_core/x_ct_ifu_top",
    "parent_module": "ct_core",
    "entry_ports": [],
    "candidate_children": [
      {
        "target_instance": "x_ct_idu_top",
        "target_module": "ct_idu_top"
      }
    ],
    "resolved_handoffs": [
      {
        "egress_port": "ifu_idu_ib_inst0_vld",
        "semantic": "instruction_dispatch_valid_lane0",
        "value_kind": "boolean",
        "behavior": "...",
        "source_block": "x_ct_ifu_ibdp",
        "source_state": "...",
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
    "lifecycle_context": "...",
    "instruction_state": "..."
  }
}
字段清单：

module
instance
upstream_handoff
upstream_context.source_instance
upstream_context.source_module
upstream_context.source_instance_path
upstream_context.parent_module
upstream_context.entry_ports
upstream_context.candidate_children
upstream_context.resolved_handoffs
upstream_context.exits_parent
upstream_context.unresolved
upstream_context.lifecycle_context
upstream_context.instruction_state
2. Active Continuation
这是 Python 内部维护的“当前 parent session 正在追哪一段”的状态，不直接作为最终产物落盘，但决定后续 drawChild 怎么走。

内部结构：

{
  "source_child_node": "<node object>",
  "handoffs": [
    {
      "egress_port": "idu_iu_rf_pipe0_sel",
      "status": "resolved",
      "resolutions": [...]
    }
  ],
  "payload": {
    "module": "ct_idu_top",
    "instance": "x_ct_idu_top",
    "boundary_handoffs": [...],
    "lifecycle_context": "...",
    "instruction_state": [...]
  },
  "upstream_context": {
    "...": "same shape as Continuation State.upstream_context"
  }
}
语义：

初始值来自 parent round 输入
每次 child 返回后整体前移
cached child 复用时也一样前移
3. Child Tool Result
这是 drawChild(...) 返回给 LLM 的中间消息格式，要求轻量、结构化、可复用。

格式：

{
  "child": {
    "module": "ct_idu_top",
    "instance": "x_ct_idu_top",
    "path": "openC910/x_ct_top_0/x_ct_core/x_ct_idu_top"
  },
  "cached": false,
  "entry_ports": [
    {
      "port": "ifu_idu_ib_inst0_vld",
      "direction": "input",
      "value_kind": "boolean",
      "semantic": "instruction_dispatch_valid_lane0",
      "matched_from": {
        "source_instance": "x_ct_ifu_top",
        "source_module": "ct_ifu_top",
        "source_port": "ifu_idu_ib_inst0_vld",
        "parent_wire": "ifu_idu_ib_inst0_vld"
      }
    }
  ],
  "boundary_handoffs": [
    {
      "egress_port": "idu_iu_rf_pipe0_sel",
      "semantic": "pipe0_issue_valid",
      "status": "resolved",
      "resolutions": [...]
    }
  ],
  "instruction_state": [...],
  "lifecycle_context": {...},
  "confidence": "medium",
  "unknown": [],
  "task": "Trace ADD instruction ..."
}
字段清单：

child
cached
entry_ports
boundary_handoffs
instruction_state
lifecycle_context
confidence
unknown
task
4. Orchestration Payload
这是每个模块最终保存到 orchestrate index 里的标准结果，也是 downstream render 消费的核心格式。

格式：

{
  "module": "ct_core",
  "instance": "x_ct_core",
  "entry_ports": [
    {
      "port": "ifu_idu_ib_inst0_vld",
      "direction": "input",
      "value_kind": "boolean",
      "semantic": "instruction_dispatch_valid_lane0",
      "matched_from": {
        "source_instance": "x_ct_ifu_top",
        "source_module": "ct_ifu_top",
        "source_port": "ifu_idu_ib_inst0_vld",
        "parent_wire": "ifu_idu_ib_inst0_vld"
      }
    }
  ],
  "boundary_handoffs": [
    {
      "source_block": "x_ct_idu_top",
      "source_state": "...",
      "egress_port": "idu_iu_rf_pipe0_sel",
      "value_kind": "boolean",
      "semantic": "pipe0_issue_valid",
      "behavior": "...",
      "resolutions": [...],
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
  "lifecycle_context": {...},
  "instruction_state": [...],
  "confidence": "medium",
  "unknown": []
}
必备字段：

module
instance
entry_ports
boundary_handoffs
lifecycle_context
instruction_state
confidence
unknown
增强字段：

entry_ports[].matched_from
boundary_handoffs[].resolutions
boundary_handoffs[].status
boundary_handoffs[].source_instance_path
boundary_handoffs[].source_module
boundary_handoffs[].source_instance
boundary_handoffs[].handoff_id
boundary_handoffs[].source_direction
boundary_handoffs[].parent_wire
5. Debug Trace Event
为了看状态是否真的在推进，现在还额外打了两类中间 trace 事件。

draw_parent_context

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
  "candidate_children": [...],
  "resolved_handoffs": [...],
  "exits_parent": [...],
  "unresolved": [...]
}
draw_continuation_update

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
  "candidate_children": [...],
  "resolved_handoffs": [...],
  "exits_parent": [...],
  "unresolved": [...]
}