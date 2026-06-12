# 数据库备份

SakuraiSenrin 的数据库备份是项目级增强能力，不属于单个插件。它负责把项目内注册的 SQLite 数据库生成一致性快照，再通过 `restic` 上传到远端仓库。当前推荐远端是 Cloudflare R2。

## 能力位置

- `src/services/backup.py`: 备份编排，负责收集数据库、生成 staging、写 manifest、调用 `restic backup` / `restic forget` / `restic restore`。
- `src/lib/db/backup.py`: SQLite 快照、文件 hash、manifest 数据结构。
- `src/services/backup_scheduler.py`: NoneBot 运行时定时调度。
- `src/lib/backup/events.py`: 备份生命周期事件和回调注册。
- `scripts/run_backup.py`: 手动执行一次备份。
- `scripts/check_backup.py`: 检查远端 restic 仓库是否可达，并列出最近快照。
- `scripts/run_restore.py`: 手动恢复 restic snapshot。

默认纳入备份的数据库包括 `core_db`、`log_db`、`snapshot_db`，以及可导入时的 `water`、`wordbank` 插件数据库。当前默认集合包含 `water_summary`、`wordbank_message_route_db`、`wordbank_message_ref_db`。数据库实例需要实现 `iter_backup_sources()` 才能被备份服务收集。

## 运行要求

- 运行环境需要能执行 `restic` 命令。
- R2 Access Key 需要具备对象读写、列出和删除权限。
- 必须妥善离线保存 `BACKUP_RESTIC_PASSWORD`。该密码用于 restic 仓库加密，丢失后无法恢复备份。
- 手机 chroot 环境下建议把 `BACKUP_LOCAL_ROOT` 放在 UFS 可写目录，例如 `./data/backup`，不要放在临时目录。
- 备份期间会先把 SQLite 快照写入本地 staging，再由 restic 上传；本地需要预留至少一份当前数据库总量的临时空间。

## 配置

配置写入 `.env.dev`、`.env.prod` 或当前 NoneBot 环境对应的 env 文件。模板见 `.env.template`。

```env
BACKUP_ENABLED=true
BACKUP_CRON_HOUR=3
BACKUP_CRON_MINUTE=20
BACKUP_LOCAL_ROOT=./data/backup

BACKUP_RESTIC_REPOSITORY=s3:https://<account_id>.r2.cloudflarestorage.com/<bucket>/sakurai-senrin
BACKUP_RESTIC_PASSWORD=<long-random-password>
BACKUP_REQUIRE_RESTIC=true

R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<r2-access-key-id>
R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
R2_BUCKET=<bucket-name>

BACKUP_RETENTION_DAILY=7
BACKUP_RETENTION_WEEKLY=4
BACKUP_RETENTION_MONTHLY=6
```

字段说明：

- `BACKUP_ENABLED`: 是否在 bot 启动时安装定时备份任务。
- `BACKUP_CRON_HOUR` / `BACKUP_CRON_MINUTE`: 每天执行备份的时间。
- `BACKUP_LOCAL_ROOT`: 本地 manifest 和 staging 根目录。
- `BACKUP_RESTIC_REPOSITORY`: restic 仓库地址。R2 使用 `s3:` 地址。
- `BACKUP_RESTIC_PASSWORD`: restic 仓库加密密码。
- `BACKUP_REQUIRE_RESTIC`: 为 `true` 时，缺少 `restic` 命令会直接失败。
- `BACKUP_RETENTION_DAILY` / `BACKUP_RETENTION_WEEKLY` / `BACKUP_RETENTION_MONTHLY`: `restic forget --prune` 的保留策略。
- `RESTIC_CACHE_DIR`: 可选。建议在受限环境下显式指向可写目录，例如 `./data/restic-cache`。

## 初始化 R2 restic 仓库

第一次使用前需要在同一套配置下执行 `restic init`：

```bash
RESTIC_REPOSITORY='s3:https://<account_id>.r2.cloudflarestorage.com/<bucket>/sakurai-senrin' \
RESTIC_PASSWORD='<long-random-password>' \
AWS_ACCESS_KEY_ID='<r2-access-key-id>' \
AWS_SECRET_ACCESS_KEY='<r2-secret-access-key>' \
restic init
```

初始化只需要执行一次。后续 bot 定时任务和 `scripts/run_backup.py` 会复用同一个仓库。

## 自动备份

`BACKUP_ENABLED=true` 时，`bot.py` 启动会安装 `database_backup_default` 定时任务，由 `nonebot_plugin_apscheduler` 按 `BACKUP_CRON_HOUR` / `BACKUP_CRON_MINUTE` 触发。

当前调度配置固定为：

- `coalesce=True`
- `misfire_grace_time=600`
- `max_instances=1`

这意味着：

- 同一时间只会跑一个备份任务，避免重入。
- 错过执行窗口 10 分钟内仍会补跑一次。
- 多次错过不会堆积成并发备份。

## 手动备份

即使 `BACKUP_ENABLED=false`，也可以用 `--force` 手动执行一次：

```bash
uv run python scripts/run_backup.py --force
```

成功后会输出本次 `run_id`、本地 manifest 路径和 restic snapshot id。manifest 会保存在：

```text
<BACKUP_LOCAL_ROOT>/manifests/<run_id>.json
```

## 健康检查

建议在改密钥、改 bucket、迁移机器或升级 `restic` 后先跑一次健康检查：

```bash
uv run python scripts/check_backup.py
```

默认会列出最近 `3` 个远端快照，也可以自定义：

```bash
uv run python scripts/check_backup.py --limit 5
```

如果仓库可达，会输出快照数量、快照时间、主机名、处理文件数和总字节数。

## 恢复验证

建议定期恢复到临时目录做验证，不要直接覆盖运行中的数据库目录：

```bash
uv run python scripts/run_restore.py latest --target ./data/restore-check
```

确认恢复内容无误后，再按维护流程停止 bot、备份当前数据目录、替换数据库文件。

## 推荐巡检顺序

建议运维按下面顺序做：

1. `uv run python scripts/check_backup.py`
2. `uv run python scripts/run_backup.py --force`
3. `uv run python scripts/run_restore.py latest --target ./data/restore-check`

如果第 1 步失败，优先检查：

- `BACKUP_RESTIC_REPOSITORY` 是否仍是 `s3:https://<account>.r2.cloudflarestorage.com/<bucket>/<path>` 格式
- `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` 是否已失效
- `RESTIC_CACHE_DIR` 是否可写
- 运行环境里是否能找到 `restic`

## 生命周期回调

备份服务会派发以下事件：

- `BackupStarted`
- `BackupSucceeded`
- `BackupFailed`
- `BackupSkipped`

其他模块可以通过 `register_backup_callback()` 注册回调，例如在备份成功或失败后通知管理员：

```python
from src.lib.backup import BackupFailed, BackupSucceeded, register_backup_callback


async def notify_backup_result(event):
    if isinstance(event, BackupSucceeded):
        ...
    if isinstance(event, BackupFailed):
        ...


register_backup_callback(notify_backup_result)
```

回调异常会被记录为 warning，不会中断备份主流程。

## 与对象存储的关系

项目内有通用对象存储 provider：

- `src/lib/object_storage/r2.py`
- `src/lib/object_storage/github.py`

`wordbank` 媒体文件可以通过 `WORDBANK_MEDIA_PROVIDER=local|r2|github` 选择存储后端。数据库备份当前不直接使用对象存储 provider 上传，而是通过 `restic` 使用 R2 的 S3 endpoint 上传。这样可以获得加密、增量、去重和保留策略能力。
