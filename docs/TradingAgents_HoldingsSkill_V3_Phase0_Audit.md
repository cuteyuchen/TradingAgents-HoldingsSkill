# TradingAgents-HoldingsSkill V3
# Phase 0｜现仓库审计与 V1.0 改造映射

> 审计对象：`cuteyuchen/TradingAgents-HoldingsSkill`  
> 审计基线：`main`（Git tree SHA：`4eb2597d85eee5b5e7d8079bac5c8e13fa995416`）  
> 目标规格：`TradingAgents-HoldingsSkill V3 / AI 投资决策系统 V1.0 冻结功能规格`  
> 文档性质：**开发前审计，不代表已经开始修改代码**

---

# 0. 结论摘要

当前仓库不是需要推倒重来的 Demo，而是已经具备一套可继续演进的 V2 产品骨架：

- FastAPI + SQLAlchemy + Alembic；
- Vue 3 + TypeScript + Naive UI；
- SQLite WAL；
- 单容器自托管；
- 用户 / JWT / 多用户隔离；
- 模型 Provider / Profile；
- 持仓截图识别、人工修正、不可变持仓快照；
- AnalysisJob / AnalysisRun；
- Fast/Deep 分析；
- 多空辩论、研究裁决、Trader、Risk、Portfolio Manager；
- 数据质量门控；
- 调度、通知、SSE；
- V1 archives 兼容。

因此 V3 的正确路线是：

> **保留 V2 业务骨架和兼容接口，新增“市场量化底座 + 实时 Monitor + Trigger + Quant Candidate + Portfolio Decision + Alpha Memory”，同时把现在过度集中在 LLM 和单文件中的逻辑逐步拆开。**

不建议：

- 重写整个后端；
- 更换 Vue / FastAPI；
- V1 就上微服务；
- V1 就上 Kafka / Redis / Celery；
- V1 就切 PostgreSQL；
- 删除现有 V2 API；
- 删除 Skill；
- 直接让 Codex 按 V1.0 规格一次性大改全仓库。

---

# 1. 当前仓库结构

```text
TradingAgents-HoldingsSkill
├── backend
│   ├── alembic
│   │   └── versions
│   ├── app
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── v2_models.py
│   │   ├── v2_schemas.py
│   │   ├── routers
│   │   │   ├── archives.py
│   │   │   ├── auth_v2.py
│   │   │   ├── portfolios_v2.py
│   │   │   ├── analysis_v2.py
│   │   │   ├── automation_v2.py
│   │   │   ├── model_settings_v2.py
│   │   │   └── model_health_v2.py
│   │   └── services
│   │       ├── holdings_service.py
│   │       ├── market_data.py
│   │       ├── analysis_engine.py
│   │       ├── model_client.py
│   │       ├── scheduler.py
│   │       ├── notifications.py
│   │       ├── skill_runtime.py
│   │       └── ...
│   └── tests
├── frontend
│   └── src
│       ├── App.vue
│       ├── router.ts
│       ├── api
│       └── views
│           ├── DashboardView.vue
│           ├── UploadView.vue
│           ├── ReportsView.vue
│           ├── SettingsView.vue
│           └── LoginView.vue
├── skill
│   └── tradingagents-holdings-advisor
│       ├── SKILL.md
│       ├── runtime.json
│       ├── references
│       └── scripts/market_snapshot.py
├── Dockerfile
└── docker-compose*.yml
```

整体属于一个合理的 **模块化单体**。

V3 应继续保持模块化单体。

---

# 2. 当前数据库能力审计

## 2.1 已有实体

当前 `v2_models.py` 已经覆盖：

### 身份与模型

- `User`
- `RefreshToken`
- `ModelProvider`
- `ModelProfile`

### 持仓

- `Portfolio`
- `HoldingUpload`
- `PortfolioSnapshot`
- `HoldingItem`

### 分析

- `AnalysisJob`
- `AnalysisRun`

### 自动化

- `Schedule`
- `NotificationChannel`
- `NotificationDelivery`

这套设计应该保留。

---

# 3. 数据库：V3 缺失实体

根据冻结规格，当前至少缺少以下领域实体。

## P0：市场数据底座

```text
SecurityMaster
TradingCalendar

MarketSnapshot
MarketMetricSnapshot
MarketScoreSnapshot
IndustrySnapshot

ProviderHealth
DataQualityRecord
SourceLineage
```

其中最重要的是：

### SecurityMaster

必须取代现在“证券代码缺失时让 LLM 猜代码”的路径。

建议字段：

```text
id
market
exchange
code
name
security_type
industry
listing_date
delisting_date
is_st
is_suspended
status
updated_at
```

### TradingCalendar

A 股交易日不能再通过“上证指数今天有没有报价”推断。

---

