# 持仓分析优化开发进度

更新日期：2026-10-03。基线：`1ef7adf`。

## 授权与完整范围

承接会话“制定持仓分析优化方案”。用户已批准开始开发，并补充了自动复盘、自学习、经验注入需求。本记录保留整份批准方案；P0—P4 已完成实现与本地验证，P5 需要真实使用观察，不能由本地测试替代。

| 阶段 | 交付范围 | 状态 |
| --- | --- | --- |
| P0 | 动作数量合同、分析状态、质量门、发布前行情刷新、候选交易校验、页面时效 | 代码完成；本地验证完成，浏览器一项经独立重跑通过 |
| P1 | 确认快照加已确认成交派生当前账户；录入/导入、去重、修订撤销、对账、账户版本 | 代码完成；本地单元/API/浏览器 mock 验证完成（无真实盘中验收） |
| P2 | 市场、财务、因子、新闻和国际事件统一证据；来源、时间与缺口追踪 | 代码完成；单元与集成验证完成（无真实数据源验收） |
| P3 | 每日持仓行动、新机会比较、条件执行、先卖后买、增量计划及失效管理 | 代码完成；单元/API/浏览器 mock 验证完成 |
| P4 | 真实账户收益、建议效果、模拟对照、昨日今日对比、真实复盘与自学习 | 代码完成；单元验证与 mock 页面验证完成（无真实账户验收） |
| P5 | 正常数据完整流程、至少十个交易日观察、用户日常使用验收 | PENDING |

## P0 实现

- 股数与目标仓位分别解析。明确的 `100股`、`1,000份` 可归一化，`20%` 仅能出现在仓位字段；拒绝比例冒充股数、数量别名冲突、非有限值、负数和分数股。
- 内容质量、行情行动证据和新增风险许可分开。字数不足不再独自阻断动作；缺引用仍降级。非市场分析角色缺失保留审计并禁止新增风险，具备必要证据的减仓继续接受独立风控校验。
- 最后报价刷新位于候选审查和组合经理之后；覆盖持仓及实际候选。持仓报价缺失失败关闭，单个候选缺价仅阻断该候选。所有报价保留来源及时间，冻结研究证据保持原样。
- 候选执行门使用服务器候选与最后报价，验证身份、停牌、涨停、报价时效、交易单位和现金；按最新价格算整手数量，共享现金预算。预计卖出资金不提前充当可用现金。
- 页面和结构化结果区分 `ACTION`、`NO_ACTION`、`WAITING`、`DATA_GAP`、`INCOMPLETE`、`EXPIRED`；缺失结论不补造“持有”。确认快照与数据新鲜度分开展示。
- 建议绑定源快照。旧来源不明时提示待核对；源快照变化或持仓数据超时显示过期。不将报价的九十秒检查窗口冒充独立的建议有效期。
- 工作流版本升级为 `v3-p0-1`，兼容工作流为 `v3-p0-legacy-1`。旧拓扑断点不能跨版本恢复，应创建新分析；同版本恢复仍重用固定证据，并拒绝过期的最终报价。

## P1 实现（当前账户事实）

### 当前账户派生

- 新增 `backend/app/portfolio/account.py`：以“最近确认快照”为基准，叠加其之后且在查询时点已经可用的已确认成交与资金事件，派生出当前持仓、可卖数量与可用资金。
- 新截图建立新基准：早于最新快照时间的流水不会再次累计；同一笔成交即使再次导入或出现在新截图里，也不会重复扣减。
- 数量语义：卖出同时扣减总持仓与可卖数量；买入增加总持仓，当日不增加可卖数量并标记 `T1_BUY_SETTLEMENT_PENDING`，次日按交易日转入可卖。卖出超过可卖数量、持仓为负、基准现金缺失等形成显式标记，而不是静默修正。
- 资金语义：当日卖出资金计入 `pending_sell_proceeds`，不充当可用资金；已交收资金才更新可用现金。资金类事件（转入/转出、股息、费用、税）按方向与金额影响可用现金。
- 冻结资金：快照没有可靠字段时不猜测，保持为空并记录缺口。
- `account_version` 由基准快照与所应用流水（含修订时间）确定性生成；账户事实任何变化都会改变版本。

