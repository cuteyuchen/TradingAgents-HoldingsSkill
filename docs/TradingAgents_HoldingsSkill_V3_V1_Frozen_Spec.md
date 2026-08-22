# TradingAgents-HoldingsSkill V3 / AI 投资决策系统 V1.0 冻结功能规格

> 文档状态：**功能冻结（Product Freeze）**  
> 适用范围：V1.0 产品开发、Codex 实施、验收与后续 V1.5 规划  
> 市场范围：**A 股（沪深普通 A 股）+ 场内 ETF**  
> 核心目标：在不承诺收益的前提下，以**长期风险调整后收益、回撤控制、低无效换手、稳定复利**为产品优化方向。  
> 最高原则：**先判断有没有必要改变当前组合，再判断改变什么。没有足够强的新证据、没有明显更优的风险收益比，就保持现状。候选可以为 0，交易可以为 0，系统不以产生建议为 KPI。**

---

## 0. 文档用途

本规格用于将现有 `TradingAgents-HoldingsSkill V2` 演进为 V3 / AI 投资决策系统 V1.0。

本阶段**不进入自动交易**，不推翻现有 V2 架构，不追求“更多 Agent”，而是建立完整的：

**市场状态 → 实时监控 → 触发 → 持仓分析 → 候选发现 → 组合决策 → 用户实际操作 → 后验结果 → Alpha Memory**

闭环。

任何开发实现若与本规格冲突，以本规格为准；若本规格未定义，则优先采用“少交易、可解释、数据可信、可回滚、可验证”的原则，不得自行扩展为高频交易或自动下单系统。

---

# 1. 产品定位

## 1.1 产品是什么

这是一个面向个人自托管场景的 **A 股 / ETF 投资决策系统**。

它的职责不是每日“推荐股票”，而是持续回答以下问题：

1. 当前市场是否适合承担风险？
2. 当前持仓是否仍然合理？
3. 当前组合是否需要调整？
4. 是否存在明显优于“维持现状”的新机会？
5. 如果需要行动，应该加仓、减仓、退出、新建仓还是换仓？
6. 如果不需要行动，明确输出 `NO_ACTION`。
7. 系统过去的判断、用户真实执行与后续结果如何？
8. 哪些信号、策略与行为真正创造或损害了风险调整后收益？

## 1.2 产品不是什么

V1.0 明确不做：

- 自动下单；
- 券商 Broker Gateway；
- 毫秒级 / 高频交易；
- 打板专用系统；
- 为了推荐而推荐；
- LLM 自由扫描 5000+ 股票后凭主观生成标的；
- LLM 直接计算基础量化指标；
- 自动修改策略因子权重；
- 保证盈利、保证胜率、保证稳定收益。

---

# 2. V2 现有能力：必须保留并演进

以下能力视为现有产品资产，不得无理由删除或重做：

- 用户注册、登录、JWT；
- 多用户数据隔离；
- Vision / Analysis / Deep Analysis 模型独立配置；
- 多模型提供商兼容；
- API Key / 通知凭据加密；
- 持仓截图上传与 AI 识图；
- 人工修改后确认不可变 Portfolio Snapshot；
- 腾讯实时行情；
- 东财 K 线、技术、资金流、公告；
- 数据质量门控；
- Fast / Deep 分析；
- 多 Agent 分析；
- Bull / Bear Debate；
- Risk / Portfolio Manager；
- 历史建议上下文；
- 建议一致性检查；
- Analysis Job 状态、取消、重试、SSE；
- 报告历史、证据、截图、前后比较；
- 定时分析；
- 快照过期阻断；
- 幂等执行；
- 连续失败自动停用计划；
- 钉钉 / 企业微信通知；
- `/api/v1/archives` 兼容接口；
- Docker / GHCR 部署方式；
- 现有 SQLite + Alembic 数据迁移体系。

V3 是在这些能力之上增加实时决策闭环，而不是整仓重写。

---

# 3. V1.0 总体决策链

```text
Provider Layer
      ↓
Data Quality Gate
      ↓
Realtime Market Snapshot
      ↓
Market Metrics
      ↓
Market Score / Market Regime
      ↓
Realtime Monitor
      ↓
Trigger Engine
      ↓
Fast / Standard / Deep Analysis
      ↓
Holding / Candidate Evaluation
      ↓
Portfolio Fit
      ↓
Portfolio Manager
      ↓
Minimum Decision Edge
      ↓
ACTION / NO_ACTION
      ↓
User Action / Trade Ledger
      ↓
Outcome Tracking
      ↓
Alpha Memory
```

`NO_ACTION` 必须与 BUY / ADD / REDUCE / EXIT 等操作拥有同等正式地位。

---

# 4. 市场范围与证券宇宙

## 4.1 V1 市场

V1 全面支持：

- 沪市普通 A 股；
- 深市普通 A 股；
- 场内 ETF。

架构层面从第一天保留：

```text
market = CN | US
```

但 V1 不实现完整美股功能。

## 4.2 “全 A”核心样本

Market Score 的全 A 样本固定为：

- 沪深两市普通 A 股；
- 不含 ETF；
- 排除停牌；
- 排除退市整理期；
- 上市不足 20 个交易日暂不进入核心宽度 / 中位数；
- ST / *ST 单独统计，但不进入核心 Market Score；
- 北交所独立观察，不混入主全 A。

---

# 5. Market Score

## 5.1 定义

范围：

```text
0 ------------------------------------ 100
最不适合承担风险                    最适合承担风险
```

Market Score 不是“风险高低分”，而是“当前市场适合承担多少风险”的综合评分。

## 5.2 五级 Regime

初始映射：

- 81–100：Strong Risk-On
- 61–80：Risk-On
- 41–60：Neutral
- 21–40：Risk-Off
- 0–20：Strong Risk-Off

这些阈值为 V1 默认配置，必须配置化；V1.5 用历史回测重新校准。

## 5.3 一级模块与基础权重

| 模块 | 权重 |
|---|---:|
| Breadth 市场宽度 | 20% |
| Trend 趋势结构 | 20% |
| Liquidity 流动性 | 15% |
| Profitability 赚钱效应 | 15% |
| Diffusion 行业/风格扩散 | 10% |
| Crowding 拥挤与集中 | 10% |
| Tail Risk 极端风险 | 10% |