## P0：交易记录

```text
TradeLedger
TradeRevision
```

用于：

- CSV
- Excel
- 截图
- 手工输入
- Snapshot 推断

必须区分：

```text
CONFIRMED
INFERRED
```

---

## P0：Trigger

```text
TriggerPlan
TriggerEvent
```

Trigger Plan 与 Trigger Event 分开。

`TriggerPlan` 是预案。

`TriggerEvent` 是盘中实际发生的事件。

---

## P0：候选

```text
CandidateSnapshot
CandidateScore
CandidateLifecycle
```

不能继续把 Candidate 只存在于 `AnalysisRun.structured_result_json`。

否则无法：

- 做 Watch → Ready → Action 生命周期；
- 盘中持续跟踪；
- 防止重复候选；
- 做历史 Outcome；
- 做 V1.5 回测。

---

## P0：正式决策

```text
DecisionRecord
DecisionOutcome
UserAction
```

目前 AnalysisRun 更像“分析报告”。

V3 必须增加真正的：

> **Decision**

Decision 与 Analysis 分开。

一个 AnalysisRun：

```text
可能没有任何 ACTION
```

但仍然可以产生：

```text
DecisionRecord = NO_ACTION
```

---

## P1：Memory

```text
MarketMemory
AssetMemory
PortfolioMemory
BehaviourMemory
StrategyVersion
```

V1 可以先部分结构化实现，不需要一开始做复杂向量数据库。

---

# 4. 当前 AnalysisJob / AnalysisRun 可复用程度

当前设计是 V3 很有价值的基础。

## AnalysisJob 已支持

- user_id
- portfolio_id
- snapshot_id
- trigger_type
- checkpoint
- mode
- status
- progress
- stage
- cancel
- retry
- idempotency
- notification

这些全部值得保留。

## V3 建议扩展

AnalysisJob 增加：

```text
trigger_event_id
analysis_kind
market_snapshot_id
strategy_version_id
parent_job_id
priority
```

其中：

```text
analysis_kind =
FAST
STANDARD
DEEP
```

为了兼容现有：

```text
quick -> FAST
deep -> DEEP
```

旧 API 不必立刻删除。

---

# 5. 当前分析链审计

当前 `analysis_engine.py` 已经具备完整分析流程：

```text
确认持仓
↓
历史上下文
↓
行情采集
↓
Quality Gate
↓
Analyst Evidence
↓
Bull / Bear
↓
Research Manager
↓
Trader
↓
Risk Revision
↓
Three-Way Risk Debate
↓
Final Quote Refresh
↓
Candidate Screening
↓
Portfolio Manager
↓
AnalysisRun / Markdown / Notification
```

这是一个很好的 V2 链路。

## 但当前最大问题

### 逻辑高度集中

`analysis_engine.py` 已超过 60 KB。

它同时负责：

- workflow orchestration；
- Quality Gate；
- Claim normalisation；
- trader；
- risk；
- candidate；
- report；
- persistence；
- notification触发。

V3 再把：

- Market Score；
- Trigger；
- Quant Candidate；
- Portfolio Sizing；
- Alpha Memory；

继续塞进去会变得不可维护。

---

# 6. V3 对 analysis_engine 的正确处理

不是删除它。

而是把它逐步变成：

> **Orchestrator**

建议最终只负责：

```text
读取 Context
↓
调用 deterministic services
↓
调用需要的 Agent
↓
执行 Decision Gate
↓
持久化结果
```

它不应该自己计算：

- Market Score；
- Candidate Quant Score；
- ETF 穿透；
- Portfolio Exposure；
- Position Size；
- Decision Edge；
- Provider Quality。

---

# 7. 推荐后端服务边界

不建议过度 DDD 化。

保持简单，但明确拆层。

```text
backend/app/services/

market/
├── security_master.py
├── calendar.py
├── providers.py
├── provider_health.py
├── snapshots.py
├── metrics.py
├── market_score.py
├── quality.py
├── monitor.py
└── triggers.py

portfolio/
├── ledger.py
├── snapshot_diff.py
├── risk.py
├── etf_lookthrough.py
├── keep_score.py
├── sizing.py
└── decision.py

candidates/
├── universe.py
├── filters.py
├── stock_score.py
├── etf_score.py
├── entry_score.py
├── portfolio_fit.py
└── lifecycle.py

memory/
├── decisions.py
├── outcomes.py
├── retrieval.py
└── daily_review.py

analysis/
├── orchestrator.py
├── agents.py
├── claims.py
├── prompts.py
└── report.py
```

第一阶段不需要一次拆完。

可以每开发一个新模块时逐渐将旧 `analysis_engine.py` 缩小。

---

# 8. P0 冲突：当前候选逻辑与冻结规格不一致