### 分析、风控与建议绑定

- `build_portfolio_state` 使用派生后的数量与现金，因此组合上下文、硬约束（可卖数量/现金）、组合决策门与候选资金校验都按“成交后的当前账户”计算。
- 分析证据中的持仓数量、可用数量与现金来自派生账户；分析结果同时写入 `account_version`（`result.account_version` 与结构化载荷根字段）。
- 首页组合区块显示派生后的可用资金、待交收卖出资金与数量；`_decision_validity` 在账户版本变化时把旧建议标记为 `EXPIRED / PORTFOLIO_ACCOUNT_CHANGED`，页面显示需要复核而不是继续给出可执行动作。
- 候选扫描的“已持有标的”集合改为按派生持仓（数量大于零）计算。
- 学习样本：`DecisionMemory.portfolio_context_json` 现在携带 `account_version`，P4 自学习可以按账户版本回溯建议所基于的账户事实。

### 成交录入与流水导入

- 手动录入沿用既有 `POST /api/v3/portfolios/{id}/ledger`；修订与撤销保留逐版本审计。
- 新增 `backend/app/portfolio/imports.py` 与两个端点：
  - `POST /api/v3/portfolios/{id}/ledger/import/preview`：解析 CSV/TXT，自动识别编码（UTF-8/GB18030）与常见中英文表头，逐行给出标准化结果、校验问题与重复标记（同一订单号、账本中已有相同事实、或文件内重复）。
  - `POST /api/v3/portfolios/{id}/ledger/import`：仅写入可用行，按“来源摘要 + 事实指纹”生成幂等键；重复提交同一文件返回跳过而不是再次记账。
- 前端新增“记录成交”抽屉（`frontend/src/components/TradeLedgerDrawer.vue`）：记录列表（修订数量/价格、撤销）、手动录入（买卖与资金类）、导入流水（预览映射与重复，确认后写入）。
- 持仓页显示派生后的当前数量与“快照 X · 成交后”提示、可用资金与待交收金额。

### P1 边界

- 截图与账本的差异仍只形成待确认对账（沿用 `portfolio_snapshot_diffs`），不会自动推断成交价或时间。
- 条件单、部分成交与换仓顺序属于 P3；P1 只保证已记录事实的正确累计与幂等。
- 冻结资金与其他资金占用只有在快照或后续扩展字段提供时才展示，缺失时明确为空。

## P2 实现（统一证据）

### 证据记录与时间语义

- 新增 `backend/app/services/unified_evidence.py`。每条证据使用同一结构：`kind`、`code`、`source`、`source_url`、`evidence_type`、`published_at`、`observed_at`、`fetched_at`、`available_at`、`valid_until`、`status`、`sectors`、`gaps`、`data`，并以内容哈希作为稳定 `id`。
- `evidence_type` 区分事实（`fact`）、供应商派生统计（`provider_derived`，如资金流）与推断（`inference`，如因子与行业关联），推断不会被当成原始事实。
- 来源链接经过白名单校验：仅接受 `http/https`，拒绝带用户名、密码或查询串中出现 `token`、`key`、`secret`、`auth` 的地址，避免把凭据写进研究产物。
- 每类证据有默认有效期（报价 90 秒、K 线 7 天、资金流/因子/估值/市场上下文 1 天、财务半年、公告 7 天、新闻与国际事件 3 天），可在记录上覆盖。
- `project_unified_evidence` 做时点投影：无法证明可用时间或时间戳晚于投影时点的记录进入 `excluded` 并给出原因，已过期记录标为 `stale` 且带 `EVIDENCE_EXPIRED`，并按代码汇总 `coverage`，最后生成确定性 `evidence_version`。

### 覆盖范围与采集方式

