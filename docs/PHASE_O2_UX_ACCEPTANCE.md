# Phase O.2 Single-User Investment Workbench UX Acceptance

记录日期：2026-09-02  
Branch：`codex/phase-o-frontend-productization`  
Baseline SHA：`1e000b086e45cf550ae7740199cd9b4a3111afc7`

## 冻结边界

本轮只重构前端信息架构、交互和视觉呈现。User/Auth/Ownership、投资算法、Runtime
Contract `2.4.0`、Shadow Contract `shadow-execution-v1`、数据库和 Alembic schema
均不改。接受测试只能在显式 `ACCEPTANCE_MODE=true` 的隔离环境运行，不能为截图或测试
写入 production DB。

## Gate

| Gate | 自动验收内容 | 结果 |
|---|---|---|
| UX-0 | Implementation Map 覆盖旧页面、能力去向、API、隐藏和 deep-link | PASS |
| UX-1 | 新 Shell、六个路由、旧 alias、Auth/Ownership、single-user header | PASS |
| UX-2 | 首页、First Run、市场摘要、Final Decision、持仓和更新 Drawer | PASS |
| UX-3 | 分析、ACTION/NO_ACTION/BLOCKED/DATA_GAP、Candidate Veto、模拟跟随 | PASS |
| UX-4 | 历史表现、Research Tab、治理和系统状态进入 Settings | PASS |
| UX-5 | Empty/Loading/Error/Offline、Light/Dark、1366/1440/1920 视觉与响应式 | PASS |
| UX-Final | Backend、Frontend、Playwright、Docker、exact-head CI | PASS |

## 路由与导航验收

- 一级导航只显示 `首页 / 持仓 / 分析 / 模拟 / 历史`，设置使用 Header 图标入口。
- `/upload`、`/reports`、`/shadow`、`/research`、`/governance`、`/system` 保留 alias。
- 主 Shell 不显示 email、当前用户或用户管理入口。
- 0 个 Portfolio 显示 Setup Checklist；1 个 Portfolio 隐藏 selector；多个 Portfolio 显示 selector。
- 私有页面仍由 Auth guard 保护，Portfolio 查询仍由后端 Ownership 约束。

## 页面验收

- 首页首屏顺序为今日市场、今日建议、我的组合、关注机会；Final Decision 永远先于 Candidate。
- Market Pulse 显示 Market Score、Regime、All-A Median、Top5 concentration、Breadth、Coverage、Freshness、Quality；详细分量进入 Collapse。
- 无候选时显示“当前没有明显优于保持现状的机会”，不显示成错误或伪造数字。
- 持仓默认列为标的、数量、成本、当前价、收益、仓位、最新建议；详情使用 Drawer。
- 更新持仓保持 `Upload → Parse → Review/Edit → Confirm`，低质量识别不能自动确认。
- 分析以 Decision Hero 开始；Why、Holding Actions、Candidates、Advanced Evidence 依次展开。
- 模拟始终显示 `SHADOW` 和“不会发送真实订单”；时间线保持 `Decision → Execution → Outcome`。
- 历史分为“历史表现 / 策略研究”；策略参数治理和系统运维进入 Settings 高级分区。

## 状态与细节验收

- 用户文案显示“暂不操作 / 需要调整 / 暂不可形成可靠行动”，技术 code 作为次级信息保留。
- `conditional_add` 显示“仅记录建议，V1 暂不模拟条件触发”。
- 缺少数据统一使用 `—` 或“不可用”，不把 Missing 转成 0。
- Empty 状态必须说明发生了什么、原因和下一步；Error 必须提供人话、Retry，技术详情默认折叠。
- runtime、hash、schema、worker、manifest、source lineage 和 raw JSON 默认折叠在 Technical Details。
- Light 为新环境默认主题，Dark 继续可用，收益使用 A 股涨红跌绿，风险使用独立 risk token。

## 浏览器与视觉证据

Playwright 必须继续覆盖 auth、session expiry、ownership、portfolio switching、upload /
confirm、ACTION、NO_ACTION、BLOCKED、DATA_GAP、Candidate Veto、Research、Governance、
Shadow、Future Quote、Offline/Error、SPA refresh、console/pageerror。

视觉 artifact 只保存在 `output/playwright/acceptance/`，不提交 PNG。至少包含：

- 1440×900：login、home first-run、home normal、holdings、holdings update、analysis NO_ACTION、analysis ACTION、analysis running、simulation、history、settings、settings system；Light/Dark 均有页面证据。
- 1366×768：home、holdings、analysis，无 Header 横向溢出。
- 1920×1080：home、simulation，内容宽度保持约 1320～1360px。

## 最终状态

工程 Gate 全部通过时使用：

```text
Phase O.2 Single-User Investment Workbench UX Rework:
AUTOMATED IMPLEMENTATION = PASS
AUTOMATED_FRONTEND_ACCEPTANCE = PASS
MANUAL_UAT = REQUIRED
LIVE_READINESS = NOT_READY
PHASE_O_FINAL = HOLD_FOR_REDESIGNED_MANUAL_UAT
```

不得把本文件或自动化结果解释成用户本人已经完成 Manual UAT，也不得进入 O.2-B 或 Phase P。
