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

## Phase K.1｜Operational Integrity Hardening

- Readiness 成为 risk-work fail-close authority：schema BEHIND/AHEAD/BROKEN 均 BLOCKED，
  Analysis create/retry、Candidate scan、Scheduled/Checkpoint 新任务统一走
  `require_runtime_ready_for_risk_work()`；重型 `PRAGMA quick_check` 只在 startup、
  daily maintenance、manual diagnostics 执行并缓存，readiness 只做轻量 probe。
- Backup verified publication：manifest 带 `status=VERIFIED` 与 `verified_at`，最终 DB
  quick-check + SHA-256 全通过后才原子发布；失败清理 final/partial；`list_backups` 只列
  verified；freshness 会实际校验最近备份；`SCHEDULED` 备份按 DAILY/WEEKLY 分桶保留；
  backup ID 使用随机 suffix。
- Restore 改为 revision-aware：旧 revision backup 先复制到临时 DB 再 Alembic upgrade head，
  然后要求 required tables、ACTIVE ParameterSet 恰有一个、governance health 与 config hash
  全部通过才判 PASS；物理 backup verification 与当前 schema 解耦。
- 生产日志链：console + memory handler 统一 `RedactingFormatter`，输出
  `request_id / analysis_job_id / backtest_run_id / parameter_set_version`，覆盖
  `api_key=xxx` 无引号形式；删除 ADVISOR_TOKEN 前缀日志。
- Graceful shutdown / recovery：全局 worker registry 在 shutdown 时发 stop signal 并
  bounded wait；Analysis stale 只按 checkpoint lease 过期判断，正常 running 单独统计。
- GHCR workflow 注入 `APP_VERSION / APP_GIT_SHA / APP_BUILD_TIME` build-args。

## 专项测试

```bash
cd backend
python -m pytest tests/test_system_health.py tests/test_backup_restore.py tests/test_diagnostics.py tests/test_shutdown_recovery.py -q
```

当前 36 项专项测试覆盖：release metadata、schema 三态、disk ratio、request ID、secret
redaction、console redaction/correlation、WAL backup 一致性、partial 失败、checksum 篡改、
unique backup ID、unverified manifest 过滤、旧 revision verify、restore drill（含旧 schema
升级、governance no-active、config hash mismatch）、restore ahead、pre-upgrade guard、
offline restore confirmation、并发 backup lock、scheduler single owner、shutdown、worker
registry signal、startup recovery counts、diagnostic bundle 不含 secret。

全量：`349 passed`。

## 未实现

Multi-node HA、PostgreSQL、Redis/Kafka/Celery、Prometheus/Grafana 强依赖、Kubernetes、自动 production restore、自动 schema downgrade、云备份上传、备份加密 Key 管理平台、Auto Trading、Broker Execution、Auto Calibration Apply、Phase L Historical Data Foundation Expansion。
