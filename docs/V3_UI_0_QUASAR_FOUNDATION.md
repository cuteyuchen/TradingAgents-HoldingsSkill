# V3-UI-0 Quasar UI Foundation

## 目标

在现有 Vue 3 + Vite 项目中接入 Quasar，建立后续所有 V3 页面可复用的 UI 基础设施。  
本阶段**不**正式重做 Dashboard / Holdings / Analysis Center。

## 为什么选择 Quasar

- Vue 3 + Vite 官方集成成熟（`@quasar/vite-plugin`）
- 提供 Layout / Drawer / Table / Tabs / Dialog / Notify 等工作台级原语
- 支持按需 tree-shaking，迁移期可与 Naive UI 共存
- 不需要把项目改造成 Quasar CLI 工程

## Vite 集成方式

```js
// frontend/vite.config.js
import { quasar, transformAssetUrls } from '@quasar/vite-plugin'

plugins: [
  vue({ template: { transformAssetUrls } }),
  quasar(),
]
```

依赖：

- `quasar@^2.18`
- `@quasar/vite-plugin@^1.12`（兼容 `@vitejs/plugin-vue@5`）
- `sass-embedded`（Quasar SCSS 需要）

入口安装：`frontend/src/plugins/quasar.ts`  
仅注册 `Notify / Dialog / Loading / Dark`，不全量导入组件。

## Legacy / V3 边界

| | Legacy | V3 |
|---|---|---|
| 路由 meta | `uiSystem: 'legacy'` 或缺省 | `uiSystem: 'v3'` |
| 组件框架 | Naive UI | **仅 Quasar + V3 Design System** |
| 目录 | `src/views`、`src/components` | `src/v3/**` |
| 样式 | Tailwind + 全局 CSS | V3 tokens + scoped |

规则：

1. 同一个 V3 页面**禁止** `import ... from 'naive-ui'`
2. Legacy 页面可暂时继续 Naive UI
3. 渐进迁移，禁止 big-bang rewrite
4. 不修改 MARKET-1 / MARKET-2 / MARKET-2.1 后端契约

## Design Tokens

文件：

- `src/v3/styles/v3-tokens.css`
- `src/v3/styles/v3-base.css`
- `src/v3/styles/v3-quasar-overrides.scss`

覆盖：Colors / Typography / Spacing / Radius / Border / Shadow / Surface / Elevation / Z-index / Motion / Layout / Density。

间距 scale：`4 / 8 / 12 / 16 / 20 / 24 / 32`  
圆角：small 6 / medium 8 / large 12（金融工作台避免大圆角）

## A 股红涨绿跌

硬约束：

- `--v3-market-up` = 红（上涨）
- `--v3-market-down` = 绿（下跌）
- `--v3-market-flat` = 中性

**风险/错误状态与涨跌色必须分离：**

| Token | 用途 | 色系 |
|---|---|---|
| `status-info/success/warning/danger/blocked` | 业务状态 | 蓝/绿/黄/红/灰 |
| `risk-low/medium/high/unknown` | 风险等级 | 青/黄/橙/灰 |

禁止：`risk-high = market-up`  
Quasar 的 `positive`/`negative` **不**映射 A 股涨跌。

## Theme Authority

唯一权威：`src/v3/composables/useV3Theme.ts`

- 偏好：`light` | `dark` | `system`
- 持久化 key：`advisor_theme`（与既有系统兼容）
- 同步：Quasar `Dark` + `documentElement.theme-dark` + 广播 `advisor-theme-changed`
- System 模式响应 `prefers-color-scheme`

## App Shell

```
src/v3/layouts/
  V3AppShell.vue   # QLayout + QHeader + QDrawer + QPageContainer
  V3Sidebar.vue    # 桌面常驻可折叠 / 移动 Overlay；selected 来自 router
  V3Topbar.vue     # 菜单入口 / 主题切换 / 设置 / 登出
```

Shell 不绑定 Dashboard/Holdings/Analysis 业务数据。

