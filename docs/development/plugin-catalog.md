# 插件总表与新插件组成要求

核对日期：2026-10-18。此表由当前源码、元数据、文档及测试路径整理，用于开发导航；不等同于线上启用状态、完整合规审计或测试通过报告。

## 1. 如何使用和维护

- 启动入口 `bot.py` 加载 `src/hooks/` 和 `src/plugins/`；admin、notice 另加载各自子插件。
- 当前列出 14 个顶层插件包、8 个 admin/notice 子插件和 3 个 hook 模块。water 的 `__init__.py` 转发 entry_plugin，二者属于同一插件。
- “命令”列为实际注册名，前缀由 NoneBot 配置决定。文档 aliases 用于帮助查询，不能据此断言有同名 matcher。
- “权限”列明确区分元数据声明和业务门禁；元数据不是运行时鉴权证明。所有业务改动仍需核对 matcher/service。
- i18n 列表示已观察到的键与元数据情况，不宣称每一条历史文案均已迁移。
- 新增、删除或改变插件入口、命令、公开接口、数据归属、文档/测试位置时，更新对应行；字段未知时写明待核对，不臆测。
- 组件规范见下节，数据库和消息细则分别只维护在两项项目 skill 中。
- 运行配置统一由 `src/config.py::GlobalConfig` 解析；remove、water report、admin member_sync 和 startup_sync 不再转导 `config`，调用方直接使用 `src.config.config`。

## 2. 新插件应包含什么

| 组成 | 要求 | 放置方式 |
| --- | --- | --- |
| 可加载入口 | matcher/事件注册、元数据、薄编排；系统插件说明 no_check 用途 | 包入口或单文件；普通组织包不重复声明插件 |
| 元数据 | create_plugin_metadata；author/version/trigger/permission/docs；展示名称和描述带 extra.i18n 的 name_key/description_key | 入口声明，复用 helper |
| 帮助接口 | 可发现的 docs 来源、全局唯一 slug、明确 parent_slug/权限/可见性 | 优先 create_docs_meta；多文件命令插件有 docs/README.MD |
| i18n | 用户文案完整模板与参数；键类型生成；语言/占位符校验 | src/locales；输出接入消息 skill |
| 命令/交互 | 参数解析、实际权限校验、非法输入、会话结束/取消 | 简单逻辑可留单文件；复杂交互放 handlers |
| 业务规则 | 权限之外的业务约束、幂等、外部依赖降级、流程编排 | 按复杂度放 services，避免透传空壳 |
| 数据访问 | 先复用公共/所有者 API；确需独立存储才建 namespace | database 中 instances/tables/repo，其他模块按需 |
| 公开接口 | 被其他插件依赖时声明稳定门面与初始化契约 | 优先 api.py 或明确公开 service/repository |
| 消息输出 | 本地化后进入计划/安全 blocks，保留上下文 | 复用 message_plan 入口，不复制底层链路 |
| 生命周期 | 初始化、调度幂等、失败日志；有 writer 时管理排空与关闭 | 适用时提供，不机械创建无用生命周期文件 |
| 测试 | 覆盖实际受影响行为与边界，隔离 I/O/加载副作用 | matcher 用 NoneBug；service/repo 可普通 pytest |
| 总表 | 用途、入口、命令、权限、数据、i18n、文档/测试、缺项 | 同步维护本文件 |

当前 `create_docs_meta` / `normalize_docs_meta` 使用 readme_path；单文件的 docstring 不会仅凭存在就自动成为 help 来源。需要内嵌来源时，先在计划中核对引擎支持，不编造尚未实现的 schema。

管理文档的 docs.permission 应与实际权限相符。`create_docs_meta` 当前默认 NORMAL，管理员节点需显式处理，不能假设 helper 已从插件 extra.permission 自动继承。完整元数据契约见 `src/lib/plugin_docs/meta.py`。

## 3. 顶层插件：入口、触发与数据

根目录均为 `src/plugins/`。表中“无独立库”不包含全体插件经过的公共运行时检查。

