# Fuyao 数据能力矩阵

> 依据 Fuyao 官方 `llms-full.txt` 与 API Reference 核对，核对日期：2026-09-02。
> 本文是实现前的能力边界记录。官方契约优先于本仓库的字段命名或示例。

## 通用契约

- Base URL：`https://fuyao.aicubes.cn`。
- REST API 使用 `X-api-key` 请求头。
- HTTP 200 只表示 HTTP 层成功；业务成功条件是响应 envelope 的 `code == 0`。
- 响应 envelope：`code`、`message`、`request_id`、`data`。
- Fuyao 时间戳是 Unix milliseconds，按 Asia/Shanghai 转换为 aware datetime。
- 业务错误必须保留 `request_id`、endpoint、latency 和安全的错误分类，但不得记录或返回 API key。
- 标的字段统一使用官方 `thscode`，例如 `600519.SH`、`300750.SZ`、`159915.SZ`。

## 矩阵

| Domain | Fuyao Endpoint | Current Consumer | Canonical Model | PIT | Persistence | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| Ticker Search | `GET /api/meta/tickers/search` | Security master 增量搜索、设置页诊断 | `SecurityIdentity` / `SecuritySearchResult` | 当前安全；历史生命周期未知 | 复用 security master；不存原始 key | P0 |
| Ticker List | `GET /api/meta/tickers/list` | Security master 同步、A 股与场内 ETF universe | `SecurityIdentity` | 当前安全；历史状态字段未由接口证明 | 复用 `security_master` 相关表 | P0 |
| Trading Calendar | `GET /api/a-share/calendar/trading-days` | Scheduler、checkpoint、PIT cutoff | `TradingCalendarDay` | 当前/历史交易日安全 | 复用本地 trading calendar | P0 |
| A-share Snapshot | `GET /api/a-share/prices/snapshot` | Market snapshot、Market Score、持仓 mark | `NormalizedQuote` / `QuoteSnapshot` | 当前快照；以响应 timestamp 为 source time | 复用 market snapshot 与 provider metadata | P0 |
| Historical Kline | `GET /api/a-share/prices/historical` | DailyBar、trend、correlation、shadow mark、research | `NormalizedDailyBar` | 历史价格可用；`available_at` 仍由采集时间建立 | 复用 `DailyBarCache` / historical foundation | P0 |
| Market Dumps | `/api/dump/market-dumps` 及对应 download-url endpoint | 全 A 10 年 bootstrap、10 日增量、复权因子 bootstrap | `NormalizedDailyBar` / `PriceBasisMetadata` | 以 dump 说明及本地导入时间审计；未证明公布时点时不升级 PIT | 复用 daily bars、price basis、source lineage；临时文件不入业务表 | P0 |
| Corporate Actions | `GET /api/a-share/corporate-actions/adjustment-factors` | 复权基础、股利/送股事件上下文 | `CorporateAction` 适配为现有 lifecycle/source payload | 生效日期可用于事件定位；公告可用时点未由接口契约证明 | 复用现有 lifecycle/source lineage；不建立第二套复权算法 | P0 |
| Financial Statements | `/api/a-share/financials/income-statements`, `/balance-sheets`, `/cash-flow-statements` | Holding/Candidate 基本面摘要、研究上下文 | `FundamentalReport` | 当前分析允许；历史 PIT 未证明 | 复用 `FundamentalReport`；保留 report date/source available 状态 | P1 |
| Financial Indicators | `GET /api/a-share/financials/indicators` | 成长、盈利、偿债、营运、现金流摘要 | `FundamentalIndicatorSummary` | 当前分析允许；历史 PIT 未证明 | 优先缓存/结构化分析结果；不铺几十个 ratio | P1 |
| Valuation | `GET /api/a-share/valuations/snapshot` | PE/PB/PS/PCF 上下文与解释 | `SecurityValuationDaily` / `ValuationSnapshot` | 当前快照安全；历史估值样本与 PIT 未提供 | 复用 `SecurityValuationDaily` 或短 TTL cache | P1 |
| Index Catalog | `GET /api/a-share-index/catalog/ths-index-list` | 行业/概念目录、分析上下文 | `IndexIdentity` | 当前目录安全；历史目录变更未证明 | 长 TTL cache；必要时复用 security master | P1 |
| Index Constituents | `GET /api/a-share-index/constituents/ths-stock-list` | 当前行业/指数广度、解释 | `IndexConstituents` | 只有 current membership；禁止历史回放 | 短期 cache，不写成历史 membership | P1 |
| Index Snapshot | `GET /api/a-share-index/prices/snapshot` | 主要指数表现 | `NormalizedQuote` / `IndexSnapshot` | 当前安全 | 复用 market context cache | P1 |
| Index Historical | `GET /api/a-share-index/prices/historical` | 指数趋势、市场 brief | `NormalizedDailyBar` | 历史价格可用；`available_at` 仍需本地建立 | 复用 historical foundation | P1 |
| Fund / ETF Metadata | `/api/fund/profile/detail`、`/api/fund/market/snapshot` 等 `/api/fund/*` 能力 | ETF 持仓详情、ETF mark、基金上下文 | `EtfMetadata` / `NormalizedQuote` | 当前资料/行情；基金披露 PIT 按具体接口审计 | 复用 ETF metadata 与 daily bars | P0/P1 |
| Fund / ETF Historical | `GET /api/fund/market/historical` | 场内 ETF 历史日线 | `NormalizedDailyBar` | 历史价格可用；披露字段需单独 PIT 判断 | 复用 daily bars | P1 |
| Limit Up / Down | `GET /api/a-share/special-data/limit-up-pool`, `/limit-down-pool`, `/limit-break-pool`, `/limit-up-ladder` | 市场情绪、风险上下文 | `MarketSpecialData` | 当前交易日或接口提供的日期；历史可用性不等于 PIT safe | 短 TTL cache；不写入 score | P1 |
| Hot List | `GET /api/a-share/special-data/hot-stock-list`, `/skyrocket-list` | 热度/拥挤上下文 | `HotListSnapshot` | 当前安全；发布/可用时间未证明 | 短 TTL cache；缺失保持 null | P1 |
| Hot Rank Trend | `GET /api/a-share/special-data/hot-stock-list-history`, `/hot-stock-rank-trend` | Candidate/Holding 解释 | `HotRankTrend` | 有日期但历史 PIT 仍未证明 | 短 TTL cache或按需查询 | P1 |
| Abnormal Movement | `GET /api/a-share/special-data/anomaly-analysis-list`, `/anomaly-analysis-stock` | 个股风险/异动证据 | `AbnormalMovement` | 当前上下文；历史 PIT 未证明 | 短 TTL cache；不作为 score 输入 | OPTIONAL |
| Dragon Tiger | `GET /api/a-share/special-data/dragon-tiger-list` | 市场活跃度与个股风险上下文 | `DragonTigerSnapshot` | 按交易日可查；公告/发布时点 PIT 未证明 | 短 TTL cache；不作为 score 输入 | OPTIONAL |

