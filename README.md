# FlowPilot

`FlowPilot` 是一个偏执行型的企业 AI 工作流自动化项目，用来补足“企业级 RAG 知识库搜索”之外的能力面。它不以检索为中心，而是以任务拆解、多 Agent 协作、工具调用、人工接管、Prompt 实验和流程可观测为核心。

## 当前能力

- `Planner / Operator / Analyst / Content / Reviewer` 五类 Agent 协作
- 销售分析与跟进计划
- 营销内容工厂
- 客服工单智能分流
- 会议纪要转执行系统
- 真实模型接入：默认 `qwen3-max`
- 真实数据库兼容：`SQLite / MySQL`
- 缓存兼容：`Redis`
- 多页面工作台：仪表盘、运行历史、审核中心、详情页、模型与 Prompt 对比页
- 图形化执行时间线
- 登录态与角色权限：`viewer / operator / reviewer / admin`
- 数据库用户表与密码哈希认证
- AI 运行指标：`prompt / model / token / latency`
- Prompt 版本管理：可切换 Prompt 方案并沉淀到运行记录
- 多模型 / Prompt 对比页：按组合聚合运行效果

## 技术栈

- `Python 3.11`
- `FastAPI`
- `LangGraph`
- `SQLAlchemy`
- `SQLite / MySQL`
- `Redis`
- `OpenAI Python SDK`
- DashScope OpenAI 兼容接口

## 环境变量

样例见 [.env.example](./.env.example)。

关键变量：

- `DASHSCOPE_API_KEY`
- `OPENAI_API_KEY`
- `OPEN_AI_KEY`
- `MODEL_NAME`
- `MODEL_BASE_URL`
- `DATABASE_URL`
- `REDIS_URL`
- `FLOWPILOT_SECRET_KEY`
- `FLOWPILOT_SESSION_COOKIE`
- `FLOWPILOT_USERS_JSON`

其中：

- `FLOWPILOT_USERS_JSON` 用于初始化数据库用户
- 用户密码会以 `PBKDF2-SHA256` 哈希形式写入数据库
- 默认模型端点为 `https://dashscope.aliyuncs.com/compatible-mode/v1`

## 本地启动

```bash
python -m uvicorn app.main:app --reload
```

启动后打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

默认演示账号：

- `admin / admin123`
- `reviewer / reviewer123`
- `operator / operator123`
- `viewer / viewer123`

首次启动时，这些账号会自动初始化到 `user_accounts` 表。

## 启动 MySQL 和 Redis

如果你想切到更接近正式环境的方式，可以先启动基础设施：

```bash
docker compose -f docker-compose.infra.yml up -d
```

然后配置环境变量：

```env
DATABASE_URL=mysql+pymysql://flowpilot:flowpilot@127.0.0.1:3306/flowpilot
REDIS_URL=redis://127.0.0.1:6379/0
```

## 测试

```bash
python -m pytest -q
```

## API

- `GET /`
- `GET /login`
- `POST /login`
- `POST /logout`
- `GET /dashboard`
- `GET /runs`
- `GET /compare`
- `GET /reviews`
- `GET /runs/{id}`
- `GET /api/health`
- `GET /api/session`
- `GET /api/workflows/templates`
- `GET /api/workflows`
- `GET /api/workflows/review-queue`
- `GET /api/workflows/graph`
- `GET /api/workflows/{id}`
- `POST /api/workflows/run`
- `POST /api/workflows/{id}/review`
- `GET /api/experiments/catalog`
- `GET /api/experiments/compare`

## 项目说明

当前版本重点展示这些能力：

- 多工作流、多 Agent 的执行编排
- 真实模型增强业务分析、内容生成和审核判断
- MySQL + Redis 兼容的持久化方案
- 多页面工作台和图形化执行时间线
- 基于角色的登录态与审核权限
- AI 调用可观测：Prompt、模型、Token、耗时
- Prompt 方案管理与多模型 / Prompt 组合对比

## 文档

- 总体设计：[docs/多Agent工作-项目设计.md](./docs/多Agent工作-项目设计.md)
- 第一步升级说明：[docs/第1步-MySQL与Redis升级.md](./docs/第1步-MySQL与Redis升级.md)
- 第二步升级说明：[docs/第2步-LangGraph状态流升级.md](./docs/第2步-LangGraph状态流升级.md)
- 第三步升级说明：[docs/第3步-前端工作台升级.md](./docs/第3步-前端工作台升级.md)
- 第四步升级说明：[docs/第4步-多页面与权限升级.md](./docs/第4步-多页面与权限升级.md)
- 第五步升级说明：[docs/第5步-数据库认证升级.md](./docs/第5步-数据库认证升级.md)
- 第六步升级说明：[docs/第6步-AI运行指标升级.md](./docs/第6步-AI运行指标升级.md)
- 第七步升级说明：[docs/第7步-Prompt版本管理与对比页.md](./docs/第7步-Prompt版本管理与对比页.md)
