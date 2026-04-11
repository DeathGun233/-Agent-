# 观测层口径修正设计

## 目标

把 FlowPilot 中“真实模型调用”和“fallback 降级执行”彻底拆开，避免 run 详情页、成本页、对比页和导出报告继续把 fallback 当成真实模型调用统计。

## 当前问题

1. `used_fallback=True` 的 `LLMCall` 仍被计入“模型调用次数”。
2. `Tokens / 成本 / 时延` 已经改成只看真实调用，但页面标题和辅助文案没有同步，容易误导。
3. 成本页与对比页仍直接聚合所有 `LLMCall`，会把 fallback 混入真实模型观测。
4. 导出报告仍沿用旧口径，没有明确展示 fallback 次数。

## 设计原则

- 不改动现有数据库结构，继续复用 `LLMCall.used_fallback`。
- 所有面向用户的“模型调用 / Tokens / 成本 / 时延”默认只统计真实模型调用。
- fallback 单独统计为 `fallback_requests`，用于表达“降级执行发生过几次”。
- 所有页面和报表统一复用同一套聚合口径，避免各算各的。

## 实施范围

- `app/main.py`
  - 统一 run 详情和对比页的聚合口径。
- `app/services.py`
  - 统一成本分析与实验分析的聚合口径。
- `app/reporting.py`
  - 导出报告补充 fallback 次数。
- `app/templates/run_detail.html`
  - 明确展示真实模型调用次数与 fallback 次数。
- `app/templates/costs.html`
  - 成本页补充 fallback 次数，避免模型成本分布误解。
- `tests/test_workflows.py`
  - 补充 run 详情、成本统计和对比统计的回归测试。

## 非目标

- 这一轮不做 fallback 原因分类看板。
- 这一轮不改 `LLMService` 的调用协议。
- 这一轮不追查为何某些 run 会进入 `llm_disabled`，那是下一阶段的运行层修正。
