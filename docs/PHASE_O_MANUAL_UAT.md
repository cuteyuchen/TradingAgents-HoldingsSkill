# Phase O Manual UAT

这份清单写给实际使用系统的人。请在真实部署地址打开浏览器，按顺序执行；每个
场景都在“执行记录”填写 `PASS` 或 `FAIL`，并附截图、时间和 request ID（若有）。
没有可用的真实数据或 deterministic fixture 时填写 `BLOCKED`，不要猜测通过。

测试环境：____________________  测试用户：____________________  日期：____________________

## 1. Login

操作：打开 `/`，确认跳转 `/login`；输入账号密码登录；退出后再次访问私有路由。
预期：登录成功进入原目标页面或 `/dashboard`；退出后私有路由再次跳转登录；错误密码有明确反馈。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 2. Import Holdings

操作：进入 `/upload`，选择或粘贴持仓截图；等待识别；检查总资产、现金、持仓和可用数量；必要时手工修正并保存；点击“仅确认快照”。
预期：上传、解析、人工确认分阶段显示；未通过质量校验不能自动 confirmed；确认后出现 snapshot ID 和时间。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 3. Dashboard

操作：进入 `/dashboard`，切换 Portfolio；检查 Market、Portfolio、Today Decision、Candidate、System 状态。
预期：页面明确当前 Portfolio；市场和组合数据来自后端；freshness、质量和 snapshot 时间可读；无数据时有下一步操作。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 4. Manual Analysis

操作：从 Dashboard 或 Upload 选择已确认 snapshot，选择 Fast/Standard/Deep 和 checkpoint，提交一次分析；观察 queued/running/progress；刷新页面。
预期：提交中按钮锁定；刷新后任务仍能恢复；失败原因保留并可 retry；成功后可打开完整 Reports。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 5. NO_ACTION

操作：使用产生 `NO_ACTION` 的真实报告或 deterministic fixture，打开 Dashboard、Reports 和 Shadow。
预期：`NO_ACTION` 是正式最终结果；页面不显示“无数据”；Shadow 不生成订单意图。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 6. ACTION

操作：使用产生 `ACTION` 的真实报告或 deterministic fixture，查看 Final Decision、Candidate 和 Shadow observation。
预期：Final Portfolio Decision 比 Candidate 更醒目；Candidate 仅是候选；Shadow 只记录 paper intent，不触发真实订单。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 7. Candidate Veto

操作：打开一个 Candidate 曾为 ACTION、但最终 Portfolio Decision 为 NO_ACTION 的报告。
预期：页面清楚显示 Candidate veto 和原因；不把 Candidate 误报为最终动作；Shadow 不生成该候选的 execution intent。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 8. Research

操作：进入 `/research`，选择 scope、replay mode、日期、horizon，创建 Backtest；在运行中刷新、取消；对完成 Run 查看 metrics、limitations、hash 和 FULL/PARTIAL/DATA_GAP。
预期：运行状态可恢复；取消后不能继续伪装为完成；DATA_GAP 明确提示历史输入缺失，不显示“完整回测”。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 9. Governance

操作：进入 `/governance`，检查 DRAFT/REVIEW/ACTIVE/SUPERSEDED/REJECTED；对 APPROVED 版本点击激活。
预期：出现两次确认，显示版本、config hash、关键变更和 evidence；没有“AI 自动优化并启用”；激活后旧 ACTIVE 变 SUPERSEDED。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 10. System Health

操作：进入 `/system`，查看 DB、Schema、Disk、Backup、Provider、Scheduler、History 和 Live Validation Readiness；点击刷新。
预期：每项有 OK/DEGRADED/BLOCKED/UNKNOWN 和原因；Readiness 不会固定显示 READY；异常有 blocker/warning。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 11. Shadow Create

操作：选择有 confirmed snapshot 的 Portfolio，进入 `/shadow`，创建 Shadow Account。
预期：创建前看到初始化 snapshot；页面明确 SHADOW/模拟验证、不会发送真实订单；账户现金和持仓是独立副本。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 12. Shadow Pending / Fill

操作：打开一条 ACTION observation，分别查看 Decision、Execution、Outcome；在有未来合格 quote 时观察 Pending/Filled。
预期：Decision、Intent、Fill、Outcome 分开；状态原因可读；`conditional_add` 明确 V1 不模拟条件触发成交；不出现真实券商订单。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 13. Shadow DATA_GAP

操作：选择缺 mark、benchmark 或未来 quote 的 Shadow observation。
预期：显示 `DATA_GAP` 或具体缺口；收益、benchmark、drawdown 缺失时显示“不可用”，不能显示 0%；样本不足显示 `INSUFFICIENT_LIVE_EVIDENCE`。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 14. Session Expiry

操作：让 access token 过期或撤销 refresh token，再在任一私有页面刷新/发起请求。
预期：统一提示登录状态过期，清理 session 并跳转 `/login`；截图或诊断下载请求同样遵守该行为。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 15. Backend Offline

操作：打开页面后停止 backend，点击刷新或执行一个请求；恢复 backend 后点击重试。
预期：页面不白屏；显示“无法连接后端”及重试；恢复后可继续读取，不要求清理 localStorage。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 16. SPA Refresh

操作：直接打开并刷新 `/dashboard`、`/reports`、`/shadow`、`/research`、`/system`。
预期：Docker production-like 服务返回 SPA，不 404；刷新后仍由 auth guard 正确判断登录状态。
记录：`PASS / FAIL / BLOCKED`；证据：____________________

## 执行记录

| 场景 | 结果 | 时间 | 证据位置 / request ID | 备注 |
|---|---|---|---|---|
| Login |  |  |  |  |
| Import Holdings |  |  |  |  |
| Dashboard |  |  |  |  |
| Manual Analysis |  |  |  |  |
| NO_ACTION |  |  |  |  |
| ACTION |  |  |  |  |
| Candidate Veto |  |  |  |  |
| Research |  |  |  |  |
| Governance |  |  |  |  |
| System Health |  |  |  |  |
| Shadow Create |  |  |  |  |
| Shadow Pending / Fill |  |  |  |  |
| Shadow DATA_GAP |  |  |  |  |
| Session Expiry |  |  |  |  |
| Backend Offline |  |  |  |  |
| SPA Refresh |  |  |  |  |

只有所有场景由用户实际确认后，才能把 `MANUAL_UAT` 从 `REQUIRED` 更新为 `PASS`。
