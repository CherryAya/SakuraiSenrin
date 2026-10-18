---
name: sakuraisenrin-database
description: 在 SakuraiSenrin 中接入公共数据库、跨插件访问数据、新建插件 namespace，或修改初始化、迁移、缓存、写入与备份接入时使用。
---

# SakuraiSenrin 数据库接入

先遵守仓库 [AGENTS.md](../../../AGENTS.md) 的计划审查流程。此技能提供开发契约，不授权运行真实迁移、备份恢复或数据写入。

## 按场景读取

- 使用现有公共数据、消费其他插件数据、设计公开接口：读 [access.md](references/access.md)。
- 新建插件数据库、增加 store/表/分片、迁移或备份接入：读 [namespace.md](references/namespace.md)。
- 同时涉及两类任务时，读取两份参考；普通文案或无数据访问的改动无需加载数据库细节。

## 工作步骤

1. 从[插件总表](../../../docs/development/plugin-catalog.md)定位数据所有者，再核对当前 service、repository、instances 和初始化代码。总表是导航，代码是实现事实。
2. 先判断能否复用所有者的公开接口，再决定是否需要新表或独立 namespace。不要为读取已有数据另建一份业务副本。
3. 在待审计划中写清：数据归属、读写接口、权限/审计、缓存及写入一致性、初始化时机、迁移和备份影响，以及验证方法。
4. 用户确认后按现有模式实施。数据层负责会话与 SQL，业务层通过公开接口消费结果；返回 DTO/标量，不把可变内部缓存或已关闭 session 的 ORM 对象当公共契约。
5. 用隔离数据库验证本次涉及的读写、幂等、失败/回滚和迁移路径；按总规则执行质量门禁，更新插件总表。

## 不可忽略的边界

- `core_db`、`log_db`、`snapshot_db` 是公共系统数据；`message_asset_db` 是投递资产缓存，不是插件业务库。
- 跨插件调用不等于允许访问对方 tables/ops/writers 或连接其 SQLite 文件；使用所有者公开 API，并声明依赖与初始化要求。
- `StateStore` 存状态，`EventStore` 存时间分片明细，`CounterStore` 存计数。选型后仍需明确实际聚合模型，不能仅凭 store 名称假定自动聚合。
- 热路径优先复用已有缓存与 buffered writer；管理员强一致操作按领域接口选择 immediate。多个 session 顺序提交不代表跨库原子。
- 实例在 `database/instances.py` 中定义并注册备份，该模块应能脱离插件启动链独立导入；不得在 handler 内创建 store。
- 改 schema 使用稳定、幂等的 patch；跨库事务、删表、数据迁移和恢复必须单独说明执行范围及风险。
