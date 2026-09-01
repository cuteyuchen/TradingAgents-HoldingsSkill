# Phase O Manual UAT

这份清单写给实际使用系统的人。Phase O.1 的自动化浏览器 acceptance 已通过，但
它不替代目标浏览器、真实部署和用户本人确认。请逐项填写“实际”和“结果”，附截图、
时间和 request ID（若有）；没有可用的真实数据时填写 `BLOCKED`，不要猜测通过。

测试环境：____________________  测试用户：____________________  日期：____________________

## 1. Login

操作步骤：打开 `/`，确认跳转 `/login`；输入账号密码登录；退出后再次访问私有路由；
再用错误密码登录一次。

预期：登录成功进入原目标页面或 `/dashboard`；退出后私有路由跳转登录；错误密码有明确反馈。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 2. Import Holdings

操作步骤：进入 `/upload`，选择或粘贴持仓截图；等待识别；检查总资产、现金、持仓和
可用数量；必要时手工修正并保存；点击“仅确认快照”。

预期：上传、解析、人工确认分阶段显示；未通过质量校验不能自动 confirmed；确认后出现
snapshot ID 和时间。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 3. Dashboard

操作步骤：进入 `/dashboard`，切换至少两个 Portfolio；检查 Market、Portfolio、Today's
Decision、Candidate、System Health、时间和 freshness。

预期：页面明确当前 Portfolio；数据来自后端；freshness、质量和 snapshot 时间可读；无
数据时有下一步操作；昨天的 ACTION 不冒充今天结果。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 4. Manual Analysis

操作步骤：从 Dashboard 或 Upload 选择已确认 snapshot，选择 Fast/Standard/Deep 和
checkpoint，提交一次分析；观察 queued/running/progress；刷新页面。

预期：提交中按钮锁定；刷新后任务仍能恢复；失败原因保留并可 retry；成功后可打开完整
Reports。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 5. Reports

操作步骤：打开 `/reports` 的 list 和 detail，查看完整分析流程、结构化证据和原始报告；
核对 checkpoint、mode、holding actions、Candidate、quality、market context、lineage。

预期：Final Portfolio Decision 比 Candidate 更明确；detail 不是只有 HTTP 200；关键证据
和数据缺口可见。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 6. NO_ACTION

操作步骤：打开产生 `NO_ACTION` 的报告、Dashboard 和 Shadow。

预期：正式显示 `NO_ACTION` 及 reason，不显示“暂无结果”；Shadow 不生成订单 intent。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 7. ACTION

操作步骤：打开产生 `ACTION` 的报告，查看 Final Decision、Candidate、Decision observation
和 Shadow。

预期：Final Portfolio Decision 与 Candidate 分开；Candidate 不是最终组合动作；Shadow
只记录 paper intent，不发送真实订单。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 8. Candidate Veto

操作步骤：打开一个 Candidate 为 `ACTION`、但 Portfolio Decision 为 `NO_ACTION` 的报告。

预期：页面显示 Candidate Veto 和原因；不把 Candidate 报成最终动作；Shadow 不生成该
候选的 candidate-driven execution intent。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 9. Research

操作步骤：进入 `/research`，选择 scope、replay mode、日期和 horizon，创建 Backtest；
在运行中刷新、取消；对完成 Run 查看 metrics、limitations、hash 和 FULL/PARTIAL/DATA_GAP。

预期：运行状态可恢复；取消后不能继续伪装为完成；`PARTIAL_PIT_RECOMPUTE` 明确提示
历史输入缺失，不显示“完整 PIT 重算”。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 10. Governance

操作步骤：进入 `/governance`，检查 proposal/detail、DRAFT/REVIEW/ACTIVE/SUPERSEDED/
REJECTED；对 APPROVED 版本点击激活。

预期：出现二次确认，显示 parameter version、config hash、关键变更和 evidence；没有
Auto Apply；请求 `emergency_override=false`。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 11. System

操作步骤：进入 `/system`，查看 DB、Schema、Disk、Backup、Provider、Scheduler、History
和 Live Validation Readiness；点击刷新。

预期：每项有 OK/DEGRADED/BLOCKED/UNKNOWN 和原因；真实 readiness 为 `NOT_READY` 时
显示 blockers，不隐藏或误导成 READY。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 12. Shadow

操作步骤：选择有 confirmed snapshot 的 Portfolio，进入 `/shadow`，创建 Shadow Account；
打开 ACTION observation，分别查看 Decision、Execution、Outcome；检查 PENDING、FILLED、
BLOCKED、EXPIRED 和缺失数据场景。

预期：页面明确 `SHADOW / 模拟验证` 和“不会发送真实订单”；Decision、Intent、Fill、
Outcome 分开；`conditional_add` 明确 V1 不模拟；`DATA_GAP` 不显示 0；`NO_ACTION` 不生成 intent。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 13. Settings

操作步骤：进入 `/settings`，查看模型、计划和通知配置；修改非敏感字段并保存；检查页面
刷新和错误重试。

预期：保存有 loading/disable；secret 不回填完整值；失败不清空用户输入；没有 Auto Trading
或 Auto Learning 开关。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 14. Backend Offline

操作步骤：打开页面后停止 backend，点击刷新或执行一个请求；恢复 backend 后点击重试。

预期：页面不白屏；显示无法连接后端和重试；恢复后可继续读取，不要求清理 localStorage。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 15. Session Expired

操作步骤：让 access token 过期或撤销 refresh token，再在任一私有页面刷新或发起请求；
同时验证报告截图/诊断下载请求。

预期：access 失效且 refresh 成功时用户不中断；两者都失效时统一清理 session，跳转
`/login?expired=1`；没有 infinite refresh loop、白屏或 toast 风暴。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 16. Deep Link Refresh

操作步骤：直接打开并刷新 `/dashboard`、`/reports`、`/shadow`、`/research`、`/system`、
`/settings`。

预期：production-like 服务不返回 404；刷新后仍由 auth guard 正确判断登录状态，页面能正常加载。

实际：________________________________________________________________________________

结果：`PASS / FAIL / BLOCKED`；证据：________________________________________________

## 执行记录

| 场景 | 结果 | 时间 | 证据位置 / request ID | 备注 |
|---|---|---|---|---|
| Login |  |  |  |  |
| Import Holdings |  |  |  |  |
| Dashboard |  |  |  |  |
| Manual Analysis |  |  |  |  |
| Reports |  |  |  |  |
| NO_ACTION |  |  |  |  |
| ACTION |  |  |  |  |
| Candidate Veto |  |  |  |  |
| Research |  |  |  |  |
| Governance |  |  |  |  |
| System |  |  |  |  |
| Shadow |  |  |  |  |
| Settings |  |  |  |  |
| Backend Offline |  |  |  |  |
| Session Expired |  |  |  |  |
| Deep Link Refresh |  |  |  |  |

只有所有场景由用户实际确认后，才能把 `MANUAL_UAT` 从 `REQUIRED` 更新为 `PASS`。
