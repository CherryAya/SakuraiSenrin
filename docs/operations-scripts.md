# Operations Scripts

本仓库的运维脚本统一放在 `scripts/` 目录。本文档作为集中索引，说明每个脚本的用途、前置条件、常用命令和输出物，避免后续维护继续分散在脚本实现里。

## 约定

- 默认通过 `uv run python <script>` 执行。
- 涉及 `nonebot.init()` 的脚本依赖当前 `.env` / NoneBot 配置可正常加载。
- 涉及历史数据迁移的脚本通常依赖旧仓库路径、旧 PostgreSQL 连接信息或历史图片目录。
- 大多数审计/维护脚本都会落一个 JSON report，默认在 `./data/db/` 或 `./data/wordbank/` 下。
- 运行前先确认当前目标环境：本地开发、测试服、生产机不要混用同一份数据库目录。

## 快速索引

| 类别 | 脚本 |
| --- | --- |
| 备份与恢复 | `scripts/run_backup.py`, `scripts/check_backup.py`, `scripts/run_restore.py` |
| 分片归档 | `scripts/archive_event_shards.py` |
| Water 迁移/审计/维护 | `scripts/migrate_water.py`, `scripts/audit_water_storage.py`, `scripts/maintain_water_storage.py` |
| Wordbank 迁移/审计/维护 | `scripts/migrate_wordbank.py`, `scripts/audit_wordbank_rules.py`, `scripts/maintain_wordbank_media.py`, `scripts/backfill_wordbank_media_cache.py` |
| System 迁移 | `scripts/migrate_system.py` |
| 文档资产维护 | `scripts/build_docs.py` |

## 备份与恢复

### `scripts/run_backup.py`

- 用途：按当前配置执行一次数据库备份。
- 前置条件：备份配置可用；如启用 restic / 远端对象存储，需要相关环境变量完整。
- 常用命令：

```bash
uv run python scripts/run_backup.py
uv run python scripts/run_backup.py --force
```

- 关键参数：
  - `--force`：即使配置中禁用了备份也强制执行。
- 输出：
  - 日志中打印 `run_id`
  - manifest 路径
  - 如果走 restic，打印 snapshot id

### `scripts/check_backup.py`

- 用途：检查备份链路是否健康，并列出远端 snapshot。
- 前置条件：备份服务配置可用，远端仓库可访问。
- 常用命令：

```bash
uv run python scripts/check_backup.py
uv run python scripts/check_backup.py --limit 10
```

- 关键参数：
  - `--limit`：最多显示多少条远端 snapshot。

### `scripts/run_restore.py`

- 用途：把指定 restic snapshot 恢复到目标目录。
- 前置条件：备份仓库可访问，目标目录可写。
- 常用命令：

```bash
uv run python scripts/run_restore.py latest --target ./tmp/restore-latest
uv run python scripts/run_restore.py <snapshot-id> --target ./tmp/restore-one
```

- 关键参数：
  - 位置参数 `snapshot`：snapshot id，或 `latest`
  - `--target`：恢复目录
- 注意：
  - 恢复动作会向目标目录写文件，生产环境建议写到新目录做比对，不直接覆盖运行目录。

## 分片归档

### `scripts/archive_event_shards.py`

- 用途：手动触发 `wordbank` / `water` 分片数据库归档，并可清理归档后的陈旧 `-wal/-shm` sidecar。
- 常用命令：

```bash
uv run python scripts/archive_event_shards.py
uv run python scripts/archive_event_shards.py --target wordbank
uv run python scripts/archive_event_shards.py --target water --include-water-summary
uv run python scripts/archive_event_shards.py --cleanup-stale-sidecars
```

- 关键参数：
  - `--target wordbank|water|all`
  - `--include-water-summary`：归档 `water_summary` 分片
  - `--cleanup-stale-sidecars`：清理已归档分片旁边残留的 `-wal/-shm`

## Water 迁移 / 审计 / 维护

### `scripts/migrate_water.py`

- 用途：把旧版 PostgreSQL `senrin_water` 数据迁移到当前 water 插件。
- 前置条件：
  - 可连接旧 PostgreSQL
  - 当前仓库运行环境正常
  - 如不传连接参数，会从 `--old-repo` 对应旧仓库中读取默认 PG 配置
