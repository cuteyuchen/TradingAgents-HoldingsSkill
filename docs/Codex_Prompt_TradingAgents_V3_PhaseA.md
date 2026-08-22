# Codex 开发任务
# TradingAgents-HoldingsSkill V3｜Phase A Contract Alignment

你现在要修改仓库：

`cuteyuchen/TradingAgents-HoldingsSkill`

这是一次**受严格范围限制的渐进式改造**。

在修改任何代码之前，先完整检查当前仓库实现，尤其是：

- `backend/app/services/analysis_engine.py`
- `backend/app/services/skill_runtime.py`
- `backend/app/v2_schemas.py`
- `backend/app/routers/analysis_v2.py`
- `backend/app/routers/automation_v2.py`
- `backend/app/v2_models.py`
- `frontend/src/api/types.ts`
- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/UploadView.vue`
- `frontend/src/views/ReportsView.vue`
- `frontend/src/views/SettingsView.vue`
- `skill/tradingagents-holdings-advisor/SKILL.md`
- `skill/tradingagents-holdings-advisor/runtime.json`
- `skill/tradingagents-holdings-advisor/references/configuration.md`
- `skill/tradingagents-holdings-advisor/references/trading-rules.md`
- `skill/tradingagents-holdings-advisor/references/multi-agent-workflow.md`
- `skill/tradingagents-holdings-advisor/references/buy-candidate-selection.md`
- `backend/tests/*`

必须先确认：

1. 哪些功能已经实现；
2. 本次合同调整会影响哪些已有功能；
3. 哪些地方存在旧规则；
4. 哪些代码已经被现有测试绑定；
5. 如何在不破坏现有 V2 的前提下修改。

不要根据这份任务描述直接覆盖现有实现。

---

# 一、本次唯一目标

完成：

`Phase A｜V3 Contract Alignment`

让现有 V2 系统的运行时语义与已经冻结的 V3 决策原则一致。

本 PR **不是完整 V3 实现**。

---

# 二、绝对禁止扩展范围

本次不要实现：

- Market Score；
- Market Regime 正式算法；
- 全 A 批量行情；
- SecurityMaster 同步；
- TradingCalendar 同步；
- Realtime Monitor；
- Trigger Engine；
- Trade Ledger；
- ETF 穿透；
- Quant Candidate V3；
- Dynamic Risk Budget；
- Decision Edge 正式量化；
- Alpha Memory；
- `/api/v3/*`；
- PostgreSQL；
- Redis；
- Celery；
- 微服务；
- 大规模前端重构。

不要新建 Alembic migration。

不要改变现有部署拓扑。

---

# 三、必须保留

以下现有能力不能回归：

- JWT；
- Refresh Token；
- 用户隔离；
- Model Provider/Profile；
- 密钥加密；
- Screenshot Upload；
- Vision Parse；
- Manual Correction；
- PortfolioSnapshot；
- HoldingItem；
- AnalysisJob；
- AnalysisRun；
- Cancel；
- Retry；
- SSE；
- Quality Gate；
- Bull/Bear Debate；
- Research Manager；
- Trader；
- Risk Revision；
- Three-Way Risk Debate；
- Portfolio Manager；
- Final Quote Refresh；
- Markdown report；
- Reports comparison；
- Scheduler；
- DingTalk；
- WeCom；
- V1 archive compatibility；
- Docker；
- CI；
- 旧数据库启动。

---

# 四、核心 V3 决策原则

必须把下面原则同时落实到：

- machine-readable runtime；
- Skill 文档；
- Backend Normalizer；
- Frontend；
- tests。

## 原则 1

`NO_ACTION` 是一等合法结果。

系统必须：

先判断：

`是否有必要改变当前组合`

再判断：

`改变什么`

---

## 原则 2

如果：

- 当前持仓没有 add/reduce/sell；
- 所有持仓只是 hold/watch；
- 没有通过门控的新 Candidate；
- Quality Gate 没有 blocked；

则最终组合级：

`final_rating = no_action`

这必须是确定性后处理，不只是 Prompt 建议。

---

## 原则 3

`NO_ACTION` 和 `WATCH_ONLY` 不同。

### NO_ACTION

证据充分。

分析完成。

决定不交易。

### WATCH_ONLY

关键数据不足或质量门控阻断。

因此：

Quality Gate blocked 时必须保留：

`watch_only`

不能被 no_action normalization 覆盖。

---

# 五、Candidate 新合同

新 Candidate 专指：

`当前未持有的新机会`

当前持仓的：

- add；
- conditional_add；

只能出现在：

`Holding Action`

不能再出现在：

`New Candidate`

---

## Candidate 数量

旧规则：

`必须 1～2`

全部删除。

新规则：

`0～3`

0 是正常结果。

不得为了凑数量生成 Candidate。

---

## Candidate 上限保护

如果模型异常返回超过 3 个：

最终结构化结果最多保留 3 个。

在最终截断前先：

- 删除无有效 code；
- 删除 current holdings；
- 删除不通过数据质量的 Candidate；
- 删除缺核心理由的 Candidate。

如果 score 是合法数字，可以优先 score 高的。

---

# 六、不再强制股票 + ETF 搭配

删除所有类似：

`如果输出两个候选，应该一个 ETF + 一个股票`

的要求。

允许任何组合：

- 0；
- 1；
- 2；
- 3；
- 全股票；
- 全 ETF；
- 混合。

---

# 七、Hard Cap 合同

新合同：

- 普通股票：20%
- 行业 / 主题 ETF：30%

删除：

`single_position_max_ratio = 0.30`

作为统一规则。

但是注意：

当前项目还没有可靠 `SecurityMaster / ETF classification`。

所以本 PR：

### 必须

- 修改 Runtime；
- 修改 Skill；
- 修改 Backend contract 常量；
- 消除统一 30% 文案；
- 加一致性测试。

### 禁止

- 根据名称猜 instrument type 后强制仓位；
- 让 LLM 判定 ETF 类型后执行 Hard Cap；
- 假装完整确定性 Hard Cap enforcement 已经完成。

真正 enforcement 留给后续 Portfolio Engine。

---

# 八、Investment Horizon

旧：

- short 1–14
- medium 14–90
- long 90+

全部更新成：

- short：1–5 trading days
- swing：6–20 trading days
- medium：21–120 trading days

UI 文案可以显示：

- 1–5
- 5–20
- 20–120

程序内部避免边界重叠。

---

# 九、Analysis Mode 兼容

当前：

`quick | deep`

建立 canonical：

`fast | standard | deep`

兼容：

`quick -> fast`

旧数据库中的 `quick` 必须仍能读取。

旧 API client 发 `quick` 必须仍然成功。

新 UI 不再主动创建 quick。

本阶段不需要重写整条分析 pipeline。

不要虚构 Fast / Standard / Deep 已经完全拥有不同 Agent 数量。

---

# 十、建议新增 backend/app/decision_contract.py

请优先新增一个轻量纯模块：

`backend/app/decision_contract.py`

它不要：

- 访问 DB；
- 访问网络；
- 调模型。

用于集中定义：

- default portfolio action；
- candidate min/max；
- hard caps；
- horizons；
- analysis mode aliases；
- no_action normalization helper。

保持简单。

不要建立复杂 DDD 框架。

---

# 十一、runtime.json

修改：

`skill/tradingagents-holdings-advisor/runtime.json`

建议版本：

`2.1.0`

不要改成完整产品 `3.0.0`，因为完整 V3 尚未实现。

新增 machine-readable：

`decision_contract`

至少包含：

- default_portfolio_action=no_action；
- candidates min=0 max=3；
- exclude current holdings=true；
- stock cap=0.20；
- sector/theme ETF cap=0.30；
- horizons；
- canonical analysis modes；
- quick alias。

Core Rules 增加：

- NO_ACTION is first-class；
- do not trade merely because analysis completed；
- first prove need to change；
- 0–3 new non-held candidates；
- existing holding add belongs to holding action。

---

# 十二、skill_runtime.py

让 runtime loader：

- 验证 decision_contract；
- 将 decision_contract 注入 runtime_prompt；
- 将 contract version / key metadata 持久化到新 AnalysisRun 的 skill_runtime metadata。

继续保留：

- Skill version；
- prompt version；
- runtime SHA256。

---

# 十三、SKILL.md

更新现有 Skill。

不要重写成完全不同的 Skill。

重点修改：

1. V1 当前运行范围为 A 股股票 + 场内 ETF；
2. 加入 NO_ACTION；
3. 删除强制 1–2 candidates；
4. 改为 0–3 新的非持仓 Candidate；
5. 已有持仓加仓只属于 Holding Action；
6. Candidate 可以为空；
7. 不承诺收益。

保留：

- Quality first；
- 当前持仓快照优先；
- T+1；
- available_qty；
- Final quote refresh；
- Claim debate；
- Risk review；
- historical consistency。

---

# 十四、references/configuration.md

将旧：

`single_position_max_ratio = 0.30`

拆成：

- `stock_hard_cap_ratio = 0.20`
- `sector_theme_etf_hard_cap_ratio = 0.30`

新增：

- candidate_min_count=0
- candidate_max_count=3
- candidate_force_output=false
- new_candidate_exclude_current_holdings=true
- default_portfolio_action=no_action

更新 horizons。

不要删除其他仍有效的配置。

---

# 十五、references/trading-rules.md

明确删除这条旧规则：

`Every daily execution must include 1-2 buy/rotation candidates`

改成：

只有明显优于保持现状且经过风险门控的新机会才进入 Candidate。

修改统一 30% rule。

修改 horizon 文案。

现有：

- T+1；
- available_qty；
- staged reduce；
- loss decision gate；
- risk revision；

继续保留。

---

# 十六、references/multi-agent-workflow.md

修改：

- horizon；
- action output note；
- Candidate 允许空；
- Candidate 不再包含 current holdings；
- 不再声明必须有 buy/rotation row。

不要在本 PR 重做整个 Agent pipeline。

---

# 十七、references/buy-candidate-selection.md

这是重点。

把语义从：

`Required Buy Module`

改成：

`Opportunity / Candidate Module`

必须回答：

1. 是否值得新增风险；
2. 是否存在明显优于不行动的新机会；
3. 如果有，哪些新非持仓资产；
4. 0～3；
5. 如果没有，为什么没有。

删除：

- current holding as add_existing candidate；
- forced 1–2；
- forced ETF + stock composition。

暂时可以保留旧 0–10 score 作为过渡评分。

但 score >=7 不等于必须输出 Candidate。

---

# 十八、analysis_engine.py

不要重写文件。

只做局部改造。

## CORE_RULES

fallback 也对齐新规则。

---

## FINAL_SCHEMA

加入：

`final_rating=no_action`

Candidate 新输出不要再要求：

`add_existing`

历史兼容读取可以保留。

---

## Candidate Prompt

从：

`输出1-2`

改：

`输出0-3`

并明确：

- 非当前持仓；
- candidates=[] 正常；
- 不得为了数量生成；
- current holding add 在 holdings 中处理。

---

## Candidate Normalization

删除当前：

`held candidate -> add_existing / conditional_add`

行为。

改：

`held candidate -> remove`

保留 Holding Action。

---

## NO_ACTION Guard

Candidate 和 holdings 都 normalize 完后：

如果：

- quality gate not blocked；
- 所有 holdings ∈ hold/watch；
- candidates=[]；

则：

`final_rating=no_action`

同步：

`portfolio_manager_final.portfolio_rating`

---

# 十九、blocked_result

保持：

`watch_only`

不要修改成 no_action。

---

# 二十、v2_schemas.py / API

AnalysisMode 接受：

- quick
- fast
- standard
- deep

创建 Job 时 canonicalize：

- quick -> fast

Database mode 是 String，无 migration。

---

# 二十一、Scheduler

不要重构。

只让 Schedule 新输入兼容：

- fast
- standard
- deep
- quick alias

旧 schedule 可以运行。

---

# 二十二、Frontend

不要做八大导航。

只做兼容。

## types.ts

允许：

`quick | fast | standard | deep`

---

## Dashboard / Upload

新建分析的选项：

- 快速
- 标准
- 深度

新请求不发送 quick。

---

## Settings

Schedule mode 同步。

---

## Reports

增加：

`no_action -> 无需调整`

Candidate 空：

正常显示：

`当前无达到行动门槛的新增机会`

如果数据质量阻断：

显示阻断原因。

旧历史报告里的：

`add_existing`

仍能显示。

---

# 二十三、测试

至少新增：

## test_no_action_when_no_change

quality pass
all holdings hold/watch
candidates empty

=> final_rating no_action

---

## test_quality_block_preserves_watch_only

=> watch_only

---

## test_candidate_zero_valid

=> succeeded

---

## test_candidate_max_three

=> <=3

---

## test_current_holding_removed_from_candidate

=> candidate removed
=> holding add can remain

---

## test_analysis_mode_quick_alias

quick -> fast

---

## test_standard_mode_accepted

---

## test_contract_runtime_sync

Backend decision contract 和 runtime.json 必须一致：

- 0/3
- 20%
- 30%
- horizons
- modes
- no_action

---

# 二十四、更新现有 E2E

`backend/tests/test_v2_portfolio_analysis.py`

如果 fake result：

- holdings all hold；
- candidates=[]；
- quality pass；

最终应断言：

`final_rating == "no_action"`

Candidate scanning phase 可以仍然存在。

“执行过候选扫描”不等于“必须产生候选”。

---

# 二十五、禁止修改历史 AnalysisRun

不要批量改旧报告。

新合同只影响新 Run。

历史展示继续兼容。

---

# 二十六、完成后必须执行

Backend：

```bash
pytest tests -q
```

Alembic：

```bash
alembic upgrade head
alembic upgrade head
```

Frontend：

```bash
npm run typecheck
npm run build
```

Docker：

```bash
docker compose build
```

如果现有 CI 命令有差异，以仓库 CI 为准。

---

# 二十七、完成后给出报告

最后必须输出：

1. 修改了哪些文件；
2. 每个文件为什么改；
3. 哪些旧功能受到影响；
4. 如何保证向后兼容；
5. 新增了哪些测试；
6. 每个测试命令结果；
7. 是否存在未解决问题；
8. 明确说明：
   - Market Engine 尚未实现；
   - Monitor 尚未实现；
   - Quant Candidate V3 尚未实现；
   - Hard Cap deterministic enforcement 尚未完整实现。

不要把 Phase A 描述成完整 V3。

---

# 二十八、最重要的验收原则

下面这些结果必须都合法：

```text
Candidate = 0
Trade = 0
Portfolio = NO_ACTION
```

系统成功分析后：

> 如果没有足够证据证明改变当前组合更好，就保持现状。

这不是降级。

这是 V3 决策系统的核心行为。