基础公式：

```text
MarketScore =
0.20 * Breadth +
0.20 * Trend +
0.15 * Liquidity +
0.15 * Profitability +
0.10 * Diffusion +
0.10 * Crowding +
0.10 * TailRisk
```

## 5.4 归一化

所有指标最终转换为 0–100。

默认采用滚动历史分位：

- 主窗口：3 年；
- 辅助展示：1 年 / 5 年；
- 正向指标：Percentile × 100；
- 反向指标：100 - Percentile × 100。

不得将不同量纲的原始指标直接相加。

---

# 6. Market Score 子指标

## 6.1 Breadth 20%

内部建议权重：

- 全 A 中位涨跌幅：25%
- 上涨家数比例：20%
- MA20 上方比例：20%
- MA60 上方比例：15%
- 60 日新高 / 新低结构：20%

同时后台存储：

- MA5 / 10 / 20 / 60 / 120 / 250 宽度；
- 20 / 60 / 120 / 250 日新高、新低；
- > +1 / +3 / +5 / +7% 股票比例；
- < -1 / -3 / -5 / -7% 股票比例。

## 6.2 全 A 中位指数

每日：

```text
MedianReturn(t) = median(有效全A股票当日收益率)
MedianIndex(t) = MedianIndex(t-1) * (1 + MedianReturn(t))
```

初始值：

```text
1000
```

必须形成历史连续序列。

## 6.3 Trend 20%

建议：

- MA5 > MA20 股票比例；
- MA20 > MA60 股票比例；
- MA60 向上股票比例；
- 全 A 中位指数趋势；
- 沪深300 / 中证500 / 中证1000 / 创业板 / 科创50 趋势确认。

主要指数仅用于辅助确认，不能覆盖全 A 内部结构。

## 6.4 Liquidity 15%

盘中必须采用 **Same-Time Comparison**。

例如 10:00 的累计成交额，只与历史 10:00 比较，不能与昨日全天比较。

指标包括：

- 同时点成交额强度；
- 预计全天成交额；
- 个股成交额中位数；
- 换手率中位数；
- 成交活跃股票比例。

预计全天成交额必须基于历史日内成交曲线，不允许简单线性外推。

## 6.5 Profitability 15%

包括：

- 全 A 中位收益；
- >3% 股票比例；
- <-3% 股票比例；
- 涨停 / 跌停结构；
- 昨日强势股延续性；
- 大幅亏损股票比例。

涨停 / 连板生态只作为组成部分，不成为主导指标。

## 6.6 Diffusion 10%

包括：

- 上涨行业比例；
- 行业中位收益；
- 强势行业数量；
- 行业收益离散度；
- 大小盘同步程度。

## 6.7 Crowding 10%

核心指标：

```text
Top5Concentration =
成交额排名前5%的股票成交额 /
全市场股票成交额
```

后台同时保存：

- Top1%
- Top3%
- Top5%
- Top10%
- Top20%

不得简单将“集中度高 = 风险高”。

必须结合：

- 集中度绝对水平；
- 集中度变化速度；
- 市场宽度；
- 全 A 中位收益。

## 6.8 Tail Risk 10%

包括：

- 跌停率；
- <-5% 股票比例；
- 市场收益离散度异常；
- 波动率异常；
- 新低急增；
- 主要指数快速下跌；
- 日内极端反转。

---

# 7. Market Score 稳定机制

## 7.1 Smoothing

默认：

```text
DisplayedScore =
70% PreviousDisplayedScore +
30% CurrentRawScore
```

参数配置化。

## 7.2 Regime Hysteresis

状态进入阈值与退出阈值不同，避免在边界反复切换。

示例：

- 进入 Risk-On：≥63
- 退出 Risk-On：<58

具体值配置化，V1.5 回测校准。

## 7.3 Confidence

Market Score 必须同时输出：

```text
score
confidence
quality_status
```

数据异常时禁止把异常评分直接用于主动加仓。

---

# 8. 实时运行模式

V1 正式采用三层速度：

## 8.1 Monitor

- 交易时段持续运行；
- 行情默认 1 分钟更新；
- 持仓 / Ready / Action Trigger 默认每 1 分钟检查；
- 不调用完整 LLM 链；
- 主要完成数据采集、量化计算、规则判断。

## 8.2 Market Score

- 默认每 5 分钟重算；
- 可配置 1 / 5 / 10 / 15 / 30 分钟；
- 默认 5 分钟。

## 8.3 Fast Analysis

仅在重要 Trigger 后启动。

Fast Analysis 只运行与本次 Trigger 相关的 Agent / 数据刷新，不从头执行全部 Agent。

## 8.4 Standard / Deep

固定时点执行战略重评。

---

# 9. Trigger Engine

## 9.1 职责

Trigger 只回答：

> 是否出现足够重要的新情况，值得重新分析？

Trigger **不等于交易**。

## 9.2 类型

V1 支持：

1. Market Trigger
2. Holding Trigger
3. Candidate Trigger
4. Portfolio Trigger
5. Event Trigger
6. Data Quality Trigger

## 9.3 市场 Trigger

默认初始阈值：

- 15 分钟 Market Score 变化 ≥8：Soft Trigger；
- 15 分钟变化 ≥15：Hard Trigger；
- Regime 切换；
- Breadth 快速恶化 / 改善；
- 同时间成交异常；
- 市场放量杀跌；
- 极端尾部风险。

阈值全部配置化。

## 9.4 Holding Trigger

每个持仓必须拥有结构化 Trigger Plan：

- Bull Trigger；
- Bear Trigger；
- Hard Invalidation；
- 价格条件；
- 趋势条件；
- ATR / 波动异常；
- 成交量异常；
- Event Trigger；
- Otherwise = NO_ACTION。

## 9.5 Candidate Trigger

包括：

- 突破关键区域 + 成交确认；
- 回撤进入合理风险收益区；
- 行业 / Regime 条件满足；
- 财报、公告、政策等事件；
- Entry Score 明显改善。

## 9.6 Portfolio Trigger

