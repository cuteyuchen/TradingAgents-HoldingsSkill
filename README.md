# TradingAgents-HoldingsSkill V2

面向 A 股与 ETF 的自托管持仓分析系统。用户登录后上传券商持仓截图，系统使用独立识图模型解析持仓，经人工确认后获取行情、技术、资金流与公告数据，结合历史分析上下文执行组合级多 Agent 分析，并保存截图、持仓快照、结构化结果与 Markdown 报告。

> 本项目仅用于研究辅助与技术演示，不构成投资建议，不连接券商自动下单，也不承诺任何收益。

## 核心能力

- 用户注册、密码登录、JWT Access Token 与可轮换 Refresh Token。
- 多用户数据隔离。
- 识图模型、快速分析模型、深度裁决模型独立配置。
- 支持 OpenAI、OpenAI Compatible、DeepSeek、Qwen、GLM、MiniMax、Anthropic、Gemini、OpenRouter、Ollama。
- API Key、钉钉 Webhook、企微 Webhook 和加签 Secret 加密保存。
- 上传 PNG、JPEG、WEBP、GIF 持仓截图。
- AI 识图后可人工修正并确认不可变持仓快照。
- 腾讯实时行情、东财 K 线、均线、量比、资金流和近期公告集中采集。
- 数据质量门控：关键行情缺失时只输出观察结果，不生成具体交易动作。
- 快速分析与深度分析。
- 深度模式包含证据包、多空辩论、研究裁决和组合经理最终结论。
- 最近历史建议、持仓变化与同向/反向建议一致性检查。
- 任务状态、取消、失败重试、SSE 进度流。
- 报告历史、结构化证据、原始截图与前后两次结果比较。
- 按交易日和用户时区自动分析。
- 持仓快照过期阻断、任务幂等、连续失败自动停用。
- 钉钉和企业微信群机器人通知。
- 保留原 `/api/v1/archives`，兼容现有 Skill 上传归档。

## 上游来源

当前 Skill 的分析规则参考并选择性吸收以下项目：

- `TauricResearch/TradingAgents`：多 Agent 图结构、分析师分工、研究辩论、Trader、Risk、Portfolio Manager。
- `simonlin1212/TradingAgents-astock`：A 股数据源、交易规则、资金面、板块与东财限流经验。
- `KylinMountain/TradingAgents-AShare`：Claim 驱动辩论、集中 DataCollector、Web 产品、定时任务和模型配置思路。

本仓库是独立产品主仓库，不计划与任一上游做整仓同步。

## 系统架构

```text
┌──────────────────────────────────────────────────────────┐
│ Vue 3 + TypeScript + Naive UI                             │
│ 登录 / 总览 / 上传确认 / 任务进度 / 报告 / 设置           │
└─────────────────────────┬────────────────────────────────┘
                          │ REST + SSE
┌─────────────────────────▼────────────────────────────────┐
│ FastAPI                                                   │
│ Auth / Models / Portfolio / Analysis / Schedule / Notify │
└──────────────┬───────────────────────────────┬───────────┘
               │                               │
       SQLite + Alembic                 Embedded Scheduler
               │                               │
┌──────────────▼───────────────────────────────▼───────────┐
│ 分析执行器                                                │
│ Vision → 持仓校验 → 市场快照 → 多 Agent → 报告 → 通知    │
└──────────────────────────────────────────────────────────┘
```

当前默认是适合个人自托管的模块化单体：FastAPI 进程内执行分析任务并运行 APScheduler。任务和接口已经抽象为独立模型，后续可迁移到 PostgreSQL、Redis 和独立 Worker。

## 核心数据链路

```text
HoldingUpload
  ↓ AI 解析 / 人工修正
PortfolioSnapshot（用户确认的唯一当前持仓）
  ↓
AnalysisJob（可追踪、取消、重试、定时触发）
  ↓
AnalysisRun（不可变结构化结果 + Markdown）
```

