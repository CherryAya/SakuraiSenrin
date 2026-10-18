# 备份与恢复

按 [.env.template](../../.env.template) 配置 `BACKUP_*` 和远端凭据，`BACKUP_ENABLED` 控制自动备份，并妥善保存仓库密码。

下面依次查询快照、强制备份和恢复到独立目录；快照列表可见不代表备份内容已验证，其他参数看 [恢复脚本](../../scripts/run_restore.py) 的 `--help`。

```bash
uv run python scripts/check_backup.py
uv run python scripts/run_backup.py --force
uv run python scripts/run_restore.py latest --target ./data/restore-check
```
