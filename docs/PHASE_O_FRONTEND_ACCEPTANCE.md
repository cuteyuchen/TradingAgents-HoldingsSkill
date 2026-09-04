# Phase O Frontend Acceptance / O.2 UX Rework

验收日期：2026-09-02
Baseline SHA：`1e000b086e45cf550ae7740199cd9b4a3111afc7`
Branch：`codex/phase-o-frontend-productization`
当前记录：Phase O.2 Single-User Investment Workbench UX Rework。

## O.2 Fuyao Addendum

本分支后续补入 Fuyao 作为主要 production financial-data provider。首页只增加紧凑的
主要指数、行业与情绪上下文；持仓页增加实时标记/今日贡献与按需基本面估值；分析页增加
市场简报和候选 evidence；设置页增加 Fuyao 连接与 capability 状态。上述内容均为
deterministic context，不改 Market Score、Candidate Score、Portfolio Gate 或 Shadow。

无 `FUYAO_API_KEY` 时应用仍应启动并显示 `未配置`/`数据受限`，不显示密钥、不把可选数据
缺失当作零。Fuyao 的真实 smoke 与 live readiness 仍须单独验证，不能由 acceptance
fixture 或本地 mock 代替；`MANUAL_UAT` 继续为 `REQUIRED`。

## Gate 结论

| Gate | 结论 | 依据 |
|---|---|---|
| UX-0 Implementation Map | PASS | `docs/PHASE_O2_UX_IMPLEMENTATION_MAP.md` |
| UX-1 Shell / Navigation / Alias | PASS | 六个用户路由、旧 deep-link、Auth/Ownership 和单用户 Header |
| UX-2 Home / Holdings | PASS | First Run、Market Pulse、Final Decision、持仓表和更新 Drawer |
| UX-3 Analysis / Simulation | PASS | 分析状态、四类决策语义、Candidate Veto、Shadow 时间线 |
| UX-4 History / Settings | PASS | 历史表现、Research、Governance/System 高级设置分区 |
| UX-5 Empty / Error / Responsive | PASS | Light/Dark、1366×768、1440×900、1920×1080 和稳定占位 |
| Frontend typecheck | PASS | `frontend`: `npm run typecheck` |
| Frontend production build | PASS | `frontend`: `npm run build` |
| Backend full regression | PASS | `backend`: `python -m pytest tests -q`；`428 passed` |
| Real browser Playwright acceptance | PASS | `npm run e2e:acceptance`；隔离 SQLite；`20 passed`；包含 UX acceptance |
| Docker production-like build/runtime | PASS | build、health、注册登录、鉴权 API、六个新路由和六个旧 alias；HTTP 与无头 Chromium smoke |
| GitHub exact-head CI | PASS | push/PR 两组 exact head 均通过：backend、frontend、frontend-acceptance、docker；run `33600976956`、`33600980668` |
| Frontend Acceptance overall | PASS（本地与远端自动化） | exact-head CI 已通过；仍保持 `MANUAL_UAT = REQUIRED` |

远端 exact-head CI 已在 PR #7 的 `a4077b5393ef62e8dd23e2819b000aac2c833148` 上通过；
本地与远端自动化验证都不替代用户人工 UAT。

## 已验证的功能面

- 路由覆盖 `/login`、`/dashboard`、`/holdings`、`/analysis`、`/simulation`、
  `/history`、`/settings`；`/upload`、`/reports`、`/shadow`、`/research`、
  `/governance`、`/system` 继续作为兼容 alias。
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
  `test-results`、screenshots、service logs 和 `facts.json`；PNG/日志只作 CI artifact，
  不提交到 Git。失败 trace/video 是否启用由磁盘容量和 CI 配置决定。
- Alembic：`heads/current/upgrade head` 均为 `20260829_0020`；Phase O.1 无 migration。
- Docker：镜像 `docker compose build` 通过；独立 Compose project、volume、高位临时端口
  完成 health、注册登录、受保护 API、六个新路由和六个旧 alias 的 HTML deep-link smoke，
  并由无头 Chromium 完成登录后 12 条路由渲染 smoke；测试后清理 volume。未触碰已有
  `phase-o2-manual-uat` 容器或真实数据卷。
- GitHub CI 已配置 backend、frontend、docker 和 `frontend-acceptance` jobs；acceptance
  job 不依赖 OpenAI、broker、webhook 或私有行情 credential。

## O.2 UX 重构状态

```text
AUTOMATED_IMPLEMENTATION = PASS
AUTOMATED_FRONTEND_ACCEPTANCE = PASS
LIVE_READINESS = NOT_READY
MANUAL_UAT = REQUIRED
PHASE_O_FINAL = HOLD_FOR_REDESIGNED_MANUAL_UAT
```

自动化 acceptance PASS 不等于 Manual UAT PASS。用户必须重新实际使用新版工作台并完成
`docs/PHASE_O_MANUAL_UAT.md`；在此之前不得把 `MANUAL_UAT` 改为 PASS，不得进入 O.2-B
或 Phase P，也不得把整个 Phase O 的最终状态写成 PASS。

## O.2-A 环境记录

- 记录日期：2026-09-01；该环境记录早于本次 UX 重构，不能替代新版 UAT。
- 环境应用代码基线：`d6f9d91543aa90eef0fcbf414ac869ad1b85a646`；本次 O.2-A 的配置、脚本和文档
  变更 exact SHA 以当前分支最终提交为准。
- Manual UAT Docker URL：`http://127.0.0.1:18082`。
- Acceptance mode：`OFF`；独立 Compose project 为 `phase-o2-manual-uat`，使用全新
  `phase-o2-manual-uat_advisor-data` volume。
- 该环境的旧入口和新版六个用户路由均应在 `Accept: text/html` 下返回 SPA `index.html`；
  本轮最终 Docker smoke 以 exact final SHA 重新记录。
- 带认证的真实 readiness endpoint 当前返回 `NOT_READY`；这只是 O.2-A 环境基线，
  不代表 Manual UAT 已完成，也不启动 O.2-B。