包括：

- 单标的集中度异常；
- Top3 / Top5 集中度；
- 行业 / 风格暴露异常；
- 组合相关性快速升高；
- 总仓位与 Regime 显著不匹配；
- ETF 穿透后真实暴露超限。

## 9.7 Data Quality Trigger

Provider 异常、数据冲突、行情缺失时：

```text
冻结受影响指标
→ fallback
→ 重新验证
→ 数据可信后才允许影响决策
```

## 9.8 防抖与冷却

Trigger 需要支持：

- 连续 N 次确认；
- 持续 N 分钟确认；
- cooldown；
- 更高级 Trigger 可突破 cooldown。

优先级：

- P0 Critical
- P1 High
- P2 Normal
- P3 Informational

---

# 10. Trigger Plan

Standard / Deep 完成后，对：

- 所有当前持仓；
- 重点 Ready / Watch 标的；

生成结构化 Trigger Plan。

必须保存：

```text
generated_at
valid_until
strategy_version
market_context
bull_conditions
bear_conditions
hard_invalidation
otherwise_action
```

Trigger Plan 不能只存在 Markdown 文本中。

---

# 11. 持仓与交易流水

## 11.1 Portfolio Snapshot

继续保留现有不可变确认快照。

当前持仓字段至少包括：

- market
- code
- name
- qty
- available_qty
- cost
- price
- market_value
- pnl
- return
- weight

## 11.2 Snapshot Diff

任意两个持仓快照可比较：

- NEW
- ADD
- REDUCE
- EXIT
- UNCHANGED

## 11.3 Trade Ledger

V1 数据模型必须完整支持：

- 截图导入；
- CSV；
- Excel；
- 手工录入；
- 快照变化推断。

字段至少：

```text
trade_time
market
code
side
price
quantity
amount
commission
tax
position_before
position_after
source
reason
confirmed
revision_history
```

“快照推断交易”必须与真实已确认交易区分。

## 11.4 交易理由

AI 可根据当时上下文和系统 Decision 生成建议理由，但用户可：

- 确认；
- 修改；
- 删除；
- 添加。

最终存储的是用户确认后的版本。

---

# 12. 持仓分析

每个持仓支持三个时间尺度：

- 短期：1–5 交易日；
- 波段：5–20 交易日；
- 中期：20–120 交易日。

分析维度：

- 市场环境；
- 行业；
- 技术；
- 基本面；
- 资金；
- 新闻 / 事件；
- 政策；
- 风险。

## 12.1 Keep Score

每个现有持仓必须拥有 Keep Score：

> 已经持有的情况下，继续持有它是否仍然合理？

Keep Score 与新建仓 Opportunity Score 分开。

## 12.2 Decision Delta

如果建议从 HOLD 变 REDUCE、从 REDUCE 变 ADD 等，必须明确列出导致结论变化的新证据。

没有足够新证据时，不允许无解释反向。

---

# 13. 股票候选模型

候选必须由 **Quant First → LLM Second**。

LLM 不得直接从全 A 自由生成推荐。

## 13.1 基础过滤

用户可配置；系统提供稳健默认预设。

可配置项：

- 排除 ST / *ST；
- 排除北交所；
- 上市不足 N 日；
- 停牌；
- 退市整理；
- 最低平均成交额；
- 最低市值；
- 最低价格；
- 其他流动性条件。

## 13.2 股票 Opportunity Score

基础结构：

| 维度 | 权重 |
|---|---:|
| Trend | 20% |
| Momentum | 15% |
| Fundamental Quality / Growth | 20% |
| Valuation | 10% |
| Flow / Price-Volume | 15% |
| Industry / Theme | 10% |
| Risk | 10% |

基础权重配置化，但 V1 不允许系统自动修改。

## 13.3 Momentum

必须采用多周期与相对强度：

- 5 / 20 / 60 / 120 日；
- vs 全 A；
- vs 所属行业；
- vs 对应宽基。

不得“涨得越多分越高”。

过度延伸需要降低 Entry 价值。

## 13.4 基本面

重点：

- ROE；
- 毛利率；
- 净利率；
- 经营现金流；
- 净利润现金含量；
- 营收同比；
- 利润同比；
- 增长连续性；
- 负债与现金安全。

优先同行业相对分位，禁止跨行业使用完全相同绝对标准。

## 13.5 Valuation

考虑：

- 当前估值；
- 自身历史估值分位；
- 行业估值分位；
- 增长与盈利质量。

不能机械使用“PE 越低越好”。

## 13.6 资金 / 量价

包括：

- 成交额趋势；
- 相对成交量；
- 换手；
- 放量上涨 / 下跌；
- 资金持续性。

“主力净流入”只能作为弱证据，不可单独触发买卖。

---

# 14. ETF 候选模型

ETF 必须独立评分，不照搬股票基本面。

| ETF 维度 | 权重 |
|---|---:|
| 标的指数 / 行业趋势 | 25% |
| 相对强度 | 20% |
| 成交与流动性 | 15% |
| 估值状态 | 15% |
| 成分股内部宽度 | 15% |
| 风险 / 波动 | 10% |

ETF 分类：

- Broad Index；
- Sector / Theme；
- Smart Beta。

不同类别允许使用不同子权重。

## 14.1 ETF 内部结构

至少计算：

- 成分股上涨比例；
- 成分股中位收益；
- 成分股 MA20 上方比例；
- Top10 权重贡献；
- 成分股成交集中度。

防止 ETF 表面上涨但内部只有少数权重股支撑。

---

# 15. Market Regime 动态调权

不同 Regime 对候选因子采用不同偏好。

例如：

- Strong Risk-On：提高 Trend / Momentum / Industry；
- Neutral：提高 Quality / Valuation / Risk-Reward；
- Risk-Off：提高 Risk / Quality / Low Volatility，降低高 Beta 高估值成长偏好。

V1 可使用配置化规则调权；不得由 LLM 临时自行修改。

---

# 16. Entry Score

Opportunity Score 回答：

> 标的是不是好机会？

Entry Score 回答：

> 当前价格是不是值得介入？

必须考虑：