| 插件 | 用途与入口 | 实际触发 / 元数据权限 | 业务数据与主要依赖 |
| --- | --- | --- | --- |
| admin | 管理总览；[入口](../../src/plugins/admin/__init__.py)加载子插件 | 总览无 matcher；子命令见下一节；SUPERUSER | 公共 user/group/member/invite/blacklist/i18n 等接口；备份注册服务；无独立 namespace |
| community_invite | 邀请须知文档；[入口](../../src/plugins/community_invite/__init__.py) | 本模块无 matcher；帮助别名含 invite；NORMAL | 文档引擎；无独立库 |
| community_miaomiao | 社区小工具目录文档；[入口](../../src/plugins/community_miaomiao/__init__.py) | 本模块无 matcher；NORMAL；缺 trigger | 文档引擎；本仓入口不提供所列社区工具实现；无独立库 |
| community_mima | 反馈群密码说明；[入口](../../src/plugins/community_mima/__init__.py) | 本模块无 matcher；帮助别名含“密码”；NORMAL | 文档引擎；无独立库 |
| help | 帮助发现、查询与渲染；[入口](../../src/plugins/help/__init__.py) | help / 帮助；NORMAL | plugin_docs 元数据/渲染/权限；统一投递资产基础设施；无业务独立库 |
| notice | 通知模块总览；[入口](../../src/plugins/notice/__init__.py)加载子插件 | 总览无 matcher；PASSIVE / SUPERUSER | 子模块处理公共 core/log/snapshot 和 Bot API；无独立 namespace |
| picsearch | 图片搜索与交互；[入口](../../src/plugins/picsearch/__init__.py) | 正则“搜图 [引擎]”，可进入选图会话；NORMAL | PicImageSearch/HTTP、图片与冷却；无独立库 |
| remove | 群内退出流程；[入口](../../src/plugins/remove/__init__.py) | remove / 退群；GROUP_ADMIN，no_check；业务校验群管理或邀请人 | group_repo / invite_repo、群信息/API；无独立库 |
| self_unban | 自助解封与配额；[入口](../../src/plugins/self_unban/__init__.py) | self.unban / 自助解封；NORMAL，no_check | 公共封禁/授权能力；私有 self_unban_db |
| sentry | 异常上报和管理员通知；[入口](../../src/plugins/sentry/__init__.py) | 被动异常处理；SUPERUSER | Sentry SDK、异常上下文、统一通知；无独立库 |
| study | 传统词库录入引导；[入口](../../src/plugins/study/__init__.py) | study / 学习及撤回 notice；NORMAL | 消费 wordbank.api；读写由 wordbank 所有者负责，不另建库 |
| test | 内部测试占位 matcher；[入口](../../src/plugins/test/__init__.py) | on_message，当前 handler 为 pass；SUPERUSER，默认帮助不可见 | 无业务处理、无独立库 |
| water | 消息计数、排行、画像、成就、赛季/日报；[包入口](../../src/plugins/water/__init__.py)转发[entry_plugin](../../src/plugins/water/entry_plugin.py) | 水王/排行别名、我有多水、我的水王成就、water、water.merge，以及 message/notice；NORMAL，管理命令 SUPERUSER | water_db 三个 store；公共用户/群组数据、apscheduler、子进程任务 |
| wordbank | 词条录入、搜索、审核、排名和被动回复；[入口](../../src/plugins/wordbank/__init__.py) | wordbank 及 add/search/pending/rank/approve/reject/delete/restore，回复路由及 message/notice；NORMAL，操作角色由业务校验 | wordbank_db 四个 store、媒体资源和缓存；对外门面 api.py |

## 4. 顶层插件：组件、i18n、文档和测试

以下目录只记录实际存在的组成。全体文档节点还由 `tests/test_plugin_docs*.py` 等公共测试覆盖；存在测试文件不表示已运行或足以证明所有行为。

