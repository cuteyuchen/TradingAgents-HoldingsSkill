# Phase O API / UI Matrix

审计日期：2026-08-30
认证方式：除登录、注册、refresh 外，页面请求统一携带 `Authorization: Bearer`。
所有请求由 `frontend/src/api/index.ts` 生成 `X-Request-ID`，后端响应的 request ID
会进入 `ApiError`。

| Frontend function | HTTP | Endpoint | Request | Response | Auth / ownership | View | Error mapping | Test / evidence |
|---|---|---|---|---|---|---|---|---|
| `api.login` | POST | `/api/v2/auth/login` | email/password/device_info | token pair | 公共；服务端认证用户 | Login | 401 登录失败 | 后端 `test_v2_auth_models.py`；浏览器待执行 |
| `api.register` | POST | `/api/v2/auth/register` + login | email/username/password | token pair | 公共；新用户 | Login | 409/422 | 后端 auth smoke；浏览器待执行 |
| `api.logout` | POST | `/api/v2/auth/logout` | refresh_token | 204/void | 当前 session | App shell | 401 后本地仍清 session | 手工待执行 |
| `api.me` | GET | `/api/v2/auth/me` | 无 | `User` | 当前用户 | App shell | 401 refresh/redirect | typecheck；浏览器待执行 |
| `api.listPortfolios` | GET | `/api/v2/portfolios` | 无 | `Portfolio[]` | 当前用户 | App / Dashboard / Upload / Reports / Shadow | 401/5xx | 组合 API 测试；浏览器待执行 |
| `api.createPortfolio` | POST | `/api/v2/portfolios` | name/market/currency/is_default | `Portfolio` | 当前用户创建 | Dashboard / Upload | 409/422 | 后端 portfolio 测试；浏览器待执行 |
| `api.uploadHoldings` | POST | `/api/v2/portfolios/{id}/uploads` | multipart screenshot，任选 parsed holdings | `HoldingUpload` | 组合 owner | Upload | 401/403/413/422/5xx | 后端 upload 测试；浏览器待执行 |
| `api.getUpload` | GET | `/api/v2/uploads/{id}` | 无 | `HoldingUpload` | owner filter | Upload | 404/401 | typecheck；轮询待执行 |
| `api.updateParsedHoldings` | PATCH | `/api/v2/uploads/{id}/parsed-holdings` | `{ parsed }` | `HoldingUpload` | owner filter | Upload | 422 字段错误 | typecheck；浏览器待执行 |
| `api.confirmUpload` | POST | `/api/v2/uploads/{id}/confirm` | 无 | confirmed `PortfolioSnapshot` | owner filter；质量门控 | Upload | 409/422 | 后端 portfolio 测试；浏览器待执行 |
| `api.getSnapshot` | GET | `/api/v2/snapshots/{id}` | 无 | `PortfolioSnapshot` | owner filter | Upload / Reports / Shadow | 404/401 | typecheck；浏览器待执行 |
| `api.createAnalysisJob` | POST | `/api/v2/analysis/jobs` | snapshot_id/mode/checkpoint/notify | `AnalysisJob` | snapshot owner | Upload / Dashboard | 409/422/503 | `test_analysis_workflow.py`；浏览器待执行 |
| `api.getAnalysisJob` | GET | `/api/v2/analysis/jobs/{id}` | 无 | job status/progress/error | job owner | Upload | 404/401 | durable polling code；浏览器待执行 |
| `api.cancelAnalysisJob` | POST | `/api/v2/analysis/jobs/{id}/cancel` | 无 | `AnalysisJob` | job owner | Upload | 409/404 | 后端 analysis tests；浏览器待执行 |
| `api.retryAnalysisJob` | POST | `/api/v2/analysis/jobs/{id}/retry` | 无 | `AnalysisJob` | job owner | Upload | 409/422 | 后端 analysis tests；浏览器待执行 |
| `api.listRuns` / `getRun` | GET | `/api/v2/analysis/runs[/{id}]` | portfolio_id 可选 | summary/detail | 当前用户和组合 owner | Reports | 404/401/5xx | `test_v2_portfolio_analysis.py`；浏览器待执行 |
| `api.compareRun` | GET | `/api/v2/analysis/runs/{id}/comparison` | 无 | comparison object | report owner | Reports | 404/409 | typecheck；浏览器待执行 |
| `api.getDashboardToday` | GET | `/api/v3/portfolios/{id}/dashboard/today` | portfolio path | Daily Dashboard | 当前用户组合 | Dashboard | 403/404/5xx | `test_daily_operations.py`；浏览器待执行 |
| `api.reconcileToday` | POST | `/api/v3/portfolios/{id}/operations/reconcile-today` | 无 | reconcile result | 当前用户组合 | Dashboard | 409/503 | `test_daily_operations.py`；浏览器待执行 |
| `api.getReplayAvailability` | GET | `/api/v3/research/replay-availability` | date range/scope portfolio | availability manifest | 当前用户；组合可选 | Research | 422/503 | `test_research_phase_i*.py`；浏览器待执行 |
| `api.listBacktests` / `createBacktest` | GET/POST | `/api/v3/research/backtests` | scope/replay/date/horizons | Backtest run | 当前用户、组合 owner | Research | 409/422/503 | `test_research_phase_i*.py`；浏览器待执行 |
| `api.getBacktest` / `cancelBacktest` | GET/POST | `/api/v3/research/backtests/{id}[/cancel]` | run id | run with status/result | 当前用户 | Research | 404/409 | research tests；浏览器待执行 |
| `api.listCalibrations` / `createCalibration` | GET/POST | `/api/v3/research/calibrations` | run id/parameter grid | Calibration report | 当前用户 | Research / Governance | 422/409 | research tests；浏览器待执行 |
| `api.listGovernanceProposals` | GET | `/api/v3/governance/proposals` | 无 | proposal list | 当前用户 | Governance | 401/5xx | `test_governance_api.py`；浏览器待执行 |
| Governance create/submit/approve/reject | POST | `/api/v3/governance/proposals/*` | proposal/value/reason | proposal/version | 当前用户；人工动作 | Governance | 409/422 | `test_governance_api.py`；浏览器待执行 |
| `api.validateParameterSet` | POST | `/api/v3/governance/parameter-sets/{id}/validate` | version id | validated version | 当前用户 | Governance | 409/422 | governance tests；浏览器待执行 |
| `api.activateParameterSet` | POST | `/api/v3/governance/parameter-sets/{id}/activate` | reason/expected active/emergency=false | active version | 当前用户；显式激活 | Governance | 409/422 | governance tests；浏览器待执行 |
| `api.listShadowAccounts` / `createShadowAccount` | GET/POST | `/api/v3/shadow/accounts` | portfolio/snapshot/name | Shadow account | 当前用户、组合 owner | Shadow | 409/422 | `test_shadow_validation.py`；浏览器待执行 |
| Shadow account pause/resume/rebase | POST | `/api/v3/shadow/accounts/{id}/*` | snapshot/acknowledge | account | 当前用户 owner | Shadow | 409/404 | `test_shadow_validation.py`；浏览器待执行 |
| Shadow decisions/orders/fills/daily | GET | `/api/v3/shadow/{decisions,orders,fills,daily}` | account/portfolio/status/limit | fact lists | 当前用户 owner | Shadow | 404/401 | `test_shadow_validation.py`；浏览器待执行 |
| `api.getShadowPerformance` / validation | GET | `/api/v3/shadow/performance`、`validation` | account/generation/portfolio | performance/cohorts | 当前用户 owner | Shadow | 404/5xx | shadow regression；浏览器待执行 |
| `api.getLiveValidationReadiness` | GET | `/api/v3/system/live-validation-readiness` | 无 | status/ready/blockers/warnings/checks/evaluated_at | 当前登录用户；组合/证据按 owner，公共 market facts 只读 | System | 401/5xx | `test_system_health.py` 16 项；浏览器待执行 |
| `api.getSystemHealth` / readiness | GET | `/api/v3/system/health`、`readiness` | 无 | component checks | 登录用户 | System / Dashboard | 401/5xx | system health tests |
| System backup/diagnostics | POST/GET | `/api/v3/system/backups*`、`diagnostics*` | reason/backup id | backup/bundle/blob | 登录用户；运维动作 | System | 400/404/409/5xx | `test_backup_restore.py`、`test_diagnostics.py` |
| History coverage/sync | GET/POST | `/api/v3/history/coverage`、`sync` | type/date/provider | coverage/sync run | 登录用户；operator guard 由后端执行 | System | 409/422/503 | `test_history_api.py`、`test_history_pit.py` |
| Model provider/profile settings | GET/POST/PATCH/DELETE | `/api/v2/model-settings/*` | provider/profile fields；secret 可选 | masked provider/profile | 当前用户 | Settings | 401/409/422/5xx | `test_v2_auth_models.py`；浏览器待执行 |
| Schedule settings | GET/POST/PATCH/DELETE | `/api/v2/schedules*` | portfolio/checkpoint/mode | schedule/job | 当前用户、组合 owner | Settings | 409/422 | `test_daily_operations.py`；浏览器待执行 |
| Notification settings | GET/POST/PATCH/DELETE | `/api/v2/notifications*` | webhook/secret/type | masked channel | 当前用户 | Settings | 409/422/5xx | auth/settings tests；浏览器待执行 |

## 错误语义

前端 `ApiError` 统一保留 `status`、`code`、`kind`、`requestId` 和 `fieldErrors`。
401 会尝试 refresh；refresh 失败后清理 session 并发出
`advisor-auth-expired`。二进制截图/诊断下载同样走 401 过期处理。

## 结论

核心页面已经使用真实 API，生产页面没有 demo fallback。矩阵中的“浏览器待执行”
是 Frontend Acceptance 的真实剩余项，不用源码检查替代 E2E 或 Manual UAT。
