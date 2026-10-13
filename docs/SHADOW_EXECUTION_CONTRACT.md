# Shadow Execution Contract

版本：`shadow-execution-v2`

## Scope

这是生产决策的纸面验证合同，不是券商执行合同。它只允许在独立
`ShadowAccount` 中记录模拟意图、模拟成交、现金和持仓变化。

明确禁止：

- Broker API、真实下单、自动交易或半自动实盘执行
- 修改真实 `PortfolioSnapshot`、真实持仓、真实 broker cash 或 `TradeLedger`
- 用 Shadow 持仓反向影响生产 Market / Candidate / Portfolio Decision
- 由 LLM 决定成交价、成交数量或成交状态

## Base Facts

create / rebase 只能使用归属当前用户和组合的 confirmed snapshot，且
`snapshot_time <= cutoff`、`created_at <= cutoff` 必须同时成立，等于 cutoff
允许使用。自动选择会跳过最新但不可信或不可见的 snapshot；显式指定不合格
snapshot 时拒绝操作。

manual 等非截图来源沿用明确的事实时间。截图的 confirmed 状态本身不能证明
观察时间；必须通过统一 `observation_time_status()` 校验，包含带时区的
`observed_at`、`USER_CONFIRMED` 标记且与 `snapshot_time` 一致。旧截图或不匹配
的确认时间属于 `UNVERIFIED`，不能作为初始化或 rebase 基准。

初始 cash 和每项总 quantity 必须已知、有限且非负；未知或无效时拒绝操作，
不得造为 0，也不得先递增 generation。已知的零现金、零数量仍是合法事实。
`available_qty` 必须有限且满足 `0 <= available_qty <= quantity` 才是合法可卖
事实。未知、负数、非有限或超过总数量的 `available_qty` 只证明可卖下界 0，
不得夹成“已知全可卖”，也不等于已知实际可卖数量为 0：
ledger、rebuild 和物化 metadata 保留 `sellable_quantity_known=false`。API 返回
`sellable_quantity=null`，用 `sellable_quantity_minimum` 单独表达可证明下界。
重建旧 `INITIAL_POSITION` 时对 `sellable_quantity` 使用相同上下界校验；
不合格记录在派生状态中保持未知且下界 0，不修改原 snapshot 或 ledger。

可卖事实不完整不禁止基于已知现金和数量的合法 BUY；新买入且完成结算的数量
可增加可证明下界。SELL 超过该下界时保持 `PENDING` 并标记
`SHADOW_BASE_FACTS_DATA_GAP`，只有完整可信 snapshot 的显式 rebase 才能建立
新的初始可卖事实。不得修改旧 ledger 或 fill 来补齐历史基准。

## Decision Boundary

生产最终决策的权威来源是成功 `AnalysisRun` 的
`structured_result_json["result"]`，并以 `decision_gate` 的最终组合动作作为
`LiveDecisionObservation.final_action`。Candidate `ACTION` 不等于最终动作。

只有 `final_action=ACTION` 且动作可被 V2 无条件执行时才能生成
`ShadowOrderIntent`。`conditional_add` 继续记录 Observation/Outcome，但 V2
不支持把触发条件降级成即时 BUY，必须标记
`CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED` 且不生成普通 intent。`NO_ACTION`
也不生成 intent，但必须生成独立的 `LiveDecisionOutcome` 样本。

## Time And Quote Proof

所有内部 `DateTime` 使用 UTC-naive 约定。每个 fill 必须满足：

1. `quote.captured_at > intent.decision_finalized_at`
2. `captured_at_precision == EXACT`
3. quote 为持久化 `LiveQuoteObservation`，质量为 `VALID` 或策略允许的 `DEGRADED`
4. 价格大于零、标的仍 active、没有 suspension 或不可成交的涨跌停
5. quote 在 intent 的有效期内且不晚于当前处理时点
6. quote 所在日期有明确的开市日历，并且统一市场会话判定 `is_market_open=true`

会话校验使用 UTC-aware quote 时间进入 `MarketSessionService`。盘前、午休、
盘后和非交易日 quote 均不能成交；在原有效期内出现后续开市时段合法 quote
可以恢复。缺日历时标记 `SHADOW_CALENDAR_DATA_GAP` 并等待补齐，不猜测次日或
延长原有效期。已完成 fill 的时间和价格不随日历修订改变。

系统只能使用应用捕获并持久化的 quote 证明未来时间。provider 的 source
timestamp、旧 close、reference price、latest-only 数据都不能单独作为成交依据。

### As-Of Mark Visibility

当估值带有 `as_of`/cutoff 时，DailyBar 和 benchmark 记录必须同时满足：
`available_at` 非空、`available_at <= cutoff`，并通过对应质量检查；LiveQuote
必须满足 `captured_at <= cutoff`、时间精度和质量检查。`available_at=NULL` 表示
无法证明 DailyBar/benchmark 在历史时点可见，不得在 as-of 查询中视为随时可见。
决策日 Portfolio reference equity 优先使用 `captured_at <= decision_finalized_at`
的合法 LiveQuote；后来写入的当日 EOD DailyBar 不得倒灌到盘中 reference equity。
如果没有可证明的 mark，必须记录 `SHADOW_MARK_DATA_GAP`，保持
`PENDING/DATA_GAP`，不得计算 Portfolio outcome。

所有用于精确 cutoff 的 LiveQuote mark 都必须是 `EXACT`，不能用 `MINUTE` 或
更粗精度补足权益。目标权重 sizing 和 hard cap 使用同一候选执行时点的完整
Shadow equity；任一持仓缺合格 mark 时继续等待。未来 mark 不能倒灌修补较早
quote，但后续权益完整的合法 quote 可以恢复成交。