| 插件 | 实际组织 / 公开入口 | 已观察 i18n | 文档 | 对应测试 |
| --- | --- | --- | --- | --- |
| admin | 入口 + 5 个命令模块 + _services；共用服务在 src/services | admin.*、plugin.admin_*；各入口有 extra.i18n | [总览](../../src/plugins/admin/docs/README.MD)，各子模块见下表 | [tests/plugins/admin](../../tests/plugins/admin/)、[i18n](../../tests/test_i18n_runtime.py)、公共备份测试 |
| community_invite | 元数据 + docs，未注册独立命令 handler | 名称/描述硬编码；未声明 extra.i18n | [README](../../src/plugins/community_invite/docs/README.MD) | 无专属目录；公共文档测试 |
| community_miaomiao | 元数据 + docs，未实现目录所述工具 | 名称/描述硬编码；未声明 extra.i18n | [README](../../src/plugins/community_miaomiao/docs/README.MD) | 无专属目录；公共文档测试 |
| community_mima | 元数据 + docs，未注册独立命令 handler | 名称/描述硬编码；未声明 extra.i18n | [README](../../src/plugins/community_mima/docs/README.MD) | 无专属目录；公共文档测试 |
| help | 单入口 + docs；渲染/注册在 src/lib/plugin_docs | help.*、docs.*、plugin.help；有 extra.i18n | [README](../../src/plugins/help/docs/README.MD) | [test_help_plugin.py](../../tests/test_help_plugin.py)、公共文档测试 |
| notice | 总览 + group/user/invite 子模块 | notice.*、plugin.notice*；各节点有 extra.i18n | [总览](../../src/plugins/notice/docs/README.MD)及子文档 | [tests/plugins/notice](../../tests/plugins/notice/) |
| picsearch | 单入口 + docs，解析/搜索/展示仍在入口模块 | picsearch.*、plugin.picsearch；name/description 用 tr，但缺 extra.i18n | [README](../../src/plugins/picsearch/docs/README.MD) | [test_plugin.py](../../tests/plugins/picsearch/test_plugin.py) |
| remove | 单入口 + docs，包含权限和确认交互 | remove.*、plugin.remove；有 extra.i18n | [README](../../src/plugins/remove/docs/README.MD) | [test_plugin.py](../../tests/plugins/remove/test_plugin.py) |
| self_unban | handlers + services + database + docs | self_unban.*、interaction.*、plugin.self_unban；有 extra.i18n | [README](../../src/plugins/self_unban/docs/README.MD) | [tests/plugins/self_unban](../../tests/plugins/self_unban/) |
| sentry | 单入口 + docs，SDK 回调与通知 | sentry.*、plugin.sentry；有 extra.i18n | [README](../../src/plugins/sentry/docs/README.MD) | [test_sentry_plugin.py](../../tests/test_sentry_plugin.py) |
| study | 单入口 + docs，依赖 [wordbank.api](../../src/plugins/wordbank/api.py) | 复用 wordbank.guided.* 等、interaction.*、plugin.study；有 extra.i18n | [README](../../src/plugins/study/docs/README.MD) | [tests/plugins/study](../../tests/plugins/study/) |
| test | 单入口 + docs；当前空处理器 | plugin.test；有 extra.i18n，无业务输出 | [README](../../src/plugins/test/docs/README.MD) | 无专属行为测试；公共文档测试 |
| water | entry_plugin + handlers/services/database/renderers；scheduler、migration、worker 等 | water.*、interaction.*、plugin.water；有 extra.i18n | [README](../../src/plugins/water/docs/README.MD)、[dev](../../src/plugins/water/docs/dev.md) | [tests/plugins/water](../../tests/plugins/water/) |
| wordbank | entry_commands/runtime + handlers/services/database/treemap；[api.py](../../src/plugins/wordbank/api.py)为公开门面 | wordbank.*、interaction.*、少量 water.*、plugin.wordbank；有 extra.i18n | [README](../../src/plugins/wordbank/docs/README.MD) | [tests/plugins/wordbank](../../tests/plugins/wordbank/) |

## 5. admin 与 notice 子插件

| 模块 / docs slug | 命令或事件 / 角色 | 数据与职责 | 文档 / 测试 |
| --- | --- | --- | --- |
| admin.group | admin group / 群组管理；SUPERUSER | 公共群组授权、封禁、退群/状态操作 | [文档](../../src/plugins/admin/docs/group/README.MD)；admin 公共入口测试 |
| admin.user | admin user / 用户管理；SUPERUSER | 公共用户状态、封禁/解封、权限管理 | [文档](../../src/plugins/admin/docs/user/README.MD)；admin 公共入口/时长测试 |
| admin.invite | admin invite / 邀请管理，以及审批/拒绝回复路由；SUPERUSER，no_check | 公共邀请与群组数据、邀请审批 | [文档](../../src/plugins/admin/docs/invite/README.MD)；admin 入口与 notice 邀请测试 |
| admin.i18n | admin i18n / 语言管理；SUPERUSER | i18n_repo：全局默认和群组覆盖 | [文档](../../src/plugins/admin/docs/i18n/README.MD)；admin 入口和 test_i18n_runtime |
| admin.backup | admin backup / 备份管理；SUPERUSER | 全部已注册 store 的备份、健康检查、恢复服务；统一读取当前配置的单一仓库，restore 仅接受快照参数；非私有业务库 | [文档](../../src/plugins/admin/docs/backup/README.MD)；[test_backup.py](../../tests/plugins/admin/test_backup.py)及公共备份测试 |
| notice.group | 入群/离群/禁言/群名事件；PASSIVE，元数据 SUPERUSER，no_check | 公共群组/成员/快照同步与通知 | [文档](../../src/plugins/notice/docs/group/README.MD)；[test_group.py](../../tests/plugins/notice/test_group.py) |
| notice.user | 请求事件处理；PASSIVE，元数据 SUPERUSER，no_check | 好友请求相关 API 与通知 | [文档](../../src/plugins/notice/docs/user/README.MD)；未定位专属 test_user |
| notice.invite | 群邀请 request / 机器人入群 notice；PASSIVE，元数据 SUPERUSER，no_check | 邀请记录、审批状态与群组处理 | [文档](../../src/plugins/notice/docs/invite/README.MD)；[test_invite.py](../../tests/plugins/notice/test_invite.py) |

