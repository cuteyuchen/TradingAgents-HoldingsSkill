# Phase O.2 Holdings Identity & Encoding Fix Acceptance

状态：

- `MANUAL_UAT = REQUIRED`
- `BLOCKER = HOLDINGS_IDENTITY_AND_ENCODING`（自动化修复已完成，等待用户重新上传真实持仓截图）
- `LIVE_READINESS = NOT_READY`
- `REAL_HOLDINGS_IMPORT_AUTOMATED_ACCEPTANCE = PASS`
- `MIGRATION = NO`，Alembic head 保持 `20260829_0020`

本轮实际记录（2026-09-04）：

- Actual baseline：`976d11582cb538a431900942842993bcd955c7ce`（给定 `6811c62f2053a35ea85ac11fdad3fd4346eb9a62` 的正常 descendant）
- Implementation SHA：`e87c658`（`fix: harden holding identity authority checks`）
- Branch：`codex/phase-o-frontend-productization`
- PR：`#7`，不新建、不合并

本轮修复范围固定在真实持仓导入链：Unicode 名称、证券身份解析、
Review 确认门和分析入口的 canonical security identity 安全校验。
不改投资算法、Market Score / Candidate Score / Portfolio Gate / Shadow。

## Unicode 修复边界

乱码修复发生在模型接口响应进入业务层的第一处字节边界：

- 非流式响应改为显式从 `content` UTF-8 bytes 解码后再解析 JSON；
- SSE 改为按 bytes 读取并显式 UTF-8 解码；
- Fuyao HTTP JSON 解码同样先处理 UTF-8 bytes；
- API JSON 响应统一补充 `charset=utf-8`；
- 前端不进行 latin1/GBK 猜测式 `replace()` 或 mojibake 修复。

后端测试覆盖 parser、API JSON、DB roundtrip 和前端确定性 fixture 的中文名称。
真实截图仍需用户重新上传后由 Manual UAT 确认。

乱码第一次出现于模型/Vision HTTP transport boundary，而不是 Vue 展示层：
响应原始 bytes 曾由缺失或错误 charset 参与解码；现统一显式 UTF-8 解码，
拒绝无效 UTF-8，不依赖 Windows locale、CP936/GBK 或前端猜编码。

## 证券身份安全

证券代码和 canonical identity 是正式持仓的 authority，名称只用于展示和辅助匹配。
新流程只允许：

- 截图/文件直接识别代码；
- 本地 Security Master 验证；
- 名称 deterministic normalization 后唯一匹配；
- 本地无结果时使用 Fuyao Security Master；
- 唯一高置信结果自动 `RESOLVED`；
- 多候选进入 `AMBIGUOUS`，由用户选择；
- 无结果显示 `UNRESOLVED`，必须手动补代码并通过 Security Master 验证；
- 无效代码显示 `INVALID`。

Confirm API 是最终防线：任何 `AMBIGUOUS` / `UNRESOLVED` / `INVALID` 都返回
`409 holding_identity_unresolved`，且不持久化 PortfolioSnapshot。
Analysis 入口和 worker 也会审计快照 identity，不完整快照返回
`409 unresolved_security_identity`，不得在分析阶段猜测证券代码。

身份字段沿用现有 DTO 和 `HoldingItem.extra_json`，无 migration：
`canonical_code`、`display_name`、`asset_type`、`exchange`、`resolution_status`、
`resolution_source`、`resolution_confidence`，并保留 `security_id` 作为可验证 authority。
代码优先顺序为：截图直接代码 → 本地 Security Master → Fuyao
`/api/meta/tickers/search` → 唯一结果；多候选为 `AMBIGUOUS`，无结果为
`UNRESOLVED`，非法或冲突代码为 `INVALID`。名称只做 NFKC、空白、大小写、
合法展示后缀和 Security Master alias 的 deterministic normalization；匹配同时约束
CN A-share、交易所和 STOCK/ETF 类型，ETF 不会落到普通股票。

Review 行显示代码、名称、数量、可用、成本、现价、市值和状态；状态文案为
“已匹配 / 需要选择 / 未找到 / 代码无效”。resolved 后名称由 Security Master
回填并只读；未 resolved 时现价和市值显示 `—`。手工代码采用 400ms debounce/blur
验证，候选弹窗只显示代码、名称、类型、交易所。

Confirm endpoint 在写入 `PortfolioSnapshot` 前再次 authoritative 验证；任一
未 resolved 行返回 HTTP `409`、`holding_identity_unresolved`，不持久化 snapshot。
历史不完整 snapshot 不删除、不覆盖，响应标记 `INCOMPLETE`，且 Analysis 入口和
worker 以 `unresolved_security_identity` 阻断。

## 自动化证据

- Backend：`489 passed`
- Frontend typecheck / build：PASS
- Playwright acceptance：`27 passed`（原 24 + Phase O.2 Case A-D）
- Docker：build、production-like startup、health、register/login、
  unresolved confirm 409、resolved snapshot `600519.SH / 贵州茅台` 均 PASS

本地专项覆盖：创业板ETF、通信ETF、有色ETF、半导体ETF、科创50ETF、
中证1000ETF、沪深300ETF、贵州茅台、宁德时代；SH/SZ/BJ、ETF、exact code、
exact unique name、ambiguous、unknown、invalid code、wrong asset type、API JSON
和 DB roundtrip 均有断言。当前 Exact-head CI 状态需以推送后的 GitHub Checks 为准。

以上自动化不替代用户本人对同一张真实持仓截图的重新 UAT。
