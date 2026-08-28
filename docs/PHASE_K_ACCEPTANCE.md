# Phase K Acceptance

## 验收基线

- 基线 Commit：`40c41376eed444ed8d2419ff4be6e62e32804347`
- 推荐分支：`codex/phase-k-production-readiness`
- Runtime / Decision Contract：`2.4.0`（未机械升级）
- Alembic Head：`20260828_0018`
- 本轮不新增 migration；backup manifest 与 diagnostic bundle 使用持久化 filesystem。

## 已实现

- `GET /healthz/live`、`GET /healthz/ready`、`GET /api/v3/system/*` 分层健康。
- `build_release_metadata()`：App/Git/Schema/Contract/ParameterSet/Uptime 单一权威。
- Schema Compatibility：CURRENT / BEHIND / AHEAD / UNKNOWN / BROKEN；AHEAD 与 BROKEN 均 BLOCKED。
- SQLite WAL Online Backup、`.partial` + atomic rename、quick check、SHA-256、JSON manifest。
- Backup retention / freshness、单并发 `BACKUP_ALREADY_RUNNING`。
- Restore validation、restore drill、offline production restore CLI。
- Pre-upgrade verified backup guard，失败即停止升级。
- Startup preflight、startup recovery report、Scheduler 单一 owner、System maintenance job。
- Request ID correlation、secret redaction、sanitized diagnostic ZIP。
- Docker liveness healthcheck、持久化 backup volume、单 worker `WEB_CONCURRENCY=1`。
- 前端 `/system` 页面；生产 restore 不提供按钮或 API。

## 专项测试

```bash
cd backend
python -m pytest tests/test_system_health.py tests/test_backup_restore.py tests/test_diagnostics.py tests/test_shutdown_recovery.py -q
```

当前 21 项专项测试覆盖：release metadata、schema 三态、disk ratio、request ID、secret redaction、WAL backup 一致性、partial 失败、checksum 篡改、restore drill、restore ahead、pre-upgrade guard、offline restore confirmation、并发 backup lock、scheduler single owner、shutdown、startup recovery counts、diagnostic bundle 不含 secret。

全量：`334 passed`。

## 未实现

Multi-node HA、PostgreSQL、Redis/Kafka/Celery、Prometheus/Grafana 强依赖、Kubernetes、自动 production restore、自动 schema downgrade、云备份上传、备份加密 Key 管理平台、Auto Trading、Broker Execution、Auto Calibration Apply、Phase L Historical Data Foundation Expansion。

