# TradingAgents-HoldingsSkill V3
# Phase A｜V3 Contract Alignment 开发规格

> 仓库：`cuteyuchen/TradingAgents-HoldingsSkill`  
> 基线：Phase 0 审计后的 `main`  
> 目标：在不重写 V2、不实现 Market Score / Monitor / Quant Candidate 的前提下，统一 V3 决策语义与现有运行时规则。  
> 本阶段性质：**合同对齐 / 兼容层 / 安全护栏**。

---

# 1. Phase A 的唯一目标

解决现在最危险的问题：

> **产品冻结规格已经进入 V3，但生产运行中的 Skill / Prompt / Normalizer / 前端仍有 V2 旧语义。**

如果不先对齐，后续 Market Score、Trigger、Candidate Engine 即使开发正确，也会继续被旧 Prompt 拉回：

- 必须推荐 1–2 个候选；
- 允许现有持仓重复出现在“新增候选”；
- 所有证券统一 30% 单标的上限；
- 旧周期 1–14 / 14–90 / 90+；
- 没有把 `NO_ACTION` 作为一等公民。

Phase A 完成后的系统应做到：

```text
分析可以成功
↓
没有足够证据证明需要改变组合
↓
NO_ACTION
```

而不是：

```text
分析成功
↓
为了生成建议而生成交易
```

---

# 2. Phase A 明确不做什么

本阶段禁止扩展到：

- Market Score；
- Market Regime 正式算法；
- 全 A 市场批量行情；
- SecurityMaster 正式数据同步；
- TradingCalendar 正式数据同步；
- Realtime Monitor；
- Trigger Engine；
- 完整 Trade Ledger；
- ETF 穿透；
- Quant Candidate V3；
- Dynamic Risk Budget；
- Decision Edge 正式量化模型；
- Alpha Memory；
- PostgreSQL / Redis / Celery；
- 前端八大一级导航重做；
- 大型数据库迁移。

这些全部留在后续 Phase。

---

# 3. 必须保留的现有能力

以下功能不能因为 Phase A 被破坏：

```text
JWT / Refresh Token
用户隔离
模型 Provider / Profile
API Key 加密
截图上传
Vision 识图
人工修正
持仓确认
不可变 PortfolioSnapshot
AnalysisJob
AnalysisRun
Cancel / Retry
SSE progress
Bull / Bear Debate
Research Manager
Trader
Risk Revision
Three-Way Risk Debate
Portfolio Manager
Quality Gate
Final Quote Refresh
Markdown Report
历史报告
报告对比
Scheduler
DingTalk
WeCom
V1 Archive compatibility
Docker
Alembic
CI
```

---

# 4. 本阶段要冻结的 V3 决策合同

## 4.1 Portfolio-level 默认决策

正式增加：

```text
NO_ACTION
```

含义：

> 当前证据不足以证明调整组合比保持现状更优。

NO_ACTION 是正常、合法、高质量结果。

它不是：

```text
分析失败
```

也不是：

```text
数据缺失
```

---

# 5. NO_ACTION 与 WATCH_ONLY 必须严格区分

## NO_ACTION

数据可以是好的。

系统完成了分析。

结论是：

```text
没有必要交易
```

例如：

- 当前持仓结构合理；
- 没有明显更优机会；
- 新机会只比现有持仓略好；
- 交易成本 / 风险不值得；
- 等待更强证据。

---

## WATCH_ONLY

表示：

```text
由于数据质量 / 行情 / 证券身份等问题，
系统无法安全给出具体交易动作。
```

因此：

```text
NO_ACTION != WATCH_ONLY
```

### 规则

Quality Gate 被 D/F 或硬检查阻断时：

```text
WATCH_ONLY
```

不得被 Normalizer 自动改成：

```text
NO_ACTION
```

---

# 6. Action 语义

## Portfolio 层

至少支持：

```text
no_action
hold
add
reduce
sell
rotate
watch_only
```

当前阶段不强制重构整个旧枚举。

重点：

```text
no_action
```

必须进入所有结构化输出和 UI 映射。

---

## Holding 层

继续兼容：

```text
add
conditional_add
hold
reduce
sell
watch
```

现有持仓加仓：

