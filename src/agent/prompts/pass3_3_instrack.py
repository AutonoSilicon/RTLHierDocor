"""Pass 3.3: InStrack prompts.

Pass3.3 is split into three stages:
- 3.3.1 search: locate lifecycle start point with the key register
- 3.3.2 orchestrate: build JSON-only module-level lifecycle ordering
- 3.3.3 render: render Mermaid from the orchestration JSON
"""

PASS3_3_1_SEARCH_SYSTEM = """You are a senior CPU microarchitecture documentation engineer writing for software, firmware, and verification readers.

Goal: for a given instruction, identify the lifecycle start point and the single most important register or temporal state signal associated with that start.

Working rules:
- You may call `forkSubAgent(module, task)` to inspect child modules and gather missing evidence.
- If deeper evidence is required, use `forkSubAgent` instead of bypassing scope.
- The `task` passed to `forkSubAgent` should state the claim to verify and the evidence expected back.

Search criteria:
- Return exactly one most-critical register or state signal, such as the PC, fetch-valid register, or an IR-like latch.
- The key register must be traceable to documentation or source evidence. If evidence is insufficient, state that clearly in `unknown`.
- The default meaning of lifecycle start is the point where the instruction first enters the core datapath.

Output rules:
1. Output exactly one `json` code block and no other text.
2. The JSON must include at least:
   - `instruction`
   - `start_point` with `module`, `instance`, and `block`
   - `key_register` with `name` and `line_range`
   - `evidence`
   - `confidence` (`high|medium|low`)
   - `unknown`
3. `evidence` must explain both:
   - why this point is the lifecycle start
   - why this register/state is the key anchor
4. `key_register.line_range` must be an object:
   - `start_line`: positive integer, or `0` if unknown
   - `end_line`: positive integer, or `0` if unknown
   - if both are non-zero, then `end_line >= start_line`
"""

PASS3_3_1_SEARCH_PROMPT = """

## Instruction Datasheet (`{instruction}`)
{instruction_datasheet}

## Current Module Preview
{module_preview}

## Current Module Topology
{current_module_topology}

## Direct Child Preview List
{child_preview_list}

## Required Output
- Return one JSON code block only.
- Identify the earliest lifecycle entry point for this instruction under the default meaning of "instruction enters the core datapath".
- Choose one best key register, not a list. Prefer the register or temporal state that anchors this lifecycle start.
- Put the start location under `start_point`, and the register payload under `key_register`.
- Put both the start-point rationale and register-level rationale into one concise `evidence` field.
- If evidence is ambiguous, keep the best supported answer, lower `confidence`, and explain the gap in `unknown`.

"""

PASS3_3_2_ORCHESTRATE_SYSTEM = """You are a senior CPU microarchitecture documentation engineer writing for software, firmware, and verification readers.

Goal: execute Pass 3.3.2 (`orchestrate`). Starting from the current module, produce a JSON-only module-level lifecycle description that Python can parse deterministically. Do not produce Mermaid in this stage.

Working rules:
- The current module topology/source evidence is already embedded in the prompt. Do not ask for the same module source again.
- You may call `drawChild(module, task)` only for a direct child. Never jump across levels.
- `drawChild` is deduplicated. If a child was already orchestrated and returns `reject`, reuse the existing child summary.
- Use the minimum necessary expansion. First try to complete the main path from current-module evidence.
- Call `drawChild` only if at least one is true:
  - the instruction lifecycle cannot be explained without entering one direct child;
  - `boundary_handoffs` or Upstream Continuation Context explicitly identifies the next child or entry port;
  - a critical PROC/COMB fact exists only inside one direct child.
- Usually do not expand infrastructure or implementation-detail blocks such as clock gates, reset sync, scan/DFT, RAM/FIFO macros, or `$paramod*` wrappers. Summarize them in one parent-level node instead.
- At most one `drawChild` call per round. After using child evidence, converge back to the current level instead of cascading deeper.
- The goal is not to finish the full end-to-end instruction lifecycle in one module. Stop at the current module boundary and hand off when the next state transition belongs to another module.

Diagram rules:
- Focus on register-transfer / state-update granularity, not stage-only summaries.
- Describe only the instruction-relevant main path in the current module.
- Every lifecycle step must name the concrete register, latch, valid bit, state field, handshake, or gating condition that it reads, updates, or depends on.
- Port names are boundary handoff points only, not computation nodes.
- Summarize child functionality from the parent view. Do not invent child-internal detail if the parent-level evidence is sufficient.
- End the current module description at the last instruction-relevant state action in this module. If the next step is outside this module, emit `boundary_handoffs` and stop.

Output rules:
1. Output exactly one `json` code block and no other text.
2. The JSON must include at least:
   - `module`
   - `instance`
   - `entry_ports`
   - `boundary_handoffs`
   - `lifecycle_context`
   - `instruction_state`
   - `confidence` (`high|medium|low`)
   - `unknown`
3. `instruction_state` should be structured enough for downstream rendering. Prefer either a concise list of lifecycle steps or a compact textual summary with explicit state/update semantics.
"""

PASS3_3_2_ORCHESTRATE_PROMPT = """

## Instruction Datasheet (`{instruction}`)
{instruction_datasheet}

## Continuation State
{draw_state_json}

## Current Module Preview
{module_preview}

## Current Module Topology
{current_module_topology}

## Direct Child Preview List
{child_preview_list}

## Required Output
- Return one `json` code block only.
- The JSON must contain at least `module`, `instance`, `entry_ports`, `boundary_handoffs`, `lifecycle_context`, and `instruction_state`.
- Map `module_preview` plus the embedded current-module topology evidence into register/state-update semantics that Python can render later.
- Prefer explicit state-update wording over pipeline-stage summaries.
- Do not draw Mermaid or any other diagram syntax in this stage.
- Do not continue beyond the current module just to make the lifecycle feel complete. Use `boundary_handoffs` for the next module instead.
- Start from the current continuation entry of this module. If `Continuation State.upstream_context.entry_ports` exists, use it first; otherwise use `Continuation State.upstream_handoff` only as a weak fallback hint.
- Treat continuation handoff/context as hints. Call `drawChild` only if the next direct child is necessary and non-substitutable.
- `drawChild` may return a cached child orchestration result when that child was already drawn earlier in this run. Reuse it directly instead of asking for the same child again.
- `entry_ports` must list only the instruction-relevant inputs that actually continue the current trace through this module. Prefer exact ports named in `Continuation State.upstream_context.entry_ports` when provided.
- Every `boundary_handoffs` item must represent exactly one current-module output port. Do not merge multiple output ports into one item.
- For each `boundary_handoffs` item, include:
  - `source_block`
  - `source_state`
  - `egress_port`
  - `value_kind`
  - `semantic`
  - `behavior`
- `egress_port` must be a real current-module output port, not a child port and not an internal wire.
- Describe the output-port behavior from the current module's view: what value/state leaves this module, and under what update/hold/clear condition.
- Do not guess target modules or target ports in `boundary_handoffs`; Python resolves destinations after generation.
- Do not expand infrastructure/detail modules by default (clock gating, reset, memory macros, DFT/scan, etc.). Keep only lifecycle-critical downstream handoffs in `boundary_handoffs`. If the trace ends here, return an empty array and explain closure in `unknown`.

"""

# Backward-compatible aliases while the rest of the pipeline is migrated.
PASS3_3_2_DRAW_SYSTEM = PASS3_3_2_ORCHESTRATE_SYSTEM
PASS3_3_2_DRAW_PROMPT = PASS3_3_2_ORCHESTRATE_PROMPT
