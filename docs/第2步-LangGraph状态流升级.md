# 第 2 步：LangGraph 状态流升级

## 这一步做了什么

这次升级把原来“在 `WorkflowEngine` 里按顺序依次调用五个 Agent”的方式，改成了真正的 `LangGraph StateGraph` 编排。

当前状态流节点如下：

- `planner`
- `operator`
- `analyst`
- `content`
- `reviewer`
- `complete_run`
- `handoff_run`

并且已经有明确的分支逻辑：

- 审核通过时，流转到 `complete_run`
- 需要人工介入时，流转到 `handoff_run`

## 为什么要做这一步

前一个版本虽然已经有多 Agent 执行链路，但本质上仍然是“手写顺序调用”。  
对外可以讲成多 Agent，但从工程结构上看，还不够像一个可扩展的工作流系统。

引入 `LangGraph` 后，项目会更贴近这些岗位要求：

- `Agentic Workflow`
- `Workflow 编排`
- `状态流`
- `多 Agent 协作`
- `可扩展执行图`

## 当前实现方式

核心文件：

- [app/services.py](D:/explore_MANG/app/services.py)

关键改动：

### 1. 定义工作流状态

新增 `WorkflowState`，在图中传递这些字段：

- `request`
- `run`
- `raw_result`
- `analysis`
- `deliverables`
- `review`

### 2. 用 StateGraph 建图

工作流入口为：

- `START -> planner`

顺序主链路为：

- `planner -> operator -> analyst -> content -> reviewer`

审核后分叉为：

- `reviewer -> complete_run`
- `reviewer -> handoff_run`

最后：

- `complete_run -> END`
- `handoff_run -> END`

### 3. 保留原有业务能力

虽然底层编排换成了 `LangGraph`，但这些业务能力没有丢：

- 真实模型分析
- 工具调用
- 日志记录
- 持久化保存
- 人工接管判断

## 这一步带来的价值

### 1. 从“顺序调用”升级到“图式编排”

这意味着后续如果要增加复杂分支，就不再需要把所有逻辑硬写在一个大函数里。

例如以后可以很自然地加：

- 失败重试节点
- 二次审核节点
- 多轮工具调用节点
- 人工确认回流节点

### 2. 更容易面试表达

现在你可以明确说：

- 项目底层使用 `LangGraph StateGraph` 管理 Agent 执行流程
- Agent 审核后会根据结果自动路由到完成节点或人工接管节点
- 工作流图而不是普通函数链，便于扩展复杂流程

### 3. 给第三步前端增强打基础

后面做运行轨迹页时，可以直接把图节点和状态显示出来，更容易做“流程可视化”。

## 新增接口

为了方便展示和调试，这一步新增了：

- `GET /api/workflows/graph`

返回内容包括：

- `runtime`
- `entrypoint`
- `nodes`
- `edges`

这样前端或调试工具可以直接拿到图结构。

## 验证方式

这一步新增的验证重点包括：

- `GET /api/workflows/graph` 能返回 `langgraph`
- 销售流程可以自动走到完成节点
- 工单故障流程可以走到人工接管节点
- 日志中会出现 `LangGraph 状态流` 相关记录

## 当前边界

这一步还没有做：

- LangGraph 持久化检查点
- 图级别的可视化前端
- 失败重试分支
- 人工接管后的恢复执行

这些可以放到后面继续增强。