```text
属于 Holding Action
```

不是“新增 Candidate”。

---

# 7. Deterministic NO_ACTION Guard

这是 Phase A 最重要的代码护栏。

在 `_normalize_final()` 或其拆出的纯函数中增加：

```text
Portfolio Action Guard
```

## Actionable holding actions

```text
add
conditional_add
reduce
sell
```

其中 `conditional_add` 可以被视为：

```text
plan exists
```

但如果当前条件尚未触发，Portfolio 级可以继续标：

```text
no_action_now
```

Phase A 为兼容现有 schema，可先不新增 `no_action_now` 枚举。

推荐规则：

### Rule A

如果：

```text
所有 Holding Action ∈ {hold, watch}
AND
Action Candidates = []
AND
Quality Gate != blocked
```

则：

```text
final_rating = no_action
```

---

### Rule B

如果：

```text
模型返回 hold
```

但实际上没有任何组合变化：

```text
normalize -> no_action
```

Portfolio Summary 可继续写：

```text
当前组合维持不变
```

---

### Rule C

如果：

```text
Quality Gate blocked
```

则保留：

```text
watch_only
```

---

### Rule D

如果存在真实：

```text
add / reduce / sell
```

则不强制改成 no_action。

---

# 8. Candidate 新语义

Phase A 后：

```text
candidates
```

专指：

> **非当前持仓的新机会。**

不是：

- 现有持仓加仓；
- 现有持仓条件加仓；
- 为了凑数量展示的观察标的。

---

# 9. Candidate 数量

正式从：

```text
必须 1～2
```

改为：

```text
0～3
```

0 是合法结果。

---

# 10. Candidate 不允许现有持仓重复

如果：

```text
candidate.code in current_holdings
```

无论模型写：

```text
add_existing
conditional_add
rotation_watch
```

都不能继续出现在：

```text
New Opportunity Candidates
```

应该：

```text
从 candidate list 移除
```

持仓是否加仓：

```text
只在 Holding Action 中表达。
```

---

# 11. Phase A 暂时不实现 Watch Pool

V3 最终有：

```text
Watch Pool
Ready
Action
```

但 Phase A 不新增 Watchlist / CandidateLifecycle 表。

因此：

- 新候选没有达到执行标准 → 可以不输出 Candidate；
- 不再用 `rotation_watch` 强行占一个 Candidate 名额；
- Watch Pool 在 Candidate Engine Phase 正式实现。

---

# 12. Candidate Max 3 的防御性归一化

模型若异常返回：

```text
4+
```

Normalizer 必须：

1. 先过滤：
   - 无代码；
   - 当前持仓；
   - 数据质量阻断；
   - 缺核心理由；
2. 有数字 score 时可优先按 score 降序；
3. 最终：
   ```text
   candidates[:3]
   ```
4. 在 `risk_warnings` 或内部日志记录：
   ```text
   candidate list truncated to 3
   ```

不允许把模型多返回的候选全部展示。

---

# 13. Candidate 不再要求强制 ETF + 股票搭配

删除类似：

```text
如果两个候选，一个 ETF，一个高风险股票
```

这种人为构成规则。

允许：

```text
0 个
1 个 ETF
1 个股票
2 个 ETF
2 个股票
3 个 ETF
3 个股票
混合
```

完全由证据和组合价值决定。

---

# 14. Hard Cap 合同

冻结：

```text
普通股票：20%
行业 / 主题 ETF：30%
```

这是：

```text
Post-action Hard Cap
```

不是要求：

```text
当前超过上限就必须立刻卖。
```

实际动作还需要：

- 市场环境；
- 流动性；
- T+1；
- 风险；
- 交易成本；
- 持仓质量。

---

# 15. Phase A 对 Hard Cap 的处理边界

当前仓库缺少正式：

```text
SecurityMaster
ETF classification
```

因此本阶段：

## 必须做

- 删除“所有标的一律 ≤30%”规则；
- Runtime 使用类型化 Cap；
- Prompt 不再说统一 30%；
- Backend Contract 定义两个默认值；
- 测试保证 Backend 与 Runtime 一致。

## 不允许做

