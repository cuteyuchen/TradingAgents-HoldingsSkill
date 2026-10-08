# Fuyao Schema Impact Audit

> 审计日期：2026-09-02。目标是 P0 先接入，尽量零 migration；只有现有 schema 无法承载生产必需信息时才允许新增 migration。

## 结论

**Migration：NO（本阶段）**。

现有数据层已能承载核心行情、交易日历、历史日线、provider lineage、市场快照、基本面摘要、估值快照、ETF metadata、research 与 shadow。Fuyao 的 `source_timestamp`、endpoint、request_id 等采集 lineage 放入现有 metadata/source lineage，不新增平行业务表。

## 逐项审计

| Existing Table / Store | Required Data | Can Reuse? | Missing? | Need Persist? | Decision |
| --- | --- | --- | --- | --- | --- |
| Security master | `thscode`、ticker、name、exchange、asset_type、currency | Yes | 无 P0 缺口 | Yes | 复用现有 security identity/lifecycle store；只映射官方字段 |
| Trading calendar | `date_ms`、交易日、source | Yes | 无 | Yes | Fuyao 同步到本地，scheduler 不在运行时现场访问 |
| Daily bars / `DailyBarCache` | OHLCV、trade_date、provider、`available_at`、quality | Yes | 没有独立 `source_timestamp` 列 | Yes | `source_timestamp` 放 `metadata_json`；保留 `available_at <= cutoff` 过滤 |
| Historical foundation | `trade_date`、OHLCV、provider lineage、PIT availability | Yes | 无 | Yes | 复用 `NormalizedDailyBar` 与现有 sync/coverage |
| Provider metadata / source lineage | endpoint、request_id、latency、provider、error category | Yes | 字段需统一安全 schema | Yes | 复用 metadata/source lineage；禁止 key 与原始 secret |
| Market snapshot | quote rows、timestamp、coverage、quality、provider chain | Yes | 无 | Yes | Fuyao 作为 primary，fallback 保留；继续计算 coverage/conflict |
| Shadow store | future quote、decision_finalized_at、source timestamp | Yes | 无 | Yes | 仅允许 timestamp 严格晚于 decision finalized；不改 fill semantics |
| `SecurityValuationDaily` | PE/PB/PS/PCF、effective/source availability | Yes | 官方无历史估值序列 | Yes | 当前快照可复用；历史位置无样本时返回“历史样本不足” |
| `FundamentalReport` | income/balance/cash-flow summary、report period、announced/published/source available | Yes | Fuyao 未明确 `available_at`/announcement contract | Yes | 可写 current context；标记 `HISTORICAL_PIT_UNKNOWN`，不用于历史回测 |
| Fundamental indicator cache / analysis JSON | 五类指标的关键子集、raw value、missing state | Yes | 无 | Optional | 优先缓存/结构化摘要，不把几十个指标铺进 UI |
| `EtfMetadataHistory` | exchange ETF metadata、fund context | Yes | 无 P0 缺口 | Yes | 复用；不把 OTC fund 放入 V1 Candidate universe |
| `PriceBasisMetadata` | adjustment/basis、source、effective date | Partial | 不能表达全部 corporate action 原始字段 | Yes | 保留现有复权语义；corporate action 原始 payload 进入现有 lifecycle/source lineage，不新增算法 |
| Security lifecycle/event payload | dividend/bonus/rights/adjustment event context | Yes | 需约束 event type/payload | Yes | 复用事件 payload；不建立第二套调整因子计算 |
| `HistoricalDataSyncRun` / sync audit | run status、row count、date range、checksum、failure | Yes | 无 | Yes | dump bootstrap 与 REST sync 共用，临时文件成功后 atomic replace |
| Market metric snapshot | breadth、turnover concentration、All-A median、quality | Yes | 无 | Yes | 继续保存 numerator、denominator、N、top_count；Fuyao 只替换输入来源 |
| Research store | DailyMarketBrief、evidence、PIT status | Yes | 可用 JSON/context 承载 | Optional | 结构化 summary 写入现有 research/analysis payload |
| Portfolio/holding snapshot | confirmed quantity、cost、mark、contribution | Yes | 无 | No new schema | quantity/cost 仍来自 confirmed snapshot；quote 只做 mark，缺失不填 0 |

## 关键字段策略

### `source_timestamp`

Fuyao envelope 的 `data.timestamp` 与历史 K 线的 `date_ms` 语义不同：前者是数据快照时间，后者是 bar 时间。两者都保留在 provider metadata 中；`available_at` 是本地确认数据可供系统使用的时间，不能用 bar 的交易时间冒充。

### `request_id` 与 endpoint

request lineage 进入可审计的 provider metadata 或 sync audit。对外 diagnostics 只返回 provider、endpoint、request_id、latency、error category、safe message，不返回请求头或 API key。

### Corporate actions

Fuyao 当前公开 contract 明确 adjustment-factor 事件字段，但没有要求系统另建复权引擎。适配层保存 dividend/bonus/allotment 原始字段与 effective date，现有 frozen adjustment semantics 继续负责价格 basis；字段不足时为 missing，而不是编造 event type 或 factor。

### 财务 PIT

Fuyao financial response 提供 `report_date_ms` / `period_end_ms` 等报告字段，但没有在官方通用契约中保证 `announcement_at`、`published_at`、`available_at` 的历史可回放语义。因此无需新增列来假装 PIT 完整；使用现有字段并显式标记 `HISTORICAL_PIT_UNKNOWN`。

## Fresh DB 与 upgrade DB

由于本阶段 migration 为 NO：

- fresh DB 使用现有 schema 即可启动，Fuyao 配置缺失时走 degraded/fallback。
- upgrade DB 不执行表结构变更，只新增 provider、缓存与 metadata 适配逻辑。
- 若后续真实数据验证证明必须保存独立的 corporate action 多事件表或历史 index membership，必须先更新本审计并另行做最小 migration；本阶段不预留 speculative table。