- 距关键均线；
- ATR 偏离；
- 近期涨幅；
- 支撑 / 阻力；
- 潜在失效距离；
- 合理收益空间；
- Risk / Reward。

系统不输出伪精确目标价。

必须使用：

- 合理介入区间；
- 核心观察区间；
- 第一压力区域；
- 风险失效区间；
- 条件式 Trigger。

---

# 17. 候选层级

V1 固定三层：

## Watchlist

- 可 10–30+；
- 值得持续关注；
- 不代表值得买。

## Ready

- 0–10；
- 机会本身较好；
- 当前差一个触发条件或 Entry 不足。

## Action

- 股票 + ETF 合计 **0–3**；
- 不强制股票 / ETF 比例；
- 允许 0；
- 当前持仓不进入“新增 Action Candidate”。

只有通过全部 Decision Gate 才可进入 Action。

---

# 18. Portfolio Fit

新增标的在进入最终 Action 前，必须评估对当前组合的影响：

- 行业相关性；
- 收益相关性；
- 风格暴露；
- 波动贡献；
- ETF 与股票底层重合；
- 分散化收益；
- 当前持仓替代关系。

## 18.1 ETF Look-Through

V1 实现 ETF 穿透分析。

保存：

```text
tracking_index
constituents
constituent_weight
effective_date
source
```

计算：

- 真实个股暴露；
- 真实行业暴露；
- ETF 间底层重合；
- ETF + 直持股票合并暴露。

---

# 19. Portfolio Manager

## 19.1 决策顺序

```text
Market Regime
→ 总风险预算
→ 当前组合是否已经足够好
→ 当前持仓是否需要处理
→ 是否存在明显更优新机会
→ Keep / Add / Reduce / Exit / New / Replace / Cash
→ 组合风险与换手成本
→ Minimum Decision Edge
→ ACTION / NO_ACTION
```

## 19.2 现金

现金正式视为资产。

系统不得因为账户有现金而强制寻找买入机会。

Risk-Off 下允许：

- 60–80% 现金；
- 极端情况下 >80% 现金。

## 19.3 最大回撤

不设置固定机械“最大回撤止损线”。

采用动态风险模型：

- Market Regime；
- 组合波动；
- 相关性；
- 持仓质量；
- 尾部风险；
- 组合集中度。

## 19.4 Hard Cap

普通单只股票：

```text
20%
```

行业 / 主题 ETF：

```text
30%
```

实际目标仓位由系统动态计算，Hard Cap 仅为不可突破上限。

宽基 ETF 可设计更高上限，但 V1 需在配置中单独定义，不与行业 ETF 共用 30%。

## 19.5 动态仓位

基础逻辑：

```text
Base Weight
× Opportunity Factor
× Regime Factor
× Volatility Adjustment
× Correlation Adjustment
× Portfolio Fit
```

最后应用：

- 单标的 Hard Cap；
- 行业上限；
- 风格上限；
- 流动性约束。

LLM 负责解释，不负责拍脑袋给数字。

## 19.6 No-Trade Zone

每个持仓输出：

- 当前仓位；
- 目标中心；
- 合理目标区间。

只要当前仓位位于 No-Trade Zone，且没有重要风险变化：

```text
NO_ACTION
```

## 19.7 加仓原则

必须同时满足：

- 原持仓逻辑成立；
- 当前 Entry 有优势；
- Regime 允许；
- 组合风险允许；
- 未超过目标区间；
- Bull Trigger 已触发或满足预定义条件。

禁止仅因为“跌了”而补仓。

## 19.8 减仓

区分：

- Risk Reduction；
- Thesis Reduction。

## 19.9 EXIT

用于：

- 核心逻辑破坏；
- Hard Invalidation；
- 重大风险事件；
- 结构性趋势失效。

成本价不得成为持有或退出理由。

## 19.10 换仓

换仓门槛最高。

要求新方案明显优于：

```text
当前持仓价值
+ 交易成本
+ 预测不确定性
+ 新增风险
```

仅有轻微评分优势时禁止换仓。

---

# 20. Minimum Decision Edge

所有 ACTION 必须明确优于：

```text
Do Nothing / Keep Current Portfolio
```

不同动作门槛：

```text
新资金建仓 < 加仓 < 换仓
```

V1 使用可配置默认阈值；V1.5 通过回测和 Outcome 校准。

---

# 21. Decision 输出

正式 Action / Holding Decision 至少回答：

- 为什么现在需要行动？
- 如果不行动会损失什么？
- 相比当前持仓优势在哪里？
- 优势是否覆盖交易成本与风险？
- 当前 Market Regime 是否支持？
- 建议目标仓位区间？
- 合理介入区间？
- 失效条件？
- Bull / Base / Bear 情景？
- 最大风险是什么？

若无法证明需要行动：

```text
NO_ACTION
```

---

# 22. Alpha Memory

## 22.1 五层记忆

1. Market Memory
2. Asset Memory
3. Portfolio Memory
4. Decision Memory
5. User Behaviour Memory

## 22.2 核心原则

```text
当前客观事实
>
当前组合状态
>
当前风险收益
>
历史相似经验
>
用户历史行为
```

记忆是证据，不是命令。

## 22.3 Decision Record

每个正式决策不可变记录：

```text
decision_id
timestamp
portfolio_snapshot_id
market_snapshot_id
asset
action
current_weight
target_weight_range
reason
evidence
confidence
decision_edge
trigger_source
model_version
strategy_version
```

`NO_ACTION` 也必须成为正式 Decision。

## 22.4 用户真实操作

系统 Decision 与 User Action 永远分开。

匹配状态：

- Fully Followed
- Partially Followed
- Ignored
- Opposite
- Manual Override

## 22.5 Outcome Tracking

默认窗口：

- 1 日
- 5 日
- 10 日
- 20 日
- 60 日
- 120 日

记录：

- Absolute Return；
- Benchmark Return；
- Excess Return；
- Maximum Favorable Excursion；
- Maximum Adverse Excursion；
- Trigger Outcome。

不能只用“后来涨没涨”评价建议。

## 22.6 Personal Fit

Personal Fit 可影响：

- Position Size；
- Minimum Decision Edge；
- Risk Warning。

不得改变：

