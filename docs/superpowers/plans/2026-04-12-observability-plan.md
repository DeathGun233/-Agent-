# 观测层口径修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一区分真实模型调用和 fallback 降级执行，修正详情页、成本页、对比页与导出报告的展示口径。

**Architecture:** 保持现有 `LLMCall` 数据结构不变，以 `used_fallback` 作为单一分界线。所有聚合逻辑只统计真实调用，同时单独暴露 fallback 次数给页面和报表。页面层只消费统一聚合结果，不自行推导统计。

**Tech Stack:** Python, FastAPI, Jinja2, pytest

---

### Task 1: 修正统计回归测试

**Files:**
- Modify: `tests/test_workflows.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: 写失败测试**
- [ ] **Step 2: 运行失败测试确认当前口径有误**
- [ ] **Step 3: 修正最小实现**
- [ ] **Step 4: 回跑目标测试**
- [ ] **Step 5: 保持变更可提交**

### Task 2: 修正详情页和导出报告

**Files:**
- Modify: `app/main.py`
- Modify: `app/reporting.py`
- Modify: `app/templates/run_detail.html`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: 写详情页/报表失败测试**
- [ ] **Step 2: 运行失败测试**
- [ ] **Step 3: 修改 `llm_summary` 与展示模板**
- [ ] **Step 4: 回跑目标测试**
- [ ] **Step 5: 保持变更可提交**

### Task 3: 修正成本页和对比页

**Files:**
- Modify: `app/main.py`
- Modify: `app/services.py`
- Modify: `app/templates/costs.html`
- Possibly Modify: `app/templates/compare.html`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: 写成本/对比页失败测试**
- [ ] **Step 2: 运行失败测试**
- [ ] **Step 3: 修改聚合逻辑与页面文案**
- [ ] **Step 4: 回跑相关测试**
- [ ] **Step 5: 全量回归并提交**