### Intraday

盘中决策从 `decision_finalized_at` 之后的第一个合法 quote 开始等待和成交。
决策前 quote 即使价格更接近，也必须忽略。

### EOD

本地时间达到 15:00 后完成的决策，其 `earliest_executable_at` 推迟到下一个
交易日开盘。15:10 Deep 使用当天收盘数据时，不得使用当天 close 模拟成交。
默认有效期为下一个交易日收盘；没有合格未来 quote 时保持 pending，过期后
转为 `EXPIRED`。

## Eligibility And Lifecycle

Intent 生命周期为：

`PENDING -> FILLED | BLOCKED | EXPIRED | SUPERSEDED`

V2 不主动生成 synthetic `PARTIAL`。历史兼容字段可以保留，但 Shadow 自身
持仓不足不能猜测部分成交。

暂停账户不推进成交；generation 不匹配的旧 intent 标记为
`SUPERSEDED`。常见阻断原因包括：

- `WAITING_FOR_FUTURE_QUOTE`
- `SHADOW_REBASE_REQUIRED`、`SHADOW_BASE_FACTS_DATA_GAP`
- `SHADOW_CALENDAR_DATA_GAP`、`SHADOW_MARK_DATA_GAP`、`QUOTE_OUTSIDE_TRADING_SESSION`
- `QUOTE_TIME_NOT_EXACT`、`QUOTE_INVALID`、`QUOTE_DATA_GAP`
- `SUSPENDED`、`INSTRUMENT_INACTIVE`
- `BLOCKED_BY_LIMIT_UP`、`BLOCKED_BY_LIMIT_DOWN`
- `SHADOW_CASH_BLOCKED`、`BLOCKED_BY_SHADOW_SELLABLE_QTY`
- `LOT_SIZE_BLOCKED`、`BLOCKED_BY_SHADOW_CONSTRAINT`

涨停买入和跌停卖出不得按限制价假装成交。停牌在有效期内等待后续合法
quote；恢复前不成交。

## Quantity, Settlement And Costs

- A-share stock 和 ETF 买入按 `SecurityMaster.lot_size`，缺省为 100 股整数手。
- Stock 买入采用 T+1；当 ETF 没有权威 T+0/T+1 元数据时采用
  `T_PLUS_1_CONSERVATIVE`。
- 买入只消耗 Shadow 自己的可用现金；真实账户现金永远不可借用。
- 卖出只使用 Shadow 自己的 `sellable_quantity`；请求数量超过可卖数量时记录
  `BLOCKED_BY_SHADOW_SELLABLE_QTY`，不生成 fill。若初始可卖事实未知，则超过
  可证明下界属于待补事实，不把它标记为已知数量不足。
- commission、minimum commission 和 sell tax 直接复用 Phase E
  `transaction_cost_estimate()`。
- V2 不建模滑点和盘口冲击；记录 `slippage_not_modeled=true`，并可展示
  `execution_delay_price_drift`，该字段不称为 synthetic slippage。

## Ledger And Rebuild

`ShadowLedgerEntry` 是 append-only 事实，包含初始现金/持仓、买入、卖出和
成本对应的 cash delta。`ShadowPosition` 和 `ShadowAccount.current_cash` 是
物化状态，不是唯一事实，必须能够由指定 `shadow_generation` 的 ledger rebuild。

每次 create / rebase 使用新的 generation。旧 generation 的 order、fill、ledger
和 daily snapshot 永久保留，绩效查询不得把不同 generation 混算。

## Outcomes And Alignment

`LiveDecisionOutcome` 独立于 fill，按 1/5/10/20/60 个交易日评估 security、
portfolio、NO_ACTION 和 Candidate Veto。Portfolio forward return 必须使用对应
Shadow Account + Generation 在 reference 时点与目标交易日的 equity；All-A
Median benchmark 独立计算，不能用 benchmark 代替 portfolio return。交易日
来自 `TradingCalendar`，未来 close 仅用于 outcome evaluation，不是 fill source。
`execution_eligible` 只由真实 Shadow Intent、合法 future quote 和最终 fill/
blocked/expired 状态推导；无 future quote 不得标记为 true。Validation summary
按 target 与 horizon 分桶，不跨样本混成一个 mean。

每日 Shadow snapshot 对每个非零持仓必须有可靠且对 snapshot cutoff 可见的
DailyBar/LiveQuote mark；任一持仓缺 mark 时 fail-close 为
`SHADOW_MARK_DATA_GAP`，不得发布虚假 equity。Benchmark 同样必须通过质量与
`available_at` 可见性检查；缺失保持 `None`，对应 excess 也保持 `None`，不得
按 0% 累计。

`DecisionActualAlignment` 只匹配确认的 `TradeLedgerEntry`，使用同 code/side
和当前交易日加下一个交易日的 timestamp window。它只增加研究维度，不修改
Shadow ledger，也不从 holdings diff 推断用户行为。

## Versioning

该合同版本只描述 Shadow 执行语义，不改变 Runtime Contract 或 Decision Contract
`2.4.0`，也不改变 Phase C/F/M 的投资评分、阈值和组合决策规则。

旧 `shadow-execution-v1` 账户的新 pending 推进必须保持等待并标记
`SHADOW_REBASE_REQUIRED`。只有显式 rebase 通过可信时间、可见性和基础事实
校验，追加新 generation 后才升级到 V2；不能只改版本号沿用旧 seed。
旧 generation 的 fill、ledger 和已完成 order 保留原事实，不自动迁移或重写。