- 记录类型覆盖：`metadata`、`quote`、`bars`、`capital_flow`、`factors`、`valuation`、`financials`、`market_breadth`、`market_turnover`、`market_indices`、`market_industry`、`announcement`、`news`、`international_event`。
- 财务与估值复用现有富曜传输与缓存（`collect_supplemental_evidence`）：元数据、批量估值、年报三表与指标；市场上下文复用 `MarketFoundationService.overview()` 的指数、涨跌家数、成交额。不新建数据源，也不改变评分。接受度模式与未配置供应商时记录缺口而不是补造数据。
- 事件关联：`_event_links` 按“明确提及标的或行业”建立事件→行业→持仓的关联，方法标记为 `explicit_security_or_sector_mention`、`causal_effect: unverified`，即“提及不等于因果”。
- 缺口可追溯：窗口内无公告、无国际事件、行业覆盖不完整、供应商不可用等都会写入 `collection_gaps`，与记录级 `gaps`、投影 `excluded` 一起构成缺口清单。
- 两层采集：缓存层复用现有进程内 TTL 缓存（`holdings-evidence:` 缓存键：估值 60 秒、财务/指标 1 小时、市场基础快照按既有有效期），分析与候选补查在缓存有效期内不重复取数；分析时按本次请求标的组装证据，候选补查通过 `merge_unified_evidence` 合并。当前没有独立的定时预取任务，是否需要由 P5 的真实使用观察决定。
- 接入点：`backend/app/services/instrument_market_evidence.py` 把 `unified_evidence` 与 `evidence_version` 写入行情快照；Agent 侧的历史 `news`/`announcements` 改为从“时点可用”的证据记录反推，因此过期事件不会进入本次分析上下文。

### P2 边界

- 本次只完成现有供应商字段范围内的补齐；北向资金等已调整披露机制的字段没有照搬旧口径，缺失时按缺口记录。
- 真实数据源连通性、抓取质量与长期缓存效果属于 P5 真实使用观察，本地测试使用夹具输入。

## P3 实现（每日操作计划）

### 计划生成与状态

- 新增 `backend/app/triggers/daily_actions.py`。以最近一份可报告分析（`RunStatus.REPORTABLE`）为来源，物化 `TriggerPlan`（`source_type=DAILY_ANALYSIS`）：报告中的持仓逐条生成，报告中缺失但账户仍持有的标的补为 `INCOMPLETE`，候选按 `CANDIDATE` 生成，完全没有标的时保留组合级 `INCOMPLETE`。
- 有效期取分析给出时间与当日收盘的较早者；重复刷新幂等，同一分析的旧计划不会被重新武装；更新分析产生新计划时，旧分析的计划保留为历史但立即停用。
- 状态与原因码：`ACTION`、`NO_ACTION`、`WAITING`、`DATA_GAP`、`INCOMPLETE`、`EXPIRED`，以及 `PLAN_SUPERSEDED`、`PLAN_VALIDITY_EXPIRED`、`EVIDENCE_CHANGED`、`EVIDENCE_EXPIRED`、`GOVERNANCE_CHANGED`、`PORTFOLIO_SNAPSHOT_CHANGED`、`PORTFOLIO_ACCOUNT_CHANGED`、`SELL_DEPENDENCY_UNFILLED`、`CONDITION_NOT_MET`、`PLAN_INVALIDATION_MET`、`OPPORTUNITY_NOT_BETTER_THAN_ALTERNATIVES` 等。
- 每日建议是派生投影，不拥有成交与现金；`evaluate_holding_plan` 对日常行动计划返回空，行动计划不会变成监控条件或触发合成条件单。

### 数量、资金与执行顺序

