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
- 按中国交易日和 `Asia/Shanghai` 业务时区调度分析；用户时区仅用于界面与通知展示。
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

## 历史研究与参数校准（Phase I）

历史研究位于独立的 Research namespace，使用已持久化事实执行 Point-in-Time Replay、Backtest、Robustness Analysis 和 Calibration Evidence。它不会调用实时行情或 LLM，不写入 DecisionMemory、TradeLedger、PortfolioSnapshot，也不会自动修改任何生产配置。当前 Alembic head 为 20260827_0017，迁移链保持 0014 → 0015 → 0016 → 0017。

支持三种明确分离的模式：

- PRODUCTION_REPLAY：重放历史上已经保存的 Market、Candidate、Portfolio、Memory 事实。
- DETERMINISTIC_RECOMPUTE：只有完整 PIT 数据集存在时才启用；当前缺少该数据集会 fail-close。
- BAR_ONLY_DIAGNOSTIC：仅研究价格因子，不宣称完整 Candidate Engine 回测。

研究页面位于 /research，包含 Data Availability、Backtest Evidence 和 Calibration Report。校准结果只生成 KEEP_CURRENT、CONSIDER_CHANGE、INSUFFICIENT_EVIDENCE 或 REJECT_CHANGE，没有 Apply 按钮；NEXT_OPEN_PROXY 只是模拟执行，不是真实成交。详细验收边界见 docs/PHASE_I_ACCEPTANCE.md。

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

- 应用：`http://localhost:8080`
- Swagger：`http://localhost:8080/docs`
- V1 兼容 API：`http://localhost:8000/api/v1`

前端静态资源与 FastAPI 已构建到同一个镜像、运行在同一个容器中。默认同时映射 `8080` 和 `8000`，两个端口都访问同一应用；保留 `8000` 是为了兼容已有 Skill 的 API 地址。

首次打开前端后创建账户。生产部署完成首个账户创建后，建议设置：

```dotenv
ALLOW_REGISTRATION=false
```

### 3. 系统内配置

首个注册账户可在“设置 → 数据与行情 → 同花顺金融数据”保存 API Key，并探测接口能力。密钥在服务器加密保存，优先于 `FUYAO_API_KEY` 环境变量；详细规则见 [金融数据配置](docs/MARKET_PROVIDER_SETTINGS.md)。

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

## 候选与无需调整

- `result.candidates` 和 `buy_candidates` 只表示当前 Action Gate 通过的、新的非当前持仓机会。
- 只有 `candidate_type=new_position`、`score >= 7`，且证据、质量门与风险门均通过的标的，才有资格进入候选列表；最多保留 3 个，评分通过也不保证一定输出。
- `rotation_watch`、评分 5–6 或证据不足的想法只能放在 `candidate_blocked_reason`、观察触发条件或报告文字中，不能进入 `candidates`。
- `candidates=[]` 是正常成功结果；当质量门通过、现有持仓均为 `HOLD/WATCH` 且没有行动候选时，组合评级应为 `no_action`，结论和最终动作必须保持现状一致。

## 自动分析

在“系统设置 → 自动分析”配置：组合、执行时间、分析模式、持仓过期天数、是否通知和连续失败阈值。A 股业务时间固定为 `Asia/Shanghai`；用户时区不改变交易日判断或计划的业务时间。

执行前系统会：

1. 使用持久化 `TradingCalendar` 检查是否为 A 股交易日；日历缺失或当天休市时闭锁调度，不用行情请求临时猜测交易日。
   启动时的离线内置日历目前只覆盖 2025–2026 年。进入 2027 年前必须更新内置交易所休市表，或通过受保护的日历同步接口写入有效数据；系统不会把未知年份猜成交易日，并会通过健康检查暴露 `calendar_not_initialized` / `calendar_out_of_range`，避免计划静默失效。
2. 获取最近一次已确认持仓。
3. 校验持仓快照是否过期。
4. 使用幂等键避免相同计划重复创建任务。
5. 连续失败达到阈值后自动停用计划。

产品标准检查点为 `09:35`、`10:30`、`13:05`、`14:30`、`15:10`。这些是运行合同标签，不会自动创建五个 Scheduler 任务；需要在设置中按需配置计划。`15:10` 用于深度日终复盘，不代表系统会自动下单。

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

### Phase I Research

```text
GET    /api/v3/research/replay-availability
GET    /api/v3/research/backtests
POST   /api/v3/research/backtests
GET    /api/v3/research/backtests/{id}
POST   /api/v3/research/backtests/{id}/heartbeat
POST   /api/v3/research/backtests/{id}/cancel
GET    /api/v3/research/calibrations
POST   /api/v3/research/calibrations
GET    /api/v3/research/calibrations/{id}
```

