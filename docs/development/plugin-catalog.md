# 插件总表

核对日期：2026-10-18；列出 14 个顶层插件、8 个子插件和 3 个 hook，water 包入口与 entry_plugin 视为同一插件。

新插件组成和协作规则见 [AGENTS.md](../../AGENTS.md)；命令列不含配置前缀，帮助 aliases 不代表 matcher，元数据权限不能替代实际鉴权。

## 顶层插件：入口、触发与数据

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

## 顶层插件：组件、i18n、文档和测试

公共文档测试覆盖所有节点；专属测试见表。

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
| water | entry_plugin + handlers/services/database/renderers；scheduler、worker 等 | water.*、interaction.*、plugin.water；有 extra.i18n | [README](../../src/plugins/water/docs/README.MD)、[dev](../../src/plugins/water/docs/dev.md) | [tests/plugins/water](../../tests/plugins/water/) |
| wordbank | entry_commands/runtime + handlers/services/database/treemap；[api.py](../../src/plugins/wordbank/api.py)为公开门面 | wordbank.*、interaction.*、少量 water.*、plugin.wordbank；有 extra.i18n | [README](../../src/plugins/wordbank/docs/README.MD) | [tests/plugins/wordbank](../../tests/plugins/wordbank/) |

## admin 与 notice 子插件

| 模块 / docs slug | 命令或事件 / 角色 | 数据与职责 | 文档 / 测试 |
| --- | --- | --- | --- |
| admin.group | admin group / 群组管理；SUPERUSER | 公共群组授权、封禁、退群/状态操作 | [文档](../../src/plugins/admin/docs/group/README.MD)；admin 公共入口测试 |
| admin.user | admin user / 用户管理；SUPERUSER | 公共用户状态、封禁/解封、权限管理 | [文档](../../src/plugins/admin/docs/user/README.MD)；admin 公共入口/时长测试 |
| admin.invite | admin invite / 邀请管理，以及审批/拒绝回复路由；SUPERUSER，no_check | 经 invitation 共享服务确认入群状态，处理有效机器人邀请或本地授权/退群；公共 invite_repo / group_repo | [文档](../../src/plugins/admin/docs/invite/README.MD)；[审批回归](../../tests/plugins/admin/test_invite_review.py)、admin 入口与 notice 邀请测试 |
| admin.i18n | admin i18n / 语言管理；SUPERUSER | i18n_repo：全局默认和群组覆盖 | [文档](../../src/plugins/admin/docs/i18n/README.MD)；admin 入口和 test_i18n_runtime |
| admin.backup | admin backup / 备份管理；SUPERUSER | 全部已注册 store 的备份、健康检查、恢复服务；统一读取当前配置的单一仓库，restore 仅接受快照参数；非私有业务库 | [文档](../../src/plugins/admin/docs/backup/README.MD)；[test_backup.py](../../tests/plugins/admin/test_backup.py)及公共备份测试 |
| notice.group | 入群/离群/禁言/群名事件；PASSIVE，元数据 SUPERUSER，no_check | 公共群组/成员/快照同步与通知 | [文档](../../src/plugins/notice/docs/group/README.MD)；[test_group.py](../../tests/plugins/notice/test_group.py) |
| notice.user | 好友请求事件处理；PASSIVE，元数据 SUPERUSER，no_check | 有效好友请求自动同意；非法 ID/空白 flag 拦截；接口失败上报，资料失败降级 | [文档](../../src/plugins/notice/docs/user/README.MD)；[请求回归](../../tests/plugins/notice/test_user_requests.py) |
| notice.invite | 明确邀请机器人本人的群 request / 机器人本人入群 notice；PASSIVE，元数据 SUPERUSER，no_check | 经 invitation 共享服务合并邀请与入群待办、审批状态与群组处理；邀请对象缺失/无效/非本人只记诊断，不审批 | [文档](../../src/plugins/notice/docs/invite/README.MD)；[test_invite.py](../../tests/plugins/notice/test_invite.py) |

子模块位于 `src/plugins/admin/` 和 `src/plugins/notice/`，各自声明元数据、文档和 plugin.* i18n 键；父节点分别为 admin 和 notice。

邀请流程的共享编排由 [src/services/invitation.py](../../src/services/invitation.py) 拥有，持久化通过公共 [invite_repo](../../src/repositories/invite.py) 接口完成，不新建 namespace 或修改表结构。请求阶段使用既有 `sub_type=bot_invite`，入群待授权阶段使用 `sub_type=joined`；同一邀请的 request / notice（任意顺序）以及同 flag 重放合并为同一待办，保留已建立的通知回复关联和原始邀请者身份，避免改变 remove 的邀请人权限判断。