- 财务事实；
- 技术事实；
- Market Score；
- Industry Strength；
- 客观 Opportunity Score。

## 22.7 历史不可篡改

- Decision 不允许直接修改；
- 可追加备注；
- Trade Ledger 可纠错，但必须有 revision history；
- Snapshot 不覆盖旧版本。

## 22.8 策略版本

所有重要历史记录绑定：

```text
strategy_version
model_version
```

旧版本结果不得直接污染新版本效果判断。

## 22.9 V1 禁止自动改策略

Alpha Memory 可以发现：

> 某信号表现可能失效

但只能提示。

策略权重变更必须通过：

- 回测；
- 人工确认；
- 新 Strategy Version。

---

# 23. 每日 / 周期复盘

## 23.1 Daily Review

收盘 Deep 后自动生成：

- 今天市场发生什么；
- 早盘判断；
- 盘中 Trigger；
- ACTION / NO_ACTION；
- 用户实际操作；
- 当前组合变化；
- 已可初评的历史 Decision；
- 次日关注条件。

## 23.2 周 / 月

V1 可提供基础统计。

高级 Alpha Review 放 V1.5。

---

# 24. 数据源与 Provider Layer

业务逻辑禁止直接写死某个数据商。

统一 Provider 抽象：

- QuoteProvider
- KLineProvider
- FundamentalProvider
- FlowProvider
- NewsProvider
- CorporateActionProvider
- CalendarProvider
- ETFConstituentProvider

## 24.1 推荐主链

- 全 A 实时 / 分钟：mootdx / 通达信体系优先；
- 持仓最终报价：腾讯 + 第二源交叉校验；
- 东财：K线、板块、资金、ETF等补充；
- 财联社：快讯；
- 巨潮 / 交易所正式披露：重大公告事实确认；
- Tushare：有 Token 时增强，不允许成为系统唯一依赖；
- AKShare：可作接口层，但必须记录其真实底层来源，不得把同源接口当双源。

---

# 25. 数据血缘

所有关键字段必须保存：

```text
value
provider
provider_endpoint
source_time
fetched_at
trade_date
available_at
freshness
quality_status
fallback_level
```

其中 `available_at` 用于防止 Alpha Memory 和未来回测出现 Look-Ahead Bias。

---

# 26. 数据可信度分层

建议：

- T0：交易所 / 巨潮 / 指数公司等正式来源；
- T1：稳定结构化行情 / 数据服务；
- T2：聚合 / 公开接口；
- T3：新闻 / 媒体；
- T4：LLM 推断。

T4 永远不能当事实。

---

# 27. 数据质量状态

字段级：

- VALID
- DEGRADED
- STALE
- CONFLICT
- MISSING
- INVALID

Snapshot 层汇总：

```text
quality_score: 0~100
confidence: 0~100
```

V2 的 A / B / C / F 可继续作为 UI 简化状态。

---

# 28. Freshness

V1 默认：

| 数据 | 正常 Freshness |
|---|---:|
| 持仓实时价格 | ≤90秒 |
| 全A快照 | ≤2分钟 |
| Market Score | ≤6分钟 |
| Trigger 状态 | ≤2分钟 |
| 1分钟线 | 当前 / 上一完整分钟 |
| 日K | 最近完整交易日 |
| 基本面 | 最近正式披露期 |
| ETF成分 | 最近有效调仓版本 |
| 公告发现 | 尽量 ≤5分钟 |

超时必须标记 `STALE`。

---

# 29. 全 A 数据 Gate

行情覆盖：

- ≥98%：正常；
- 95–98%：DEGRADED，禁止因此主动激进加仓；
- <95%：冻结 Market Score，显示最后一次可靠值。

禁止因为 Provider 故障导致 Market Score 瞬间异常并触发交易。

---

# 30. 关键行情双源校验

当前持仓、Action Candidate、Trigger 涉及标的在正式动作建议前必须刷新关键价格。

若同时间窗口两源价格差异 >0.5%，或昨收 / 停牌 / 涨跌幅状态矛盾：

```text
CONFLICT
```

重新获取后再决定。

不得让 LLM判断“哪个数据源是真的”。

---

# 31. 同口径原则

例如 Top5% 成交集中度的分子分母必须来自：

- 同一 Snapshot；
- 同一时间；
- 同一 Provider / 同一数据定义。

禁止跨源拼接核心量化公式。

---

# 32. Provider Health

每个 Provider 记录：

- 成功率；
- P50 / P95 延迟；
- 连续失败；
- 最近成功时间；
- 限流状态；
- 字段缺失率；
- 冲突率。

状态：

- HEALTHY
- DEGRADED
- CIRCUIT_OPEN
- RECOVERING

连续异常必须熔断。

Fallback 不能静默，必须记录 `fallback_level`。

---

# 33. 数据缺失决策规则

- 市场核心数据不完整：禁止主动提高风险仓位；
- 持仓关键数据不完整：禁止生成新的精确加减仓数量；
- 重要持仓关键数据异常：暂停依赖该数据的组合主动调仓；
- Candidate 数据不完整：最多进入 Watch；
- 基本面缺失：普通股票不得获得高置信度中期 Action；
- ETF 成分过旧：降低 Portfolio Fit Confidence。

---

# 34. AI / Multi-Agent

产品逻辑角色：

- Market Agent
- Quant Agent
- Technical Agent
- Fundamental Agent
- Flow Agent
- Policy / Macro Agent
- News / Event Agent
- Bull Researcher
- Bear Researcher
- Risk Agent
- Portfolio Manager

Agent 数量不是 KPI。

## 34.1 Fast

用于：

- Trigger；
- 盘中复核；
- 候选初筛。

只运行相关模块。

## 34.2 Standard

用于：

- 09:35；
- 14:30；
- 手动完整复核。

## 34.3 Deep

用于：

- 收盘；
- 新建重要仓位；
- 大比例调仓；
- 重大事件；
- 用户手工要求。

## 34.4 Evidence

重要结论必须绑定：

- 数据源；
- 时间；
- 指标；
- 新闻 / 公告；
- available_at。

尽量区分：

- Fact
- Interpretation
- Opinion

---