子模块源码分别为 `src/plugins/admin/<模块名>.py` 和 `src/plugins/notice/<模块名>.py`。所有列出的子节点都有自己的元数据、文档来源及对应 plugin.* i18n 键；父节点分别为 admin 和 notice。

## 6. hook 模块

| 模块 | 实际职责 / 元数据 | 文档与测试 | 数据与 i18n |
| --- | --- | --- | --- |
| [plugin.py](../../src/hooks/plugin.py) | 当前为插件扩展点总览元数据，无处理器实现；PASSIVE / SUPERUSER，slug hook.plugin | [文档](../../src/hooks/docs/plugin/README.MD)；公共文档测试 | plugin.hook_plugin；有 extra.i18n |
| [processor.py](../../src/hooks/processor.py) | 事件上下文、同步、忽略/超管/黑名单/授权/禁言检查与清理；PASSIVE / SUPERUSER，slug hook.processor | [文档](../../src/hooks/docs/processor/README.MD)；[tests/hooks/test_processor.py](../../tests/hooks/test_processor.py) | 公共用户/群组/成员与审计；plugin.hook_processor 及对应运行时键 |
| [startup_sync.py](../../src/hooks/startup_sync.py) | 超管启动备份恢复确认的回复路由；matcher 显式 SUPERUSER；缺插件元数据 | 无专属 docs；[test_startup_sync.py](../../tests/test_startup_sync.py) | startup_sync 服务拥有当前配置仓库的恢复与文案，不按环境选择 profile；此模块只路由/投递 |

## 7. 私有 namespace 与公开访问

| 所有者 | namespace / store | 初始化与公开边界 |
| --- | --- | --- |
| self_unban | self_unban_db：StateStore(self_unban.db) | service.ensure_initialized 幂等初始化；业务通过 self_unban 服务/仓储 |
| water | water_db：StateStore(core.db)、CounterStore(logs)、CounterStore(summary)，分片 store 由 AliasStore 包装 | entry_plugin 启动初始化及 runtime reload；插件内复用 water_repo，跨插件需先明确公开门面 |
| wordbank | wordbank_db：StateStore(wordbank_main.db)、StateStore(wordbank_message_route.db)、EventStore(wordbank_logs)、EventStore(wordbank_message_ref)，事件 store 由 AliasStore 包装 | bootstrap/lifecycle 与服务初始化；跨插件经 wordbank.api，study 是消费方 |

这些 namespace 都在 `database/instances.py` 注册备份。系统 core_db/log_db/snapshot_db 和 message_asset_db 不属于某个业务插件的私有库；详细接入读[数据库 skill](../../.agents/skills/sakuraisenrin-database/SKILL.md)。

## 8. 当前已确认的待完善项

这些是只读核对结果，尚未在本次规则重写中迁移业务代码：

1. 三个 community 插件的名称/描述为裸文案，缺 extra.i18n；community_miaomiao 还缺 trigger。应先审查这些节点的定位，再补本地化和元数据。
2. community 邀请/密码 README 描述了快捷入口，但本模块没有注册 matcher。不能将帮助 aliases 当作本仓已实现的直接命令；需要核对预期路由或外部社区实现。
3. picsearch 的名称/描述虽用 tr，metadata 没有 extra.i18n，需核对动态帮助语言呈现。
4. startup_sync hook 缺 __plugin_meta__/extra.docs；需确认它继续作为可加载模块，还是并入已声明插件后补齐契约。
5. 部分模块只有公共文档测试，没有专属行为测试；没有业务行为的占位节点不需凑测试，有实际行为的模块按风险补覆盖。
6. 新规则要求产品文案全量 i18n，本表只标记已发现问题，尚未逐条审计 renderer、README 正文和所有历史输出；迁移范围需另行计划。