Research API 只接受研究范围、日期、模式和受限参数网格；Outcome、Return、Score、Source ID 和历史事实等 server-owned 字段不能由客户端提交。Research Run、Calibration Report 与 Portfolio 均按当前用户隔离。

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

## GHCR 部署与升级

GitHub Actions 在 `main`、版本标签或手动触发时发布单一多架构镜像，支持 `linux/amd64` 和 `linux/arm64`。ARM64 服务器拉取相同标签时会自动选择 ARM64 镜像：

```text
ghcr.io/cuteyuchen/tradingagents-holdings-advisor:latest
ghcr.io/cuteyuchen/tradingagents-holdings-advisor:sha-<commit>
ghcr.io/cuteyuchen/tradingagents-holdings-advisor:1.0.0
ghcr.io/cuteyuchen/tradingagents-holdings-advisor:v1.0.0
```

### 版本号硬规则

- **根目录 `VERSION` 是应用版本的单一真实可信来源（SSOT）**。应用版本从 `1.0.0` 开始，统一在此维护；同步更新 `frontend/package.json` 的 `version`，以及 `frontend/package-lock.json` 中存在的应用包版本（当前为顶层 `version` 与 `packages[""].version`，不修改依赖包版本）。版本必须内嵌到前后端构建产物；部署 Compose 不得通过环境变量覆盖应用版本、提交号或构建时间（`APP_VERSION`、`APP_GIT_SHA`、`APP_BUILD_TIME`），这些信息必须来自镜像构建。
- **每次准备发布修改前，必须先递增根 `VERSION`，并严格遵循 SemVer**：向后兼容的修复递增 patch（如 `1.0.0 → 1.0.1`）；向后兼容的新功能递增 minor 并归零 patch（如 `1.0.1 → 1.1.0`）；不兼容的重大变更递增 major 并归零 minor、patch（如 `1.1.0 → 2.0.0`）。
- **只有本次修改尚未发布时，才可沿用同一待发布版本号**，并在最终提交上重新完成全部发布验收。已发布版本号绝对不得被不同提交覆盖；不得把已有版本标签重新指向另一提交或以同一版本重推不同提交的镜像。Git 版本标签必须与 `VERSION` 一致，例如 `1.0.1` 对应 `v1.0.1`。
- **应用版本与内部 Skill/Contract 版本独立**。根 `VERSION`（如 `1.0.0`）标识应用发布；`skill/tradingagents-holdings-advisor/runtime.json` 的内部版本（如 `2.4.0`）及 `skill/tradingagents-holdings-advisor/SKILL.md` 中的规则说明负责 Skill/Contract 演进。二者职责独立，不要求数值相同，不得用内部协议版本替代应用版本，也不得为应用升级机械改写内部版本。

### 全链路版本对账与一致性门槛

以发布提交中的根 `VERSION` 和 `git rev-parse HEAD` 得到的**完整 commit SHA（当前为 40 位）**为基准，记录构建镜像 digest，并逐项对账：

| 核验对象 | 实际路径或入口 | 必须满足的一致性要求 |
| --- | --- | --- |
| 源码与包元数据 | 根 `VERSION`、`frontend/package.json`、`frontend/package-lock.json` | 所有应用包版本与根 `VERSION` 完全一致，版本变更包含在发布提交中。 |
| 公开 API 与后端 | `GET /openapi.json` 的 `info.version`；`backend/app/main.py` 的 FastAPI `app.version`；`backend/app/config.py` 的 `settings.APP_VERSION` | 生产公开 API、运行容器中的 `app.version` 和 `settings.APP_VERSION` 均等于发布版本。 |
| 前端界面 | `frontend/vite.config.js` 从根 `VERSION` 内嵌 `VITE_APP_VERSION`；`frontend/src/components/AppVersion.vue`；`frontend/src/views/LoginView.vue`、`frontend/src/v3/layouts/V3Topbar.vue` 与 `frontend/src/views/SystemView.vue` | 登录/注册页、各业务页顶部和系统运维页的应用版本一致（界面可带 `v` 前缀）。实际访问 `/dashboard`、`/holdings`、`/settings?section=system` 核验，不能只检查源码或构建日志。 |
| OCI 镜像标签与注解/labels | GHCR 的 `<VERSION>`、`v<VERSION>` 标签；`org.opencontainers.image.version`、`org.opencontainers.image.revision` | 版本标签指向验收通过的镜像 digest；版本元数据等于根 `VERSION`，revision 等于完整发布 SHA。现有 `.github/workflows/docker-images.yml` 写入的是镜像 config labels；若镜像另带同名 OCI annotation，也必须一致，不能假定 manifest 顶层存在该字段。 |
| 生产实际运行容器 | Compose 的 `advisor` 服务、容器内 `/VERSION`；需登录的 `GET /api/v3/system/release` 返回 `app_version`、`git_sha`、`build_time` | 从实际容器的 image ID 检查镜像元数据，并核对运行时版本和完整 SHA；运行时 `build_time` 等于镜像内嵌的 `APP_BUILD_TIME`。不得仅凭 `latest`、`sha-<commit>` 标签或短 SHA 宣布一致。 |