# 35. 页面信息架构

V1 一级导航：

1. 总览
2. 市场
3. 持仓
4. 机会
5. 自选
6. 复盘
7. 任务
8. 设置

AI 分析不单独作为一级页面。

---

# 36. 总览

第一屏必须回答：

- 市场怎么样；
- 组合安全吗；
- 是否需要处理；
- 是否有新 Action；
- 系统 / 数据是否正常。

核心卡片：

```text
Market Score
Market Regime
Confidence
当前仓位
建议风险区间
组合状态
操作必要性
最终结论
```

`NO_ACTION` 必须可以成为页面主结论。

总览还需包含：

- 全 A 平均 vs 中位；
- 指数 vs 普通股票；
- Top5% 成交集中度；
- 当前组合摘要；
- Action / Ready / Watch 数量；
- 系统状态；
- 待我处理 Inbox。

---

# 37. 市场页

Tab：

- 市场状态
- 市场宽度
- 行业 / 风格
- 历史

必须支持：

- Market Score 七大贡献；
- 今日 / 5日 / 20日 / 历史分位；
- 全 A 中位指数；
- 沪深300 / 中证1000 / 创业板对比；
- 行业热力图；
- 行业趋势 / 宽度 / 动量 / 拥挤。

---

# 38. 持仓页

顶部：

- 总资产；
- 市值；
- 现金；
- 今日收益；
- 累计收益；
- 仓位；
- 组合风险；
- 组合相关性；
- 行业 / 风格暴露。

列表：

- 名称；
- 当前价格；
- 仓位；
- 成本；
- 盈亏；
- 短 / 波段 / 中期状态；
- Keep Score；
- 当前建议；
- 下一 Trigger。

详情 Tab：

- 决策
- 行情
- 逻辑
- 事件
- 历史

---

# 39. 机会页

Tab：

- Action
- Ready
- Watchlist

Action 0–3。

每个 Action 至少显示：

- Opportunity Score；
- Entry Score；
- Market Fit；
- Portfolio Fit；
- Personal Fit；
- 当前状态；
- 合理介入区；
- 失效条件；
- 建议仓位区间；
- 相比当前组合的增益理由。

---

# 40. 自选

支持：

- 分组；
- 标签；
- 备注；
- 自定义买入逻辑；
- 自定义 Trigger；
- 是否自动分析；
- 是否实时监控。

自选与系统候选必须区分。

---

# 41. 复盘

Tab：

- 今日
- 交易
- 决策
- 周 / 月总结

Trade Ledger 与 Decision History 可相互跳转。

---

# 42. 任务

Tab：

- 实时监控
- 计划任务
- 分析任务
- 数据任务

支持：

- 当前状态；
- 最近运行；
- 错误；
- Retry；
- Cancel；
- SSE；
- Provider 异常。

---

# 43. 设置

分类：

- 投资参数
- 数据源
- AI 模型
- 监控与 Trigger
- 任务计划
- 通知
- 交易导入
- 系统

核心数字必须配置化，不得散落硬编码。

---

# 44. 每日任务调度

## 44.1 盘前 08:45–09:15

- 检查交易日；
- 同步证券列表；
- 更新复权数据；
- 财务 / 公告；
- ETF 成分；
- 行业分类；
- Provider Health；
- 加载昨日 Market State。

## 44.2 09:20–09:25

- 当前持仓；
- Watch / Ready；
- 昨日 Trigger Plan；
- 隔夜重大公告检查。

## 44.3 09:25 集合竞价

V1：

- 监控；
- 记录异常；
- 不直接形成普通新建仓 Action。

## 44.4 09:30

启动 Realtime Monitor。

## 44.5 09:35 Standard

生成：

- Market Regime；
- 组合状态；
- 持仓建议；
- Ready / Action；
- Trigger Plan；
- 当日风险预算。

若最终为普通 `NO_ACTION`：

**不推手机。**

## 44.6 10:30 Fast Market Review

若无实质变化：

```text
NO MATERIAL CHANGE
```

不产生全套报告、不通知。

## 44.7 11:30

保存上午 Snapshot。

默认不强制 LLM。

## 44.8 13:05

午后 Fast Market Review。

## 44.9 14:30 Standard

重点：

- 尾盘调整；
- 隔夜风险；
- 是否存在高质量尾盘机会；
- 更新 Trigger Plan。

若无变化：

**不通知。**

## 44.10 14:55 后

新出现的普通候选优先转为次日 Ready。

除非：

- P0；
- 已存在预定义 Trigger Plan；
- 高置信度重大事件。

## 44.11 15:00

- 停止实时价格 Trigger；
- 保存最终 Market Snapshot；
- 保存 Portfolio Snapshot；
- 更新 Outcome。

## 44.12 15:10 Deep Daily Review

运行完整深度链。

生成：

- 日复盘；
- 次日 Trigger Plan；
- Alpha Memory 更新；
- 数据质量报告。

---

# 45. 通知

最高原则：

> 系统尽量安静。没有真正需要用户处理的事情，就不要为了展示 AI 在工作而通知。

## 45.1 P0 Critical

立即通知，不受免打扰影响。

例如：

- Hard Invalidation；
- 持仓重大风险事件；
- 极端 Market Risk；
- 数据严重异常导致保护性冻结。

## 45.2 P1 Actionable

通过 Decision Gate 后的正式：

- ADD
- REDUCE
- EXIT
- NEW_POSITION
- REPLACE

允许遵守免打扰。

## 45.3 P2

系统内展示，默认不推。

## 45.4 P3

仅记录。

## 45.5 NO_ACTION

Fast / Standard 的普通 NO_ACTION 默认不推。

## 45.6 收盘日报

每天固定推送 1 条。

即使全天无交易，也包含：

- Market Score；
- 今日收益；
- 组合状态；
- 今日操作；
- 新风险；
- Action；
- 次日关注。

## 45.7 渠道

保留：

- 钉钉；
- 企业微信。

V1 建议增加：

- 飞书。

通知 Provider 抽象，方便未来扩展。

---

# 46. 待处理 Inbox

仅进入真正需要用户动作的事项：

- 新正式交易建议；
- 重大持仓风险；
- 交易流水待确认；
- 数据修复需要人工介入。

