# Phase O Manual UAT

这份清单写给实际使用系统的人。Phase O.1 的自动化浏览器 acceptance 已通过，但
它不替代目标浏览器、真实部署和用户本人确认。请逐项填写“实际”和“结果”，附截图、
时间和 request ID（若有）；没有可用的真实数据时填写 `BLOCKED`，不要猜测通过。

测试环境：Docker production-like（`phase-o2-manual-uat`）  测试用户：用户本人  日期：2026-09-01

## 运行环境记录

Manual UAT 必须使用正常运行模式；不得使用 Acceptance runner、fixture 或临时 SQLite。

Manual UAT URL：`http://127.0.0.1:18082`

启动命令：`powershell -ExecutionPolicy Bypass -File .\scripts\start_uat.ps1`

停止命令：`powershell -ExecutionPolicy Bypass -File .\scripts\stop_uat.ps1`

Runtime DB path：Docker volume `phase-o2-manual-uat_advisor-data`，容器内
`/app/data/advisor.db`

Acceptance mode：`OFF`；确认方式：System release/health 或容器有效环境变量中
`ACCEPTANCE_MODE=false`。

真实数据只保存在本地运行环境，不得提交截图、CSV、SQLite 或券商凭据。

## 1. Login

操作步骤：打开 `/`，确认跳转 `/login`；输入账号密码登录；退出后再次访问私有路由；
再用错误密码登录一次。

预期：登录成功进入原目标页面或 `/dashboard`；退出后私有路由跳转登录；错误密码有明确反馈。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 2. Portfolio Create / Select

操作步骤：登录后新建一个 Portfolio；再在 Dashboard、Reports、Research、Shadow 和
System 间切换 Portfolio，最后切回刚创建的 Portfolio。

预期：Portfolio 创建成功；当前 Portfolio 标识在各页面一致；切换不会串出其他用户或其他
Portfolio 的持仓、报告和 Shadow 数据。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 3. Upload

操作步骤：进入 `/upload`，选择或粘贴持仓截图；等待识别；检查总资产、现金、持仓和
可用数量；必要时手工修正并保存；点击“仅确认快照”。

预期：上传、解析、人工确认分阶段显示；未通过质量校验不能自动 confirmed；确认后出现
snapshot ID 和时间。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 4. Holdings Review

操作步骤：在 Upload 解析完成后逐项检查代码、名称、数量、可用数量、成本、现金和解析质量；
手工修正一项字段后保存，再返回 Review。

预期：Review 显示结构化持仓和质量问题；手工修正可保存；解析失败停留在 Review，不生成
confirmed snapshot。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 5. Confirmed Snapshot

操作步骤：在 Review 页面点击“仅确认快照”，然后在 Dashboard、Reports 和 Shadow 查看该
snapshot 的 ID、时间和来源。

预期：只有用户明确确认后生成 confirmed PortfolioSnapshot；confirmed snapshot 不被后续
页面静默改写；现金语义区分 broker available cash、reserve 和 spendable。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 6. Dashboard / Market Card

操作步骤：进入 `/dashboard`，切换至少两个 Portfolio；检查 Market、Portfolio、Today's
Decision、Candidate、System Health、时间和 freshness。

预期：页面明确当前 Portfolio；数据来自后端；freshness、质量和 snapshot 时间可读；无
数据时有下一步操作；昨天的 ACTION 不冒充今天结果。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 7. Manual Analysis

操作步骤：从 Dashboard 或 Upload 选择已确认 snapshot，选择 Fast/Standard/Deep 和
checkpoint，提交一次分析；观察 queued/running/progress；刷新页面。

预期：提交中按钮锁定；刷新后任务仍能恢复；失败原因保留并可 retry；成功后可打开完整
Reports。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 8. Analysis Progress

操作步骤：提交 Manual Analysis 后停留在进度区域，记录 queued、running、阶段进度、完成或
失败状态；在 running 期间刷新浏览器，再点击重试或打开完成结果。