核验必须使用上述实际存在的路径、组件和接口。系统运维页会缩写提交号，完整 SHA 必须从发布信息接口或运行容器读回；不得编造其他版本 API、组件或核验命令。任何版本或完整 SHA 不一致、缺失或为 `UNKNOWN`，均阻断发布验收。

### 发布验收四步闭环

**`VERSION` 变更及前端应用包元数据同步必须随发布提交进入版本控制；以下四步全部通过，才能宣布发布完成：**

1. **测试前置通过**：在待发布源码上完成本 README 的开发与测试检查，包括隔离数据库上的 Alembic 空库升级与重复升级、后端 `pytest tests -q`、前端 `npm run typecheck`、`npm run build`、`npm run e2e:acceptance`。测试不得使用生产数据目录；有失败不得进入升级。
2. **同完整 SHA 的 CI 与双架构镜像验证全绿**：核对 `.github/workflows/ci.yml` 的后端、前端、前端验收及 Docker 检查，与 `.github/workflows/docker-images.yml` 的构建及 `Verify both image architectures and release metadata` 步骤，均对应同一完整发布 SHA 且成功。`linux/amd64`、`linux/arm64` 镜像都必须构建成功，manifest 架构齐全，各架构运行验证通过，版本与完整 SHA 符合上表。两套工作流独立触发，必须按 SHA 核验结果；镜像已推送或另一提交的 CI 全绿不能替代此门槛。保留工作流结果与验收镜像 digest。
3. **升级前取得 SQLite 一致性备份并保留原数据挂载**：在旧容器仍运行时，通过现有系统运维页创建并校验备份，或登录后调用 `POST /api/v3/system/backups`（请求体 `{"reason":"PRE_UPGRADE"}`），再调用 `POST /api/v3/system/backups/{backup_id}/verify`，确认 `verified=true`、校验和匹配且 SQLite 检查通过。现有 `backend/app/system/backup.py` 使用 SQLite 在线 backup API；WAL 模式下不得只复制正在运行的 `advisor.db`。保留备份 `.sqlite` 与 `.json` 清单，完成备份后才可升级。保留现场原 Compose/env 与实际数据挂载，升级前通过 `docker inspect` 核对源目录及权限。`./backend/data -> /app/data` 及默认 `./backend/data/backups` 仅为仓库模板示例，不代表所有生产环境；现部署 `oracle-prod` 的真实路径为 `/srv/apps/tradingagents-holdings/data -> /app/data`，备份位于该目录下的 `backups`。升级必须继续沿用现场既有数据挂载，严禁用仓库模板覆盖现有生产配置、换挂空卷或新数据卷，亦不得删除原数据；启动时的自动备份保护不能替代升级前的验收备份。
4. **线上读回并完成对账**：升级后读回实际运行容器的 image ID、digest、OCI 版本与 revision、`/VERSION`、`settings.APP_VERSION`、`app.version`，再检查生产 `/openapi.json`、前端界面及 `/api/v3/system/release` 的版本、完整 `git_sha` 和构建时间，与同一发布提交和验收镜像逐项核对并保留结果。任一项不一致或无法取得证据，都不能宣布发布完成。

### 部署操作与线上读回

本地 ARM64 打包与调试示例（正式发布仍须满足上述双架构及构建元数据门槛）：

```bash
docker buildx build --platform linux/arm64 -t tradingagents-holdings-advisor:1.0.0-arm64 --load .
docker save -o tradingagents-holdings-advisor-1.0.0-arm64.tar tradingagents-holdings-advisor:1.0.0-arm64
```

检查已发布镜像的架构：

```bash
docker buildx imagetools inspect ghcr.io/cuteyuchen/tradingagents-holdings-advisor:1.0.0
```

服务器首次部署：

```bash
cp .env.deploy.example .env
# 修改 ADVISOR_TOKEN、APP_SECRET_KEY、PUBLIC_APP_URL 等生产配置
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d --remove-orphans
```

后续更新在四步闭环的测试、CI/镜像验证及升级前备份门槛通过后，拉取并重建容器：

```bash
docker compose -f docker-compose.deploy.yml pull advisor
docker compose -f docker-compose.deploy.yml up -d --no-deps advisor
```

