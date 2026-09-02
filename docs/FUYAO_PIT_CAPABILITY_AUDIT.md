# Fuyao PIT Capability Audit

> 审计日期：2026-09-02。PIT 判断只接受官方文档明确证明的 available/publication semantics；有日期字段不等于历史时点可用。

## 判定枚举

- `CURRENT_SAFE`：可以用于当前分析或当前展示。
- `HISTORICAL_PIT_SAFE`：官方契约明确提供足够的历史可用时点语义，可以用于 cutoff 回放。
- `HISTORICAL_PIT_UNKNOWN`：有历史/报告日期，但没有足够的 announcement/published/available 证据。
- `NOT_HISTORICAL`：接口只提供当前状态，不能回放历史状态。

## 逐域审计

| Domain | Current Use | Historical Use | Status | Guard |
| --- | --- | --- | --- | --- |
| A-share snapshot | 当前 quote、Market Snapshot、持仓 mark | 不作为历史回放数据源 | `CURRENT_SAFE` | 记录 response timestamp；stale/missing/conflict 继续走质量门 |
| Trading calendar | 当前 scheduler 与 cutoff | 历史交易日判断 | `HISTORICAL_PIT_SAFE` | 只使用已同步的本地 calendar；远端不可用时不现场猜测 |
| Historical Kline | 趋势、correlation、benchmark、Shadow mark | 历史价格与 bar cutoff | `HISTORICAL_PIT_SAFE` | `available_at IS NOT NULL AND available_at <= cutoff`；source timestamp 不替代 available_at |
| Market daily dump | bootstrap 历史日 K | 历史价格与研究 | `HISTORICAL_PIT_UNKNOWN` | 导入时建立本地 available_at；未证明 dump 发布时点的任务不宣称 full PIT |
| Corporate actions | 当前/历史复权事件与解释 | 历史价格 basis | `HISTORICAL_PIT_UNKNOWN` | ex-date 是生效定位，不代表事件在历史 cutoff 已公开可用 |
| Financial statements | 当前基本面摘要 | 历史回测财务因子 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | Fuyao 的 `report_date_ms` 不自动等同 announcement/published/available_at |
| Financial indicators | 当前成长/盈利/偿债/营运/现金流摘要 | 历史指标回放 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | 没有可证明的 PIT 时间轴时标记 `NON_PIT_CONTEXT` |
| Valuation snapshot | 当前 PE/PB/PS/PCF | 历史估值位置 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | 无历史 valuation series 时显示“历史样本不足”，不输出便宜/昂贵 |
| Index catalog | 当前指数/行业目录 | 历史目录/分类 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | 当前目录变更不可回放 |
| Index constituents | 当前成分、当前行业广度 | 历史成分回放 | `CURRENT_SAFE` + `NOT_HISTORICAL` | 禁止用 current constituents 回放 2024-01-01；除非后续官方提供 historical membership |
| Index snapshot/historical | 当前主要指数与历史指数价格 | 历史指数价格 | `HISTORICAL_PIT_SAFE` for price, otherwise `HISTORICAL_PIT_UNKNOWN` | 只对价格 series 使用已有 available_at gate |
| ETF profile/market | 当前 ETF metadata、行情、解释 | 历史基金披露 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | 场内 ETF 可进 V1 universe；OTC fund 不自动扩展 Candidate |
| Limit up/down/ladder | 当前交易日市场情绪 | 历史情绪回放 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | `date`/`date_ms` 仅说明查询日期，不证明发布可用时点 |
| Hot list/history/rank trend | 当前热度与按日期查询的热榜 | 历史热度回放 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | 没有发布时间/可用时间契约；只作 NON-PIT context |
| Abnormal movement | 当前异动证据 | 历史异动回放 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | missing 保持 missing，不填 0 |
| Dragon Tiger | 当前/按交易日的榜单上下文 | 历史榜单回放 | `CURRENT_SAFE` + `HISTORICAL_PIT_UNKNOWN` | 交易日不等于可用时点；不作为 score 输入 |

## 硬边界

1. 财务、财务指标和 valuation 当前数据可以进入 `CURRENT_ANALYSIS_ALLOWED`，但历史研究必须保留 `HISTORICAL_PIT_NOT_PROVEN` / `NON_PIT_CONTEXT`。
2. 当前指数成分只允许 current analysis，不允许拿来回放任意历史日期。
3. 任何领域没有证据时写 `UNKNOWN`，绝不猜 `YES`。
4. LLM context 必须携带 PIT status；LLM 不能把 `NON_PIT_CONTEXT` 改写成 `FULL_PIT_EQUIVALENT`。
5. Shadow 仍只接受严格晚于 `decision_finalized_at` 的 future quote；PIT audit 不改变 Shadow fill contract。

## 实现验收要求

- 结构化响应包含 `pit_status`、`source_available_at`（如有）与 `evidence_status`。
- 缺少 announcement/published/available 字段时，测试必须断言不会升级为 `HISTORICAL_PIT_SAFE`。
- 所有 historical query 继续执行本地 cutoff filter；provider 只提供数据，不绕过现有 PIT gate。
