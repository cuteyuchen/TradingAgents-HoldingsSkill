# V3-MARKET-2.1 Live Provider Hardening

本阶段基于本地可用的 MARKET-2 提交 `4aa91d8`（用户给出的冻结 SHA `61da24f47db903ab308394d0769bc9229dab8eff` 不在本地对象库中）。目标是把真实 Provider 的字段、单位、时间和失败语义收敛到既有 MARKET-2 public contract；没有新增 SecurityMaster、TradingCalendar、Provider Registry 或行情缓存。

## Live Probe

从 `backend/` 运行：

```powershell
python -m scripts.market_live_probe --codes 600519.SH,159915.SZ,000300.SH,000001.SH,000001.SZ --include quote,bars,order-book,capital-flow --output ./tmp/market-live-probe.json
```

`--now 2026-09-11T10:00:00+08:00` 只用于确定性 session simulation，不得把模拟时间标为真实盘中验证。Probe 只调用 `InstrumentMarketService`，通过现有 Provider Registry、SecurityMaster 与 TradingCalendar 工作；不调用 LLM/Agent，不写 Portfolio/AnalysisRun，不下单。写文件前会再次移除 raw、headers、endpoint、URL、token、Cookie、credential 等字段，并把异常收敛为稳定错误码。

默认标的覆盖 STOCK、ETF、INDEX，以及 `000001.SH`/`000001.SZ` 的跨交易所 identity/cache 隔离。SecurityMaster 缺失会输出 `MASTER_DATA_MISSING`，不会由 quote 自动创建 instrument。

## 口径与单位

- Quote：`volume=shares`、`turnover=CNY`。Tencent 的 lots 与 10k CNY 仅在真实响应明确声明时转换；缺少或无法确认单位时返回 `null`，附 `UNIT_SEMANTICS_UNKNOWN`，不猜倍率。
- Quote 一致性：facade 重新计算 `change` 与 `change_pct`。Provider 百分比和价格推导值冲突时保留价格推导值并附 `QUOTE_CHANGE_CONFLICT`，质量降级。
- 指数：volume/turnover 仍为 `null`，附 `INDEX_VOLUME_SEMANTICS_UNKNOWN`。
- Order Book：五档按 bid 降序、ask 升序，level 为 1..5；lots 缺少 `lot_size` 时 volume 为 `null` 并降级，不默认 100。
- Capital Flow：正数为净流入、负数为净流出，`provider_derived=true`、`methodology=provider_defined`，最高质量不超过 B；ETF 默认 unsupported。
- Bars：指数只允许 `none`；ETF 按 provider capability；fallback 不静默改 adjustment。周/月由 daily 聚合时使用首开、最高、最低、末收、成交量/额求和，bar date 为最后交易日。

## 时间、session 与 fallback

`observed_at` 表示 Provider 观测时间，`fetched_at` 表示本次读取时间。Tencent/Eastmoney 的仅 `HH:MM:SS` 字段不会绑定到 fetched date；Fuyao 保留 row timestamp 与 response timestamp 的来源元数据。session/data_basis 继续由现有 `MarketSessionService` 和 `TradingCalendar` 决定：盘中 `live`，正式收盘 `session_close`，非交易日 `previous_session_close`。午休不因无新成交立即 stale；收盘后到 20:00 不因时间流逝自动 stale；新交易日盘中上一交易日 close 会 stale。

Primary 失败后由既有 fallback chain 继续尝试，成功结果标 `fallback=true`、`PROVIDER_FALLBACK`，质量不高于 B。健康登记复用现有 Provider Health Registry；超时、429、401/403、5xx、解析失败分别归类为 `PROVIDER_TIMEOUT`、`PROVIDER_RATE_LIMITED`、`PROVIDER_AUTH_FAILED`、`PROVIDER_UNAVAILABLE`、`PROVIDER_PARSE_FAILED`，重试仍由 Provider 自己的 bounded policy 控制。