部署 Compose 应维持生产实际环境中的数据卷挂载（仓库模板示例为 `./backend/data -> /app/data`，生产如 `oracle-prod` 实际为 `/srv/apps/tradingagents-holdings/data -> /app/data`），升级前须通过 `docker inspect` 核对源目录及权限；升级镜像不会覆盖既有 SQLite 数据库、上传截图和分析产物。严禁用仓库模板覆盖现有生产配置或换挂空卷。使用 `IMAGE_TAG=sha-<commit>` 可以锁定并回滚到指定提交。

升级后，在原部署目录执行以下只读核验，将输出与发布版本、完整 SHA、验收镜像 digest 及原数据挂载对账。镜像检查使用实际运行容器的 image ID：

```bash
release_container="$(docker compose -f docker-compose.deploy.yml ps -q advisor)"
docker inspect "$release_container" --format 'image_id={{.Image}} image_ref={{.Config.Image}} mounts={{json .Mounts}}'
release_image="$(docker inspect "$release_container" --format '{{.Image}}')"
docker image inspect "$release_image" --format 'digests={{json .RepoDigests}} version={{index .Config.Labels "org.opencontainers.image.version"}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
docker image inspect "$release_image" --format '{{json .Config.Env}}' | docker compose -f docker-compose.deploy.yml exec -T advisor python -c 'import json, sys; env = dict(item.split("=", 1) for item in json.load(sys.stdin)); print(json.dumps({key: env.get(key) for key in ("APP_GIT_SHA", "APP_BUILD_TIME")}, indent=2))'
docker compose -f docker-compose.deploy.yml exec -T advisor python - <<'PY'
import json
from pathlib import Path
from urllib.request import urlopen
from app.config import settings
from app.main import app

with urlopen("http://127.0.0.1:8000/openapi.json") as response:
    openapi = json.load(response)
print(json.dumps({
    "VERSION": Path("/VERSION").read_text(encoding="utf-8").strip(),
    "settings.APP_VERSION": settings.APP_VERSION,
    "app.version": app.version,
    "openapi.info.version": openapi["info"]["version"],
    "git_sha": settings.APP_GIT_SHA,
    "build_time": settings.APP_BUILD_TIME,
}, ensure_ascii=False, indent=2))
PY
```

该读回还须结合生产访问地址的 `/openapi.json`、实际前端页面，以及登录后 `/api/v3/system/release` 的响应核验；容器内检查通过不能替代线上全链路验收。

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

后端 `pytest` 在收集测试模块前自动设置独立临时数据库、分析产物和备份目录，并关闭调度器。显式指定的隔离测试路径会保留；指向默认用户数据库 `backend/data/advisor.db` 或默认产物、备份目录的配置会拒绝启动。测试结束保留诊断目录，路径显示在 pytest 输出末尾，不自动删除。

A 股全市场行情默认由 Fuyao 以 `limit=6000` 获取，超大批次失败后自动退回每页 100 条；分页使用实际返回行数推进偏移量，兼容服务端限制批次大小。默认补全顺序为 Tencent、Eastmoney。东财行情一次失败后在进程共享的熔断器中跳过 300 秒，冷却结束仅允许一次恢复探测；海外部署已确认东财持续返回 502 时，可设置 `EASTMONEY_QUOTE_ENABLED=false` 完全跳过行情请求。已有部署可将 `MARKET_QUOTE_ALL_A_FALLBACK_PROVIDERS` 更新为 `tencent,eastmoney_batch`。

Overview/Foundation 的全市场快照和主要指数共用缓存刷新机制，同一个键的并发请求只拉取一次。默认盘中缓存 30 秒、盘后 300 秒、失败 15 秒，分别通过 `MARKET_FOUNDATION_LIVE_CACHE_SECONDS`、`MARKET_FOUNDATION_CLOSED_CACHE_SECONDS`、`MARKET_FOUNDATION_FAILURE_CACHE_SECONDS` 调整；响应保留原始行情时间戳。

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

GitHub Actions 会执行 Alembic 空库升级与重复升级、全部后端测试、前端 TypeScript 类型检查与构建，以及包含前后端的单一 Docker 镜像构建。

## Codex 验证清单

自动化测试使用模拟模型和模拟市场快照验证核心约束。交付前建议 Codex 在独立环境补充以下真实链路测试：

1. 使用实际视觉模型解析至少三种不同券商截图，核对代码、总持仓、可用数量、成本、盈亏金额与盈亏率。
2. 分别验证项目实际准备使用的 OpenAI Compatible、Anthropic 或 Gemini 模型接口。
3. 在交易时段和收盘后验证腾讯行情、东财 K 线、资金流与公告字段。
4. 验证行情不可用时是否阻断具体交易动作并保留 `watch_only`/缺失证据说明，且不产生具体买卖数量。
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
