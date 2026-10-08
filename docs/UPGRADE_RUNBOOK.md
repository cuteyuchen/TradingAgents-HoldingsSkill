# Upgrade Runbook

## 目标

把当前自托管部署升级到新版本，且不丢失可恢复能力。

## 前置条件

- 停止新分析任务或等待非交易时段。
- 确认 `BACKUP_DIR` 位于持久化 volume。
- 准备足够磁盘空间（至少数据库大小加一个 verified backup）。

## 步骤

1. 拉取代码并确认 release metadata：

```bash
git fetch --all --prune
git checkout <target-release>
git rev-parse HEAD
```

2. 在旧版本上创建 verified `PRE_UPGRADE` backup：

```bash
cd backend
python -m app.system.startup
```

`app.system.startup` 会先执行 pre-upgrade guard；backup 失败时进程以非零退出，migration 不会执行。

3. 如果 Docker 启动命令已切换到 `python -m app.system.startup && uvicorn ...`，则由容器入口自动完成 backup → `alembic upgrade head` → startup preflight。

4. 升级后执行：

```bash
curl -fsS http://127.0.0.1:8000/healthz/live
curl -fsS http://127.0.0.1:8000/healthz/ready
curl -fsS -H "Authorization: Bearer $ADVISOR_TOKEN" http://127.0.0.1:8000/api/v3/system/release
```

5. 创建一次 manual backup 并执行 restore drill：

```bash
POST /api/v3/system/backups
POST /api/v3/system/backups/{id}/verify
POST /api/v3/system/backups/{id}/restore-drill
```

## Migration 失败处理

- 不自动 downgrade。
- 记录 backup id、failure reason、operator recovery command。
- 从最近 verified backup 走 offline restore（必须先停止服务）。
- 修复 migration 后重新执行 `python -m app.system.startup`。

