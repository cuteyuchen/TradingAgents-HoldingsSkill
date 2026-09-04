# Phase O.2 UX Implementation Map

## 目标与边界

本轮把前端从技术后台的信息架构调整为单用户投资驾驶舱。地图只描述前端能力迁移，不改变后端 User/Auth/Ownership、投资算法、Decision Contract、Shadow Contract、数据库或 Alembic schema。

设计输入：

- `New_UI_Wireframe.md`
- `Single_User_Investment_Workbench_Product_Design.md`
- `UI_Architecture_Comparison_Report.md`

执行优先级：线框与单用户产品设计高于当前旧 UI；参考仓库只提供信息架构和交互启发，不迁入其额外业务复杂度。

## 页面与能力迁移

| 旧页面 / 组件 | 新页面 | 旧能力去向 | API 复用 | 默认隐藏 / 下沉 | Deep-link |
|---|---|---|---|---|---|
| `DashboardView.vue` | `/dashboard` 首页 | 今日市场、最终组合决策、组合摘要、候选机会、数据新鲜度、首次使用清单 | `getDashboardToday`、`listPortfolios`、`listProfiles`、`listSchedules`、`getSystemReadiness` | 工程状态、工作流节点、通知与原始 score decomposition 下沉到技术详情或设置 | 保留 `/dashboard`，`?portfolio=` 继续选择组合 |
| `UploadView.vue` | `/holdings` 持仓页中的更新持仓 Drawer / page-local flow | 上传截图、识别轮询、人工核对、保存修正、质量门控、确认 snapshot、发起分析 | `uploadHoldings`、`getUpload`、`updateParsedHoldings`、`retryUploadParse`、`confirmUpload`、`getSnapshot`、分析 job API | upload id、raw OCR、parse code、source lineage 默认隐藏 | `/upload` 重定向到 `/holdings?action=update`，保留 `portfolio`、`job`、`focus` |
| `ReportsView.vue` | `/analysis` 今日分析 | 报告列表、报告详情、Final Portfolio Decision、holding actions、candidate、veto、结构化证据、Markdown、截图、比较 | `listRuns`、`getRun`、`compareRun`、`getSnapshot`、`getUploadImage` | analyst / agent / token / runtime / hash / raw JSON 默认折叠 | `/reports` 重定向到 `/analysis`，保留 `portfolio`、`run` |
| `ShadowValidationView.vue` | `/simulation` 模拟跟随 | Shadow account 创建、暂停/恢复、rebase、Decision/Execution/Fill/Outcome、performance、校验、未来行情事实 | Shadow account、decision、order、fill、daily、performance、validation API | execution contract、observation hash、lineage、内部 account 字段进入技术详情 | `/shadow` 重定向到 `/simulation`，保留 `portfolio`、`shadow` |
| `ResearchView.vue` | `/history?tab=research` 策略研究 Tab | replay availability、PIT capability、backtest 创建/取消/恢复、calibration 结果 | `getReplayAvailability`、`getRecomputeCapability`、backtest / calibration API | PIT、Capability、Manifest、Hash、engine 版本折叠 | `/research` 重定向到 `/history?tab=research`，保留原 query |
| `GovernanceView.vue` | `/settings?section=strategy` 策略参数（高级） | Parameter registry、版本、Proposal、Review、Validate、Activate、Rollback、事件证据、二次确认 | 全部 governance API | 仅高级设置展示；禁止 Auto Apply，保留 DRAFT/REVIEW/ACTIVE/SUPERSEDED/REJECTED | `/governance` 重定向到 `/settings?section=strategy` |
| `SystemView.vue` | `/settings?section=system` 系统状态（高级） | health、readiness、Live Validation readiness、backup、restore drill、diagnostics、history coverage/sync | 全部 system 与 history API | schema、scheduler、worker、runtime、Git SHA、provider diagnostics 折叠 | `/system` 重定向到 `/settings?section=system` |
| `SettingsView.vue` | `/settings` 设置 | provider、model profile、自动分析 schedule、通知、组合入口、外观 | model settings、schedule、notification、portfolio API | 邮箱、用户中心、内部 id、secret 不回填 | `/settings` 直接保留；`section` 控制高级分区 |
| `App.vue` | 全局 App Shell | 登录态恢复、portfolio context、主题切换、退出登录、全局消息与路由承载 | `me`、`listPortfolios`、`logout`、session refresh | email、User ID、大号 Portfolio context bar、System 一级入口、用户管理入口 | `/` 仍重定向 `/dashboard`；旧入口由 router alias 兼容 |
| `router.ts` | 六个用户路由 + alias | lazy route、auth guard、session expiry、SPA refresh | 不新增后端请求 | 旧技术页面不再出现在主导航 | `/dashboard`、`/holdings`、`/analysis`、`/simulation`、`/history`、`/settings` |

## 新一级信息架构

```text
首页       /dashboard
持仓       /holdings
分析       /analysis
模拟       /simulation
历史       /history
设置       /settings
```

旧链接继续有效并只做导航迁移：

```text
/upload      -> /holdings?action=update
/reports     -> /analysis
/shadow      -> /simulation
/research    -> /history?tab=research
/governance  -> /settings?section=strategy
/system      -> /settings?section=system
```

Redirect 必须保留原 query 中的 `portfolio`、`run`、`job`、`focus`、`shadow` 等有意义参数，不能破坏已有自动化测试与用户收藏链接。

## 生产能力保留确认

- 持仓确认仍经过 `Upload -> Parse -> Review/Edit -> Confirm`，低质量识别不会自动确认。
- 分析最终决策仍来自后端 `DecisionMemory` / analysis run；前端不调用 LLM 二次生成结论。
- Candidate 仍是候选层，Portfolio Gate 仍具有最终优先级；Candidate Veto 继续显式展示。
- Shadow 仍是 paper-only；Decision、Execution、Outcome 独立展示，`conditional_add` 继续是只记录建议。
- Research 仍是研究与校准流程，不自动修改生产参数。
- Governance 仍需要人工 Review、二次确认和显式 Activate，不能 Auto Apply。
- System 仍读取真实 Live Validation readiness，并继续显示 `NOT_READY` 与 actionable blockers。
- 旧 View 文件在迁移期保留作为能力参考与回滚边界；主 router 不把旧页面同步 import 到初始 bundle。

## 请求预算约束

- 首页每次组合切换只请求一次 `getDashboardToday`，系统设置与组合列表不在子卡片重复请求。
- 持仓页以一次 snapshot 请求作为表格事实来源，更新 Drawer 只在进入流程后请求 upload/image。
- 分析页按组合请求 runs，选中 run 后再请求一次 detail；截图只在用户打开相关区域时请求。
- 模拟页按组合请求 account/validation，选中 account 后请求 performance 与列表事实，不在多个摘要卡重复调用。
- 历史与设置的高级区块按 Tab/section 加载，避免首页或普通设置首屏拉取完整运维数据。

## 视觉与文案验收边界

- Light 是无已保存 preference 时的默认主题，Dark 与 Light 使用同一组语义 token。
- A 股收益使用涨红跌绿；风险使用独立 risk 语义颜色，不能复用收益颜色表达风险。
- 首屏优先展示用户语言；技术 code、hash、runtime、schema、source lineage 进入 `TechnicalDetails` / Collapse。
- Empty、Loading、Error、DEGRADED、Offline 状态都必须给出发生了什么、原因和下一步。
- 1440x900 是基准；1366x768 不横向滚动 header，1920x1080 内容宽度保持约 1320~1360px。