这是本次审计最重要的发现之一。

当前 Skill 明确要求：

> 每次运行需要给出 1–2 个买入 / 轮动候选。

当前代码也执行：

```text
candidate_screening
```

并让 LLM 输出候选。

这与 V3 冻结原则直接冲突。

V3 已经确定：

```text
Action Candidates = 0～3
```

且：

```text
没有明显优于当前组合的机会
→ 0
```

因此必须修改：

- `SKILL.md`
- `references/buy-candidate-selection.md`
- `references/configuration.md`
- `runtime.json`
- `analysis_engine.py`
- Candidate 前端显示逻辑
- 对应测试

---

# 9. P0 冲突：候选当前允许重复现有持仓

当前规则允许：

```text
现有持仓
+
candidate_type = add_existing
```

进入 Candidate。

V3 已冻结：

> “新增 Action Candidate”只表示新的非持仓机会。

已有持仓的加仓：

```text
属于 Holding Decision
```

不是 New Candidate。

因此 V3 页面和数据模型应明确分成：

```text
Holding Actions
+
New Opportunity Candidates
```

避免同一只证券一会儿在持仓、一会儿在新增候选中重复出现。

---

# 10. P0 冲突：当前单持仓 Hard Cap = 30%

Skill 当前配置：

```text
single_position_max_ratio = 0.30
```

冻结规格：

```text
普通股票 Hard Cap = 20%
行业 / 主题 ETF Hard Cap = 30%
```

因此当前统一 Hard Cap 必须取消。

V3 应根据：

```text
security_type
etf_category
```

选择不同约束。

---

# 11. P0 冲突：当前投资周期定义不同

现有：

```text
short = 1～14
medium = 14～90
long = 90+
```

冻结：

```text
短期 = 1～5
波段 = 5～20
中期 = 20～120
```

需要统一更新。

不允许 Skill、后端、UI 各使用一套周期。

---

# 12. P0 冲突：当前候选核心仍由 LLM 生成

当前：

```text
market
↓
LLM Candidate Screening
↓
score 0～10
```

V3 必须改成：

```text
Security Universe
↓
Quant Filters
↓
Deterministic Factors
↓
Opportunity Score
↓
Entry Score
↓
Market Fit
↓
Portfolio Fit
↓
Ready Pool
↓
LLM Research
↓
Decision Gate
↓
Action 0～3
```

这是 V3 最大的业务逻辑变化之一。

---

# 13. P0 冲突：现有 Portfolio Manager 缺少“先证明需要行动”

现有 Portfolio Manager 已经存在，这是优势。

但目前的输入重点仍是：

```text
研究
Trader Proposal
Risk
Candidates
```

V3 要新增一个最上层比较：

```text
Plan A = 保持当前组合
Plan B = 加新资产
Plan C = 加 / 减现有持仓
Plan D = 换仓
```

然后计算：

```text
Decision Edge
```

如果优势不够：

```text
NO_ACTION
```

这个 Gate 应该是确定性 + 结构化规则为主，不能只依赖 Portfolio Manager 的自然语言判断。

---

# 14. 数据层重大问题：存在两套行情采集实现

当前同时存在：

### 后端

```text
backend/app/services/market_data.py
```

以及：

### Skill

```text
skill/.../scripts/market_snapshot.py
```

两者功能已经明显重叠：

- 腾讯行情；
- 东财；
- 新闻；
- sector；
- quote fallback；
- quality；
- final refresh。

Skill 版本甚至还有新浪 fallback，而后端版本是另一套实现。

这是明显的：

> **Data Logic Drift Risk**

V3 不应该继续维护两套逐渐分叉的市场数据逻辑。

---

# 15. 数据层推荐方案

V3 的数据“真源”：

```text
Backend Provider Layer
```

负责：

- Quote
- KLine
- Fundamentals
- News
- Announcement
- ETF Constituent
- Calendar
- Security Master

Skill 不再拥有独立业务数据定义。

Skill 如果还要支持独立运行：

方案 A：

```text
优先调用 V3 API 获取 normalized snapshot
```

方案 B：

保留 standalone script 作为 fallback，但：

```text
只能是兼容层
```

不允许它和后端拥有不同的：

- Quality Gate；
- Market Score；
- Candidate Score；
- Hard Cap；
- Trigger 规则。

---

# 16. 当前数据源无法直接承担全 A 实时计算

后端当前 `market_data.py` 的使用方式主要是：

```text
针对当前持仓逐只拉
```

并且东财请求有串行节流。

这适合：

- 数个持仓；
- 低频 K 线；
- 公告；
- 资金流。

不适合：

```text
5000+ A股
每分钟
```

因此 V3 必须增加：

> **批量全市场 Quote Provider**

并将：

