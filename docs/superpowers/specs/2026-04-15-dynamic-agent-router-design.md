# Dynamic Agent Router Design

## Goal

Make the workflow behave like a state-driven multi-agent system instead of a fixed linear chain. Phase 1 keeps the current agent set and tool layer, but inserts a router that chooses the next agent from current state and can send execution back to Planner once when the plan needs revision.

## Scope

- Preserve existing agents: `PlannerAgent`, `OperatorAgent`, `AnalystAgent`, `ContentAgent`, `ReviewerAgent`.
- Add a `RouterAgent` that decides the next node after each major step.
- Replace fixed linear graph edges with router-mediated conditional edges.
- Track route decisions in workflow state and final `run.result`.
- Allow at most one `planner` re-entry to avoid infinite loops.
- Keep `ToolCenter` dispatch unchanged; issue #2 can handle model-driven tool calling later.

## Routing Rules

The router is deterministic in this phase so tests and disabled-LLM mode stay stable:

- After `planner`, route to `operator`.
- After `operator`, route to `analyst` when raw data exists.
- After `analyst`, route to `content` when analysis exists.
- After `content`, route to `planner` once if deliverables are empty or missing; otherwise route to `reviewer`.
- After `reviewer`, route to `handoff_run` when review status is `waiting_human`; otherwise route to `complete_run`.

Each decision records `from_node`, `next_node`, `reason`, and `replan_count`.

## Non-Goals

- No dynamically registered agents.
- No LLM-driven router in this phase.
- No tool calling protocol refactor.
- No UI redesign beyond graph metadata already exposed by `/api/workflows/graph`.

## Test Strategy

- Assert graph metadata exposes the router node and conditional route edges.
- Unit-test the router replan decision when content deliverables are missing.
- Add workflow-level coverage that route decisions are persisted in `run.result`.

