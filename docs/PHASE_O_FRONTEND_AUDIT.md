# Phase O Frontend Product Audit

审计日期：2026-09-01
Baseline SHA：`3d68a777ebfd9aed6117778397bae99ba53edccc`
Branch：`codex/phase-o-frontend-productization`
Phase O 实现基线 SHA：`c6147bc979c629ab7e339b4bab4355347a023a6d`
Phase O.1 验证结果以最终交付报告中的提交 SHA 为准。

## 审计范围

本审计覆盖 `frontend/src/App.vue`、路由、共享 API、全部正式页面、对应
FastAPI 路由，以及 Phase H～N 验收文档。`当前可用` 描述源码是否已经接上
真实后端；`E2E` 描述本阶段是否已有真实浏览器证据。两者不混为一谈。

状态只使用：`PASS`、`PARTIAL`、`BROKEN`、`MISSING`、`NOT_APPLICABLE`。

## 页面矩阵

| 页面 | 用户目标 | 当前可用 | API存在 | 写操作真实有效 | Loading | Empty | Error | Auth | E2E | 状态 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `/login` | 登录、注册、过期后重新进入 | 是 | 是 | 是 | 是 | 不适用 | 是 | 公共入口 | PASS | PASS |
| `/dashboard` | 查看今日市场、组合、最终决策并启动分析 | 是 | 是 | 是 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |
| `/upload` | 上传、解析、修正并确认持仓快照 | 是 | 是 | 是 | 是 | 是 | 部分，操作错误已提示 | 是 | PASS | PASS |
| `/reports` | 查看报告详情、最终组合决策、候选和 veto | 是 | 是 | 不适用 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |
| `/shadow` | 创建纸面账户并分离查看 Decision/Execution/Outcome | 是 | 是 | 是 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |
| `/research` | 创建、恢复、取消 Backtest 并查看历史证据 | 是 | 是 | 是 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |
| `/governance` | 人工审核、验证、激活参数版本 | 是 | 是 | 是 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |
| `/system` | 检查系统健康和 Live Validation Readiness | 是 | 是 | 是 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |
| `/settings` | 保存模型、计划和通知配置 | 是 | 是 | 是 | 是 | 是 | 是，可重试 | 是 | PASS | PASS |

页面 PASS 仅表示源码、真实 API 链路和可重复浏览器验收已经通过；它不代表真实
行情已经具备，也不替代人工 UAT。

## 共享产品基础设施

| 能力 | 实现位置 | 审计结论 |
|---|---|---|
| Portfolio context | `frontend/src/composables/portfolio.ts`、`App.vue` | localStorage 持久化、全局选择、切换事件已接入 |
| Auth session | `frontend/src/api/index.ts`、`router.ts` | access/refresh token、401 refresh、过期跳转、logout 已接入 |
| HTTP error mapping | `frontend/src/api/index.ts`、`components/ErrorState.vue` | 401/403/404/409/422/5xx/network/timeout 有区别化语义和 request ID |
| Loading / Empty / Error | `frontend/src/components/` | Reports、Research、Governance、System、Settings、Shadow 使用共享状态；Dashboard 已接入 ErrorState |
| Shanghai timezone | `frontend/src/utils/ui.ts` | 日期时间统一 `Asia/Shanghai`，显示“北京时间” |
| Number formatting | `frontend/src/utils/ui.ts` 及页面 formatter | 缺失值为“不可用”或“—”，不将 DATA_GAP 填成 0 |
| SPA history fallback | `backend/app/main.py` | 非 API 的无扩展路径返回 `static/index.html`，API 路径仍返回 404 |

## Fake / Placeholder / Dead Control Audit

本次源码审计使用了 `rg` 检索 `TODO`、`FIXME`、`mock`、`demo`、
`placeholder`、`hardcoded`、`开发中`、`即将上线`、`disabled`、
`console.log` 和 `临时数据`。

- 未发现生产页面在 API 失败后回退 demo 数据或固定 chart 数组。
- 未发现 `console.log`、`TODO` 或 `FIXME` 形式的生产占位逻辑。
- 检索到的 `placeholder` 均属于输入框提示，不是数据回退。
- `disabled` 均用于没有 Portfolio、没有 snapshot 或写操作进行中的保护。
- “自动分析”指已有 scheduler 创建分析任务，不是 Auto Trading；没有真实券商
  写入口，也没有 Auto Apply Calibration。
- 生产环境的第三方模型、通知 provider、上传识图和 long-running worker 仍需人工
  UAT；自动 acceptance 使用显式开启的 deterministic provider，仅作用于隔离运行。

## 已知审计结论

1. Final Portfolio Decision 在 Reports 中独立于 Candidate 展示，Candidate
   不再作为最终组合动作的替代物。
2. Candidate 缺少 coverage、Entry、R/R 或其他关键因子时显示“不可用”，不会
   以数字 0 伪造完整数据。
3. Shadow 顶部明确 `SHADOW / 模拟验证` 和“不会发送真实订单”，并拆分
   Decision、Execution、Outcome。
4. Research 对 `QUEUED` / `RUNNING` 做轮询，刷新页面后从 durable run 恢复。
5. Governance 激活需要二次确认、版本/hash/变更/evidence 展示，并固定发送
   `emergency_override: false`。
6. System 使用真实的 `GET /api/v3/system/live-validation-readiness`，不在前端
   hardcode `READY`。
7. Playwright acceptance 通过真实 FastAPI、SQLAlchemy 和 fresh SQLite，未对正式
   业务 API 做全面浏览器 mock；失败诊断保留 trace、screenshot、video 和日志。
8. Acceptance runner 固定交易日 `2026-08-21` 和 UTC cutoff，并由后端 authority
   形成 AnalysisRun、CandidateRun、Shadow intent/fill 事实。

## 自动化 Gate 结果

- Playwright acceptance：`PASS`，15/15，运行于真实 Vue + FastAPI + SQLAlchemy +
  isolated SQLite。
- Browser console/pageerror：`PASS`，未发现未处理 `console.error`、`pageerror` 或
  unhandled rejection。
- Desktop screenshots：`PASS`，覆盖 1366×768、1440×900、1920×1080。
- Backend regression：`PASS`，当前本地全量 `427 passed`。
- Frontend typecheck/build：`PASS`。
- Alembic：`20260829_0020`，无 Phase O.1 migration。
- Docker build 和独立 Compose runtime smoke：`PASS`；使用独立 project、volume 和
  临时端口，未触碰默认数据卷。
- GitHub CI 已加入 `frontend-acceptance` job；精确提交的远端 CI 结果以 PR/Actions
  为准。

## 仍然保留

- `AUTOMATED_FRONTEND_ACCEPTANCE = PASS` 只代表自动化 Gate；`MANUAL_UAT = REQUIRED`。
- 当前真实 Live Validation 仍诚实返回 `LIVE_READINESS = NOT_READY`，具体 blockers
  必须在 `/system` 查看。
- 用户完成 [PHASE_O_MANUAL_UAT.md](PHASE_O_MANUAL_UAT.md) 前，不进入 Phase P，
  也不能把 Phase O 最终状态写成 PASS。