```text
mootdx / 通达信批量行情
```

作为优先方案之一。

---

# 17. 全 A 数据不能全部写 SQLite

如果直接存：

```text
5000 股票
×
240 分钟
×
每个交易日
```

SQLite 很快会变成大量无价值写入。

V1 推荐：

## 实时层

全 A 1 分钟 Raw Snapshot：

```text
主要存在内存
```

必要时保留当前 / 最近若干快照。

## SQLite 持久层

保存：

- 5 分钟 MarketMetricSnapshot；
- 5 分钟 MarketScore；
- Industry Snapshot；
- Trigger 事件；
- 关键持仓 / Ready / Action quote；
- Decision 前后的关键 market snapshot。

## 大规模历史量化数据

为 V1.5 回测预留：

```text
本地列式存储（例如 Parquet）
```

而不是全部塞进事务数据库。

这属于实现建议，不改变产品规格。

---

# 18. SQLite 是否需要替换

V1：

> **不需要。**

当前 Docker 部署默认已经使用：

```text
SQLite WAL
```

单用户 / 少用户自托管足够。

真正应该优先解决的是：

- 不必要的写放大；
- 并发任务；
- provider 限流；
- 数据模型；
- monitor 稳定性。

而不是提前引入 PostgreSQL。

---

# 19. Scheduler 审计

当前 Scheduler 优点：

- APScheduler；
- 单独 Schedule 表；
- user timezone；
- idempotency；
- stale snapshot；
- failure disable；
- run-now；
- 交易日判断；
- 后台线程运行 AnalysisJob。

适合保留处理：

```text
09:35 Standard
10:30 Fast Review
13:05 Fast Review
14:30 Standard
15:10 Deep
```

---

# 20. Scheduler 当前不足

它不应该承担：

```text
1分钟 Realtime Monitor
```

推荐：

```text
Scheduler
= 固定时间任务
```

新增：

```text
MonitorEngine
= 交易时段持续循环
```

再新增：

```text
TriggerEngine
= 根据 Monitor 状态产生事件
```

三者职责必须分开。

---

# 21. Realtime Monitor 部署建议

当前部署：

```text
单容器
单 Uvicorn
单进程
```

所以 V1 可以在应用 lifespan 中启动：

```text
MonitorEngine
```

和现有 Scheduler 类似。

但必须保证：

- 单实例；
- 可安全停止；
- 可恢复；
- 数据源异常不会阻塞 Web API；
- 每个 loop 有超时；
- 不运行 LLM；
- Trigger 事件幂等。

未来如果切多 Worker，再做独立 Worker / Leader Lock。

V1 不必提前复杂化。

---

# 22. 交易日历当前实现需要替换

当前交易日判断本质上依赖：

> 上证指数是否返回今天日期的报价。

这可以继续作为：

```text
sanity check
```

但不能成为正式 Calendar。

需要：

```text
TradingCalendar
```

本地缓存。

A 股策略时间全部使用：

```text
Asia/Shanghai
```

用户 timezone 只负责：

- UI；
- 通知；
- 用户个性化展示。

---

# 23. Symbol Resolution 风险

当前当持仓代码缺失时：

```text
LLM 根据证券名称匹配六位代码
```

虽然要求“不确定就 null”，但仍然不适合作为长期基础能力。

V3：

```text
SecurityMaster exact / alias match
↓
名称 + 市场 + 证券类型
↓
价格交叉验证
↓
仍不确定
→ 人工确认
```

LLM 最多：

```text
提供候选辅助
```

不能成为最终代码事实源。

---

# 24. 数据质量现状

当前已经有很好的基础：

- quote coverage；
- quality grade；
- missing fields；
- blocked / watch_only；
- final quote refresh；
- source chain。

这些全部保留。

---

# 25. 数据质量 V3 增强

从 AnalysisRun 级粗粒度：

```text
A/B/C/D/F
```

升级为：

### 字段级

```text
VALID
DEGRADED
STALE
CONFLICT
MISSING
INVALID
```

再汇总成：

```text
quality_score
confidence
```

并增加：

```text
SourceLineage
ProviderHealth
```

---

# 26. 当前通知逻辑与冻结规格冲突

现有逻辑：

```text
job.notify = true
↓
AnalysisRun 成功
↓
向所有启用渠道发送
```

也就是说：

即便：

```text
NO_ACTION
```

只要 job.notify=true，也会发。

V3 已冻结：

- 普通 NO_ACTION 不通知；
- 09:35 NO_ACTION 不通知；
- 14:30 无变化不通知；
- P0 必发；
- P1 Actionable 才推；
- P2 只 UI；
- 每日收盘固定摘要。

因此通知必须从：

```text
AnalysisRun-driven
```

改为：

```text
Decision / Event-driven
```

