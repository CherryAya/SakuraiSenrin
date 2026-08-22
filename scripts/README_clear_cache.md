# 消息缓存清理脚本

## 使用场景

当你修改了 help 插件的配置（如群号、文案等），但数据库中还保留着旧的消息缓存时，使用此脚本精确清理受影响的缓存。

## 脚本说明

### `clear_help_cache.py`

**功能：** 只清理 `source_kind='help'` 的消息缓存，保留其他插件的缓存记录。

**适用场景：**
- 修改了 `src/lib/plugin_docs/meta.py` 中的 `HELP_SUPPORT_GROUPS`（群号）
- 修改了 help 插件的文案或配置
- 需要强制重新生成 help 消息

**不适用场景：**
- 其他插件的缓存问题（需要修改脚本的 `source_kind` 过滤条件）
- 需要清理所有缓存（直接删除数据库文件更快）

## 使用方法

### 1. 在生产环境执行

```bash
# 进入项目根目录
cd /path/to/SakuraiSenrin

# 执行清理脚本
python3 scripts/clear_help_cache.py
```

### 2. 查看输出

脚本会输出：
1. **清理前**的缓存统计（按 source_kind 分组）
2. **删除的记录数**
3. **清理后**的缓存统计（验证是否清理干净）

示例输出：
```
=== Clear Help Cache ===
[Before] Cache statistics:
[ClearCache] Total cache records: 8234
  - message_plan: 5123
  - wordbank: 2890
  - help: 221
[ClearCache] Found 221 help cache records, preparing to delete...
[ClearCache] Successfully deleted 221 help cache records
[ClearCache] Verification passed: all help cache cleared
[After] Cache statistics:
[ClearCache] Total cache records: 8013
  - message_plan: 5123
  - wordbank: 2890
=== Done ===
```

### 3. 重启 bot

清理缓存后，重启 bot 进程让新代码生效：

```bash
# 根据你的部署方式重启
systemctl restart sakurai-bot
# 或
pm2 restart sakurai-bot
```

## 注意事项

1. **脚本是幂等的**：多次运行不会有副作用
2. **不影响其他缓存**：只删除 `source_kind='help'` 的记录
3. **必须重启 bot**：清理缓存后，bot 进程需要重启才能加载新的 `HELP_SUPPORT_GROUPS` 配置

## 原理说明

### 为什么需要清理缓存？

1. **模块级常量缓存**：Python 在 bot 启动时加载 `HELP_SUPPORT_GROUPS`，保留在内存中
2. **消息 hash 计算**：help 消息的内容（包括群号）被序列化后计算 SHA-256 作为缓存 key
3. **缓存命中**：如果内存中的群号没变，生成的消息 hash 也不会变，数据库会命中旧缓存

### 为什么不删除整个数据库？

- 数据库中有 **8000+ 条有效缓存**，涉及其他插件
- 删除所有缓存会导致大量消息需要重新发送，增加 QQ 风控风险
- 精确清理只影响 help 插件，其他插件继续复用缓存

### 为什么不用 `content_hash` 而是 `source_kind`？

- `content_hash` 在代码改动前后会不同，无法精确定位旧缓存
- `source_kind` 是插件标签，稳定且易于过滤
- 清理 help 相关的**所有缓存**（包括新旧 hash）更安全

## 扩展用法

### 清理其他插件的缓存

修改脚本中的过滤条件：

```python
# 清理 wordbank 插件的缓存
delete(MessageAsset).where(MessageAsset.source_kind == "wordbank")

# 清理多个插件
delete(MessageAsset).where(MessageAsset.source_kind.in_(["help", "wordbank"]))
```

### 只清理特定时间之前的缓存

```python
# 清理 2026-08-01 之前的 help 缓存
cutoff_time = int(datetime(2026, 8, 1).timestamp())
delete(MessageAsset).where(
    MessageAsset.source_kind == "help",
    MessageAsset.created_at < cutoff_time
)
```