普通数据同步、P2 Trigger、AI 完成消息不得塞入 Inbox。

---

# 47. V1.0 必须实现

以下为 V1.0 Scope：

### 市场
- 全 A Security Universe
- 实时全 A 快照
- 全 A 中位涨跌
- 全 A 中位指数
- Top5% 成交集中度
- Breadth / Trend / Liquidity / Profitability / Diffusion / Crowding / Tail Risk
- Market Score
- Market Regime
- 历史分位
- Score Smoothing / Hysteresis
- Confidence

### 实时
- 1 分钟 Monitor
- 5 分钟 Market Score
- Trigger Engine
- 防抖 / cooldown
- Trigger Plan
- Fast Analysis

### 持仓
- 现有截图 → Snapshot 全部保留
- Snapshot Diff
- Trade Ledger 数据模型
- Keep Score
- 三周期分析
- Decision Delta
- Portfolio Risk

### 候选
- 股票 Quant Candidate Pipeline
- ETF Candidate Pipeline
- Watch / Ready / Action
- Entry Score
- Market Fit
- Portfolio Fit
- Personal Fit
- 0–3 Action
- ETF Look-Through

### Portfolio Manager
- 动态风险预算
- Cash
- No-Trade Zone
- 目标仓位区间
- 单股 20% Hard Cap
- 行业主题 ETF 30% Hard Cap
- Minimum Decision Edge
- NO_ACTION
- ADD / REDUCE / EXIT / NEW / REPLACE

### Memory
- Decision Record
- NO_ACTION Record
- User Action
- Outcome Tracking
- Decision Delta
- Asset Memory
- Market Memory
- 基础 Portfolio Memory
- Personal Fit
- Strategy Version
- Daily Review

### 数据
- Provider 抽象
- Data Lineage
- Freshness
- Data Quality
- Provider Health
- Fallback
- Circuit Breaker
- 全 A Coverage Gate
- 关键价格双源校验

### 产品
- 8 个一级导航
- 实时 Monitor UI
- Trigger Feed
- 待处理 Inbox
- 调度体系
- 通知分级
- 收盘日报
- 设置配置化

---

# 48. V1.5 延后

V1.5 再实现：

- 完整因子回测；
- Candidate Strategy Backtest；
- Market Regime Backtest；
- Paper Portfolio；
- Minimum Decision Edge 数据校准；
- Market Score 权重历史优化；
- 高级 Signal Performance；
- 高级用户行为统计；
- 自动历史相似场景统计；
- 周 / 月高级 Alpha Review；
- 因子失效检测；
- 更系统的策略实验框架；
- 美股 Market Regime 正式功能。

回测必须防止：

- Look-Ahead Bias；
- Survivorship Bias；
- 复权错误；
- 停牌处理错误；
- 涨跌停错误；
- ST 状态历史错配；
- T+1；
- 费用 / 印花税；
- 滑点。

---

# 49. 明确不做

V1 / V1.5 均不规划：

- 自动实盘下单；
- Broker 自动执行；
- 高频交易；
- 分钟内大量换手；
- 自动改策略权重；
- 自动复制用户偏好为推荐逻辑；
- “每天必须推荐 3 个”；
- 强制满仓；
- 精确目标价伪预测；
- 无证据的新闻买卖；
- “主力资金流入”单因子交易；
- LLM 自己编造基础数据。

---

# 50. 核心系统成功指标

系统 KPI 不使用：

- 推荐数量；
- 交易次数；
- “选中上涨股票数”；
- 单纯胜率。

核心评估：

- Portfolio Return；
- Benchmark Excess Return；
- Max Drawdown；
- Volatility；
- Sharpe；
- Sortino；
- Calmar；
- Turnover；
- Unnecessary Trade Count；
- NO_ACTION 的保护价值；
- Decision Edge 实际兑现情况；
- Data Quality；
- Trigger 有效性。

---

# 51. 关键数据实体

V1 数据模型至少应具备：

```text
User
Portfolio
HoldingUpload
PortfolioSnapshot
PortfolioPosition

TradeLedger
TradeRevision

SecurityMaster
TradingCalendar

MarketSnapshot
MarketMetricSnapshot
MarketScoreSnapshot
IndustrySnapshot

ProviderHealth
DataQualityRecord
SourceLineage

CandidateSnapshot
CandidateScore
CandidateLifecycle

TriggerPlan
TriggerEvent

AnalysisJob
AnalysisRun
EvidenceClaim

DecisionRecord
DecisionOutcome
UserAction

AssetMemory
MarketMemory
PortfolioMemory
BehaviourMemory

StrategyVersion
ModelProfile
Schedule
NotificationChannel
NotificationEvent
```

不得要求一次迁移就删除旧 V2 表；优先新增 / 扩展并保持兼容。

---

# 52. 兼容性与迁移约束

1. `/api/v1/archives` 继续兼容。
2. V2 PortfolioSnapshot 历史不得丢失。
3. V2 AnalysisRun / Report 历史不得丢失。
4. 老用户凭据和模型配置不得失效。
5. 老通知配置继续工作。
6. 数据库迁移只能通过 Alembic。
7. 现有 Docker 部署方式应继续可用。
8. 新实时模块出现故障，不得导致旧的手动分析功能完全不可用。
9. V3 应允许通过 Feature Flag 逐步启用新模块。

---

# 53. 建议 Feature Flags

至少：

```text
market_score_enabled
realtime_monitor_enabled
trigger_engine_enabled
candidate_engine_enabled
portfolio_manager_v3_enabled
alpha_memory_enabled
etf_lookthrough_enabled
provider_health_enabled
```

方便分阶段上线与回滚。

---

# 54. V1 验收总门槛

只有满足以下条件，V1 才算可用：

## 54.1 Market

- 全 A 样本计算口径一致；
- Market Score 可追溯到所有子指标；
- 每个子指标可查看原值 / 分位 / 来源；
- 数据覆盖不足时能冻结而不是输出伪结果；
- 全 A 中位指数可连续回放。

## 54.2 Realtime