- 计划数量 - 已关联的已确认成交 = 剩余数量；`executable_quantity` 只在通过账户、行情、治理与组合约束后给出；多笔买入共享同一份真实可用现金预算，预计卖出资金不提前计入。
- 先卖后买：买入计划可依赖卖出计划，只有在依赖卖出已全部成交且资金真实可用后才进入可执行；部分成交按剩余数量重算，反向操作必须给出新证据并让旧计划失效。
- 成交关联（`POST /api/v3/triggers/plans/{id}/fills`）：只接受同一账户下、已确认、标的与方向匹配、时间落在计划有效期内的真实成交，且不能被其他计划重复关联；系统不会创建或推断成交。
- 复核（`POST /api/v3/triggers/plans/{id}/recheck`）：只在报价有效时记录条件观察值（含来源与时间）；账户事实变化或基准流水被修订时拒绝复核并要求重新分析（`account_change_requires_new_analysis`、`account_revision_requires_new_analysis`）。
- 新机会比较：候选与“保持现有持仓 / 保留现金 / 加仓已有 / 买入新标的”比较，比较证据缺失时记 `DATA_GAP`，机会不优于现状时记 `NO_ACTION`，不会把评分包装成盈利概率。

### 接口与页面

- 新增 `GET /api/v3/triggers/daily-plan` 与 `POST /api/v3/triggers/daily-plan/refresh`。
- 首页新增“今日行动计划”（`frontend/src/components/DailyActionPlan.vue`）：状态计数摘要、逐条计划/已成交/剩余/可执行数量、执行条件、依赖提示、有效期、复核按钮、成交关联下拉与“记录实际成交”抽屉。

## P4 实现（真实收益、复盘与自学习）

### 真实账户与建议效果分开核算

- 新增 `backend/app/memory/facts.py`：`ledger_facts_at` 按查询时点读取账本事实，并回滚该时点之后的修订、尊重撤销，历史复盘不会使用未来修改。
- 新增 `backend/app/memory/performance.py`：
  - `account_performance` 用已确认的收盘快照对计算期初/期末权益，剔除出入金，按移动加权成本计算已实现/未实现盈亏，累计费用、税费与股息，收益率使用修正 Dietz；数据不足时给出原因码并保持待补齐，不补造精确收益。
  - `performance_report` 同时给出真实账户、建议效果（标注 `is_actual_account_pnl: False`，未成交不计入真实收益，条件未触发不算预测失败）、模拟对照（`HYPOTHETICAL_LONG_VS_CASH_BEFORE_COSTS`，未计成本）、昨日今日对比（动作与数量变化）、四维复盘（事实 / 逻辑 / 执行 / 市场结果）与周度重复问题（按 交易日+标的+维度 去重）。

### 经验库与角色注入

- 新增 `backend/app/memory/learning.py`：经验以 `LearningHypothesis` 版本化保存结论、适用范围（市场状态 / 标的类型 / 动作 / 周期 / 角色）、支持证据、反例、检验结果、权重、状态（`pending`/`reference`/`verified`/`disabled`）、形成时点与可用时间。
- 检验只用独立后续样本（正向前推）：按 5 日窗口、现金流口径的对/现金粗收益模拟，拒绝条件未观测与未触发样本，同日同标的去重；输出明确 `validation_basis`，并声明 `model_weights_changed: False`、策略参数变更需走治理流程。
- `build_learning_context` 按当前市场状态、标的类型与动作过滤，只取检验时点已可用的 `reference`/`verified` 经验，生成固定 `context_id`；`for_role` 把工作流节点映射到角色，未知研究角色不获得不相关经验。
- 引用记录：`AnalysisJob` 绑定分析结果中的 `learning_context_id` 与 `learning_refs`；true multi-agent 路径按节点角色写入 `LearningContextReference`，并强制 `available_at <= 截止时点`，历史回放只能看到当时存在的经验。分析提示词明确“经验是历史建议，不是当前事实，不得覆盖当前门控”。
- 自动闭环：15:30 调度复盘在原有结果核算基础上刷新学习假设并附加绩效报告；1/5/20 交易日结果窗口沿用既有 `DecisionOutcome`。
- 迁移：`backend/alembic/versions/20261003_0023_learning_hypotheses.py` 新增经验、检验与引用三张表。
- 接口：`GET /api/v3/portfolios/{id}/memory/performance`、`GET /api/v3/portfolios/{id}/memory/learning`、`POST /api/v3/portfolios/{id}/memory/learning/refresh`。
- 页面：首页“昨日执行与今日变化”（紧凑）与历史页“真实账户与建议复盘”（完整）复用 `frontend/src/components/PortfolioReviewPanel.vue`，展示真实账户指标、费用/税费/股息、建议效果、昨日今日变化、自学习经验（含状态与适用条件）与模拟对照明细。

