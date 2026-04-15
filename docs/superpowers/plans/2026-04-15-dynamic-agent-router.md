# Dynamic Agent Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the fixed linear LangGraph workflow into a state-driven router flow with one guarded replan loop.

**Architecture:** Add a deterministic `RouterAgent` in `app/services.py` and route all major workflow nodes through it. Store route decisions in `WorkflowState` and final `run.result`, while preserving existing agent APIs and ToolCenter behavior.

**Tech Stack:** Python, FastAPI, LangGraph, pytest

---

### Task 1: Lock graph metadata expectation

**Files:**
- Modify: `tests/test_workflows.py`
- Modify: `app/services.py`

- [ ] **Step 1: Write failing graph metadata assertions**

```python
def test_graph_endpoint_exposes_langgraph_shape() -> None:
    login_as("viewer", "viewer123")
    response = client.get("/api/workflows/graph")
    body = response.json()
    assert response.status_code == 200
    assert body["runtime"] == "langgraph"
    assert "router" in body["nodes"]
    assert any(edge["from"] == "planner" and edge["to"] == "router" for edge in body["edges"])
    assert any(edge["from"] == "router" and edge["to"] == "planner" for edge in body["edges"])
```

- [ ] **Step 2: Run failing test**

Run: `python -m pytest tests/test_workflows.py -k graph_endpoint -q`
Expected: FAIL because `router` is not yet exposed.

### Task 2: Lock router behavior and persisted route trail

**Files:**
- Modify: `tests/test_workflows.py`
- Modify: `app/services.py`

- [ ] **Step 1: Write failing tests**

```python
def test_router_requests_one_replan_when_content_is_missing() -> None:
    decision = RouterAgent().decide(
        last_node="content",
        state={"deliverables": {}, "replan_count": 0},
    )
    assert decision["next_node"] == "planner"
    assert decision["replan_count"] == 1


def test_workflow_result_records_route_decisions() -> None:
    body = create_sales_run()
    route_decisions = body["result"]["route_decisions"]
    assert route_decisions[0]["from_node"] == "planner"
    assert route_decisions[0]["next_node"] == "operator"
    assert any(item["next_node"] == "reviewer" for item in route_decisions)
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_workflows.py -k "router_requests_one_replan or workflow_result_records_route_decisions" -q`
Expected: FAIL because `RouterAgent` and `route_decisions` do not exist yet.

### Task 3: Implement router and graph rewiring

**Files:**
- Modify: `app/services.py`

- [ ] **Step 1: Add state fields**

Add `last_node`, `route_decisions`, `next_node`, and `replan_count` to `WorkflowState`.

- [ ] **Step 2: Add `RouterAgent`**

Add a deterministic router class with `decide(last_node, state)` returning a dict containing `from_node`, `next_node`, `reason`, and `replan_count`.

- [ ] **Step 3: Rewire LangGraph**

Add `router` as a graph node. Each major agent node goes to `router`; `router` conditionally routes to the next agent, terminal completion, or the guarded planner re-entry.

- [ ] **Step 4: Persist route decisions**

Include `route_decisions` in final `run.result` after review, and keep route logs visible through `RouterAgent` logs.

### Task 4: Verify and publish

**Files:**
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Run targeted tests**

Run: `python -m pytest tests/test_workflows.py -k "graph_endpoint or router_requests_one_replan or workflow_result_records_route_decisions" -q`
Expected: PASS

- [ ] **Step 2: Run full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 3: Commit and PR**

Commit: `feat: add dynamic agent router`
Open a draft PR against `main` and comment on issue #4 in Chinese with scope and validation.

