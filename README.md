# TradingAgents-HoldingsSkill — 以 Skill 为核心的 A 股持仓交易助手

## 项目定位

**TradingAgents-HoldingsSkill** 是面向 A 股/ETF 持仓截图的日内/隔夜操作建议 Skill。它强调建议质量优先：先完成持仓解析、行情/资金面/消息面/板块位置核验、质量门控和风控约束，再输出建议；前后端只负责把已经展示给用户的建议归档，上传失败不改变建议本身。

前后端当前为归档模式：

- 后端：保存持仓截图、解析后的持仓 JSON、建议过程 Markdown。
- 前端：单页归档工作台，展示归档列表、渲染后的 Markdown、持仓数据和原始截图。
- 鉴权：单一静态 Bearer Token，前端登录密码即 `ADVISOR_TOKEN`。

## 架构

```
┌──────────────┐   先展示建议，再上传归档   ┌───────────┐   查询归档   ┌────────────┐
│ TradingAgents│ ─────────────────────▶ │  backend  │ ◀────────▶ │  frontend  │
│ HoldingsSkill│                        │ FastAPI   │             │  Vue3+TS   │
└──────────────┘                        │ SQLite    │             │  Naive UI  │
                                        │ artifacts │             │ Markdown   │
                                        └───────────┘             └────────────┘
```

## 目录结构

```
TradingAgents-HoldingsSkill/
├── skill/
│   └── tradingagents-holdings-advisor/
│       ├── SKILL.md
│       └── references/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口，只注册归档 API
│   │   ├── config.py            # 环境变量配置
│   │   ├── database.py          # SQLAlchemy engine/session/建表
│   │   ├── auth.py              # 单 Token Bearer 鉴权
│   │   ├── models.py            # ORM，保留历史表兼容已有 SQLite
│   │   ├── schemas.py           # Pydantic schema
│   │   ├── routers/archives.py  # 归档上传、列表、详情、删除
│   │   └── services/pnl.py      # 历史盈亏字段修复
│   ├── tests/
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/                 # 归档 API 客户端 + 类型
│   │   ├── views/ArchiveList.vue# 单页归档工作台
│   │   └── App.vue main.ts
│   └── Dockerfile + nginx.conf
├── docker-compose.yml
└── .env.example
```

## 快速开始

### Docker 一键启动

```bash
cd backend && python -m app.seed
cd ..
cp .env.example .env
# 编辑 .env，填入 ADVISOR_TOKEN
docker compose up -d --build
```

- 后端：`http://localhost:8000`
- 前端：`http://localhost:8080`
- Swagger：`http://localhost:8000/docs`

### 本地开发

后端：

```powershell
.\backend\scripts\start-dev.ps1
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## API 概览

除 `/healthz` 外，看板 API 均需要 `Authorization: Bearer <ADVISOR_TOKEN>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 服务健康检查 |
| GET | `/api/v1/auth/verify` | 校验前端登录密码 |
| POST | `/api/v1/archives` | 上传归档：截图、持仓 JSON、建议 Markdown、可选 meta |
| GET | `/api/v1/archives` | 归档列表 |
| GET | `/api/v1/archives/{id}` | 单条归档详情：Markdown、持仓 JSON、截图 data_url |
| DELETE | `/api/v1/archives/{id}` | 删除归档及其文件目录 |

归档文件落盘到：

```
backend/data/artifacts/<archive_id>/
├── screenshot.<ext>
├── holdings.json
└── advice.md
```

## 持仓数量语义

- `qty` 是总持仓。
- `available_qty` 只是当前可卖/可交易数量。
- `qty - available_qty` 是不可用数量，可能来自挂单、冻结或 T+1 限制，不能推断为已减仓。
- 减仓/卖出建议数量不得超过 `available_qty`。

## 测试

```bash
python -m pytest backend/tests/test_archives.py backend/tests/test_archive_only_routes.py backend/tests/test_smoke.py -q
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

后端测试覆盖归档上传、详情读取、删除、截图 data_url、持仓不可用数量语义，以及旧接口不再暴露。

## 使用 Skill

`skill/tradingagents-holdings-advisor/` 是主能力目录。配置以下环境变量后，Skill 会在展示建议后自动上传归档：

```
ADVISOR_API_URL=http://localhost:8000/api/v1
ADVISOR_TOKEN=adv_xxx
```

未配置后端时，Skill 仍可独立完成分析和建议输出。