## 分层与路由约束

### CORE

行情快照、交易日历、历史核心行情必须接入 provider 链路，并且在 Fuyao 未配置、未授权或限流时由现有 Eastmoney/Tencent fallback 继续提供能力。核心数据质量不足时继续遵守现有 coverage freeze 规则。

### ENRICHMENT

估值、财务、财务指标、指数/行业用于 deterministic context、Candidate evidence、Portfolio explanation。它们不修改已封存的 Market Score、Candidate Score、Portfolio Gate 或 Shadow contract。

### OPTIONAL

热榜、异动、龙虎榜和连板等只用于 context/evidence。optional endpoint 失败只能使对应字段缺失或降级，不能把 Market Score 全部 BLOCK，也不能把缺失转换为 0。

### API 使用约束

- 全 A 快照使用官方分页或批量能力，禁止逐股 5000 次请求。
- 关键持仓使用批量 quote，并复用已有 market snapshot；Dashboard、Monitor、Analysis 不各自刷新全 A。
- 所有 endpoint 由统一 `FuyaoClient` 处理鉴权、timeout、retry、envelope、业务错误、latency 与 request lineage。
- V1 可交易 universe 仍是 A-share stocks 与 exchange-traded ETFs；Fuyao 支持其他资产不等于 Candidate universe 自动扩大。
