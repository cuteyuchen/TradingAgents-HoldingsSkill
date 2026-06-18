# TradingAgents-HoldingsSkill — 以 Skill 为核心的 A 股持仓交易助手

## GitHub 简介

**TradingAgents-HoldingsSkill** 是一个基于 TradingAgents 思路开发的 A 股/ETF 持仓交易 Skill，支持持仓截图解析、实时/最近交易行情采集、多智能体投研辩论、三方风控、今日操作建议、非持仓买入候选、Alpha 记忆与可选的前后端看板持久化。

## 项目定位

这个项目的核心不是前端或后端，而是 `skill/` 下的持仓交易分析能力。它把 TradingAgents 的多智能体投研流程改造成面向真实持仓截图的日内/隔夜操作流程：先解析持仓，再集中采集行情、资金流、板块、新闻和基本面数据，随后通过多空辩论、研究总监裁决、交易员方案、三方风控和组合经理最终决策，输出可执行的当前持仓操作建议与今日非持仓买入/轮动候选。

前后端只是增强层：后端负责保存每次 Skill 执行的完整 transcript、claims、持仓快照、候选和 Alpha；前端负责把这些历史决策用看板方式展示出来，方便复盘和远程访问。

## 基于的 TradingAgents 仓库

本项目参考并融合了以下三个 TradingAgents 系列仓库的设计思想：