- 常用命令：

```bash
uv run python scripts/migrate_water.py
uv run python scripts/migrate_water.py --from-date 20250101 --to-date 20250331
uv run python scripts/migrate_water.py --chunk-size 20000 --fetch-size 100000 --prefetch-batches 4
```

- 关键参数：
  - `--old-repo`
  - `--pg-host/--pg-port/--pg-user/--pg-password/--pg-database`
  - `--no-reset-target`
  - `--from-date` / `--to-date`
  - `--chunk-size`
  - `--fetch-size`
  - `--prefetch-batches`
- 输出：
  - 默认 report：`./data/db/water-migration-report.json`

### `scripts/audit_water_storage.py`

- 用途：审计 water 当前存储布局、日志索引基线和归档状态，并可做 benchmark。
- 常用命令：

```bash
uv run python scripts/audit_water_storage.py
uv run python scripts/audit_water_storage.py --inspect-archives
uv run python scripts/audit_water_storage.py --strict-indexes
```

- 关键参数：
  - `--db-root`
  - `--namespace`
  - `--report`
  - `--benchmark-iterations`
  - `--inspect-archives`
  - `--strict-indexes`
- 输出：
  - 默认 report：`./data/db/water-storage-audit.json`

### `scripts/maintain_water_storage.py`

- 用途：一次性执行 water 存储整理，包括历史 summary 回填、冗余日志索引清理、core summary 修剪，并可附带审计。
- 常用命令：

```bash
uv run python scripts/maintain_water_storage.py
uv run python scripts/maintain_water_storage.py --run-audit
uv run python scripts/maintain_water_storage.py --skip-summary-backfill
```

- 关键参数：
  - `--summary-batch-size`
  - `--skip-summary-backfill`
  - `--skip-log-index-drop`
  - `--skip-summary-prune`
  - `--run-audit`
- 输出：
  - 默认 report：`./data/db/water-storage-maintenance-report.json`

## Wordbank 迁移 / 审计 / 维护

### `scripts/migrate_wordbank.py`

- 用途：把旧版 PostgreSQL `senrin_wordbank` 数据迁移到当前 SQLite `wordbank`。
- 特点：
  - 支持完整迁移
  - 支持从历史 report 追加补录
  - 支持从分类错误报告中重试特定类别
- 前置条件：
  - 旧 PostgreSQL 可访问，或已有可复用的迁移 report
  - 历史图片目录 / mapping 文件可用时建议显式提供
- 常用命令：

```bash
uv run python scripts/migrate_wordbank.py
uv run python scripts/migrate_wordbank.py --no-import-logs
uv run python scripts/migrate_wordbank.py --append-from-report ./data/wordbank/migration-report.json
uv run python scripts/migrate_wordbank.py --append-from-error-report ./data/wordbank/error-categories.json --error-category trigger_shape_empty
```

- 关键参数：
  - `--old-repo`
  - `--image-root`
  - `--mapping-file`
  - `--pg-host/--pg-port/--pg-user/--pg-password/--pg-database`
  - `--no-reset-target`
  - `--no-import-logs`
  - `--progress-step`
  - `--append-from-report`
  - `--append-from-error-report`
  - `--error-category`：可重复传入
- 输出：
  - 默认 report：`./data/wordbank/migration-report.json`

### `scripts/audit_wordbank_rules.py`

- 用途：审计 `wordbank` 规则，重点检查非法 `call_count` window，并可安全修复。
- 常用命令：

```bash
uv run python scripts/audit_wordbank_rules.py
uv run python scripts/audit_wordbank_rules.py --apply
```

- 关键参数：
  - `--db-path`
  - `--report`
  - `--apply`：把非法 window 安全收敛到 3 个月
- 输出：
  - 默认 report：`./data/db/wordbank-rule-audit-report.json`

### `scripts/maintain_wordbank_media.py`

- 用途：维护 `wordbank` 图片远端化与本地缓存元数据。
- 当前能力：
  - 扫描待同步图片
  - 并发上传远端对象
  - `--verify-remote` 时优先拉远端对象清单做差集比对
  - 可重建本地缓存元数据
- 常用命令：