- 根据证券名称字符串猜 ETF 分类后强制仓位；
- 用 LLM 判断 security_type 并作为硬约束；
- 假装 Hard Cap Enforcement 已经完整完成。

正式确定性 Enforcement：

```text
Portfolio Engine Phase
```

---

# 16. Investment Horizon 新合同

旧：

```text
short 1–14
medium 14–90
long 90+
```

改为：

```text
short = 1～5 trading days
swing = 5～20 trading days
medium = 20～120 trading days
```

---

# 17. Horizon 边界定义

由于：

```text
5
20
```

处于边界，避免程序出现双重所属。

推荐机器定义：

```text
short:
1 <= days <= 5

swing:
6 <= days <= 20

medium:
21 <= days <= 120
```

UI 仍可展示：

```text
1～5
5～20
20～120
```

语义上表示区间衔接。

如果未来需要严格从第 5 天切换，可再定义。

---

# 18. Analysis Mode 兼容层

当前：

```text
quick
deep
```

V3 合同：

```text
fast
standard
deep
```

---

# 19. 兼容规则

```text
quick -> fast
fast -> fast
standard -> standard
deep -> deep
```

旧数据库行：

```text
quick
```

必须仍能读取。

旧 API client：

```text
quick
```

必须仍能创建任务。

---

# 20. Phase A 不假装三种模式已经完全不同

当前 `analysis_engine.py` 的所谓 quick/deep：

主要差异是：

```text
manager_profile
```

整条 Phase 仍基本都会跑。

因此 Phase A：

- 建立 canonical mode；
- 前后端接受新枚举；
- 保留 quick alias；
- 不大改 pipeline。

后续再真正优化：

```text
Fast
Standard
Deep
```

不同 Agent / Token / Debate 深度。

---

# 21. 推荐新增文件

```text
backend/app/decision_contract.py
```

职责：

> 只定义 V3 决策合同和纯函数，不访问 DB，不访问网络，不调用 LLM。

---

# 22. decision_contract.py 推荐结构

示意：

```python
class DecisionContract:
    candidate_min = 0
    candidate_max = 3

    stock_hard_cap = 0.20
    sector_theme_etf_hard_cap = 0.30

    horizon_short = (1, 5)
    horizon_swing = (6, 20)
    horizon_medium = (21, 120)
```

并提供纯函数：

```python
normalize_analysis_mode()
has_actionable_portfolio_change()
should_normalize_to_no_action()
```

如果希望使用 Pydantic：

```text
可以
```

但不要为了本阶段引入复杂领域框架。

---

# 23. runtime.json 修改

文件：

```text
skill/tradingagents-holdings-advisor/runtime.json
```

建议：

```text
version:
2.0.0 -> 2.1.0
```

不要直接叫：

```text
3.0.0
```

因为整个产品 V3 尚未完成。

---

# 24. runtime.json 新增 decision_contract

示意：

```json
{
  "decision_contract": {
    "default_portfolio_action": "no_action",
    "candidate_count": {
      "min": 0,
      "max": 3
    },
    "new_candidates_exclude_current_holdings": true,
    "position_hard_caps": {
      "stock": 0.20,
      "sector_theme_etf": 0.30
    },
    "horizons": {
      "short": [1, 5],
      "swing": [6, 20],
      "medium": [21, 120]
    },
    "analysis_modes": {
      "canonical": ["fast", "standard", "deep"],
      "aliases": {
        "quick": "fast"
      }
    }
  }
}
```

---

# 25. runtime.json Core Rules 必须增加

至少加入：

```text
NO_ACTION is a first-class valid decision.
```

```text
Do not recommend a trade merely because the analysis pipeline completed.
```

```text
First determine whether the current portfolio needs to change.
```

```text
New candidates may contain zero to three non-held instruments.
```

```text
Current holdings must never appear as new candidates.
```

```text
Stock hard cap is 20%; sector/theme ETF hard cap is 30% when instrument type is verified.
```

---

# 26. runtime.json 必须删除/替换的旧概念

删除任何等价于：

```text
candidate already held may be shown as add candidate
```

的新 Candidate 规则。

改成：

```text
existing holding add decisions belong to holding actions.
```

---

# 27. skill_runtime.py 修改

文件：