---

# 27. Notification 数据模型建议

保留：

```text
NotificationChannel
NotificationDelivery
```

扩展：

```text
notification_type
priority
decision_id
trigger_event_id
daily_review_id
quiet_hours_applied
reason
```

通知 Delivery 不能只关联 AnalysisRun。

---

# 28. 前端现状

当前一级导航：

```text
总览
今日持仓
分析报告
系统设置
```

当前页面的好处：

- 视觉体系已经建立；
- Naive UI 已稳定；
- Dashboard 有决策卡；
- Upload 有完整持仓确认工作流；
- Reports 有非常丰富的多 Agent 渲染；
- Settings 有模型 / 调度 / 通知。

因此：

> 不重写前端框架。

---

# 29. V3 前端迁移映射

## 现 Dashboard

保留骨架，升级为：

```text
总览
```

增加：

- Market Score；
- Regime；
- Confidence；
- 全 A 中位；
- Top5 Concentration；
- Portfolio Status；
- Action / Ready / Watch；
- Inbox；
- Provider status。

---

## 现 UploadView

不要删除。

从一级导航移出或并入：

```text
持仓
→ 更新持仓
```

它继续承担：

- Screenshot；
- Clipboard；
- Vision；
- Manual correction；
- Confirm Snapshot。

---

## 现 ReportsView

不要删除。

重构为：

```text
复盘 / 决策详情
```

已有能力大量可复用：

- Evidence；
- Quality；
- Debate；
- Research；
- Trader；
- Risk；
- Portfolio；
- Candidates；
- Screenshot；
- Run Comparison。

---

## 现 SettingsView

保留并扩展：

- 投资参数；
- 数据源；
- AI 模型；
- Monitor；
- Trigger；
- Schedule；
- Notification；
- Import；
- System。

---

# 30. V3 新页面

新增：

```text
市场
持仓
机会
自选
复盘
任务
```

最终一级导航：

```text
总览
市场
持仓
机会
自选
复盘
任务
设置
```

---

# 31. 当前 API 可复用

## 完全保留

```text
/api/v2/auth/*
/api/v2/model-settings/*
/api/v2/portfolios/*
/api/v2/uploads/*
/api/v2/snapshots/*
/api/v2/analysis/jobs/*
/api/v2/analysis/runs/*
/api/v2/schedules/*
/api/v2/notifications/*
```

并继续：

```text
/api/v1/archives/*
```

兼容。

---

# 32. V3 API 推荐策略

不建议为了 V3 把所有 `/api/v2` 改名。

推荐：

### 旧资源

继续 `/api/v2`

### 新领域

新增 `/api/v3`

例如：

```text
/api/v3/market/overview
/api/v3/market/score
/api/v3/market/metrics
/api/v3/market/history

/api/v3/monitor/status
/api/v3/triggers
/api/v3/trigger-plans

/api/v3/portfolios/{id}/risk
/api/v3/portfolios/{id}/exposure
/api/v3/trades

/api/v3/candidates
/api/v3/watchlist

/api/v3/decisions
/api/v3/outcomes
/api/v3/reviews

/api/v3/providers/health
/api/v3/settings/investment
/api/v3/settings/monitor
```

这样可以渐进迁移。

---

# 33. Skill Runtime 是“真实生产逻辑”的一部分

当前应用启动时：

```text
runtime_prompt()
```

会将 Skill `runtime.json` 转成 prompt 注入分析引擎。

并且每个 AnalysisRun 会保存：

```text
Skill version
Prompt version
Runtime SHA256
```

这是一个非常好的可审计设计。

必须保留。

但也意味着：

> **不能只修改后端代码而忘了 Skill。**

否则运行时 prompt 仍然可能强制旧规则。

---

# 34. 当前存在“配置多源真相”问题

现在同一个参数可能出现在：

- `runtime.json`
- `SKILL.md`
- `references/configuration.md`
- `references/trading-rules.md`
- `references/buy-candidate-selection.md`
- `analysis_engine.py`
- 前端默认值
- env

虽然 Skill 声称 `configuration.md` 是单一真源，但应用代码并没有真正解析它作为结构化配置。

V3 必须收敛。

---

# 35. V3 配置建议

建立真正结构化：

```text
StrategySettings
```

分：

```text
market_score
monitor
trigger
candidate
portfolio
data_quality
notification
```

代码提供 default。

数据库支持用户覆盖。

Skill prompt 从同一份已解析配置生成关键约束。

不要继续靠：

> “Markdown 写了一个值，Python 又手写一次。”

---

# 36. 需要立即删除/修正的旧规则

开发开始后第一批应该改：

### 删除“必须推荐 1–2 个候选”

替换：

```text
0～3
```

### 删除“两个候选要一个 ETF + 一个股票”