### Breakpoints

| 宽度 | Sidebar |
|---|---|
| ≥1024 | 常驻，可折叠至 64px |
| <1024 | Overlay Drawer + Topbar 菜单按钮 |

375 / 768 / 1280 / 1920 基础布局需正常。

## V3 组件清单

```
src/v3/components/
  V3PageHeader.vue      # title/description/eyebrow/actions/status
  V3Section.vue         # 轻量区块，支持 compact/dense
  V3Metric.vue          # label/value/secondary/trend/status
  V3StatusBadge.vue     # neutral/info/success/warning/danger/blocked
  V3DataTimestamp.vue   # 行情时间 + 策略分析时间分离
  V3LoadingState.vue    # skeleton 优先
  V3EmptyState.vue
  V3ErrorState.vue
  V3DataTable.vue       # dense/loading/empty/row click/sticky
  V3DetailDrawer.vue    # 为 InstrumentDetail 打底
  V3Tabs.vue
  V3ChartContainer.vue  # 仅壳，不自造 K 线
```

### 时间语义（产品级硬约束）

`V3DataTimestamp` 必须能同时表达：

- 行情：15:00 已收盘
- 分析：15:10

Quote/session timestamp 与 strategy analysis timestamp **始终分开**。

### Number Typography

金融数字使用 `.v3-number { font-variant-numeric: tabular-nums; }`

## Tailwind Policy

Tailwind 保留，职责收敛为：

- layout / spacing / responsive / display / flex / grid

V3 页面不要用 Tailwind 手造 Button/Input/Select/Dialog/Drawer/Tabs/Table/Menu/Card。

## Naive UI Migration Policy

迁移期允许双框架 bundle，必须：

- 不全量 icon import
- 不无差别全量 plugin/component import
- 不在 UI-0 强删 Naive UI

## Router Migration Policy

- 后续业务页面优先保留原 URL，仅替换 component
- Route meta 扩展见 `src/v3/types/router-meta.d.ts`

## Accessibility

- keyboard reachable
- focus visible
- semantic buttons / headings
- accessible labels
- Drawer/Dialog focus behavior
- 禁止纯 div click 代替 button

## Bundle Tradeoff

双框架成本在迁移期可接受。tree-shaking 依赖 `@quasar/vite-plugin` 按需编译。

## 如何新增一个 V3 页面（标准流程）

1. 路由 meta 设 `uiSystem: 'v3'`
2. 使用 V3 App Shell / Page Container（自动生效）
3. 使用 Quasar primitives（QBtn/QDialog/...）
4. 使用 V3 tokens（`--v3-*`）
5. 使用 `V3PageHeader` / `V3Section` / `V3StatusBadge` / `V3Metric`...
6. **禁止** `import ... from 'naive-ui'`
7. API layer 保持不变
8. 添加 acceptance（e2e）

## Foundation Showcase

- 路由：`/v3/foundation`
- 开关：`VITE_ENABLE_V3_FOUNDATION=true` 或 development 模式
- 组件：`src/v3/views/V3FoundationView.vue`
- 页面数据为 **development showcase data**，不可冒充真实行情
- 普通生产导航不显示

## Deferred Scope（本阶段明确不做）

- 不正式迁移 Dashboard / Holdings / Analysis / Login / Settings
- 不改分析工作流、Candidate Engine、Portfolio Gate
- 不新增 Agent、不做交易执行
- 无数据库 migration
- 不实现完整 InstrumentDetail 业务
- 不自造 SVG K 线

## 后续阶段

1. **V3-UI-0** Quasar Foundation（本阶段）
2. V3-UI-1 Unified InstrumentDetail
3. V3-UI-2 Dashboard
4. V3-UI-3 Holdings
5. V3-UI-4 Analysis Center

## 验证

```bash
cd frontend
npm run typecheck
npm run build
npm run e2e -- e2e/v3-foundation.spec.ts
```

完整 acceptance（需后端）：

```bash
npm run e2e:acceptance
```