预期：进度状态可恢复且不会跳回假完成；失败保留原因并支持 retry；成功后只出现真实生成的
报告入口；没有模型凭据时明确显示配置缺口。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 9. Reports

操作步骤：打开 `/reports` 的 list 和 detail，查看完整分析流程、结构化证据和原始报告；
核对 checkpoint、mode、holding actions、Candidate、quality、market context、lineage。

预期：Final Portfolio Decision 比 Candidate 更明确；detail 不是只有 HTTP 200；关键证据
和数据缺口可见。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 10. ACTION / NO_ACTION

操作步骤：分别打开 `ACTION` 和 `NO_ACTION` 报告、Dashboard 与 Shadow；对没有 ACTION 的
结果检查 reason，对有 ACTION 的结果检查最终组合决策。

预期：`ACTION`、`NO_ACTION`、`BLOCKED`、`DATA_GAP` 语义独立；`NO_ACTION` 是合法正式结果，
不会显示成“暂无结果”；不产生真实订单。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 11. Candidate

操作步骤：在 Dashboard、Reports 和 Candidate 相关区域查看本次 CandidateRun；对每个候选检查
阶段、评分字段、Portfolio Fit、Decision Edge、数据覆盖和最终组合动作的关系。

预期：Candidate 由后端 Candidate Engine 产生；0 个 ACTION Candidate 可以是正常成功；
Candidate 不替代 Final Portfolio Decision，不显示为已执行订单。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 12. Candidate Veto

操作步骤：打开一个 Candidate 为 `ACTION`、但 Portfolio Decision 为 `NO_ACTION` 的报告。

预期：页面显示 Candidate Veto 和原因；不把 Candidate 报成最终动作；Shadow 不生成该
候选的 candidate-driven execution intent。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 13. Research

操作步骤：进入 `/research`，选择 scope、replay mode、日期和 horizon，创建 Backtest；
在运行中刷新、取消；对完成 Run 查看 metrics、limitations、hash 和 FULL/PARTIAL/DATA_GAP。

预期：运行状态可恢复；取消后不能继续伪装为完成；`PARTIAL_PIT_RECOMPUTE` 明确提示
历史输入缺失，不显示“完整 PIT 重算”。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 14. Governance

操作步骤：进入 `/governance`，检查 proposal/detail、DRAFT/REVIEW/ACTIVE/SUPERSEDED/
REJECTED；对 APPROVED 版本点击激活。

预期：出现二次确认，显示 parameter version、config hash、关键变更和 evidence；没有
Auto Apply；请求 `emergency_override=false`。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 15. System Health / Live Readiness

操作步骤：进入 `/system`，查看 DB、Schema、Disk、Backup、Provider、Scheduler、History、
Worker recovery、Monitor 和 Live Validation Readiness；点击刷新并记录 readiness endpoint
返回的真实状态。

预期：每项有 OK/DEGRADED/BLOCKED/UNKNOWN 和原因；真实 readiness 为 `NOT_READY` 时
显示 blockers，不隐藏或误导成 READY。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 16. Shadow

操作步骤：选择有 confirmed snapshot 的 Portfolio，进入 `/shadow`，创建 Shadow Account；
打开 ACTION observation，分别查看 Decision、Execution、Outcome；检查 PENDING、FILLED、
BLOCKED、EXPIRED 和缺失数据场景。

预期：页面明确 `SHADOW / 模拟验证` 和“不会发送真实订单”；Decision、Intent、Fill、
Outcome 分开；`conditional_add` 明确 V1 不模拟；`DATA_GAP` 不显示 0；`NO_ACTION` 不生成 intent。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 17. Settings

操作步骤：进入 `/settings`，查看模型、计划和通知配置；修改非敏感字段并保存；检查页面
刷新和错误重试。

预期：保存有 loading/disable；secret 不回填完整值；失败不清空用户输入；没有 Auto Trading
或 Auto Learning 开关。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 18. Backend Offline / Retry

操作步骤：打开页面后停止 backend，点击刷新或执行一个请求；恢复 backend 后点击重试。