### 本轮修复的集成缺陷

- true multi-agent 编排逐节点写学习引用时使用了 `AnalysisRun.portfolio_id`（该字段实际在 `AnalysisJob` 上），导致所有节点以 `non_retryable` 失败、整轮分析以 `blocked` 结束；改为读取作业的持仓归属后恢复完成态。
- 复盘面板读取的经验标题/适用范围字段名与接口返回不一致（`conclusion`/`scope_summary` 对 `statement`/`scope`），经验标题会显示为空；已按接口字段读取。
- 行动计划的状态计数摘要是“状态→数量”映射，页面直接渲染会显示原始对象；改为中文计数摘要。
- `test_daily_action_plans.py` 中一个未完成用例引用了未定义变量；补齐为“重复刷新幂等且不重新武装”，并为“新计划启用、旧计划停用”补上缺失断言。
- true multi-agent 工作流版本断言与文档仍写 `v3-p0-1`，与当前 `v3-holdings-p4-1` 不一致；同步更新。

## 自学习范围保留

P1 开始保存学习样本，包括建议、理由、市场和账户版本、触发条件、实际成交、模型及策略版本。P4 复用现有历史案例、结果核算、15:30 复盘与调度，不建立第二套事实来源。以下为实现范围与验收口径：

完整自学习验收包括：

1. 每日可执行性检查，建议后第 1、5、20 个交易日等到期窗口评估，以及周度重复问题汇总；缺数据保持待评估。
2. 分开评估事实、逻辑、执行和市场结果；未成交不记作真实收益，条件未触发不直接记作预测失败。
3. 结构化经验保存适用范围、支持证据、反例、版本、可用时间和待验证/可参考/已验证/停用状态。
4. 构建版本固定的 `learning_context`，按角色检索经验并记录引用；历史回放只读取当时可用经验。
5. 使用未参与提炼的后续样本比较有无经验的表现，去重同日同标的重复建议，保留失效降权与停用记录。
6. 模型权重不自动更新；仓位上限、策略参数等变更走已有治理与回滚流程。

## 验证与边界

联合验证使用独立临时数据库和模拟模型/行情输入；浏览器验证使用独立本地页面与 mock API。不将这些结果视为真实盘中数据、策略收益或生产验收。没有部署或提交。

早期一轮子测试因导入顺序错误使用了默认数据库，写入了 28 个测试账号及关联记录。已完成以下处置：

- SQLite 在线备份保存到 `backend/data/backups/p0-before-test-cleanup-20261002-6550f380.db`，备份完整性检查通过。
- 逐项核对测试账号标识、创建时间、外键闭包和上传内容后，单事务删除九张表内明确新增的 252 条记录；未重置自增序列。
- 28 个测试上传文件（合计 504 字节）已移动到 `backend/data/backups/p0-test-uploads-20261002` 保留，未删除用户图片。
- 回滚前后，除上述目标记录外的全部数据表行数及内容摘要完全一致；外键检查和 SQLite 完整性检查通过。
- 证券主表相关行及旧持仓、旧报告与现有旧备份一致。没有本次测试前的结构快照，因此不能确认 `init_db` 是否新增过表或字段；未擅自删除表或回退结构。
- 处置收据：`backend/data/backups/p0-test-cleanup-receipt-20261002.json`。
- 新增测试入口隔离，确保测试模块收集前就设置临时数据库、产物和备份目录，拒绝默认用户存储路径；关闭调度器并保留失败诊断目录。

