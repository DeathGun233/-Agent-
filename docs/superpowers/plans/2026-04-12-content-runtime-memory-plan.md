# Content Runtime Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing lightweight runtime memory loop so `ContentAgent` reads relevant history and feedback during workflow execution.

**Architecture:** Keep the current PR #7 lightweight design. Add a `content_memory()` query in `AgentMemoryService`, pass a derived `content_context` into `ContentAgent.generate()`, and persist that context in `run.result` alongside the existing analyst and reviewer context objects.

**Tech Stack:** Python, FastAPI, Pydantic, pytest, LangGraph workflow engine

---

### Task 1: Add the regression test first

**Files:**
- Modify: `tests/test_workflows.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write the failing test**

```python
def test_runtime_memory_context_is_recorded_for_content_agent() -> None:
    first = create_support_run()
    login_as("reviewer", "reviewer123")
    reviewed = client.post(
        f"/api/workflows/{first['id']}/review",
        json={"approve": True, "comment": "保留责任人、风险说明和升级判断。"},
    )
    assert reviewed.status_code == 200

    second = create_support_run()

    content_context = second["result"]["content_context"]

    assert content_context["memory_hits"] >= 1
    assert len(content_context["memory"]["recent_runs"]) >= 1
    assert len(content_context["memory"]["feedback_samples"]) >= 1
    assert any("历史记忆" in log["message"] for log in second["logs"] if log["agent"] == "ContentAgent")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflows.py -k content_agent -q`
Expected: FAIL because `content_context` / ContentAgent memory wiring does not exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/test_workflows.py
git commit -m "test: cover content runtime memory context"
```

### Task 2: Wire runtime memory into ContentAgent

**Files:**
- Modify: `app/services.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write minimal implementation**

```python
class WorkflowState(TypedDict, total=False):
    content_context: dict[str, Any]


class AgentMemoryService:
    def content_memory(...):
        ...


class ContentAgent:
    def generate(..., memory_context: dict[str, Any], ...) -> tuple[dict[str, Any], Any]:
        user_prompt = (
            ...
            f"历史记忆 JSON：\n{json.dumps(memory_context, ensure_ascii=False)}"
        )


def _content_step(self, state: WorkflowState) -> WorkflowState:
    content_memory = self.memory_service.content_memory(...)
    content_context = {
        "memory": content_memory,
        "memory_hits": self.memory_service.memory_hits(content_memory),
    }
    deliverables, llm_call = self.content_agent.generate(..., memory_context=content_context, ...)
    state["content_context"] = content_context
```

- [ ] **Step 2: Persist the new context in workflow results**

```python
run.result = {
    ...
    "content_context": state.get("content_context", {}),
    ...
}
```

- [ ] **Step 3: Run test to verify it passes**

Run: `python -m pytest tests/test_workflows.py -k content_agent -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/services.py tests/test_workflows.py
git commit -m "feat: add content runtime memory context"
```

### Task 3: Run regression verification

**Files:**
- Modify: none
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Run focused runtime-memory tests**

Run: `python -m pytest tests/test_workflows.py -k runtime_memory -q`
Expected: PASS

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest -q`
Expected: PASS with no failures
