# 公共数据与跨插件访问

以下路径均相对于仓库根目录。先看数据所有者和现有领域接口，再看底层表。

## 公共数据库

| 归属 | 存储 | 接入位置 |
| --- | --- | --- |
| 用户、群组、成员、邀请、黑名单、开关/i18n 配置 | core_db / StateStore | `src/repositories/` 和 `src/services/` 中对应公开接口 |
| 审计和插件使用等事件 | log_db / EventStore + AliasStore | 现有审计服务、trace/writer 链路 |
| 名称、名片等历史快照 | snapshot_db / EventStore + AliasStore | 对应 repository 的名称/成员更新流程 |
| 消息与转发复用资产 | message_asset_db | 仅由消息投递基础设施管理，参见消息投递 skill |

实例来源：[src/database/instances.py](../../../../src/database/instances.py)；系统初始化：[src/services/db.py](../../../../src/services/db.py)。

公共 repository 对象从 `src.repositories` 导入，例如：

```python
from src.repositories import user_repo

user = await user_repo.get_user(user_id)
```

这里的 `user_id` 使用项目已有的字符串 ID。先核对返回值可空和缓存回填行为；读取不到数据时按业务降级，不在 handler 里另写一条 SQL“补救”。

现有 repository 可能返回 CacheItem，消费方只读使用；修改经过 repository 的保存/领域操作，使 cache、diff、writer 和审计保持一致。新公共接口优先返回 DTO 或标量，不泄漏 session 和可变内部缓存。

## 跨插件访问

1. 识别所有者：业务属于 wordbank，就消费 wordbank 能力，而不是在 study 或其他插件重复建词库表。
2. 优先使用所有者明确导出的 `api.py` 或服务门面。当前实例是 [wordbank/api.py](../../../../src/plugins/wordbank/api.py)，[study](../../../../src/plugins/study/__init__.py)通过它消费词库服务。
3. 没有公开接口时，先在计划中提出由所有者增加窄接口，再让消费方调用。不要把内部 tables/ops/writers 暴露成便捷捷径。
4. 声明依赖、加载顺序和幂等初始化要求。Python 能 import 某模块不代表对应插件已经启用或数据库已经初始化；不得仅靠“先导入一下”推断可用。
5. 区分纯类型/数据接口与会触发 NoneBot 注册、调度器或连接初始化的入口。只读调查不启动机器人；离线脚本避免依赖插件入口副作用。
6. 跨插件修改状态时，让所有者执行权限、幂等、缓存失效和审计。消费方不得绕过这些规则直接改对方的表或缓存。
7. 返回契约说明缺失、未初始化、不可用及失败语义；对用户显示的错误交给 i18n，内部异常细节记录日志。

需要跨多个插件共享的稳定能力，可以在审查通过后抽到 `src/services/` 或 `src/repositories/`，避免形成循环依赖。不能仅为了避开一次 import 就复制业务数据或流程。

## 写入与事务

`WritePolicy` 定义于 `src/database/consts.py`。已有 repository 支持 buffered/immediate 时优先复用其接口；不要给接口传入它实际不支持的参数。

- 高频事件默认走已注册的 buffered writer、聚合或批写链路；说明成功响应代表“已入队”还是“已持久化”。
- 管理操作需要强一致时使用所有者的 immediate 路径，确认缓存、落库和审计同步完成。
- `read_session()` 用于查询；`write_session()` 的上下文负责提交/回滚。不要把若干独立 session 的顺序执行描述成一个事务。
- core/log/snapshot 的原子联动复用 [src/lib/db/atomic.py](../../../../src/lib/db/atomic.py) 和现有系统服务，先核对 attached schema、journal mode 与路由要求。
- 独立插件 namespace 不会自动加入上述系统事务。新跨库联动需单独设计可用的事务或补偿方案，并经用户审查。

## 验证重点

按本次实际行为选择隔离测试：缓存命中与回填、更新后缓存一致性、权限拒绝、重复操作、依赖不可用、writer 失败/排空，以及跨库成功与回滚。测试不能连接真实业务库或触发远端备份。

新增/调整公开接口、依赖或数据归属后，更新插件总表相应项。