```text
backend/app/services/skill_runtime.py
```

当前只读取：

- version；
- rules；
- phases；
- checkpoints。

新增：

```text
decision_contract
```

到：

- validation；
- `runtime_prompt()`；
- `runtime_metadata()`。

---

# 28. Runtime Prompt 的输出

建议增加：

```text
Decision contract:
- Default portfolio action: NO_ACTION
- New candidates: 0-3
- Current holdings excluded from new candidates
- Stock hard cap: 20%
- Sector/theme ETF hard cap: 30%
- Horizons...
- Analysis modes...
```

这样生产运行模型不会只读旧自然语言。

---

# 29. Skill SKILL.md 修改

文件：

```text
skill/tradingagents-holdings-advisor/SKILL.md
```

需要处理：

## A. 描述

当前涉及：

```text
A-share/HK-related
```

V1 应收敛为：

```text
A-share stocks and exchange-traded ETFs
```

美国市场只是未来架构能力，不在当前 Skill 运行范围。

---

## B. Core Rule

加入：

> 先判断是否需要改变当前组合，再判断改变什么。

---

## C. Candidate

删除：

```text
Select 1-2 buy/rotation candidates
```

改：

```text
Select 0-3 new non-held candidates only when they pass the decision gate.
```

---

## D. 输出

Candidate section：

```text
可以明确输出“当前无新增 Action Candidate”
```

不是必须有标的。

---

# 30. multi-agent-workflow.md 修改

文件：

```text
references/multi-agent-workflow.md
```

至少修改：

## Intent Horizon

旧：

```text
short(1-14)
medium(14-90)
long(90+)
```

改为：

```text
short
swing
medium
```

使用新窗口。

---

## Action Output Note

旧：

```text
必须同时包含当前持仓操作表 + 今日买入/轮动表
```

可以继续保留两个 Section。

但第二个 Section 必须允许：

```text
0 行
```

并显示：

```text
当前无新增 Action Candidate
```

---

## Current Holding Candidate

删除：

```text
current holding labeled add_existing
```

---

# 31. configuration.md 修改

文件：

```text
references/configuration.md
```

必须修改：

```text
single_position_max_ratio = 0.30
```

拆为：

```text
stock_hard_cap_ratio = 0.20
sector_theme_etf_hard_cap_ratio = 0.30
```

---

## Horizon

改为：

```text
horizon_short_days = 1–5
horizon_swing_days = 6–20
horizon_medium_days = 21–120
```

---

## Candidate

增加：

```text
candidate_min_count = 0
candidate_max_count = 3
candidate_force_output = false
new_candidate_exclude_current_holdings = true
```

---

## NO_ACTION

增加：

```text
default_portfolio_action = no_action
```

---

# 32. trading-rules.md 修改

文件：

```text
references/trading-rules.md
```

当前存在最直接的旧规则：

```text
Every daily execution must include 1-2 buy/rotation candidates
```

必须删除。

替换：

> Candidate output is optional. Only output 0–3 new non-held candidates that clearly improve the current portfolio opportunity set after risk and turnover considerations.

---

# 33. trading-rules.md Position Rules

旧：

```text
single_position_max_ratio = 30%
```

替换：

```text
Verified normal stock:
post-action target <=20%

Verified sector/theme ETF:
post-action target <=30%
```

如果类型未验证：

```text
不要声称 30% ETF cap 可用
```

---

# 34. trading-rules.md Dual Horizon

旧 Short / Medium 二轨要更新为：

```text
Short
Swing
Medium
```

但 Phase A 不强制重做三轨 Agent。

推荐文本：

> The contract recognizes three horizons. The existing dual-track implementation remains transitional until a later analysis-pipeline phase.

避免文档声称代码已经实现三轨完整推理。

---

# 35. buy-candidate-selection.md 修改

这是重点文件。

必须删除：

```text
Required Buy Module
```

中的“每次一定产生候选”语义。

---

# 36. Candidate Module 新定义

改为：

```text
Opportunity Module
```

它回答：

1. 当前是否值得新增风险？
2. 是否存在明显优于“什么都不做”的新机会？
3. 如果有，哪些非持仓资产值得进入 Action Candidate？
4. 最多 3 个。
5. 如果没有，为什么没有？

