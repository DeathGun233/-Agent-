# 第 7 步：Prompt 版本管理与多模型 / Prompt 对比页

这一轮升级的目标，是把项目从“能调模型”推进到“能做 AI 实验和效果对比”。

## 这一步补了什么

### 1. Prompt 版本管理

新增 Prompt 方案目录，当前内置三套方案：

- `balanced-v1`
- `ops-deep-v1`
- `exec-brief-v2`

每套方案都包含：

- 名称
- 版本号
- 描述
- `Analyst / Content / Reviewer` 三类 Agent 的附加指令

这样做的意义是：

- Prompt 不再只是散落在代码里的字符串
- 每次运行都能知道用了哪套 Prompt 方案
- 后续可以继续加新版本做 A/B 对比

### 2. 多模型选择

仪表盘现在支持在发起任务时切换模型，当前内置：

- `qwen3-max`
- `qwen-plus`
- `qwen-turbo`

工作流发起后，会把本次模型配置沉淀到运行结果里，并在每次 LLM 调用日志中带上模型名。

### 3. 执行配置沉淀

每次运行都会把以下信息写入结果：

- `execution_profile.model_name`
- `execution_profile.model_label`
- `execution_profile.prompt_profile.profile_id`
- `execution_profile.prompt_profile.name`
- `execution_profile.prompt_profile.version`

同时每条 LLM 调用日志也会记录：

- `prompt_profile_id`
- `prompt_profile_name`
- `prompt_profile_version`

这样做之后：

- 单次任务详情页能看到本次模型和 Prompt 方案
- 多次任务聚合时也能按组合做统计

### 4. 对比页

新增页面：

- `/compare`

页面会按：

- 工作流类型
- 模型
- Prompt 方案

三个维度做组合聚合，并展示：

- 运行次数
- 平均评分
- 平均 Tokens
- 平均耗时
- 人工接管率
- Fallback 率
- 最近一次运行时间

这让项目更像一个真正的 AI 应用工程平台，而不是单次调用 demo。

## 这一步为什么重要

如果项目只展示：

- 调了哪个模型
- 跑出了什么结果

那更像普通集成。

这一步补完以后，你可以更自然地讲：

- 我做了 Prompt 方案管理
- 我支持不同模型的任务级切换
- 我把运行指标和 Prompt 版本沉淀到任务记录
- 我能按模型 / Prompt 组合对结果做对比分析

这非常贴近 AI 应用开发岗位的预期。

## 主要改动文件

- `app/prompt_catalog.py`
- `app/models.py`
- `app/llm.py`
- `app/services.py`
- `app/main.py`
- `app/templates/base.html`
- `app/templates/dashboard.html`
- `app/templates/runs.html`
- `app/templates/run_detail.html`
- `app/templates/compare.html`
- `tests/test_workflows.py`

## 验证结果

```bash
python -m pytest -q
```

结果：

- `13 passed`

## 使用方式

1. 在仪表盘选择一个工作流
2. 选择模型
3. 选择 Prompt 方案
4. 启动任务
5. 去详情页看单次运行的 Prompt / 模型 / Token / 耗时
6. 去对比页看不同组合的聚合效果

## 下一步建议

如果继续沿着 AI 应用开发方向增强，最自然的下一步是：

- Prompt 历史版本新增 / 编辑能力
- 模型路由策略
- 自动评测集与基线对比
- 失败重试和结构化输出校验
