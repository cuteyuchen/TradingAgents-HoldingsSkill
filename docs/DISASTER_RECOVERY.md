# Disaster Recovery

## 原则

- Backup is not success until verified。
- Restore 必须先在临时 DB 上 drill。
- 生产 restore 只允许 offline CLI，绝不自动执行。
- 不承诺未经验证的 RPO/RTO 或“零数据丢失”。

## DB Corruption

1. 停止应用，`PRAGMA quick_check` 定位损坏范围。
2. 如果 `quick_check` 失败，进入 BLOCKED，不要自动选择最近 backup 覆盖生产。
3. 创建 `PRE_RESTORE_SAFETY` backup（如果原 DB 仍可读）。
4. 使用 verified backup 执行 restore drill。
5. 停止服务后执行 offline restore：

```bash
python -m app.system.restore --backup <backup-id> --target <db-path> --yes
python -m app.system.startup
```

6. 启动后检查 readiness、governance、release metadata。

## Bad Migration

- 不自动 downgrade。
- 记录 backup id 与失败原因。
- 从 migration 前 verified backup 恢复，再手动修复并重新升级。

## Disk Full

- `DISK_DEGRADED_RATIO` / `DISK_BLOCKED_RATIO` 会显示 DEGRADED / BLOCKED。
- 清理旧 backup（保留最后一个 verified backup）或扩容 volume。
- BLOCKED 时风险增加型工作 fail-close，但不会自动卖出持仓。

## Lost ACTIVE Governance Row

- Governance 有历史但无 ACTIVE：`NO_ACTIVE_PARAMETER_SET_WITH_HISTORY`，Readiness BLOCKED。
- 禁止自动猜回 Legacy；由 operator 审计后创建新版本并显式激活。

## Provider Outage

- Operational Health 显示 DEGRADED / BLOCKED。
- Liveness 保持 true，避免 Docker restart loop。
- 新风险增加 fail-close；已有持仓不自动卖出。

## 恢复验证

每次 DR 后必须验证：

- DB 可写、quick check ok
- schema state = CURRENT
- governance = OK/DEGRADED（无 BLOCKED）
- scheduler 单 owner、worker recovery 无 stranded jobs
- 最近 verified backup 新鲜度正常