---

# 37. Candidate / Holding Consistency

旧：

```text
现有持仓可作为 add_existing candidate
```

删除。

改：

```text
Current holdings are never new candidates.
```

现有持仓加仓：

```text
Holding Action only.
```

---

# 38. “推荐一个 ETF + 一个股票”规则

如果文件中有：

```text
两个候选时做一个 ETF + 一个股票
```

删除。

---

# 39. Candidate Score

当前：

```text
score >= 7
```

Phase A 可以暂时保留。

原因：

```text
完整 Quant Candidate 尚未实现
```

但是必须改成：

```text
7+ 仅表示旧候选评分达到最低条件
```

不能因此强制输出。

---

# 40. analysis_engine.py 修改点

文件：

```text
backend/app/services/analysis_engine.py
```

不允许重写整个文件。

只做局部、可测试的合同对齐。

---

# 41. CORE_RULES

当前启动后会被 runtime prompt 覆盖。

因此：

- 保留 fallback；
- fallback 也要含 NO_ACTION；
- 不再强制候选；
- 不出现统一 30%。

---

# 42. FINAL_SCHEMA

Portfolio-level：

```text
final_rating
```

说明中加入：

```text
no_action
```

Candidate：

从：

```text
new_position/add_existing/rotation_watch
```

收敛为：

```text
new_position
```

Phase A 若为了兼容历史报告需要读旧类型：

```text
可以继续 parse
```

但新的输出不能再生成 add_existing。

---

# 43. candidate_screening Prompt

旧：

```text
输出 1-2 个候选或明确阻断
```

改：

```text
输出 0-3 个非当前持仓的新机会。
没有明显更优机会时 candidates=[] 是正确结果。
不得为了满足数量而生成候选。
```

还要明确：

```text
当前持仓加仓由 Holding Action 表达。
```

---

# 44. Candidate Evidence Gate

当前缺：

```text
sector_heat
ETF leaders
news
```

会清空 Candidate。

这个安全机制：

```text
保留
```

但清空后：

```text
不是系统失败
```

最终可能：

```text
NO_ACTION
```

若 Quality Gate 本身没 blocked。

---

# 45. _normalize_final Candidate 逻辑

必须修改。

旧逻辑：

```text
若 candidate 是 holding
→ 自动改 add_existing / conditional_add
```

新逻辑：

```text
若 candidate 是 holding
→ remove from candidates
```

并可加入：

```text
risk_warnings:
"候选 XXXX 为当前持仓，已从新增候选中移除；加仓应由持仓动作表达。"
```

---

# 46. _normalize_final Final Rating

加入：

```text
normalize_no_action
```

在：

- Holding actions；
- Candidate filtering；
- Quality Gate；

都处理完成后再判定。

顺序很重要。

推荐：

```text
1 normalize holdings
2 normalize candidates
3 preserve quality-blocked watch_only
4 detect actionable change
5 if none -> no_action
6 finalize portfolio_manager_final
```

---

# 47. portfolio_manager_final

确保：

```text
portfolio_rating
```

同步最终：

```text
no_action
```

不能出现：

```text
result.final_rating = no_action
portfolio_manager_final.portfolio_rating = hold
```

---

# 48. Markdown Report

如果：

```text
NO_ACTION
```

报告显示：

```text
组合方向：NO_ACTION
```

并用自然语言：

```text
当前没有足够证据证明调整优于保持现状。
```

---

# 49. Candidate Markdown

Candidate 为空时：

不要显示：

```text
错误
```

而显示：

```text
当前无达到 Action Gate 的新增机会。
```

Data Quality 被阻断时则显示：

```text
候选被数据质量门控阻断
```

这两个原因要区分。

---

# 50. v2_schemas.py 修改

当前：

```text
AnalysisMode = Literal["quick", "deep"]
```

修改成兼容输入。

推荐：

```text
Literal["quick", "fast", "standard", "deep"]
```

在创建 Job 时：

```text
normalize
```

成 canonical。

---

# 51. AnalysisJob mode 数据库

当前是：

```text
String(16)
```

无需 migration。

旧：

```text
quick
```