好友请求、群邀请、管理员审批和邀请持久化统一通过 [request_flags](../../src/lib/request_flags.py) 校验非空白 flag，合法标识不作裁剪。无 flag 的机器人入群 notice 仍是有效的本地授权事件。已知平台拒绝与网络故障按实时成员状态降级，不把未确认的操作记为成功。

邀请通知的名称优先使用事件扩展字段，群名还会复用本次实时群列表结果，缺失时依次回退到本地资料和 ID，不额外请求名称 API。有效名称通过已有 repository 更新缓存与保存，通知保留昵称及 ID；合并事件仍显示原邀请者，不将机器人或后续操作者名称错配给原邀请者。

只有 `invited_id` 明确等于机器人 ID 的请求进入审批；缺失、非法或其他对象的请求记录诊断后忽略。执行审批/授权前通过 `get_group_list` 实时确认机器人是否在群：已入群时同意只做本地群授权、拒绝则退群；尚未入群时，仅处理可确认对象的有效机器人邀请。不能确认状态、失效邀请和不可信旧记录均不标记成功。已授权群自动同意仅适用于确证邀请机器人本人且机器人尚未在群的请求。

## hook 模块

| 模块 | 实际职责 / 元数据 | 文档与测试 | 数据与 i18n |
| --- | --- | --- | --- |
| [plugin.py](../../src/hooks/plugin.py) | 当前为插件扩展点总览元数据，无处理器实现；PASSIVE / SUPERUSER，slug hook.plugin | [文档](../../src/hooks/docs/plugin/README.MD)；公共文档测试 | plugin.hook_plugin；有 extra.i18n |
| [processor.py](../../src/hooks/processor.py) | 事件上下文、同步、忽略/超管/黑名单/授权/禁言检查与清理；PASSIVE / SUPERUSER，slug hook.processor | [文档](../../src/hooks/docs/processor/README.MD)；[tests/hooks/test_processor.py](../../tests/hooks/test_processor.py) | 公共用户/群组/成员与审计；plugin.hook_processor 及对应运行时键 |
| [startup_sync.py](../../src/hooks/startup_sync.py) | 超管启动备份恢复确认的回复路由；matcher 显式 SUPERUSER；缺插件元数据 | 无专属 docs；[test_startup_sync.py](../../tests/test_startup_sync.py) | startup_sync 服务拥有当前配置仓库的恢复与文案，不按环境选择 profile；此模块只路由/投递 |

## 私有 namespace 与公开访问

| 所有者 | namespace / store | 初始化与公开边界 |
| --- | --- | --- |
| self_unban | self_unban_db：StateStore(self_unban.db) | service.ensure_initialized 幂等初始化；业务通过 self_unban 服务/仓储 |
| water | water_db：StateStore(core.db)、CounterStore(logs)、CounterStore(summary)，分片 store 由 AliasStore 包装 | entry_plugin 启动初始化及 runtime reload；插件内复用 water_repo，跨插件需先明确公开门面 |
| wordbank | wordbank_db：StateStore(wordbank_main.db)、StateStore(wordbank_message_route.db)、EventStore(wordbank_logs)、EventStore(wordbank_message_ref)，事件 store 由 AliasStore 包装 | bootstrap/lifecycle 与服务初始化；跨插件经 wordbank.api，study 是消费方 |

这些 namespace 都在 `database/instances.py` 注册备份。系统 core_db/log_db/snapshot_db 和 message_asset_db 不属于某个业务插件的私有库；详细接入读[数据库 skill](../../.agents/skills/sakuraisenrin-database/SKILL.md)。

## 已知缺项

- 三个 community 插件的名称/描述未本地化，缺 extra.i18n；community_miaomiao 还缺 trigger。
- community 邀请/密码文档所述快捷入口未在本模块注册 matcher，需核对外部实现。
- picsearch 名称/描述使用 tr，但 metadata 缺 extra.i18n。
- startup_sync hook 缺插件元数据和文档声明。
- 有业务行为但仅有公共文档测试的模块仍需专属回归，空占位节点无需补虚构行为测试。
- i18n 记录只含已发现问题，renderer、README 正文和历史输出尚未完成全量审计。