## Cache 与 Evidence

现有 TTL 保持：Quote 4s、Order Book 2s、Capital Flow 45s、Live Bars 30s、Closed/Historical Bars 6h、失败 1s。`force_refresh=True` 跳过 Quote cache；刷新结果只更新最终 Quote，不修改冻结 `instrument_market` Evidence。Batch Quote 继续一次 qualified provider call，避免 20 个标的退化成 20 次 HTTP。

## Dashboard Portfolio Race

Dashboard 读取组合时使用 AbortController、request sequence 和 active portfolio id 三重保护。组合 A 请求启动后切换到 B，即使 A 晚于 B 返回，也不能覆盖页面上的 B；被 abort 的旧请求不产生错误 Toast。URL 中的 `?portfolio=<id>` 仍由既有 Portfolio context 在刷新后恢复。

## 验证边界

CI 默认使用 offline fixtures/mock transport，不依赖公网行情。当前环境若无法访问真实 Provider，Probe 必须标记 `LIVE_PROVIDER_VERIFICATION=BLOCKED_BY_ENVIRONMENT`，不得伪装成功。收盘后只能报告 `session_close` 已验证，非交易日只能报告 `previous_session_close` 已验证；盘中 live、极端行情一致性、Level-2 一致性、长期资金流口径稳定性、人工 UI、移动端和真实投资收益仍需后续验证。

再次运行 Probe 后，`tmp/market-live-probe-*.json` 默认不提交；最终汇报必须分别列出 Quote、Bars、Order Book、Capital Flow 的 `LIVE VERIFIED` 状态，`TESTED` 不等于 `LIVE VERIFIED`。

## 自动化验证

- Backend：`python -m pytest tests -q` -> `716 passed, 152 warnings`。
- Frontend：`npm run typecheck` 通过；`npm run build` 通过，只有既有大 chunk 警告。
- Acceptance：新增 Dashboard portfolio race 用例通过；本次完整运行 `31 passed, 1 failed`，失败为既有 ownership 用例导航时 Windows `net::ERR_NO_BUFFER_SPACE`，属于环境资源阻塞，不是业务断言失败。
- Docker：`docker compose build` 因 Docker Desktop Linux engine 未运行、缺少 `dockerDesktopLinuxEngine` pipe 阻塞；未 reset Docker 或数据库。

## 本次手工 Probe 结果

Probe 文件记录的时间为 `2026-09-11T16:20:28Z` 至 `2026-09-11T16:20:38Z`；按 `Asia/Shanghai` 为 `2026-09-12 00:20`，TradingCalendar 判定为非交易日，因此本次 session basis 是 `previous_session_close`。

- Quote：`LIVE VERIFIED`（默认 critical chain 实际由 Fuyao 缺失配置后 fallback 到 Tencent；另用 facade 直连 `eastmoney_batch` 验证 600519、159915、000001.SZ 的 shares/CNY 单位和完整日期时间）。Fuyao 本身为 `BLOCKED_BY_ENVIRONMENT`，未伪装成功。
- Bars：`LIVE VERIFIED`（Eastmoney daily fallback 返回 OHLC、shares、CNY；未声称 Fuyao historical 已验证）。
- Order Book：`LIVE VERIFIED`（Tencent 五档、买卖映射、排序、lots→shares、内外盘均有真实返回）。
- Capital Flow：`LIVE VERIFIED`（Eastmoney 日级 current/history，正负号和五类净流入字段均有真实返回；`observed_at` 缺失因此质量为 B）。
- Index / cross-exchange：`BLOCKED_BY_MASTER_DATA`。当前本地 SecurityMaster 缺少 `000300.SH` 和 `000001.SH`，Probe 正确输出 `MASTER_DATA_MISSING`；没有自动创建标的。

这组结果只证明上述一次运行和当前收盘/非交易日路径；不代表真实盘中长时间稳定性、极端行情一致性或长期资金流口径稳定性。