历史报告只能作为上下文，不会覆盖本次确认持仓。

## 快速开始

### 1. 准备环境变量

```bash
cp .env.example .env
```

至少修改：

```dotenv
ADVISOR_TOKEN=adv_replace_me
APP_SECRET_KEY=replace_with_a_stable_random_secret_at_least_32_bytes
PUBLIC_APP_URL=http://localhost:8080
```

`APP_SECRET_KEY` 用于 JWT 签名、模型 API Key 和通知凭据加密。保存凭据后不要更改，否则旧数据无法解密。

### 2. Docker 启动

```bash
docker compose up -d --build
```

访问：

- 前端：`http://localhost:8080`
- 后端：`http://localhost:8000`
- Swagger：`http://localhost:8000/docs`

首次打开前端后创建账户。生产部署完成首个账户创建后，建议设置：

```dotenv
ALLOW_REGISTRATION=false
```

### 3. 系统内配置

1. 在“系统设置 → 模型配置”新增模型供应商。
2. 配置至少一个默认 `vision` 模型。
3. 配置至少一个默认 `analysis` 或 `deep_analysis` 模型。
4. 测试模型连接。
5. 在总览中新建持仓组合。
6. 上传今日持仓截图并核对识图结果。
7. 确认快照后执行快速或深度分析。

## 模型用途

| 用途 | 说明 |
|---|---|
| `vision` | 持仓截图解析，模型必须支持图片输入 |
| `analysis` | 证据整理、分析师、多空辩论等高频步骤 |
| `deep_analysis` | 最终组合裁决；未配置时回退到 `analysis` |

OpenAI Compatible 可接入 vLLM、LM Studio、llama.cpp、自定义中转服务或其他兼容 `/chat/completions` 的接口。本地 Ollama 默认地址为 `http://host.docker.internal:11434/v1`。

## 持仓数量语义

- `qty`：总持仓。
- `available_qty`：当前可卖/可交易数量。
- `unavailable_qty = qty - available_qty`：可能来自挂单、冻结或 T+1，不能推断为已经减仓。
- 减仓或卖出建议的最大数量由 `available_qty` 决定。
- 盈亏率使用小数，例如 `-27.73%` 保存为 `-0.2773`。
- “新标准券”“标准券”和国债逆回购不作为股票/ETF 持仓。
- 同时存在总资产和总市值时，修正后未使用资金为 `total_assets - total_market_value`。

## 自动分析

在“系统设置 → 自动分析”配置：组合、时区、执行时间、分析模式、持仓过期天数、是否通知和连续失败阈值。

执行前系统会：

1. 检查是否为 A 股交易日。
2. 获取最近一次已确认持仓。
3. 校验持仓快照是否过期。
4. 使用幂等键避免相同计划重复创建任务。
5. 连续失败达到阈值后自动停用计划。

个人部署推荐开盘后首次分析时间为 `09:35`，也可以增加 `10:00`、`12:00`、`14:30` 等计划。

## 钉钉与企业微信

支持钉钉自定义机器人、钉钉加签 Secret、企业微信群机器人、测试发送，以及分析完成后的摘要和报告链接。服务端只允许官方 Webhook 域名，通知失败不会改变分析报告的成功状态。

## API 概览

### V2

```text
POST   /api/v2/auth/register
POST   /api/v2/auth/login
POST   /api/v2/auth/refresh
GET    /api/v2/auth/me

/api/v2/model-settings/providers
/api/v2/model-settings/profiles
POST   /api/v2/model-settings/profiles/{id}/test

/api/v2/portfolios
POST   /api/v2/portfolios/{id}/uploads
PATCH  /api/v2/uploads/{id}/parsed-holdings
POST   /api/v2/uploads/{id}/confirm
/api/v2/snapshots/{id}

POST   /api/v2/analysis/jobs
GET    /api/v2/analysis/jobs/{id}
GET    /api/v2/analysis/jobs/{id}/events
POST   /api/v2/analysis/jobs/{id}/cancel
POST   /api/v2/analysis/jobs/{id}/retry
/api/v2/analysis/runs
/api/v2/analysis/runs/{id}
/api/v2/analysis/runs/{id}/comparison

/api/v2/schedules
POST   /api/v2/schedules/{id}/run-now
/api/v2/notifications
POST   /api/v2/notifications/{id}/test
```