- 交易时段 1 分钟 Monitor 稳定运行；
- Market Score 默认 5 分钟更新；
- 无 Trigger 时不启动大量 AI；
- Trigger 不会因价格边界抖动重复轰炸；
- Trigger → Fast Analysis → Decision 有完整状态链。

## 54.3 Holding

- 任一正式建议都基于确认持仓；
- 可查看当前与上一快照差异；
- 每个持仓都有 Trigger Plan；
- HOLD → REDUCE 等变化有 Decision Delta；
- 可输出 NO_ACTION。

## 54.4 Candidate

- 候选不是 LLM 自由生成；
- 股票 / ETF 评分链可解释；
- Action 最多 3；
- 允许 Action = 0；
- Ready 不会被通知成“可以买”；
- 新候选必须通过 Portfolio Fit。

## 54.5 Portfolio

- 单股永不超过 20% Hard Cap；
- 行业主题 ETF 永不超过 30% Hard Cap；
- Risk-Off 可主动保留高现金；
- 小幅评分差异不能触发换仓；
- 当前组合已优时能明确维持不动。

## 54.6 Memory

- 每次正式 Decision 不可变；
- NO_ACTION 可回看；
- 用户实际操作与系统建议分开；
- 可查看 5 / 20 / 60 日后验；
- Personal Fit 不修改 Opportunity Score；
- Strategy Version 可追踪。

## 54.7 Data

- 关键字段具备来源和时间；
- fallback 可追踪；
- Provider 异常有熔断；
- 关键 Action 前重新验证价格；
- 数据冲突不会被 LLM强行裁决。

## 54.8 Notifications

- 普通 NO_ACTION 不推；
- P0 必推；
- P1 仅在真正通过 Decision Gate 后推；
- 收盘固定日报可送达；
- 通知可回到具体 Decision。

---

# 55. Codex 开发约束

开发时必须遵守：

1. **先分析现有实现，再修改。**
2. 已实现功能若与 V3 新规格存在冲突：
   - 保留可复用部分；
   - 明确迁移路径；
   - 不并行保留两套相互冲突的业务逻辑。
3. 不允许未经规格确认新增自动交易。
4. 不允许把 Market Score / Candidate Score 的计算交给 LLM。
5. 所有权重、阈值、Hard Cap、freshness、cooldown 均集中配置化。
6. 所有关键计算要有单元测试。
7. 关键历史数据必须不可变 / 可审计。
8. 所有新表 / 字段通过 Alembic。
9. 所有 Provider 通过接口抽象，不在业务层散落 HTTP 调用。
10. 所有实时任务必须考虑限流、熔断、重试、幂等和资源释放。
11. 不得为“实时”而每分钟运行完整多 Agent。
12. 默认操作必须是 `NO_ACTION`，而不是 BUY。
13. 候选必须先 Quant Filter，再进入 LLM Research。
14. 任何重大买卖结论都应能追溯到 Evidence。
15. 回测功能在 V1 不实现，但 V1 数据结构不得阻碍 V1.5 回测。

---

# 56. 推荐开发阶段

## Phase 0：现仓库审计

输出：

- 当前模块地图；
- 当前数据库 ER；
- 当前 API；
- 当前前端页面；
- 当前分析链；
- 与本规格冲突点；
- 可复用点；
- 必须迁移点。

未完成审计，不开始大规模改造。

## Phase 1：Data Foundation

- Security Master；
- Calendar；
- Provider Abstraction；
- Provider Health；
- Market Snapshot；
- Lineage；
- Quality Gate；
- Historical Market Metrics。

## Phase 2：Market Engine

- 全 A 指标；
- Median Index；
- Top5 Concentration；
- Market Score；
- Regime；
- 历史分位；
- UI。

## Phase 3：Realtime + Trigger

- Monitor；
- Trigger Engine；
- Trigger Plan；
- Fast Analysis；
- Trigger Feed。

## Phase 4：Portfolio Foundation

- Trade Ledger；
- Portfolio Risk；
- ETF Look-Through；
- Keep Score；
- No-Trade Zone；
- Decision Delta。

## Phase 5：Candidate Engine

- 股票筛选；
- ETF筛选；
- Opportunity / Entry；
- Watch / Ready / Action；
- Market Fit；
- Portfolio Fit。

## Phase 6：Portfolio Manager V3

- Dynamic Risk Budget；
- Position Sizing；
- Hard Cap；
- Decision Edge；
- ACTION / NO_ACTION。

## Phase 7：Alpha Memory

- Decision Record；
- User Action；
- Outcome；
- Memory Retrieval；
- Daily Review。

## Phase 8：UX / Schedule / Notifications

- 8 大导航；
- Dashboard；
- Tasks；
- Inbox；
- Notification Priority；
- Daily Report。

## Phase 9：稳定性与验收

- 全链路测试；
- Provider 故障演练；
- 数据缺失演练；
- Trigger 抖动测试；
- 历史迁移测试；
- 性能测试；
- 交易日长时间运行测试。

---

# 57. 最终冻结原则

V1.0 所有功能都必须服从以下原则：

> **先判断有没有必要改变当前组合，再判断改变什么。**

> **没有足够强的新证据、没有明显更优的风险收益比，就保持现状。**

> **候选可以为 0，交易可以为 0。**

> **系统不以产生建议、增加换手或抓住每一次短线波动为目标。**

> **数据质量不过关，模型没有权限推动交易建议。**

> **Quant 负责计算，LLM 负责研究、解释、证据组织与多视角判断。**

> **现金是一种合法资产。**

> **用户拥有最终决策权，系统永不自动下单。**

---

# 58. 文档冻结状态

至此，V1.0 产品层核心需求冻结。

后续允许变化的内容仅包括：

- 经历史数据 / 回测证明需要调整的阈值；
- 数据源具体实现替换；
- UI 视觉细节；
- 性能优化；
- 不改变产品决策语义的技术实现。

以下变更必须重新走产品确认：

- Market Score 一级模型变更；
- Candidate 一级因子体系变更；
- Action 数量规则变更；
- Hard Cap 变更；
- 自动下单；
- NO_ACTION 语义变更；
- Alpha Memory 可自动修改策略；
- Portfolio Manager 核心决策顺序变更。