预期：页面不白屏；显示无法连接后端和重试；恢复后可继续读取，不要求清理 localStorage。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 19. Session Expiry

操作步骤：让 access token 过期或撤销 refresh token，再在任一私有页面刷新或发起请求；
同时验证报告截图/诊断下载请求。

预期：access 失效且 refresh 成功时用户不中断；两者都失效时统一清理 session，跳转
`/login?expired=1`；没有 infinite refresh loop、白屏或 toast 风暴。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 20. SPA Refresh

操作步骤：直接打开并刷新 `/dashboard`、`/reports`、`/shadow`、`/research`、`/system`、
`/settings`，并验证浏览器前进/后退后当前 Portfolio 和 session 仍正确。

预期：production-like 服务不返回 404；刷新后仍由 auth guard 正确判断登录状态，页面能正常加载。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 21. Viewport 1366×768

操作步骤：将浏览器窗口设置为 `1366×768`，重复检查 Dashboard、Reports、System、Shadow 的
表格、卡片、弹窗和操作按钮。

预期：无横向溢出、遮挡、超屏 modal、字段截断或不可点击控件；关键状态和下一步操作可见。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 22. Viewport 1440×900

操作步骤：将浏览器窗口设置为 `1440×900`，重复检查 Dashboard、Reports、System、Shadow 的
表格、卡片、弹窗和操作按钮。

预期：无横向溢出、遮挡、超屏 modal、字段截断或不可点击控件；关键状态和下一步操作可见。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 23. Viewport 1920×1080

操作步骤：将浏览器窗口设置为 `1920×1080`，重复检查 Dashboard、Reports、System、Shadow 的
表格、卡片、弹窗和操作按钮。

预期：内容保持稳定最大宽度；无过度拉伸、空白错位、遮挡、字段截断或不可点击控件。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 执行记录

| 场景 | 结果 | 时间 | 证据位置 / request ID | 备注 |
|---|---|---|---|---|
| Login |  |  |  |  |
| Portfolio Create / Select |  |  |  |  |
| Upload |  |  |  |  |
| Holdings Review |  |  |  |  |
| Confirmed Snapshot |  |  |  |  |
| Dashboard / Market Card |  |  |  |  |
| Manual Analysis |  |  |  |  |
| Analysis Progress |  |  |  |  |
| Reports |  |  |  |  |
| ACTION / NO_ACTION |  |  |  |  |
| Candidate |  |  |  |  |
| Candidate Veto |  |  |  |  |
| Research |  |  |  |  |
| Governance |  |  |  |  |
| System Health / Live Readiness |  |  |  |  |
| Shadow |  |  |  |  |
| Settings |  |  |  |  |
| Backend Offline / Retry |  |  |  |  |
| Session Expiry |  |  |  |  |
| SPA Refresh |  |  |  |  |
| Viewport 1366×768 |  |  |  |  |
| Viewport 1440×900 |  |  |  |  |
| Viewport 1920×1080 |  |  |  |  |

## 反馈分类

- `BLOCKER`：登录失败、白屏、Upload 断链、Portfolio 串线、Report 打不开、Shadow 创建失败。
- `USABILITY`：不知道下一步、ACTION/Candidate 易混淆、按钮位置不清楚、文案过于技术。
- `VISUAL`：视口溢出、modal 超屏、table 撑爆、字段截断。
- `DATA`：Provider 无数据、Market snapshot 缺失、future quote 缺失；先记录真实环境事实。
- `ALGORITHM_OBSERVATION`：例如“为什么这只股票是 ACTION”；只记录到
  `docs/PHASE_O_ALGORITHM_OBSERVATIONS.md`，Phase O 不修改投资算法。
- `OUT_OF_SCOPE`：Broker Write、Auto Trading、Auto Learning、Phase P 或其他冻结范围外请求。

只有所有场景由用户实际确认后，才能把 `MANUAL_UAT` 从 `REQUIRED` 更新为 `PASS`。
