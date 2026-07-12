# 备份管理模块

## 概览
备份管理模块用于在 bot 内直接检查远端备份仓库、查看最近快照、手动触发数据库备份，以及把指定快照恢复到当前本地数据，方便超级管理员做日常巡检和紧急操作。

## 权限与触发
- 触发方式: 指令触发
- 权限: 超级管理员

## 用法
- Help: `admin.backup`

## 子功能目录
- `check` 健康检查: 检查远端仓库是否可达，并返回最新快照摘要。
- `snapshots` 查看快照: 列出最近若干个远端快照。
- `run` 立即备份: 立即触发一次数据库备份并返回结果。
- `restore` 恢复到本地: 把指定远端快照恢复到当前本地数据，并立即刷新运行时状态。

## 子功能详情
### `check` 健康检查
- 摘要: 检查远端仓库是否可达，并返回最新快照摘要。
- 别名: 健康检查
- 指令: `#admin.backup check`
- Demo: admin-backup-check.webp
#### 说明
适合在改配置、换密钥、迁移机器后先确认远端仓库仍然可访问。
#### 前置条件
当前环境需要已配置可用的备份 profile，以及对应的 restic 仓库和 R2 `AK/SK`。
#### 完整流程
直接执行命令即可。
```demo
USER: #admin.backup check
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
- 指令: `#admin.backup snapshots [limit]`
- Demo: admin-backup-snapshots.webp
#### 说明
默认展示最近 5 个快照，也可以手动传一个数量限制。
#### 前置条件
远端仓库必须可达。
#### 完整流程
可不传参数，也可以显式传入要查看的数量。
```demo
USER: #admin.backup snapshots 2
BOT: 最近 2 个快照：
- 快照: d3da8f8f
- 快照: b34d2f64
```
#### 失败情况
当参数不是大于 0 的整数时，会直接返回参数错误。

### `run` 立即备份
- 摘要: 立即触发一次数据库备份并返回结果。
- 别名: 立即备份
- 指令: `#admin.backup run`
- Demo: admin-backup-run.webp
#### 说明
该命令会复用当前环境默认的正式备份 profile，等价于运维侧执行 `uv run python scripts/run_backup.py --force`。
#### 前置条件
当前环境必须已经完成 restic 仓库初始化。
#### 完整流程
直接执行命令，等待返回 `run_id`、manifest 路径和 snapshot id。
```demo
USER: #admin.backup run
BOT: 备份已完成。
run_id: default-1781271320-78e963f9
snapshot: d3da8f8f5d690393...
```
#### 失败情况
如果远端仓库不可写、本地 staging 空间不足或备份服务异常，会直接返回失败原因。

### `restore` 恢复到本地
- 摘要: 把指定远端快照恢复到当前本地数据，并立即刷新运行时状态。
- 别名: 恢复到本地
- 指令: `#admin.backup restore <snapshot_id|latest> [profile] [confirm-production]`
#### 说明
该命令会直接覆盖当前 bot 使用的本地数据库文件，适合在启动同步被跳过后，或需要手动回滚到某个远端快照时使用。开发环境可显式指定生产 profile 做覆盖恢复；生产环境只允许从自身默认 profile 恢复，并且必须额外带上 `confirm-production`。
#### 前置条件
当前环境必须可以访问目标备份 profile 对应的远端 restic 仓库，且命令执行者确认本地现有数据可以被覆盖。
#### 完整流程
显式传入快照 ID，或使用 `latest` 恢复最近快照；可选再带一个 profile 名；恢复完成后会立即刷新核心仓库缓存，以及 `wordbank`、`water` 的运行时状态。
```demo
USER: #admin.backup restore latest
BOT: 远端快照已恢复到本地，并已刷新运行时状态。
BOT: profile: dev
BOT: 快照引用: latest
```
#### 失败情况
如果快照不存在、远端仓库不可读、manifest 缺失、profile 不允许当前环境恢复，或运行时刷新失败，会直接返回失败原因。