可以继续存在。

---

# 52. analysis_v2.py 修改

创建 Job 时：

```text
mode = normalize_analysis_mode(payload.mode)
```

Response 继续返回实际 mode。

旧 client 发：

```text
quick
```

也成功。

---

# 53. scheduler.py / automation_v2.py

不做 Scheduler 架构修改。

只保证：

- Schedule 创建接受新 Mode；
- 旧 Schedule `quick` 仍可运行；
- 新任务时 canonicalize。

---

# 54. 前端 types.ts

当前：

```text
AnalysisMode = 'quick' | 'deep'
```

改为：

```text
'quick' | 'fast' | 'standard' | 'deep'
```

其中：

```text
quick
```

仅为了读旧数据。

---

# 55. DashboardView / UploadView

新建任务的 UI 选项建议显示：

```text
快速
标准
深度
```

提交：

```text
fast
standard
deep
```

不要再新建 `quick`。

---

# 56. SettingsView Schedule

Schedule Mode 同步使用：

```text
fast
standard
deep
```

已有 `quick` Schedule：

- 读取时能显示；
- 编辑保存后可转换 canonical。

---

# 57. 前端 actionLabels

Reports 中增加：

```text
no_action: '无需调整'
```

不要显示生硬：

```text
no_action
```

---

# 58. Reports Candidate Empty State

如果 candidates=[]：

根据：

```text
candidate_status
candidate_blocked_reason
quality_gate
```

展示：

### 正常

```text
暂无达到行动门槛的新增机会
```

### 数据问题

```text
候选分析被数据质量门控阻断
```

---

# 59. Skill 与 Backend Contract 同步测试

新增测试：

```text
test_decision_contract_sync.py
```

读取：

```text
runtime.json
```

对比：

```text
backend decision_contract
```

至少断言：

- candidate min；
- candidate max；
- stock cap；
- sector ETF cap；
- horizon；
- mode alias；
- default action。

防止以后 Markdown / Python 再次漂移。

---

# 60. 必须新增的后端单测

## Test 1

```text
all holdings hold/watch
candidates=[]
quality pass
```

结果：

```text
final_rating=no_action
```

---

## Test 2

Quality Gate blocked：

结果：

```text
watch_only
```

不能变 no_action。

---

## Test 3

模型返回：

```text
4 candidates
```

最终：

```text
<=3
```

---

## Test 4

Candidate code 已存在 holdings：

最终：

```text
candidate removed
```

---

## Test 5

Holding Action：

```text
add
```

可以继续存在。

但：

```text
same code not in candidates
```

---

## Test 6

`quick`

输入：

```text
quick
```

canonical：

```text
fast
```

---

## Test 7

`standard`

可创建 AnalysisJob。

---

## Test 8

旧 `deep`

行为不回归。

---

# 61. 更新现有 E2E

文件：

```text
backend/tests/test_v2_portfolio_analysis.py
```

当前 fake 模型返回：

```text
hold
candidates=[]
```

Phase A 后应该期望：

```text
final_rating=no_action
```

---

# 62. E2E Candidate 断言

增加：

```text
structured["result"]["candidates"] == []
```

是合法成功。

---

# 63. E2E 不要求删除 candidate_screening 阶段

当前完整 pipeline 仍可保留：

```text
buy_candidate_selection
```

说明：

```text
扫描运行了
```

不代表：

```text
必须产生候选
```

---

# 64. Skill 文档一致性检查

建议新增一个轻量测试或脚本搜索这些旧字符串：

```text
1-2 buy
must include 1-2
single_position_max_ratio = 0.30
short(1-14
medium(14-90
add_existing candidate
```

发现后：

```text
fail
```

可仅扫描关键 Skill 文件。

---

# 65. Phase A 不需要 Alembic

本阶段：

```text
0 migration
```

如果 Codex 尝试新建数据库表：

```text
停止
```

除非是修复当前仓库已有的明显 bug 且与本任务直接相关。

---

# 66. Phase A 不改 Market Data

不改：

```text
market_data.py
market_snapshot.py
```

除非其中出现本阶段明确要清理的 Candidate / Hard Cap 规则。

不要借机重构 Provider。

---

