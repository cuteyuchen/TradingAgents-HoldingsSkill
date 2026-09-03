# Phase O.2 Holdings Identity & Encoding Fix Acceptance

状态：

- `MANUAL_UAT = REQUIRED`
- `BLOCKER = HOLDINGS_IDENTITY_AND_ENCODING`（自动化修复已完成，等待用户重新上传真实持仓截图）
- `LIVE_READINESS = NOT_READY`
- `REAL_HOLDINGS_IMPORT_AUTOMATED_ACCEPTANCE = PASS`
- `MIGRATION = NO`，Alembic head 保持 `20260829_0020`

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

## 自动化证据

- Backend：`485 passed`
- Frontend typecheck / build：PASS
- Playwright acceptance：`27 passed`（原 24 + Phase O.2 Case A-D）
- Docker：build、production-like startup、health、register/login、
  unresolved confirm 409、resolved snapshot `600519.SH / 贵州茅台` 均 PASS

以上自动化不替代用户本人对同一张真实持仓截图的重新 UAT。
