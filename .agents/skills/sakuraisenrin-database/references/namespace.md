# 插件独立 namespace 接入

以下路径均相对于仓库根目录。适用于确有独立持久化需求的插件；只有读取公共数据时使用 access.md 中的公开接口。

## 1. namespace 与 store 选型

namespace 使用静态、可识别的插件标识，沿用 `<plugin_name>_db` 习惯，不来自用户输入，不借用 `core_db`、`log_db`、`snapshot_db` 等系统归属。

物理根由 `src/lib/consts.py::GLOBAL_DB_ROOT` 定义，当前为 `./data/db`。store 的 base_dir 是 `GLOBAL_DB_ROOT / namespace`。同一插件多个 store 可以共享 namespace，但 filename/prefix 必须避免冲突。

| 类型 | 用途 | 当前实例 |
| --- | --- | --- |
| StateStore | 状态、配置、索引、映射 | self_unban_db、water core、wordbank main/route |
| EventStore | 按时间分片的事件、明细日志 | log/snapshot、wordbank log/ref |
| CounterStore | 计数/聚合场景的分片存储 | water logs/summary |

`EventStore` 和 `CounterStore` 都基于 SegmentStore，不能仅凭类型名认为数据自动完成业务聚合。时间字段、聚合粒度、分片键、查询限窗、冷热策略都要明确。

## 2. 最小组成

```text
src/plugins/example/database/
  instances.py     # store 声明与备份注册
  tables.py        # 插件自己的 DeclarativeBase、模型、索引
  repo.py          # 初始化与最小可测数据访问接口
  __init__.py      # 公开 repository 导出
```

`ops.py`、`writers.py`、`types.py`、`patches.py` 按复杂度添加，不创建空壳。以下 example 名称为新插件示例，应替换为实际归属。

### instances.py

```python
from src.lib.backup import register_backup_database
from src.lib.db.connectors import StateStore

example_db = StateStore(
    namespace="example_db",
    filename="core.db",
)
register_backup_database(example_db)
```

需要时间分片时复用 `EventStore` / `CounterStore`；需要绑定 ops 路由时参考 `src/lib/db/alias.py::AliasStore`。先核对构造参数和时间字段，而不是照搬另一个插件的分片格式。

实例模块只做声明、patch 注册和备份登记，不能在导入时打开业务连接、调用 Bot API 或安装调度器。备份发现会尝试导入 `src.database.instances` 和 `src.plugins/*/database/instances.py`；新增更深的目录不在当前自动发现模式内，需单独设计注册。

### tables.py 与 repo.py

为插件创建独立 DeclarativeBase，不把私有模型塞入 CoreBase。字段、唯一约束和索引体现幂等与查询需求，时间值复用项目时间工具。

```python
from sqlalchemy import select

from .instances import example_db
from .tables import ExampleBase, ExampleState

class ExampleRepository:
    @classmethod
    async def init_all_tables(cls) -> None:
        await example_db.init(ExampleBase)

    async def get_value(self, entity_id: int) -> str | None:
        async with example_db.read_session() as session:
            return await session.scalar(
                select(ExampleState.value).where(ExampleState.id == entity_id)
            )
```

示例假设模型有整数 id 和字符串 value；返回标量，避免 ORM 对象在 session 关闭后发生隐式加载。写入通过 `write_session()`，高频写入另复用 BatchWriter，而不是让 handler 每次直接提交。

对外导出 repository 或服务实例，记录公开接口。当前最小实现可参考 [self_unban/database](../../../../src/plugins/self_unban/database/)；分片/缓存/批写可参考 [water/database](../../../../src/plugins/water/database/)。

## 3. 生命周期与迁移

- 由启用插件的初始化流程或服务的幂等初始化调用 `init_all_tables()`。计划说明何时初始化、失败如何报告、是否需要 warm_up；不要在每条消息里重建表。
- store 的 `init(base)` 会建立基础表并应用 patch registry；现有库升级不能只依赖 create_all。
- schema 变更使用 `src/lib/db/schema.py` 的 `SchemaPatch` / `PatchRegistry`，在初始化前注册。
- patch_id 在对应 store 内稳定唯一，建议带插件和变更语义。已应用 patch 不改含义；新增变更使用新 patch，并验证重复初始化。
- 新旧 schema、已有行、新分片和需要回灌的冷分片按实际影响验证。迁移执行、删表、重命名数据文件和真实数据回填必须在批准计划中单独列出。
- 恢复后通过公共恢复流程重置 store 的进程内派生状态，不让旧 manifest/连接缓存继续指向已替换文件。

## 4. 备份与冷热数据

每个 store 声明后立即 `register_backup_database(...)`，不要把登记藏在 handler、service 初始化或插件启动回调里。标准 store 已提供备份源枚举能力；AliasStore 注册会解包为物理 store。

StateStore 备份源包含现存主库；分片 store 涉及热库、冷归档和 manifest。对冷数据的读取使用明确 ColdPolicy 和时间范围，不能把目录扫描当唯一索引。

开发验证优先使用临时目录、隔离 store 和注册发现测试。远端健康检查、强制备份和恢复会访问真实环境，只有计划明确授权时才执行。备份登记不等于已完成备份或恢复验证。

## 5. 交付检查

确认 namespace/filename/prefix 不冲突；初始化和重复初始化可用；读写与约束可测；迁移/失败回滚有相应证据；备份登记可独立发现；插件总表写明数据所有者及 store。按共同规则执行全局门禁，报告未通过项。
