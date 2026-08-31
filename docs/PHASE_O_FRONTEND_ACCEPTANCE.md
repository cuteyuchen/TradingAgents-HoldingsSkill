# Phase O Frontend Acceptance

验收日期：2026-08-30
Baseline SHA：`3d68a777ebfd9aed6117778397bae99ba53edccc`
Branch：`codex/phase-o-frontend-productization`

## Gate 结论

| Gate | 结论 | 依据 |
|---|---|---|
| Frontend source/product audit | PASS | `PHASE_O_FRONTEND_AUDIT.md`、`PHASE_O_API_UI_MATRIX.md` |
| Frontend typecheck | PASS | `frontend`: `npm run typecheck` |
| Frontend production build | PASS | `frontend`: `npm run build` |
| Backend readiness regression | PASS | `test_system_health.py`: 16 passed |
| Shadow/system focused regression | PASS | `test_system_health.py test_shadow_validation.py test_daily_operations.py`: 69 passed |
| Real browser Playwright acceptance | HOLD | 已用真实容器与隔离 SQLite 完成 CLI smoke，但尚未配置可重复执行的 deterministic backend runner 和完整四场景 acceptance |
| Desktop visual QA | PASS | 已检查 1366×768、1440×900、1920×1080 截图；未发现 overlap、clipping、空白页或主按钮超屏 |
| Docker production-like start/deep-link | PASS | `docker compose build`、独立容器启动、健康检查、注册登录、Portfolio 创建、verified backup、`/shadow`/`/research`/`/system` 直接刷新均已通过 |
| Frontend Acceptance overall | HOLD | 完整可重复浏览器 acceptance 和人工 UAT 仍未完成 |

## 已验证的功能面

- 路由覆盖 `/login`、`/dashboard`、`/upload`、`/reports`、`/shadow`、
  `/research`、`/governance`、`/system`、`/settings`。
- App shell 有当前用户、Portfolio selector、当前快照时间、系统状态入口和
  logout。
- 统一 API 层处理 401 refresh、request ID、403/404/409/422/5xx、network 和
  timeout；截图/诊断 blob 请求也能处理 session expiry。
- Upload 需要人工确认后才生成 confirmed snapshot；分析任务有 queued/running/
  failed/succeeded/cancelled 状态和轮询。
- Reports 将 Final Portfolio Decision 与 Candidate 分离，Candidate 缺数据时显示
  “不可用”。
- Research 支持 Backtest queued/running 轮询、刷新恢复、取消和 FULL/PARTIAL/
  DATA_GAP 文案。
- Governance 保持人工审批和手工激活，没有 Auto Apply；激活有二次确认，
  `emergency_override` 固定为 false。
- System 的 Live Validation Readiness 来源为真实后端接口，不在前端写死 READY。
- Shadow 只记录 paper-only Decision/Execution/Outcome；`conditional_add` 明确
  V1 不模拟条件触发成交；样本不足不展示策略有效。
- Settings 不回填完整 secret，保存失败不清空用户输入，写操作有 loading/disable。

## Build 性能记录

本次 `npm run build` 与 Docker build：

- 最大入口 JS：约 `1,506.98 kB`，gzip 约 `417.24 kB`。
- 最大路由 JS：`ReportsView`，约 `137.08 kB`，gzip 约 `57.10 kB`。
- 各正式页面已经使用动态路由 chunk。
- Vite 仍提示存在超过 500 kB 的 chunk。主要原因是 `main.ts` 全局注册
  `naive-ui`，本阶段不为拆除全局组件注册引入高风险重构；该项记录为后续性能
  debt。

## 本轮补充证据

- Docker production-like 使用 `phase-o-live` 独立 Compose project 与命名数据卷，容器健康检查为 healthy，启动时 Alembic 升级到 `20260829_0020`。
- 容器内通过真实页面完成注册、登录、创建 Portfolio 和 verified backup；`/shadow`、`/research`、`/system` 直接地址跳转并刷新后仍返回应用页面，不发生 404。
- 容器关键 API 请求均返回 2xx；登出后访问 `/shadow` 正确跳转 `/login?redirect=/shadow`；干净登录页与受保护页面 console error/warning 为 0。
- 容器 readiness 在创建 backup 后仍为真实返回的 `NOT_READY`，剩余 blocker 包括 provider、quote pipeline、market refresh、confirmed snapshot、analysis、candidate 和 future quote observation，未被前端隐藏或硬编码为 READY。
- 证据目录：`output/playwright/phase-o-live/.playwright-cli/` 与
  `output/playwright/phase-o-live/docker-final-cli/`。

## 必须人工补充的验收

1. 使用真实 backend、frontend 和隔离 SQLite，完成登录到上传、确认、分析、报告、
   Shadow 的 happy path。
2. 逐页执行 ACTION、NO_ACTION、BLOCKED、DATA_GAP 四种决策场景，并核对页面不会
   把 Candidate 当 Final，也不会把 DATA_GAP 当 0。
3. 使用第二个账户验证 Portfolio、Reports、Research、Shadow 不能越权读取。
4. 在真实部署环境重复检查部署配置、数据卷和用户可见的错误恢复行为。

## 冻结声明

```text
AUTOMATED_FRONTEND_ACCEPTANCE = HOLD
LIVE_READINESS = NOT_READY
MANUAL_UAT = REQUIRED
```

这不是整个 Phase O 的 PASS；在用户完成 `docs/PHASE_O_MANUAL_UAT.md` 前，不能把
`MANUAL_UAT` 写成 PASS，也不能进入长期真实数据验证。
