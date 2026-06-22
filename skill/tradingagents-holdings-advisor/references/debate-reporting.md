# Debate Reporting

Use this file whenever executing the skill, not just when designing it. The user wants the reasoning transcript, not only the final trade table.

This file integrates the **claim-driven debate** system from `TradingAgents-AShare` (structured claims with IDs, evidence, confidence, status tracking) and the **two-layer debate** model from `TradingAgents-astock` (bull/bear investment debate + three-way risk debate).

## Default Rule

When the user says "执行", "分析", "重新分析", "给操作建议", or asks for a timed checkpoint, print the detailed debate unless the user explicitly asks for a concise answer.

For a multi-holding portfolio, print full detail for:

1. The largest loser.
2. The largest market-value position.
3. Any position with fresh red-flag news, capital outflow, or code ambiguity.
4. Any proposed buy/add/major sell.
5. Any buy/rotation candidate, including "watch only" candidates.

Small, low-risk positions can use a compressed debate.

## Claim-Driven Debate System

### Claim Structure

Every bull/bear/risk response must produce structured claims. This is the core innovation from `TradingAgents-AShare`:

```
Claim {
  claim_id: "INV-1" | "INV-2" | ...  (INV for investment debate, RISK for risk debate)
  speaker: "bull" | "bear" | "aggressive" | "conservative" | "neutral"
  stance: "bullish" | "bearish" | "risk_accept" | "risk_avoid" | "risk_balance"
  claim: "具体论点（一句话概括）"
  evidence: ["证据1", "证据2", "证据3"]  // max 3 pieces of evidence
  confidence: 0.0 - 1.0  // self-assessed confidence
  status: "open" | "addressed" | "resolved" | "unresolved"
  target_claim_ids: ["INV-1"]  // which claims this responds to
}
```

### Claim Status Lifecycle

```
open → addressed (opponent responded) → resolved (accepted/refuted with evidence)
                                      → unresolved (disputed, no clear winner)
```

Unresolved claims are the **key uncertainty points** that the Research Manager / Risk Manager must explicitly weigh in their verdict.

### Debate Round Goals

