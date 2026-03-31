# 第 8 步：Prompt 历史版本、多模型路由与自动评测升级

## 本轮目标

围绕 AI 应用开发能力继续强化项目，而不是偏传统后台能力。重点补齐三条线：

1. Prompt 历史版本新增与编辑
2. 多模型路由策略
3. 自动评测集与基线对比

## 已完成能力

### 1. Prompt 历史版本管理

- 新增 `prompt_profiles` 持久化表
- 支持内置 Prompt 自动种子化
- 支持自定义 Prompt 新增
- 支持自定义 Prompt 编辑
- 支持基于旧版本 Prompt 继续派生新版本
- 前端新增 `Prompt 管理` 页面

### 2. 多模型路由策略

- 扩展 `ExecutionProfile`，支持：
  - 主模型
  - Prompt 引用
  - 路由策略
  - 各 Agent 路由结果
- 内置多种路由策略：
  - `single-model-v1`
  - `balanced-router-v1`
  - `speed-router-v1`
  - `strict-review-v1`
- `Analyst / Content / Reviewer` 三类 Agent 会按路由策略选择模型
- 每次 LLM 调用都会记录：
  - `route_target`
  - `routing_policy_id`
  - `routing_policy_name`

### 3. 自动评测与基线对比

- 新增 `evaluation_runs` 持久化表
- 预置 `ops-regression-v1` 评测集
- 评测覆盖：
  - 销售分析
  - 营销内容
  - 客服分流
  - 会议纪要
- 支持候选方案与基线方案对比
- 输出指标包括：
  - 平均得分
  - 得分差值
  - 平均 Tokens
  - 平均耗时
  - 人工接管率

## 页面与接口

### 新页面

- `/prompts`
  Prompt 历史版本管理页
- `/evaluations`
  自动评测与基线对比页

### 主要接口

- `GET /api/prompts`
- `POST /api/prompts`
- `PUT /api/prompts/{profile_id}`
- `GET /api/evaluations`
- `GET /api/evaluations/{evaluation_id}`
- `GET /api/experiments/catalog`

## 工程改动

- 数据模型扩展：
  - `PromptProfile`
  - `RoutingPolicyRef`
  - `EvaluationRun`
- 仓储层新增：
  - Prompt Profile 存取
  - Evaluation 存取
- 服务层新增：
  - `PromptProfileService`
  - `EvaluationService`
- 工作流引擎支持：
  - Prompt 选择
  - 路由策略选择
  - 评测时无持久化运行

## 测试结果

- 已通过 `python -m pytest -q`
- 结果：`15 passed`

## 对 AI 应用开发岗位的价值

这一步让项目从“能运行的多 Agent 工作流”进一步升级成“可做实验、可管 Prompt、可做基线评测的 AI 应用工程平台”，更贴近以下岗位能力：

- Prompt Engineering
- 多模型应用开发
- Agent 工作流编排
- AI 可观测性
- 模型效果评测与回归验证