完全按照评分和组合价值。

### 删除“现有持仓可进入新增 Candidate”

加仓放到 Holding Action。

### `single_position_max_ratio=30%`

改为类型化：

```text
stock_hard_cap=20%
sector_etf_hard_cap=30%
```

### 旧 Horizon

改成：

```text
1–5
5–20
20–120
```

### 旧固定 85% exposure gate

不再作为统一硬门槛。

改为：

```text
Dynamic Risk Budget
```

---

# 37. Tests 审计

当前测试优点：

- Auth；
- Portfolio；
- Screenshot；
- Snapshot；
- Analysis；
- Debate；
- Risk；
- Candidate；
- Schedule；
- Notification；
- Alembic；
- Docker build。

这非常值得保留。

---

# 38. 测试中已有旧规格绑定

例如当前 E2E 明确断言：

```text
buy_candidate_selection
```

属于每次完整 Analysis 的阶段。

V3 后：

```text
Candidate Engine
```

可以运行但：

```text
Action Candidates = []
```

必须被视为完全成功。

测试重点应改成：

> 没有候选也是合法结果。

---

# 39. V3 新增关键测试

## Market Score

- 全 A 样本过滤；
- Median Return；
- Median Index；
- Top5 Concentration；
- percentile；
- smoothing；
- hysteresis；
- missing coverage freeze。

## Trigger

- soft；
- hard；
- debounce；
- cooldown；
- dedup；
- data-error dismissal；
- trigger ≠ action。

## Candidate

- 0 candidate 合法；
- current holding 不进入 new candidates；
- ETF 可排前三；
- stock/ETF 任意组合；
- Entry 过热降级；
- Risk Gate。

## Portfolio

- stock <= 20%；
- sector ETF <=30%；
- high correlation reduces size；
- current portfolio good => NO_ACTION；
- slight score gain => no replacement；
- Risk-Off => high cash accepted。

## Data

- stale；
- conflict；
- provider fallback；
- coverage <95% freeze；
- cross-source price conflict。

## Memory

- Decision immutable；
- NO_ACTION outcome；
- UserAction separated；
- strategy_version。

---

# 40. CI/CD 审计

当前 CI 已有：

```text
Alembic upgrade
pytest
Vue typecheck
Vue build
Docker build
```

足够继续。

建议 V3 增加：

- deterministic market tests；
- provider adapter mocked tests；
- migration upgrade from V2 fixture；
- long-running monitor simulation；
- trigger load test；
- SQLite WAL concurrency test。

---

# 41. 部署审计

当前：

```text
一个镜像
一个容器
FastAPI
Vue static
APScheduler
SQLite
```

符合个人自托管目标。

V1 保持。

---

# 42. 什么时候才考虑 Redis / PostgreSQL

不是现在。

出现以下情况再迁：

- 多进程 / 多 Worker；
- 多服务器；
- 多用户高并发；
- Monitor 独立 Worker；
- SQLite 写锁明显成为瓶颈；
- 市场历史查询体量大幅上升。

V1 不应为未来假设提前增加运维复杂度。

---

# 43. 推荐 V3 数据存储分工

## SQLite

存：

- 用户；
- 配置；
- Portfolio；
- Snapshot；
- Trade Ledger；
- Trigger；
- Candidate State；
- Decision；
- Outcome；
- Provider Health；
- MarketScore；
- 聚合 MarketMetric；
- Memory metadata。

## 内存

存：

- 当前全 A 1-min quote snapshot；
- rolling market state；
- Trigger evaluation state。

## 可选本地列式文件

存：

- 大规模历史 OHLCV；
- 回测所需历史 factor input；
- 高频历史数据。

V1 可以先只做接口与目录约定。

---

# 44. 现有能力与 V3 映射总表

| 当前能力 | 处理 | V3 目标 |
|---|---|---|
| Auth/JWT | KEEP | 原样保留 |
| Multi-user isolation | KEEP | 原样保留 |
| Model Provider/Profile | KEEP+EVOLVE | 增调用统计 |
| Screenshot Vision | KEEP | 持仓入口之一 |
| PortfolioSnapshot | KEEP | 不可变事实源 |
| HoldingItem | KEEP+EVOLVE | 接 SecurityMaster |
| AnalysisJob | KEEP+EVOLVE | Fast/Standard/Deep + Trigger |
| AnalysisRun | KEEP | 分析过程记录 |
| Reports | KEEP+EVOLVE | 复盘/Decision Detail |
| Quality Gate | EVOLVE | 字段级 + Provider Health |
| Tencent quote | KEEP | Provider之一 |
| Eastmoney | KEEP | 辅助，不做全A单点 |
| Market snapshot | REPLACE/UNIFY | Backend Provider Layer |
| Candidate LLM scan | REPLACE | Quant First |
| Candidate 1–2 | REPLACE | 0–3 |
| Existing-holding candidate | REPLACE | Holding Action |
| Portfolio Manager | KEEP+EVOLVE | Decision Edge / NO_ACTION |
| Scheduler | KEEP | 固定时间分析 |
| Realtime monitor | ADD | 1min |
| Trigger Engine | ADD | 事件驱动 |
| Trade Ledger | ADD | 完整交易记录 |
| ETF look-through | ADD | Portfolio Fit |
| Alpha Memory | ADD | Decision/Outcome闭环 |
| DingTalk/WeCom | KEEP | Priority/quiet hours |
| Notification on every run | REPLACE | Event/Decision driven |
| Frontend 4 nav | EVOLVE | 8 nav |
| V1 archive API | KEEP | 长期兼容 |