# 67. Phase A 不改通知策略

现有：

```text
AnalysisRun 完成后通知
```

与 V3 最终设计不同。

但通知事件化属于后续 Phase。

本阶段：

```text
保持现状
```

避免 scope creep。

---

# 68. Phase A 不新增 v3 API

本阶段不需要：

```text
/api/v3/*
```

因为还没有新的 Market / Trigger / Candidate 领域资源。

继续兼容：

```text
/api/v2/*
```

---

# 69. 向后兼容要求

必须保证：

- 已有数据库可直接启动；
- Alembic 不需要新 migration；
- 旧 `quick` task 可读取；
- 旧 AnalysisRun 可展示；
- 旧 `add_existing` 历史报告前端可展示；
- 新报告不再产生 `add_existing` candidate；
- V1 Archive routes 不变；
- JWT 不变；
- Screenshot Flow 不变。

---

# 70. 旧历史报告不能被重写

历史：

```text
AnalysisRun
```

是不可变历史。

Phase A 不能做：

```text
批量修复旧报告
```

只对新 AnalysisRun 使用新合同。

---

# 71. 建议增加 contract version

每个新 AnalysisRun 的：

```text
structured_result_json
```

建议借现有：

```text
skill_runtime
```

记录：

```text
decision_contract_version
```

例如：

```text
v3-contract-2026-08-23
```

无需新增数据库列。

---

# 72. Feature Flag

Phase A 可增加：

```text
V3_CONTRACT_ENABLED=true
```

但我的推荐是：

> **不增加。**

理由：

Contract Alignment 是修正旧错误语义，不应该长期双轨。

需要兼容的是：

```text
旧输入
```

不是继续生产两套决策规则。

---

# 73. 代码风格要求

新增纯函数尽量：

- 无副作用；
- 易单测；
- 不依赖 Session；
- 不调用网络；
- 不调用模型。

避免继续扩大：

```text
analysis_engine.py
```

---

# 74. 最小改动原则

如果一个旧函数能通过：

```text
import decision_contract
```

解决：

不要：

```text
新建完整 domain framework
```

---

# 75. 预计修改文件

核心：

```text
backend/app/decision_contract.py              [NEW]

backend/app/services/analysis_engine.py
backend/app/services/skill_runtime.py
backend/app/routers/analysis_v2.py
backend/app/routers/automation_v2.py
backend/app/v2_schemas.py

frontend/src/api/types.ts
frontend/src/views/DashboardView.vue
frontend/src/views/UploadView.vue
frontend/src/views/ReportsView.vue
frontend/src/views/SettingsView.vue

skill/tradingagents-holdings-advisor/runtime.json
skill/tradingagents-holdings-advisor/SKILL.md
skill/tradingagents-holdings-advisor/references/configuration.md
skill/tradingagents-holdings-advisor/references/trading-rules.md
skill/tradingagents-holdings-advisor/references/multi-agent-workflow.md
skill/tradingagents-holdings-advisor/references/buy-candidate-selection.md

backend/tests/test_v2_portfolio_analysis.py
backend/tests/test_decision_contract.py                 [NEW]
backend/tests/test_skill_runtime.py
```

可能不需要每个文件最终都改。

Codex 应先检查：

```text
是否真的受本阶段影响
```

再修改。

---

# 76. 禁止 Codex 做的事

不要：

- 删除 V2；
- 修改数据库结构；
- 新增 Market Engine；
- 新增 Redis；
- 新增 Celery；
- 新增 PostgreSQL；
- 大幅移动目录；
- 重写 `analysis_engine.py`；
- 修改 Auth；
- 修改 Screenshot storage；
- 修改 Vision；
- 改 Archive API；
- 重做 UI；
- 改 Docker topology；
- 引入新的状态管理库；
- 引入重量级依赖；
- 使用 mock/fake 数据充当新功能实现。

---

# 77. 验收标准

Phase A 只有满足全部才算完成。

## Contract