最终验证结果：

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 受影响后端模块联合回归、隔离与归档兼容 | 334 passed；仅既有 TestClient 弃用提示 | `output/p0-backend-regression-20261002.log` |
| 前端类型检查与最终构建 | 通过；保留既有大包提示 | `output/playwright/p0-verified-20261002/frontend-build.log` |
| 完整浏览器验收套 | 43/44 passed；一项功能断言通过但控制台出现 `ERR_NO_BUFFER_SPACE` | `output/playwright/p0-verified-20261002/playwright.log` |
| 原失败浏览器用例独立新环境重跑 | 1/1 passed，无代码修改；资源错误未能归因，不宣称整套一次全绿 | `output/playwright/p0-identity-recheck-20261002/playwright.log` |
| 新状态、页面退出竞态、首页来源一致性定向验证 | 12/12 passed | `frontend/e2e/advice-validity.spec.ts`、`analysis-lifecycle.spec.ts`、`dashboard-decision-source.spec.ts` |
| 差异格式 | `git diff --check` 通过 | 当前工作区 |
| P1 账户派生、重复导入、修订撤销与账户版本变更 | 8 passed | `backend/tests/test_portfolio_account.py` |
| P1 账户/导入 API（含幂等重复提交） | 1 passed | `backend/tests/test_ledger_import_api.py` |
| P1 受影响后端联合回归（P0 模块 + P2 前新测试） | 321 passed；无失败 | 本次运行，未写入固定日志文件 |
| P1 前端类型检查与构建 | 通过；保留既有大包提示 | `npm run typecheck`、`npm run build` |
| P1 浏览器用例：成交录入、派生数量、导入预览去重 | 2 passed | `frontend/e2e/trade-ledger.spec.ts` |
| mock 浏览器用例回归（构建产物预览服务器） | 15/15 passed | `advice-validity`（含新增账户变化用例）、`dashboard-decision-source`、`analysis-lifecycle`、`trade-ledger` |
| P2/P3/P4 后端用例：统一证据、行动计划、学习与绩效 | 58 passed | `backend/tests/test_unified_evidence.py`、`test_daily_action_plans.py`、`test_learning_performance.py` |
| true multi-agent、P0 集成与导出回归 | 52 passed（修复引用作业归属前为 19 失败） | `test_true_multi_agent_workflow.py`、`test_true_multi_agent_safety.py`、`test_decision_p0_integration.py`、`test_analysis_run_export.py` |
| 全量后端回归 | 959 passed / 1 failed；唯一失败为下方已知夹具问题 | `output/p2p4-backend-regression-20261003c.log` |
| P3/P4 行动计划与复盘页面用例（构建产物预览服务器） | 17/17 passed | `frontend/e2e/daily-plan-review.spec.ts`、`advice-validity`、`dashboard-decision-source`、`analysis-lifecycle`、`trade-ledger` |
| 真实前后端启动 | 后端 18000 `/healthz` 200；前端 5173 登录页渲染正常，`/api` 代理直达后端 | 本次运行 |
| 真实数据库迁移 | 由启动预检自动完成 0020 → 0023，并生成升级前备份 | `backend/data/backups/backup_20261003_065045_213a1e4a.sqlite` |

临时测试输出 `frontend/test-results/.last-run.json` 的清理被自动安全检查拒绝，未提供具体原因，文件保留。

基线环境下的 `tests/test_v2_portfolio_analysis.py::test_v2_portfolio_flow` 失败：该用例使用无有效行情的测试证券（600002 报价 STALE、510002 MISSING），被 P0 的“必需持仓缺价失败关闭”判定阻断为 `watch_only`。该检查位于 P0 的行情刷新与决策门，不是本轮引入；本次未修改该用例，也未放宽失败关闭规则，作为待处理的测试夹具问题保留。

用户真实数据库已按项目自带流程升级到 `20261003_0023`（升级前生成 `backend/data/backups/backup_20261003_065045_213a1e4a.sqlite` 与校验记录），前端 5173 与后端 18000 已启动供查看；后端本次以 `SCHEDULER_ENABLED=false` 运行，不代表已开启定时任务。

P5 仍需真实使用验收。本地验证使用夹具输入、独立临时数据库与 mock 页面，不能替代真实行情、真实账户与连续交易日观察。没有进行生产部署或真实盘中验收。