- [`TauricResearch/TradingAgents`](https://github.com/TauricResearch/TradingAgents)：多智能体金融研究流程、分析师分工、研究/交易/风控协作框架。
- [`KylinMountain/TradingAgents-AShare`](https://github.com/KylinMountain/TradingAgents-AShare)：面向 A 股市场的 claim-driven 多空辩论、集中式数据收集、风险修正循环、双周期分析等设计。
- [`simonlin1212/TradingAgents-astock`](https://github.com/simonlin1212/TradingAgents-astock)：A 股数据源路由、7 类分析师、质量门控、信号数据层、沪深 300 Alpha 记忆等设计。

本项目不是上述仓库的直接 fork，而是在它们的架构思想基础上，把能力收敛到“每日真实持仓操作建议”这个 Skill 场景，并配套了可选的本地持久化和看板。

## 架构

```
┌──────────────┐   上传 run(Phase 6)    ┌───────────┐   查询    ┌────────────┐
│ TradingAgents│ ─────────────────────▶ │  backend  │ ◀──────▶ │  frontend  │
│ HoldingsSkill│ ◀────拉取历史(Phase 0) │ FastAPI   │           │  Vue3+TS   │
└──────────────┘                        │ + SQLite  │           │ + ECharts  │
                                        │ + AKShare │           └────────────┘
                                        └───────────┘
                                          自动抓沪深300
```

- **Skill**：持仓截图解析 + 公共行情/资金流/板块/新闻/基本面采集 + 多空辩论 + 三方风控 + 操作建议
- **后端**：Python + FastAPI + SQLite（单文件零运维）+ AKShare（沪深300）
- **前端**：Vue 3 + TypeScript + Vite + Naive UI + TailwindCSS + ECharts（亮/暗主题看板）
- **鉴权**：单一静态 Bearer Token（单用户场景）；前端登录密码即 `ADVISOR_TOKEN`
- **部署**：docker-compose 一键起（后端 + 前端 + 数据卷）

## 目录结构

```
TradingAgents-HoldingsSkill/
├── skill/
│   ├── SKILL.md                 # Skill 入口与执行流程
│   ├── data-sources.md          # 数据源路由、行情/资金流/新闻/基本面策略
│   ├── multi-agent-workflow.md  # 多智能体分析、质量门控、辩论和组合综合
│   ├── trading-rules.md         # A 股交易约束、仓位和触发规则
│   ├── debate-reporting.md      # 8 段 transcript 与 claim 输出格式
│   ├── buy-candidate-selection.md
│   ├── python-execution.md
│   ├── persistence.md           # Phase 0/Phase 6 后端集成契约
│   └── configuration.md
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 + lifespan + 定时抓沪深300
│   │   ├── config.py            # 环境变量配置
│   │   ├── database.py          # SQLAlchemy engine/session/建表
│   │   ├── auth.py              # 单 Token Bearer 鉴权
│   │   ├── models.py            # ORM（与 skill 输出契约 1:1）
│   │   ├── schemas.py           # Pydantic 上传/查询 schema
│   │   ├── routers/             # runs/portfolio/holdings/candidates/benchmark/watchlist/health
│   │   ├── services/            # alpha 计算 + 沪深300抓取 + 失败追踪
│   │   └── seed.py              # 生成 Token
│   ├── tests/test_smoke.py      # 冒烟测试（上传→alpha→查询全链路）
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/                 # API 客户端 + 类型定义（types.ts）
│   │   ├── components/          # DebateTimeline/ClaimTable/VerdictCard/QualityGateTable/AlphaChart
│   │   ├── views/               # RunList/RunDetail/Holdings/Candidates/Watchlist
│   │   └── App.vue main.ts
│   └── Dockerfile + nginx.conf
├── docker-compose.yml
└── .env.example
```

## 数据模型（与 skill 输出契约 1:1）

`runs`（根）/ `run_quality_gates` / `holdings_snapshots`(+alpha) / `holding_indicators` / `claims`(INV-/RISK-) / `research_verdicts` / `trader_proposals` / `risk_revisions`(pass/revise/reject+4类约束) / `pm_finals` / `candidates` / `benchmark_prices`(沪深300) / `watchlist` / `health_log`(失败计数)

## 快速开始

### 方式一：Docker 一键启动

```bash
# 1. 生成 Token
cd backend && python -m app.seed   # 打印 ADVISOR_TOKEN
cd ..

# 2. 写入 .env
cp .env.example .env
# 编辑 .env，填入上面的 token

# 3. 启动
docker compose up -d --build

# 后端 http://localhost:8000  (Swagger: /docs)
# 前端 http://localhost:8080
# 远程访问: http://<主机IP>:8080，页面输入 ADVISOR_TOKEN 登录
```

### 方式二：本地开发（前后端分离）

```bash
# 后端
cd backend
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip install httpx   # 测试用
set ADVISOR_TOKEN=adv_xxx
uvicorn app.main:app --reload

# 前端（另一个终端，vite 代理 /api → :8000）
cd frontend
npm install
npm run dev    # http://localhost:5173，也监听 0.0.0.0 支持 http://<主机IP>:5173
```

## 测试

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_smoke.py -q
```

冒烟测试验证：上传 run → alpha 计算（raw return − 沪深300 同期涨幅）→ 列表 → 详情 → timeline 全链路。

## API 概览

除 `/healthz` 外，看板 API 均需要 `Authorization: Bearer <ADVISOR_TOKEN>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/auth/verify` | 校验前端登录密码（Bearer） |
| POST | `/api/v1/runs` | 上传完整 run（Bearer） |
| GET | `/api/v1/runs` | 决策列表（可按 code 筛选，Bearer） |
| GET | `/api/v1/runs/{id}` | 单次决策完整详情（Bearer） |
| GET | `/api/v1/portfolio/current` | 最新持仓快照（Bearer） |
| GET | `/api/v1/holdings/{code}/timeline` | 标的决策序列 + alpha（Bearer） |
| GET | `/api/v1/memory/context` | Phase 0 同标的记忆 + 跨标的 lessons（Bearer） |
| GET | `/api/v1/candidates` | 候选跟踪（Bearer） |
| GET | `/api/v1/benchmark/hs300` | 沪深300 基准（Bearer） |
| GET/POST/DELETE | `/api/v1/watchlist` | 自选股管理（Bearer） |
| GET/POST | `/api/v1/health[/outcome]` | 检查点健康度（Bearer） |

## Alpha 计算

上传新 run 时，对每个 holding：
1. `raw_return = (本次 price − 上次同标的 advice price) / 上次 price`
2. 从 `benchmark_prices` 取同期沪深300涨幅 `benchmark_return`
3. `alpha = raw_return − benchmark_return`
4. 沪深300缺失 → 标 `[数据缺失]`，降低 alpha 置信度

沪深300由后端启动时回填 + 每个交易日 15:35（Asia/Shanghai）定时刷新。

## 使用 Skill

`skill/` 是本项目主能力目录，可以作为 Codex Skill 安装或复制到本地 Skill 目录使用。其中 `persistence.md` 定义了上传/拉取契约。设置环境变量即可启用可选的后端记忆与看板：

```
ADVISOR_API_URL=http://localhost:8000/api/v1
ADVISOR_TOKEN=adv_xxx
```

- **Phase 0**：Skill 执行开始时拉取同标的最近 5 次决策 + alpha，注入 trading memory
- **Phase 6**：Skill 执行末尾上传完整 run
- 未配置时 Skill 仍可独立运行（trading memory 回退到对话历史）

## 边界

- 单 Token 单用户（非多用户/JWT）
- 单一组合（非多组合）
- 不做行情实时推送、不连券商自动交易
- Skill 不强制依赖前后端系统（未配置仍可独立跑）
