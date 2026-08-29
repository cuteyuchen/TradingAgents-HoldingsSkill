# Shadow Execution Contract

版本：`shadow-execution-v1`

## Scope

这是生产决策的纸面验证合同，不是券商执行合同。它只允许在独立
`ShadowAccount` 中记录模拟意图、模拟成交、现金和持仓变化。

明确禁止：

- Broker API、真实下单、自动交易或半自动实盘执行
- 修改真实 `PortfolioSnapshot`、真实持仓、真实 broker cash 或 `TradeLedger`
- 用 Shadow 持仓反向影响生产 Market / Candidate / Portfolio Decision
- 由 LLM 决定成交价、成交数量或成交状态

## Decision Boundary

生产最终决策的权威来源是成功 `AnalysisRun` 的
`structured_result_json["result"]`，并以 `decision_gate` 的最终组合动作作为
`LiveDecisionObservation.final_action`。Candidate `ACTION` 不等于最终动作。

只有 `final_action=ACTION` 才能生成 `ShadowOrderIntent`。`NO_ACTION` 不生成
intent，但必须生成独立的 `LiveDecisionOutcome` 样本。

## Time And Quote Proof

所有内部 `DateTime` 使用 UTC-naive 约定。每个 fill 必须满足：

1. `quote.captured_at > intent.decision_finalized_at`
2. `captured_at_precision == EXACT`
3. quote 为持久化 `LiveQuoteObservation`，质量为 `VALID` 或策略允许的 `DEGRADED`
4. 价格大于零、标的仍 active、没有 suspension 或不可成交的涨跌停
5. quote 在 intent 的有效期内且早于当前处理时点

系统只能使用应用捕获并持久化的 quote 证明未来时间。provider 的 source
timestamp、旧 close、reference price、latest-only 数据都不能单独作为成交依据。

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

`PENDING -> FILLED | PARTIAL | BLOCKED | EXPIRED | SUPERSEDED`

暂停账户不推进成交；generation 不匹配的旧 intent 标记为
`SUPERSEDED`。常见阻断原因包括：

- `WAITING_FOR_FUTURE_QUOTE`
- `QUOTE_TIME_NOT_EXACT`、`QUOTE_INVALID`、`QUOTE_DATA_GAP`
- `SUSPENDED`、`INSTRUMENT_INACTIVE`
- `BLOCKED_BY_LIMIT_UP`、`BLOCKED_BY_LIMIT_DOWN`
- `SHADOW_CASH_BLOCKED`、`SHADOW_SELLABLE_QTY_BLOCKED`
- `LOT_SIZE_BLOCKED`、`BLOCKED_BY_SHADOW_CONSTRAINT`

涨停买入和跌停卖出不得按限制价假装成交。停牌在有效期内等待后续合法
quote；恢复前不成交。

## Quantity, Settlement And Costs

- A-share stock 和 ETF 买入按 `SecurityMaster.lot_size`，缺省为 100 股整数手。
- Stock 买入采用 T+1；当 ETF 没有权威 T+0/T+1 元数据时采用
  `T_PLUS_1_CONSERVATIVE`。
- 买入只消耗 Shadow 自己的可用现金；真实账户现金永远不可借用。
- 卖出只使用 Shadow 自己的 `sellable_quantity`，不足时记录 partial 或 block。
- commission、minimum commission 和 sell tax 直接复用 Phase E
  `transaction_cost_estimate()`。
- V1 不建模滑点和盘口冲击；记录 `slippage_not_modeled=true`，并可展示
  `execution_delay_price_drift`，该字段不称为 synthetic slippage。

## Ledger And Rebuild

`ShadowLedgerEntry` 是 append-only 事实，包含初始现金/持仓、买入、卖出和
成本对应的 cash delta。`ShadowPosition` 和 `ShadowAccount.current_cash` 是
物化状态，不是唯一事实，必须能够由指定 `shadow_generation` 的 ledger rebuild。

每次 create / rebase 使用新的 generation。旧 generation 的 order、fill、ledger
和 daily snapshot 永久保留，绩效查询不得把不同 generation 混算。

## Outcomes And Alignment

`LiveDecisionOutcome` 独立于 fill，按 1/5/10/20/60 个交易日评估 security、
portfolio、NO_ACTION 和 Candidate Veto。交易日来自 `TradingCalendar`，未来
close 仅用于 outcome evaluation，不是 fill source。

`DecisionActualAlignment` 只匹配确认的 `TradeLedgerEntry`，使用同 code/side
和当前交易日加下一个交易日的 timestamp window。它只增加研究维度，不修改
Shadow ledger，也不从 holdings diff 推断用户行为。

## Versioning

该合同版本只描述 Shadow 执行语义，不改变 Runtime Contract 或 Decision Contract
`2.4.0`，也不改变 Phase C/F/M 的投资评分、阈值和组合决策规则。
