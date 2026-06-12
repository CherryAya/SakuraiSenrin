# 备份管理模块

## 概览
备份管理模块用于在 bot 内直接检查远端备份仓库、查看最近快照，以及手动触发一次数据库备份，方便超级管理员做日常巡检和紧急操作。

## 权限与触发
- 触发方式: 指令触发
- 权限: 超级管理员

## 子功能目录
- `check` 健康检查: 检查远端仓库是否可达，并返回最新快照摘要。
- `snapshots` 查看快照: 列出最近若干个远端快照。
- `run` 立即备份: 立即触发一次数据库备份并返回结果。

## 子功能详情
### `check` 健康检查
- 摘要: 检查远端仓库是否可达，并返回最新快照摘要。
- 别名: 健康检查
- 指令: `#admin backup check`
- Demo: admin-backup-check.png
#### 说明
适合在改配置、换密钥、迁移机器后先确认远端仓库仍然可访问。
#### 前置条件
当前环境需要已配置 `BACKUP_RESTIC_REPOSITORY`、`BACKUP_RESTIC_PASSWORD` 和有效的 R2 `AK/SK`。
#### 完整流程
直接执行命令即可。
```demo
USER: #admin backup check
BOT: 远端备份仓库可用，共 2 个快照。
BOT: 最新快照:
BOT: - 快照: d3da8f8f
BOT:   时间: 2026-06-12T21:35:21+08:00
```
#### 失败情况
如果仓库地址错误、AK/SK 失效或 `restic` 不可用，会直接返回失败原因。

### `snapshots` 查看快照
- 摘要: 列出最近若干个远端快照。
- 别名: 快照列表
- 指令: `#admin backup snapshots [limit]`
- Demo: admin-backup-snapshots.png
#### 说明
默认展示最近 5 个快照，也可以手动传一个数量限制。
#### 前置条件
远端仓库必须可达。
#### 完整流程
可不传参数，也可以显式传入要查看的数量。
```demo
USER: #admin backup snapshots 2
BOT: 最近 2 个快照：
BOT: - 快照: d3da8f8f
BOT: - 快照: b34d2f64
```
#### 失败情况
当参数不是大于 0 的整数时，会直接返回参数错误。

### `run` 立即备份
- 摘要: 立即触发一次数据库备份并返回结果。
- 别名: 立即备份
- 指令: `#admin backup run`
- Demo: admin-backup-run.png
#### 说明
该命令会复用正式备份服务，等价于运维侧执行 `uv run python scripts/run_backup.py --force`。
#### 前置条件
当前环境必须已经完成 restic 仓库初始化。
#### 完整流程
直接执行命令，等待返回 `run_id`、manifest 路径和 snapshot id。
```demo
USER: #admin backup run
BOT: 备份已完成。
BOT: run_id: default-1781271320-78e963f9
BOT: snapshot: d3da8f8f5d690393...
```
#### 失败情况
如果远端仓库不可写、本地 staging 空间不足或备份服务异常，会直接返回失败原因。