- [ ] NO_ACTION 是一等合法结果；
- [ ] Candidate 允许 0；
- [ ] Candidate 最多 3；
- [ ] Candidate 只包含非持仓；
- [ ] 当前持仓加仓只在 Holding Action；
- [ ] 不强制 ETF + 股票组合；
- [ ] 普通股票 20%；
- [ ] 行业/主题 ETF 30%；
- [ ] 不再存在统一 30% 规则；
- [ ] short / swing / medium 新周期；
- [ ] quick -> fast alias；
- [ ] fast / standard / deep 可接受。

---

## Runtime

- [ ] runtime.json 有 machine-readable contract；
- [ ] runtime_prompt 包含新合同；
- [ ] runtime metadata 保留 SHA；
- [ ] 新 AnalysisRun 能追溯合同版本。

---

## Backend

- [ ] 无动作时 deterministic -> no_action；
- [ ] blocked 仍是 watch_only；
- [ ] Candidate 去除当前持仓；
- [ ] Candidate <=3；
- [ ] 旧 quick 兼容；
- [ ] 无数据库 migration。

---

## Frontend

- [ ] NO_ACTION 显示“无需调整”；
- [ ] Candidate 为空不是错误；
- [ ] Fast / Standard / Deep 可选；
- [ ] 旧报告仍可读；
- [ ] typecheck 通过；
- [ ] build 通过。

---

## Tests

- [ ] pytest 全量通过；
- [ ] Alembic upgrade 仍通过；
- [ ] frontend typecheck 通过；
- [ ] frontend build 通过；
- [ ] Docker build 通过；
- [ ] 新 Contract Tests 通过。

---

# 78. 手工验收场景

## 场景 A｜优秀组合，无更好机会

输入：

```text
所有持仓 hold
candidates=[]
quality=A
```

结果：

```text
Portfolio = NO_ACTION
```

页面：

```text
无需调整
```

---

## 场景 B｜有一只弱仓需减仓

输入：

```text
600000 reduce
其他 hold
candidate=[]
```

结果：

```text
不能 NO_ACTION
```

---

## 场景 C｜当前持仓适合加仓

输入：

```text
600519 add
```

Candidate 模型又返回：

```text
600519
```

结果：

```text
Holding Action 保留 add
New Candidate 删除 600519
```

---

## 场景 D｜没有新增机会

Candidate 模型：

```text
[]
```

AnalysisRun：

```text
succeeded
```

不是：

```text
failed
```

---

## 场景 E｜数据质量失败

quote 缺失：

```text
watch_only
```

不是：

```text
no_action
```

---

## 场景 F｜模型乱给 5 个 Candidate

最终：

```text
最多 3 个
```

---

# 79. PR 建议

Phase A 推荐：

```text
1 个 PR
```

但内部 commit 可以拆：

### Commit 1

```text
decision contract + runtime
```

### Commit 2

```text
analysis normalizer + API compatibility
```

### Commit 3

```text
Skill docs alignment
```

### Commit 4

```text
frontend compatibility
```

### Commit 5

```text
tests
```

---

# 80. PR 标题建议

```text
feat: align V2 runtime with V3 decision contract
```

---

# 81. PR 描述必须明确

说明：

```text
This PR does NOT implement the V3 market engine, realtime monitor,
trigger engine, quant candidate engine, or alpha memory.
```

防止后续误以为：

```text
V3 已完成。
```

---

# 82. Phase A 完成后的系统状态

系统仍然主要是：

```text
V2 功能架构
```

但决策合同已经升级：

```text
V3 Contract
```

所以状态可以叫：

```text
V2 Runtime + V3 Decision Contract
```

---

# 83. Phase A 后的下一阶段

下一阶段：

# Phase B｜Market Data Foundation

进入：

```text
SecurityMaster
TradingCalendar
Provider Adapter
Provider Health
Data Lineage
Field-level Quality
All-A Batch Quote Foundation
```

然后才：

```text
Phase C Market Engine
```

---

# 84. 最终冻结语句

Phase A 的核心原则：

> **分析完成不等于必须交易。**

> **没有足够强的新证据、没有明显更优的风险收益比，就保持当前组合。**

> **新增候选可以为 0。**

> **已有持仓的加仓不是“新增候选”。**

> **数据不足导致的 WATCH_ONLY 与证据充分后的 NO_ACTION 必须严格区分。**

> **本阶段只统一合同，不提前伪造后续量化能力。**
