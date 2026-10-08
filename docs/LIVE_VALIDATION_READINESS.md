# Live Validation Readiness

本阶段新增只读接口：

`GET /api/v3/system/live-validation-readiness`

该接口是进入真实市场长期验证前的证据门，不会触发行情请求、分析任务、交易或
数据库写操作。它不等同于策略有效性，也不等同于 Phase P 的 Live Evidence。

## 返回契约

```json
{
  "status": "READY | NOT_READY",
  "ready": false,
  "blockers": [{"key": "portfolio_snapshot", "reason": "..."}],
  "warnings": [{"key": "shadow_subsystem", "reason": "..."}],
  "checks": {
    "database": {"status": "OK"},
    "schema": {"status": "OK"}
  },
  "evaluated_at": "2026-08-30T00:00:00+00:00"
}
```

`checks` 中的状态统一使用 `OK`、`DEGRADED`、`BLOCKED`、`UNKNOWN`。任何
`BLOCKED` 或 `UNKNOWN` 都进入 `blockers`，任意 blocker 存在时整体为
`NOT_READY`。`DEGRADED` 进入 `warnings`，不会单独阻断，但不能被前端隐藏。

## 检查项

| Key | 来源 / 判定 | 失败语义 |
|---|---|---|
| `database` | 复用 system readiness 的 DB 检查 | DB 不可用、必需表缺失或 quick check 失败 |
| `schema` | Alembic DB revision 与代码 head | UNKNOWN、BEHIND、AHEAD 或 BROKEN 不得进入 live validation |
| `disk` | 备份目录磁盘剩余空间 | critical 为 BLOCKED，低余量为 DEGRADED |
| `backup` | 最近 verified backup freshness | 备份策略不可用或过期 |
| `scheduler` | scheduler authority；配置禁用时明确 BLOCKED | 没有唯一可运行的 scheduler |
| `worker_recovery` | durable worker recovery report | 恢复错误或 stale durable jobs |
| `governance` | 当前 ACTIVE 参数可解析 | 没有可解析生产参数 |
| `trading_calendar` | CN TradingCalendar 当前日和后续交易日 | 日历未初始化或超出范围 |
| `market_provider` | persisted quote ProviderHealth | 没有最近成功 provider 为 BLOCKED，恢复中为 DEGRADED |
| `quote_pipeline` | 最近有效 `MarketSnapshot` | 没有有效快照或质量非法 |
| `market_refresh` | MarketSnapshot freshness | 有效快照过期时 BLOCKED |
| `portfolio_snapshot` | 当前用户最近 confirmed snapshot，最多 3 天 | 缺失或过期 |
| `analysis_smoke` | 当前用户已持久化 AnalysisRun | 没有分析报告事实 |
| `candidate_smoke` | 当前用户 `COMPLETED` CandidateRun | 没有完成的候选扫描事实 |
| `shadow_subsystem` | Shadow 必需表和汇总健康 | schema 未安装为 BLOCKED，运行异常为 DEGRADED |
| `future_quote_observation` | 合法价格且质量为 VALID/DEGRADED 的 LiveQuoteObservation | 没有未来 quote 观察 |
| `real_broker_write_path` | 当前系统能力边界 | 没有真实券商写入口为 OK，属于安全边界证明 |

## 休市日语义

如果上海时区当前日期存在明确的 `TradingCalendar(is_open=false)`，
`trading_calendar` 返回 `OK / non_trading_day`。此时不要求当天产生新行情：

- 最近一个有效 `MarketSnapshot` 仍可作为市场状态来源；
- `quote_pipeline` 可以保持 `OK`；
- `market_refresh` 对已知休市日使用 `closed_day_grace=true`，不会因为没有“今日
  quote”误判为 stale；
- provider、portfolio snapshot、analysis、candidate、future quote 等其他证据
  仍必须分别满足，休市日不会绕过整体 readiness gate。

如果当前日期没有日历事实，则不能猜测是交易日还是休市日，返回 BLOCKED。

## Ownership 和安全边界

- Portfolio snapshot、AnalysisRun、CandidateRun 使用当前登录用户过滤。
- Market provider、MarketSnapshot、TradingCalendar 是共享只读市场事实。
- Future quote 观察属于 Shadow 事实，只做存在性和质量检查，不把它当作收益证据。
- Endpoint 没有任何写操作，不会自动创建 snapshot、analysis、candidate 或 shadow。
- 系统没有 Real Broker Order、Auto Trading 或 Auto Apply Calibration 路径。

## 测试

`backend/tests/test_system_health.py` 覆盖：

- 缺 Portfolio Snapshot、Provider、Analysis、Candidate、Future Quote 时整体为
  `NOT_READY`；
- `blockers`、`warnings`、`checks`、`evaluated_at` 结构稳定；
- 休市日没有当天行情时，最近有效市场快照仍可使 market refresh 保持 OK；
- 当前测试使用数据库 fixture，不调用第三方 provider。

## 当前 Gate

本接口的计算实现和回归测试已完成，但本地/部署环境是否 `READY` 必须以真实
endpoint 返回为准。即使返回 `READY`，也仍需用户完成
`PHASE_O_MANUAL_UAT.md`；三项 Gate 必须分别记录：

`AUTOMATED_FRONTEND_ACCEPTANCE = PASS`  以及
`MANUAL_UAT = PASS`  以及
`LIVE_READINESS = READY`

才允许进入 `Phase P｜Live Evidence Accumulation`。