---

# 45. 改造优先级

## P0-0：先修“规则冲突”

在做 Market Score 前，先保证现系统不会继续受旧规则驱动。

需要修改：

```text
runtime.json
SKILL.md
configuration.md
buy-candidate-selection.md
trading-rules.md
analysis_engine prompt/schema
tests
```

目标：

- NO_ACTION 一等公民；
- Candidate 0–3；
- 不强制产生买入候选；
- Stock 20%；
- Sector ETF 30%；
- horizons 1–5 / 5–20 / 20–120；
- new candidate 排除 holdings。

这一步不意味着实现全部 V3，只是停止旧规则继续成为未来开发的错误地基。

---

# 46. 推荐实际开发阶段

## Phase A｜V3 Contract Alignment

只处理：

- Skill runtime；
- config schema；
- action semantics；
- candidate semantics；
- horizon；
- Hard Cap；
- AnalysisMode 兼容。

完成后：

> 旧 V2 仍然可以运行，但决策语义已经不再和 V3 冲突。

---

## Phase B｜Market Data Foundation

新增：

- SecurityMaster；
- TradingCalendar；
- Provider abstraction；
- Provider Health；
- Data Lineage；
- Market Snapshot contract；
- all-A batch quotes。

不做 UI 大改。

---

## Phase C｜Market Engine

新增：

- Median Return；
- Median Index；
- Breadth；
- Top5；
- Liquidity；
- Trend；
- Diffusion；
- Tail Risk；
- Market Score；
- Regime；
- history。

然后做 Market 页。

---

## Phase D｜Realtime / Trigger

新增：

- Monitor Engine；
- Trigger Plan；
- Trigger Event；
- debounce；
- cooldown；
- Fast Analysis；
- Trigger Feed。

---

## Phase E｜Portfolio Foundation

新增：

- Trade Ledger；
- snapshot diff；
- portfolio exposure；
- ETF look-through；
- Keep Score；
- No-Trade Zone。

---

## Phase F｜Candidate Engine

新增：

- universe；
- filters；
- stock quant；
- ETF quant；
- opportunity；
- entry；
- ready；
- action；
- portfolio fit。

此时才真正替换旧 LLM Candidate Screening。

---

## Phase G｜Portfolio Manager V3

新增：

- dynamic risk budget；
- sizing；
- hard caps；
- decision edge；
- plan comparison；
- NO_ACTION。

---

## Phase H｜Alpha Memory

新增：

- DecisionRecord；
- UserAction；
- Outcome；
- Memory retrieval；
- Daily Review。

---

## Phase I｜UX / Notification

完成：

- 8 nav；
- Inbox；
- quiet notification；
- closing report；
- P0/P1。

---

# 47. 不建议的开发顺序

不要：

```text
先重做前端
```

因为没有 Market API，页面只能写假数据。

不要：

```text
先重写 analysis_engine
```

因为它现在还能工作。

不要：

```text
先做 Alpha Memory
```

因为 Decision/Trigger/Candidate 数据结构还没有。

不要：

```text
先做回测
```

V1.5。

不要：

```text
先上 Redis/Postgres
```

不是当前瓶颈。

---

# 48. 第一批 Alembic 建议

建议不要一个 migration 创建几十张表。

### Migration 0004｜Market Foundation

```text
security_master
trading_calendar
provider_health
source_lineage
data_quality_records
```

### Migration 0005｜Market State

```text
market_snapshots
market_metric_snapshots
market_score_snapshots
industry_snapshots
```

### Migration 0006｜Portfolio Decision Foundation

```text
trade_ledger
trade_revisions
trigger_plans
trigger_events
```

### Migration 0007｜Candidates

```text
candidate_snapshots
candidate_scores
candidate_lifecycle
watchlist
```

### Migration 0008｜Decisions & Memory

```text
strategy_versions
decision_records
decision_outcomes
user_actions
market_memory
asset_memory
portfolio_memory
```

