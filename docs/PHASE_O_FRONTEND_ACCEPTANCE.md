# Phase O Frontend Acceptance

验收日期：2026-09-01
Baseline SHA：`3d68a777ebfd9aed6117778397bae99ba53edccc`
Branch：`codex/phase-o-frontend-productization`
Phase O 实现基线 SHA：`c6147bc979c629ab7e339b4bab4355347a023a6d`
Phase O.1 验证结果以最终交付报告中的提交 SHA 为准。

## Gate 结论

| Gate | 结论 | 依据 |
|---|---|---|
| Frontend source/product audit | PASS | `PHASE_O_FRONTEND_AUDIT.md`、`PHASE_O_API_UI_MATRIX.md` |
| Frontend typecheck | PASS | `frontend`: `npm run typecheck` |
| Frontend production build | PASS | `frontend`: `npm run build` |
| Backend full regression | PASS | `backend`: `427 passed` |
| Real browser Playwright acceptance | PASS | `npm run e2e:acceptance`；15/15；真实 Vue + FastAPI + SQLAlchemy + isolated SQLite |
| Desktop visual QA | PASS | 同一 acceptance 覆盖 1366×768、1440×900、1920×1080，并保存 screenshots |
| Docker production-like build/runtime | PASS | `docker compose build`；独立 Compose project/volume 完成 health、注册登录、API、deep-link HTML 刷新 smoke |
| GitHub CI acceptance configuration | PASS | `.github/workflows/ci.yml` 已加入 Node 20、Python 3.12、Chromium 和 artifact 上传的 `frontend-acceptance` job |
| Frontend Acceptance overall | PASS | 自动化 acceptance 已封板；人工 UAT 仍保持 `REQUIRED` |

远端精确 SHA 的 GitHub Actions 结果以 PR/Actions 为准；本地自动化验证不替代远端
CI，也不替代用户人工 UAT。

## 已验证的功能面

- 路由覆盖 `/login`、`/dashboard`、`/upload`、`/reports`、`/shadow`、
  `/research`、`/governance`、`/system`、`/settings`。
- Auth happy path、logout、access token 失效后 refresh 成功，以及 refresh 失效后清
  session 并跳转 `/login?expired=1`。
- Portfolio context 在 Dashboard、Reports、Research、Shadow 间保持正确组合；
  两个用户的 Portfolio 数据不越权泄露。
- Upload 经过真实上传、deterministic recognition、review/edit、confirmed snapshot；
  invalid parse 留在 review，不生成 confirmed snapshot。
- Dashboard 按固定交易日过滤分析，昨天的 ACTION 不冒充今天建议，并显示“今日尚未
  完成分析”。
- ACTION、NO_ACTION、BLOCKED、DATA_GAP 和 Candidate Veto 均保持独立语义；Final
  Portfolio Decision 不被 Candidate 替代，DATA_GAP 不填充 0。
- Reports 验证 list、detail、Final Decision、checkpoint、mode、holding actions、
  candidate、veto、quality、market context、lineage 和结构化证据。
- Research 真实创建 deterministic Backtest，刷新后恢复 durable run，并显示
  `PARTIAL_PIT_RECOMPUTE`，不误称为完整 PIT 重算。
- Governance 保留 proposal/detail、版本/hash/evidence 和 Activate 二次确认；无
  Auto Apply，`emergency_override=false`。
- Shadow 保持纸面验证，Decision、Intent、Fill、Outcome 分开；覆盖 PENDING、FILLED、
  BLOCKED、EXPIRED，`conditional_add` 明确 V1 不模拟，NO_ACTION 不生成 intent。
- Future Quote 流程由后端 authority 形成：ACTION → PENDING intent → 合法未来 quote
  → maintenance → FILLED；前端不自行生成 fill。
- ErrorState/retry、SPA deep-link refresh 和浏览器 `console.error`、`pageerror`、
  unhandled rejection 监听均通过。
- System 页面读取真实 readiness endpoint；真实返回 `NOT_READY` 时展示 blockers，
  不硬编码为 READY。

## 构建与运行证据

- Playwright 版本：`@playwright/test` `1.62.1`；CI 固定 Node 20，CI retry 最多 1 次。
- Acceptance 命令：`npm run e2e:acceptance`。
- Runner：`scripts/run_acceptance.py`；每次创建临时目录、fresh SQLite、独立 artifacts，
  Alembic upgrade head 后 seed deterministic facts，启动真实 FastAPI 和 Vite，再执行
  Chromium acceptance，退出清理 runtime。若本机需要指定 Node，可设置
  `PLAYWRIGHT_NODE_BIN`；CI 使用 runner 自身的 Node 20。
- 固定交易日：`2026-08-21`；固定 UTC cutoff：`2026-08-21T06:00:00Z`；页面至少有
  UTC → `Asia/Shanghai` 的北京时间断言。
- Deterministic provider 仅在 `ACCEPTANCE_MODE=true` 且 provider 名为 `acceptance` 时
  生效；生产默认关闭，不提供 production HTTP test-control 路由，不绕过 Auth/Ownership，
  不改变投资算法或 Shadow authority。
- Acceptance artifacts：`output/playwright/acceptance/`，包含 `playwright-report`、
  `test-results`、trace、screenshots、video（失败时）、service logs 和 `facts.json`。
- Alembic：`heads/current/upgrade head` 均为 `20260829_0020`；Phase O.1 无 migration。
- Docker：镜像 `docker compose build` 通过；独立 Compose project、volume、高位临时端口
  完成 health、注册登录、受保护 API 和 `/shadow`、`/research`、`/system` 的 HTML
  deep-link smoke，测试后清理 volume。未触碰已有运行容器或真实数据卷。
- GitHub CI 已配置 backend、frontend、docker 和 `frontend-acceptance` jobs；acceptance
  job 不依赖 OpenAI、broker、webhook 或私有行情 credential。

## 仍然保留

```text
AUTOMATED_FRONTEND_ACCEPTANCE = PASS
LIVE_READINESS = NOT_READY
MANUAL_UAT = REQUIRED
```

自动化 acceptance PASS 不等于 Manual UAT PASS。用户完成
`docs/PHASE_O_MANUAL_UAT.md` 前，不得把 `MANUAL_UAT` 改为 PASS，不得进入 Phase P，
也不得把整个 Phase O 的最终状态写成 PASS。