### V1 兼容

现有 Skill 仍可使用：

```dotenv
ADVISOR_API_URL=http://localhost:8000/api/v1
ADVISOR_TOKEN=adv_xxx
```

兼容接口：

```text
GET    /api/v1/auth/verify
POST   /api/v1/archives
GET    /api/v1/archives
GET    /api/v1/archives/context
GET    /api/v1/archives/{id}
DELETE /api/v1/archives/{id}
```

## 本地开发与测试

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
pytest tests -q
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run typecheck
npm run build
npm run dev
```

```bash
docker compose build
docker compose up -d
```

GitHub Actions 会执行 Alembic 空库升级与重复升级、全部后端测试、前端 TypeScript 类型检查与构建，以及 Docker Compose 镜像构建。

## Codex 验证清单

自动化测试使用模拟模型和模拟市场快照验证核心约束。交付前建议 Codex 在独立环境补充以下真实链路测试：

1. 使用实际视觉模型解析至少三种不同券商截图，核对代码、总持仓、可用数量、成本、盈亏金额与盈亏率。
2. 分别验证项目实际准备使用的 OpenAI Compatible、Anthropic 或 Gemini 模型接口。
3. 在交易时段和收盘后验证腾讯行情、东财 K 线、资金流与公告字段。
4. 验证行情不可用时是否降级为 `watch_only`，且不产生具体买卖数量。
5. 构造 `available_qty=0` 和卖出数量超过可用数量的结果，确认服务端会阻断或修正。
6. 连续上传不同持仓，检查历史上下文、已执行减仓识别和反向建议说明。
7. 使用真实钉钉与企业微信机器人验证普通 Webhook、钉钉加签和报告链接。
8. 验证定时任务在交易日、非交易日、过期持仓和连续失败时的行为。
9. 使用已有 V1 SQLite 数据库升级，核对旧归档数量、截图文件和兼容接口。
10. 重启容器后确认用户、模型密钥、Webhook、持仓快照和报告均可恢复。

## 目录结构

```text
backend/
├── alembic/
├── app/
│   ├── routers/                 # V1 归档 + V2 业务 API
│   ├── services/
│   │   ├── model_client.py      # LLM/VLM 供应商适配
│   │   ├── holdings_service.py  # 持仓解析与校验
│   │   ├── market_data.py       # 集中市场证据快照
│   │   ├── analysis_engine.py   # 分析任务编排
│   │   ├── skill_runtime.py     # 从仓库 Skill 加载版本化规则
│   │   ├── scheduler.py         # 自动分析
│   │   └── notifications.py     # 钉钉/企微
│   ├── v2_models.py
│   └── v2_schemas.py
└── tests/

frontend/src/
├── api/
├── views/
│   ├── LoginView.vue
│   ├── DashboardView.vue
│   ├── UploadView.vue
│   ├── ReportsView.vue
│   └── SettingsView.vue
├── router.ts
└── App.vue

skill/tradingagents-holdings-advisor/
├── SKILL.md
├── runtime.json               # 后端实际加载的规则与版本
├── references/
└── scripts/
```

## 当前边界

- 系统不自动下单。
- 免费公共数据源可能出现限流、临时不可用或字段变化；报告会保存缺失和降级信息。
- 当前默认使用 SQLite 和进程内任务，适合个人或小规模自托管。
- 多实例、高并发生产部署应将数据库升级到 PostgreSQL，并将任务执行迁移到独立队列 Worker。
- 公共行情、资金流和公告只作为研究证据，交易前必须再次核对券商实时数据。