具体表结构应在各 Phase 开发前再细化，不建议现在一次写死全部字段。

---

# 49. Feature Flag 建议

V3 新模块都应该可以独立关闭：

```text
market_score_enabled
realtime_monitor_enabled
trigger_engine_enabled
candidate_engine_v3_enabled
portfolio_manager_v3_enabled
alpha_memory_enabled
etf_lookthrough_enabled
provider_health_enabled
```

这样：

- 出问题可回退；
- V2 分析仍可使用；
- 可以逐模块验证。

---

# 50. Phase 0 最终判断

## 可以直接复用

约 **40%～50%** 的产品骨架可以继续使用：

- Auth；
- Portfolio；
- Vision；
- Snapshot；
- Job；
- Run；
- SSE；
- LLM client；
- Scheduler；
- Notifications infrastructure；
- Report UI；
- Settings UI；
- Docker / CI；
- Alembic。

## 需要演进

约 **20%～30%**：

- analysis engine；
- quality gate；
- scheduler semantics；
- notifications；
- reports；
- frontend navigation；
- Skill runtime；
- settings/config。

## V3 真正新增

约 **30%～40%**：

- Market Engine；
- Realtime Monitor；
- Trigger；
- Quant Candidate；
- ETF Look-Through；
- Portfolio Risk / Sizing；
- Decision Records；
- Alpha Memory。

这意味着：

> **V3 是一次较大的演进，但不是重写。**

---

# 51. 最大技术风险排序

## Risk 1｜规则漂移

Skill / config / Python / UI 同时存在参数。

**优先解决。**

## Risk 2｜数据源双实现

Backend + Skill script 各维护行情逻辑。

**优先统一。**

## Risk 3｜候选 LLM-first

与 V3 Quant-first 原则冲突。

**必须替换。**

## Risk 4｜全 A 高频数据写入方式

错误实现会拖垮 SQLite。

**Monitor 与历史存储分离。**

## Risk 5｜实时任务与 AI 任务互相阻塞

Monitor 必须无 LLM，Fast Analysis 独立任务化。

## Risk 6｜通知噪声

现有每次 AnalysisRun 通知不适用于 V3。

## Risk 7｜长期演进中 analysis_engine 继续膨胀

必须逐步 Orchestrator 化。

---

# 52. 现阶段不需要修改的东西

暂时不要碰：

- Login；
- JWT；
- Refresh Token；
- screenshot storage；
- Vision parse 主流程；
- Upload confirm；
- `/api/v1/archives`；
- Docker packaging；
- frontend technology；
- model provider basics。

这些不是 V3 当前瓶颈。

---

# 53. Phase 0 验收结果

本次审计可以给出明确结论：

### 结论 1

现有仓库具备 V3 演进基础。

### 结论 2

不需要重构技术栈。

### 结论 3

第一步不是 Market Score，而是：

> **对齐旧 Skill / Runtime 与 V1.0 冻结规则。**

否则后续新模块完成后，旧 prompt 仍会把系统往旧逻辑拉。

### 结论 4

第二步是：

> **建立统一 Provider / SecurityMaster / Calendar / Data Quality 基础。**

### 结论 5

第三步才是：

> **Market Score。**

### 结论 6

Realtime Monitor / Trigger 在 Market Engine 稳定之后接入。

### 结论 7

Candidate Engine 必须彻底从 LLM-first 改为 Quant-first。

### 结论 8

Portfolio Manager 必须从“给一个最终建议”升级为：

```text
比较“不动”和“行动”
```

并允许：

```text
NO_ACTION
```

成为最高质量结果。

---

# 54. 下一步开发入口

Phase 0 审计结束后，不建议直接让 Codex“实现 V3”。

建议先下发：

# Phase A｜V3 Contract Alignment

Codex 的第一批任务只做：

1. 建立 V3 结构化默认配置；
2. 修改 Skill runtime；
3. 修改 Candidate 语义；
4. 修改 Hard Cap；
5. 修改 horizon；
6. 正式定义 `NO_ACTION`；
7. 增加 `fast / standard / deep` 兼容层；
8. 更新相关单测；
9. 不实现 Market Score；
10. 不实现 Realtime Monitor；
11. 不改数据库大结构；
12. 保证现有 V2 E2E 仍可运行。

这样第一批 PR 的风险最小，也最容易审查。

---

# 55. 审计冻结建议

如果本审计结论被接受，则开发阶段应遵守：

> **V2 能力优先保留；V3 新功能渐进加入。**

> **先统一规则，再统一数据，再做市场，再做实时，再做候选，再做 Portfolio Manager，再做 Memory。**

> **禁止一次 PR 改完整个系统。**

> **每个 Phase 都必须独立可验收、可回滚。**

