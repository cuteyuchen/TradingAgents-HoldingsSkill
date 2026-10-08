# Release Checklist

以下步骤应在正式发布前逐项执行；任何一项失败都必须停止发布并记录原因。

## 1. 源码与构建

```bash
git fetch --all --prune
git status --short
git diff --check
git rev-parse HEAD
python -m compileall -q backend/app
```

## 2. 后端测试

```bash
cd backend
python -m pytest tests -q
python -m pytest tests/test_parameter_governance.py tests/test_governance_api.py tests/test_alpha_memory.py -q
```

## 3. 前端

```bash
cd frontend
npm run typecheck
npm run build
```

## 4. Migration

```bash
cd backend
alembic heads
alembic current
python -c "import app.main"
```

必须验证：

- fresh DB → `alembic upgrade head`
- `0018` → head（本轮 head 仍为 `0018` 时至少验证 current/head 一致）
- head → head 幂等

## 5. Backup / Restore Drill

```bash
cd backend
python - <<'PY'
from app.system.backup import create_backup, verify_backup, restore_drill
m = create_backup(reason="MANUAL")
print(verify_backup(m["backup_id"]))
print(restore_drill(m["backup_id"]))
PY
```

## 6. Health / Readiness

```bash
curl -fsS http://127.0.0.1:8000/healthz/live
curl -fsS http://127.0.0.1:8000/healthz/ready
curl -fsS -H "Authorization: Bearer $ADVISOR_TOKEN" http://127.0.0.1:8000/api/v3/system/readiness
```

## 7. Docker

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose exec advisor python -m app.system.startup
```

## 8. 发布信息

- 确认 `APP_GIT_SHA`、`APP_BUILD_TIME` 由镜像 build arg 注入。
- 确认 `GET /api/v3/system/release` 返回期望版本。
- 确认没有未提交修改、没有 secret 进入 `.env.example` 或日志。