```bash
uv run python scripts/maintain_wordbank_media.py --only-unsynced
uv run python scripts/maintain_wordbank_media.py --only-unsynced --verify-remote --batch-size 200 --concurrency 16
uv run python scripts/maintain_wordbank_media.py --dry-run --only-unsynced
```

- 关键参数：
  - `--dry-run`
  - `--limit`
  - `--batch-size`
  - `--concurrency`
  - `--id-start`
  - `--only-unsynced`
  - `--verify-remote`
  - `--rebuild-cache-metadata`
  - `--report`
- 输出：
  - 默认 report：`./data/db/wordbank-media-maintenance-report.json`
- 建议：
  - 大批量补传时优先使用 `--verify-remote --concurrency N`
  - 常规补传不要把 `concurrency` 设得过高，先从 `8/16/32` 试起

### `scripts/backfill_wordbank_media_cache.py`

- 用途：从现有 `media_cache` 文件反填 `local_cache_path` / `cache_file_size` 等缓存元数据。
- 适用场景：
  - 缓存目录还在，但 DB 元数据缺失
  - 历史缓存迁移后需要重新挂回数据库
- 常用命令：

```bash
uv run python scripts/backfill_wordbank_media_cache.py
uv run python scripts/backfill_wordbank_media_cache.py --dry-run
uv run python scripts/backfill_wordbank_media_cache.py --include-existing
```

- 关键参数：
  - `--dry-run`
  - `--limit`
  - `--id-start`
  - `--include-existing`
  - `--report`
- 输出：
  - 默认 report：`./data/db/wordbank-media-cache-backfill-report.json`

## System 迁移

### `scripts/migrate_system.py`

- 用途：把旧版 PostgreSQL `senrin_system` 数据迁移到当前 `core.db`。
- 常用命令：

```bash
uv run python scripts/migrate_system.py
uv run python scripts/migrate_system.py --no-reset-target
```

- 关键参数：
  - `--old-repo`
  - `--pg-host/--pg-port/--pg-user/--pg-password/--pg-database`
  - `--report`
  - `--no-reset-target`
- 输出：
  - 默认 report：`./data/db/system-migration-report.json`

## 文档资产维护

### `scripts/build_docs.py`

- 用途：维护插件帮助文档相关 demo PNG 资产，不属于线上运行时运维，但属于仓库资产维护脚本。
- 子命令：
  - `build`：生成 demo 图、合成 collection 图、再做校验
  - `generate`：只生成 feature demo 图
  - `compose`：只合成 collection 图
  - `validate`：只校验 README 和 demo 资产
- 常用命令：

```bash
uv run python scripts/build_docs.py build
uv run python scripts/build_docs.py build -j 4 --columns 2 --thumb-width 700
uv run python scripts/build_docs.py validate
```

- 关键参数：
  - `-j/--workers`
  - `--columns`
  - `--thumb-width`

## 维护建议

后续新增脚本时，建议同步满足以下要求：

1. 脚本头部 docstring 直接说明用途，不写模糊名字。
2. 所有脚本都提供 `argparse` 帮助文本和默认输出路径。
3. 涉及修改数据的脚本优先提供 `--dry-run` 或等价预览模式。
4. 涉及批处理的脚本优先提供 `--limit`、分页或并发控制参数。
5. 任何新脚本都要在本文档登记：
   - 用途
   - 前置条件
   - 常用命令
   - 关键参数
   - 默认 report / 输出位置

## 推荐执行顺序

几个常见场景可以按下面顺序处理：

### Wordbank 图片远端化

1. `uv run python scripts/maintain_wordbank_media.py --dry-run --only-unsynced`
2. `uv run python scripts/maintain_wordbank_media.py --only-unsynced --verify-remote --batch-size 200 --concurrency 16`
3. `uv run python scripts/backfill_wordbank_media_cache.py --include-existing`

### Water 存储整理

1. `uv run python scripts/audit_water_storage.py`
2. `uv run python scripts/maintain_water_storage.py --run-audit`
3. 如需归档事件库，再执行 `uv run python scripts/archive_event_shards.py --target water --include-water-summary`

### 备份巡检

1. `uv run python scripts/check_backup.py`
2. `uv run python scripts/run_backup.py`
3. 如需演练恢复，执行 `uv run python scripts/run_restore.py latest --target ./tmp/restore-latest`