Each round has an escalating objective (inspired by TradingAgents-AShare's round goal system):

| Round | Goal | Description |
|---|---|---|
| Round 1 | 建立核心论点 | Both sides present their strongest 2-3 claims with evidence |
| Round 2 | 攻防核心论点 | Attack opponent's claims, defend own claims, mark addressed/resolved/unresolved |
| Round 3+ | 收敛结论 | Identify which claims remain unresolved, prepare closing argument |

**Configurable Parameters** (defaults shown; `configuration.md` is the single source of truth):
- `max_debate_rounds` (default: 2): Controls investment debate depth. 2 rounds = 4 total responses (Bull→Bear→Bull→Bear).
- `max_risk_discuss_rounds` (default: 1): Controls risk debate depth. 1 round = 3 total responses (Aggressive→Conservative→Neutral).
- `max_revision_retries` (default: 1): Controls how many times the Risk Manager can send Trader back.
- `claims_per_side_min` (default: 2) / `claims_per_side_target` (default: 3): tracked claims per side.
- `evidence_per_claim_max` (default: 3): max evidence items per claim.

## Transcript Structure

Follow this order:

### 1. Evidence Pack (证据包)

```markdown
**证据包**
- 持仓来源: [截图/手动输入/历史对话]
- 解析意图: [标的/周期/关注点/风险偏好]
- 时间: [YYYY-MM-DD HH:MM]
- 当前检查点: [09:25/10:00/12:00/14:30]
- 代码假设: [每个标的的代码和置信度]
- 数据质量: [A/B/C/D/F] — [关键缺失项]
- 交易记忆: [过去同标的决策，如有]
```

### 2. Data Quality Gate Summary (质量门控摘要)

```markdown
**质量门控**
| 分析师 | 硬检查 | LLM复审 | 综合评级 | 关键缺失 |
|---|---|---|---|---|
| 技术分析 | ✓/✗ | 通过/不通过 | A-F | ... |
| ... | ... | ... | ... | ... |
```

Only show this table if quality is B or below. For grade A, simply state "数据质量: A — 可正常执行".

### 3. Bull vs Bear Debate (多空辩论) — Claim-Driven

For each holding that needs full debate:

```markdown
**多空辩论 — [标的名称] ([代码])**

Round 1 — 建立核心论点:
| Claim ID | 方 | 论点 | 证据 | 置信度 | 状态 |
|---|---|---|---|---:|---|
| INV-1 | 多头 | ... | 证据1, 证据2 | 0.8 | open |
| INV-2 | 空头 | ... | 证据1, 证据2 | 0.7 | open |

Round 2 — 攻防:
| Claim ID | 方 | 论点 | 目标Claim | 状态变化 |
|---|---|---|---|---|
| INV-3 | 多头 | 反驳INV-2 | INV-2 | INV-2→addressed |
| INV-4 | 空头 | 反驳INV-1 | INV-1 | INV-1→unresolved |

**未解决论点 (Unresolved Claims):**
- INV-1: [论点] — 多头未能有效回应空头的[证据]
- INV-3: [论点] — 双方证据均有道理，需要更多数据确认
```

### 4. Research Manager Verdict (研究总监裁决)

```markdown
**研究总监裁决**
- 评级: Buy / Overweight / Hold / Underweight / Sell
- 胜出方: [多头/空头] — [理由]
- 关键未解决论点处理: [对每个 unresolved claim 的判断]
- 战略行动: [具体行动方向]
- 置信度: [高/中/低]
```

### 5. Trader Proposal (交易员方案)

```markdown
**交易员方案**
| 标的 | 动作 | 触发价 | 数量/比例 | 止盈 | 止损 | 失效条件 | 当前检查点规则 |
|---|---|---:|---:|---:|---:|---|---|
```

If the Risk Manager sends the proposal back for revision, show:

```markdown
**风控退回 — 第 N 次修正**
- 修正原因: [具体原因]
- 硬性约束 (hard_constraints): [不可违反的条件]
- 建议约束 (soft_constraints): [建议遵守的条件]
- 修正后方案:
| 标的 | 动作 | 触发价 | 数量/比例 | 变化说明 |
|---|---|---:|---:|---|
```

### 6. Three-Way Risk Debate (三方风控辩论) — Claim-Driven

```markdown
**三方风控 — [标的名称]**

| Claim ID | 方 | 论点 | 证据 | 置信度 | 状态 |
|---|---|---|---|---:|---|
| RISK-1 | 激进 | ... | ... | 0.7 | open |
| RISK-2 | 中立 | ... | ... | 0.6 | addressed |
| RISK-3 | 保守 | ... | ... | 0.8 | resolved |

**未解决风控论点:**
- RISK-1: [论点]
```

### 7. Portfolio Manager Final Decision (组合经理最终决策)

```markdown
**组合经理最终决策**
- 组合评级: [整体评级]
- 现金目标: [操作后目标现金比例]
- 风控裁决: pass / revise / reject
- 硬性约束: [不可违反]
- 去风险触发器: [需要立即减仓的条件]

| 标的 | 最终动作 | 数量/比例 | 优先级 | 备注 |
|---|---|---:|---|---|
```

### 8. Buy/Rotation Candidate Plan (今日买入/轮动候选)

```markdown
**今日买入/轮动候选**
| 候选 | 类型 | 消息面/催化 | 资金面 | 板块位置 | 入场条件 | 仓位 | 止盈1 | 止盈2 | 止损 | 取消条件 | 评分 |
|---|---|---|---|---|---|---:|---:|---:|---:|---|---:|
```

## A-Share Bull Framework

Use these bullish arguments when evidence supports them:

- Policy tailwind: national/industry support, strategic sector, regulatory easing.
- Northbound or institutional confirmation (北向资金/主力确认).
- Hot-money momentum: limit-up chain (涨停板连板), strong theme attribution, early sector rotation.
- Valuation digestion: forward PE/PEG and earnings trajectory can support premium.
- Lockup/reduction overhang cleared or absent (解禁/减持出清).
- Strong relative performance versus index and sector (相对强度).
- VPA signals: volume expansion on up days, OBV uptrend, no divergence.

## A-Share Bear Framework

Use these bearish arguments when evidence supports them:

- Policy headwind or rumor-only theme (政策逆风/纯概念炒作).
- Lockup expiry, insider reduction, pledge, ST/delist risk (解禁/减持/质押/ST风险).
- Hot-money withdrawal, fund outflow, volume divergence (游资撤退/资金流出/量价背离).
- Valuation bubble or weak earnings/cash flow (估值泡沫/盈利质量差).
- T+1 trap after sharp rally or weak bounce (急涨后T+1锁仓风险).
- Northbound/main funds leaving (北向/主力出逃).
- Underperformance versus index during a market rebound (反弹中跑输大盘).
- VPA divergence: price up but volume down, selling climax patterns.

## Risk Debate Framework

**Aggressive (激进派):**
- Limit-up/momentum and policy support can justify maintaining or adding.
- Missing early rotation may be a risk.
- Retail and hot-money effects can extend beyond fundamentals.
- VPA accumulation signals suggest institutional buying.

**Neutral (中立派):**
- Direction matters less than position size under T+1 and price limits.
- Use northbound/fund flow as confirmation, not sole thesis.
- Near lockup/reduction windows, reduce gradually rather than binary all-in/all-out.
- Position sizing > direction judgment; use valuation ranges and rotation cycle timing.

**Conservative (保守派):**
- Any new buy is locked by T+1.
- Limit-down can make stop-loss unexecutable.
- Policy reversal and hot-money exits are sudden.
- Weak fundamentals or red-flag announcements justify trimming even during rebounds.
- VPA selling climax signals suggest distribution.

## Risk Revision Loop Output

If the Risk Manager / Portfolio Manager sends the Trader proposal back for revision:

```markdown
**风控修正循环**
- 裁决: revise
- 修正原因: [具体原因]
- 硬性约束 (hard_constraints):
  - [约束1]
  - [约束2]
- 建议约束 (soft_constraints):
  - [建议1]
- 去风险触发器:
  - [触发条件1]
- 执行前提条件:
  - [前提1]

**修正后交易方案:**
[Trader produces revised proposal incorporating constraints]
```

Max 1 revision. If still unsatisfactory, default to reject.

## Quality-Gated Reporting

Do not replace missing mandatory evidence with a lower-quality report. If
mandatory quote, market, sector, capital-flow, or risk data is missing, state
"暂不能给出交易建议", list the missing fields, and explain the next collection
step. If the user requests brevity, keep the same evidence and quality gates but
write fewer words.

Never skip the debate entirely for material decisions. Show at least the top claim from each side and the verdict when action advice is allowed.
When persistence is configured, upload the three risk rows as structured `claims` with `claim_id` values `RISK-1`, `RISK-2`, and `RISK-3`; speakers must be `aggressive`, `neutral`, and `conservative` respectively.

## Formatting Template (Compact Version)

Use this compact but detailed shape when full detail would be too long:

```markdown
**证据包**
- 持仓来源: | 时间: | 代码假设: | 数据质量: | 意图:

**多空辩论** (Claim-Driven)
| 标的 | 多头核心Claim | 空头核心Claim | 未解决 | 研究经理裁决 |
|---|---|---|---|---|

**交易员方案** (含风控修正)
| 标的 | 动作 | 触发价 | 数量/比例 | 失效条件 | 风控状态 |
|---|---|---:|---:|---|---|

**今日买入/轮动候选**
| 候选 | 消息面/催化 | 资金面 | 板块位置 | 入场条件 | 仓位 | 止盈 | 止损/取消条件 | 评分 |
|---|---|---|---|---|---:|---|---|---:|

**三方风控** (Claim-Driven)
| 标的 | 激进核心Claim | 保守核心Claim | 中立核心Claim | 组合经理最终意见 |
|---|---|---|---|---|
```

If a single holding needs more detail, expand it into prose under the tables.

## Do Not Hide Uncertainty

- If ETF code is uncertain, put the uncertainty in the evidence pack and debate table.
- If data quality is C or worse, the conservative argument must explicitly say so.
- If a final recommendation depends on a level, show the level.
- If no immediate buy is allowed, still show watch-only candidates and the exact trigger that would change the decision.
- If claim status is "unresolved", explicitly list it and explain why it couldn't be resolved.
- If risk revision was triggered, show both the original and revised proposals.
- If trading memory exists for this ticker, reference the past decision and alpha performance.
