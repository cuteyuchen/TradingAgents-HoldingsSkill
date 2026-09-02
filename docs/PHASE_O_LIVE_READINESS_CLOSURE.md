# Phase O Live Readiness Closure

## 当前状态

记录日期：2026-09-02

阶段：O.2-A 环境准备完成；O.2 UX 重构自动化通过；O.2-B 尚未开始，等待用户本人完成新版 Manual UAT。

应用代码基线 SHA：`d6f9d91543aa90eef0fcbf414ac869ad1b85a646`

Manual UAT URL：`http://127.0.0.1:18082`

Compose project：`phase-o2-manual-uat`

Runtime DB：Docker volume `phase-o2-manual-uat_advisor-data`，容器内
`/app/data/advisor.db`
Acceptance mode：`OFF`

本文件记录 O.2-A 的 readiness 基线，不把 smoke 结果写成 Live Evidence，也不提前进入
Phase P。只有用户明确确认 `MANUAL_UAT = PASS` 后，才可以按实时 endpoint 重新开始 O.2-B。

## O.2 UX 自动化记录

2026-09-02 的本地工程验证已完成：前端 typecheck/build 通过，后端全量 `428 passed`，
Playwright acceptance `20 passed`，Alembic `heads/current` 均为 `20260829_0020`，无新增
migration；独立 Docker smoke 完成 health、注册登录、鉴权 API、六个新路由和六个旧 alias
的 HTML 及无头 Chromium 渲染检查后已清理。

PR #7 的 exact-head GitHub CI 已通过（push run `33600976956`、pull-request run
`33600980668`；backend、frontend、frontend-acceptance、docker 全部成功）。

这些结果只证明实现和自动化验证，不产生真实行情、真实组合快照、真实分析或 Live Evidence。
新版界面的 `MANUAL_UAT` 仍为 `REQUIRED`，由用户本人实际使用确认；`LIVE_READINESS` 仍为
`NOT_READY`，`PHASE_O_FINAL` 仍为 `HOLD_FOR_REDESIGNED_MANUAL_UAT`。

## Readiness Endpoint 基线

请求：带认证 `GET /api/v3/system/live-validation-readiness`
实际返回：`status=NOT_READY`，`ready=false`，评估时间为 2026-09-01（Asia/Shanghai）。

| Check | 实际状态 | 实际原因/事实 |
|---|---|---|
| database | `OK` | 数据库可写，必需表齐全 |
| schema | `OK` | DB 与 code head 均为 `20260829_0020` |
| disk | `OK` | free ratio 约 `0.931686` |
| backup | `BLOCKED` | 尚无 verified backup |
| scheduler | `OK` | `SCHEDULER_ENABLED=true`，单一 embedded scheduler running |
| trading calendar | `OK` | local calendar `ready`，trade date `2026-09-01` |
| market provider | `BLOCKED` | `quote_provider_not_observed`，配置 primary 为 `eastmoney_batch` |
| quote pipeline | `BLOCKED` | `market_snapshot_not_observed` |
| market refresh | `BLOCKED` | `market_refresh_not_observed` |
| portfolio snapshot | `BLOCKED` | `confirmed_portfolio_snapshot_missing` |
| analysis smoke | `BLOCKED` | `successful_analysis_run_not_observed` |
| candidate smoke | `BLOCKED` | `successful_candidate_run_not_observed` |
| shadow subsystem | `OK` | schema 已安装，尚无 active account |
| future quote observation | `BLOCKED` | `future_quote_observation_not_observed` |
| real broker write path | `OK` | `real_broker_order_path_not_exposed` |

System health 另显示 realtime monitor 为 `OK / monitor_disabled_by_config`；这不等于 future
quote observation 已产生，也不绕过 readiness gate。

## Before → Action → After

| Before | Action | After |
|---|---|---|
| 已有 `phase-o-live` 容器使用早于应用代码基线的镜像，数据库含 `Docker Acceptance` 用户标记。 | 保留旧容器和原 volume 不动；从应用代码基线构建新的 `phase-o2-manual-uat` project，显式设置 `ACCEPTANCE_MODE=false`，使用新 volume。 | 新容器 healthy；release 返回运行时构建 SHA；新 DB 无 Acceptance 用户/Portfolio；`ACCEPTANCE_MODE=False`。 |
| 默认 compose 的固定 container name 会与旧容器冲突。 | 将 compose container name 改为可选环境变量，启停脚本使用独立名称、端口和 project。 | `start_uat.ps1` / `stop_uat.ps1` 可重复使用，不触碰旧 project。 |
| 真实浏览器 HTML deep link 需要 SPA fallback。 | 以 `Accept: text/html` 对六个私有前端路由执行 HTTP smoke。 | 六个路由均返回 `200 text/html` 的 `index.html`。 |
| 新环境尚无业务数据。 | 只走正式注册、登录和 readiness 读取 API；不 seed、不 mock、不手工写 ProviderHealth/MarketSnapshot。 | login API 成功；readiness 如上保持 `NOT_READY`。 |

## O.2-B 入口条件

以下事项保留给用户 UAT PASS 后的 O.2-B，不能在本阶段代办：

- 真实 provider health、MarketSnapshot 和 refresh 事实。
- 用户通过 UI Upload → Review → Confirm 产生的 confirmed PortfolioSnapshot。
- 一次真实 production analysis path 和 Candidate Engine smoke；`NO_ACTION` 仍可为合法结果。
- 用户通过 UI 创建的 paper-only Shadow，以及真实 future quote observation。
- verified backup freshness、monitor/scheduler authority 和其余运行治理核验。

当前结论：`LIVE_READINESS = NOT_READY`；`MANUAL_UAT = REQUIRED`；
`PHASE_O_FINAL = HOLD_FOR_REDESIGNED_MANUAL_UAT`。
