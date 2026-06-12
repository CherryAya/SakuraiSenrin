# 数据库使用规范

本规范适用于 SakuraiSenrin 当前的 sharedDB v2 体系。

## 1. 选型

- `StateStore`：少量状态、配置、索引、映射表。
- `EventStore`：按时间分片的事件/日志/明细记录。
- `CounterStore`：高写低读的计数类数据，优先做小时/天级聚合。

## 2. 写入原则

- 热路径优先走仓储层，不在 handler 里直接写 SQL。
- 高频写入使用批量/缓冲写入，避免每条消息都同步落库。
- 写入前先判断数据是否能聚合，能聚合就不要存明细。
- 默认写入策略用 `WritePolicy.BUFFERED`。
- 只有管理员操作、修复操作、强一致更新才用 `WritePolicy.IMMEDIATE`。

## 3. 查询原则

- 日常查询优先查聚合表、计数表、索引表。
- 只有确实需要历史明细时，才按时间分片 hydrate 冷库。
- 长时间范围查询要显式限窗，避免一次扫太多分片。

## 4. 分片与冷热分层

- 活跃数据保留在 `.db`。
- 冷数据归档为 `.db.zst`。
- `manifest` 记录分片状态，不能依赖目录扫描作为唯一真相。
- 对于几乎不回看的数据，优先让它进入冷归档。

## 5. 备份与恢复

- 所有数据库实例都应实现 `iter_backup_sources()`。
- 备份服务会收集热库、冷归档和 manifest。
- 恢复时先恢复文件，再按需要 hydrate 或重建索引。

## 6. 推荐约束

- 插件层不要直接跨层访问表。
- `init_all_tables()` 应作为初始化入口。
- 新功能优先设计成“可聚合、可归档、可按时间分片”。
- 移动端/低资源环境优先使用 SQLite + sharedDB v2，不优先引入重型数据库。

## 7. 简单判断

- 配置/映射表 -> `StateStore`
- 日志/事件流 -> `EventStore`
- 计数/统计 -> `CounterStore`

## 8. 示例

```python
from src.lib.db.connectors import CounterStore, EventStore, StateStore

core_db = StateStore(namespace="core_db", filename="core.db")
log_db = EventStore(namespace="log_db", prefix="log", fmt="%Y%m")
water_db = CounterStore(namespace="water_db", prefix="logs", fmt="%Y_%m")
```
