"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 14:10:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:33:19
Description: log db 常量
"""

from types import MappingProxyType

from .base import BaseAuditEnum


class OneBotV11Event(BaseAuditEnum):
    EVENT = "Event"
    MESSAGE_EVENT = "MessageEvent"
    PRIVATE_MESSAGE_EVENT = "PrivateMessageEvent"
    GROUP_MESSAGE_EVENT = "GroupMessageEvent"
    NOTICE_EVENT = "NoticeEvent"
    GROUP_UPLOAD_NOTICE_EVENT = "GroupUploadNoticeEvent"
    GROUP_ADMIN_NOTICE_EVENT = "GroupAdminNoticeEvent"
    GROUP_DECREASE_NOTICE_EVENT = "GroupDecreaseNoticeEvent"
    GROUP_INCREASE_NOTICE_EVENT = "GroupIncreaseNoticeEvent"
    GROUP_BAN_NOTICE_EVENT = "GroupBanNoticeEvent"
    FRIEND_ADD_NOTICE_EVENT = "FriendAddNoticeEvent"
    GROUP_RECALL_NOTICE_EVENT = "GroupRecallNoticeEvent"
    FRIEND_RECALL_NOTICE_EVENT = "FriendRecallNoticeEvent"
    NOTIFY_EVENT = "NotifyEvent"
    POKE_NOTIFY_EVENT = "PokeNotifyEvent"
    LUCKY_KING_NOTIFY_EVENT = "LuckyKingNotifyEvent"
    HONOR_NOTIFY_EVENT = "HonorNotifyEvent"
    REQUEST_EVENT = "RequestEvent"
    FRIEND_REQUEST_EVENT = "FriendRequestEvent"
    GROUP_REQUEST_EVENT = "GroupRequestEvent"
    META_EVENT = "MetaEvent"
    LIFECYCLE_META_EVENT = "LifecycleMetaEvent"
    HEARTBEAT_META_EVENT = "HeartbeatMetaEvent"

    __labels__ = MappingProxyType(
        {
            EVENT: "事件",
            MESSAGE_EVENT: "消息事件",
            PRIVATE_MESSAGE_EVENT: "私聊消息事件",
            GROUP_MESSAGE_EVENT: "群聊消息事件",
            NOTICE_EVENT: "通知事件",
            GROUP_UPLOAD_NOTICE_EVENT: "群文件上传通知事件",
            GROUP_ADMIN_NOTICE_EVENT: "群管理员变动通知事件",
            GROUP_DECREASE_NOTICE_EVENT: "群成员减少通知事件",
            GROUP_INCREASE_NOTICE_EVENT: "群成员增加通知事件",
            GROUP_BAN_NOTICE_EVENT: "群禁言通知事件",
            FRIEND_ADD_NOTICE_EVENT: "好友添加通知事件",
            GROUP_RECALL_NOTICE_EVENT: "群消息撤回通知事件",
            FRIEND_RECALL_NOTICE_EVENT: "好友消息撤回通知事件",
            NOTIFY_EVENT: "提醒事件",
            POKE_NOTIFY_EVENT: "戳一戳提醒事件",
            LUCKY_KING_NOTIFY_EVENT: "群红包提醒事件",
            HONOR_NOTIFY_EVENT: "群荣誉变更提醒事件",
            REQUEST_EVENT: "加群请求事件",
            FRIEND_REQUEST_EVENT: "好友请求事件",
            GROUP_REQUEST_EVENT: "群请求事件",
            META_EVENT: "元事件",
            LIFECYCLE_META_EVENT: "生命周期元事件",
            HEARTBEAT_META_EVENT: "心跳元事件",
        },
    )


class AuditContext(BaseAuditEnum):
    GLOBAL = "GLOBAL"
    GROUP = "GROUP"
    USER = "USER"
    GUILD = "GUILD"
    SYSTEM = "SYSTEM"

    __labels__ = MappingProxyType(
        {
            GLOBAL: "全局",
            GROUP: "群组",
            USER: " 用户",
            GUILD: "频道/公会",
            SYSTEM: "系统自身",
        },
    )


class AuditCategory(BaseAuditEnum):
    """
    审计日志分类 - 资源维度
    """

    ACCESS = "ACCESS"
    PERMISSION = "PERMISSION"
    PLUGIN = "PLUGIN"
    SYSTEM = "SYSTEM"
    TASK = "TASK"
    FILE = "FILE"

    __labels__ = MappingProxyType(
        {
            ACCESS: "黑名单/风控",
            PERMISSION: "权限管理",
            PLUGIN: "插件模块",
            SYSTEM: "系统核心",
            TASK: "定时任务",
            FILE: "文件操作",
        },
    )


class AuditAction(BaseAuditEnum):
    """
    审计日志动作 - 操作维度
    """

    # === 基础 CRUD ===
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"

    # === 交互/状态 ===
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    CONNECT = "CONNECT"

    # === 群组管理 ===
    KICK = "KICK"
    BAN = "BAN"
    UNBAN = "UNBAN"
    MUTE = "MUTE"
    UNMUTE = "UNMUTE"

    # === 审批流程 ===
    APPROVE = "APPROVE"
    REJECT = "REJECT"

    # === 功能控制 ===
    ENABLE = "ENABLE"
    DISABLE = "DISABLE"
    TRIGGER = "TRIGGER"
    RELOAD = "RELOAD"

    # === 权限控制 ===
    CHANGE = "CHANGE"
    GRANT = "GRANT"
    REVOKE = "REVOKE"

    __labels__ = MappingProxyType(
        {
            # 基础
            CREATE: "创建/新增",
            UPDATE: "更新/修改",
            DELETE: "删除/移除",
            # 状态
            LOGIN: "登录",
            LOGOUT: "登出",
            CONNECT: "建立连接",
            # 管控
            KICK: "踢出成员",
            BAN: "封禁/拉黑",
            UNBAN: "解封/白名单",
            MUTE: "禁言",
            UNMUTE: "解除禁言",
            # 审批
            APPROVE: "审批通过",
            REJECT: "审批拒绝",
            # 功能
            ENABLE: "启用功能",
            DISABLE: "禁用功能",
            TRIGGER: "触发执行",
            RELOAD: "重载配置",
            # 权限
            CHANGE: "权限变更",
            GRANT: "授予权限",
            REVOKE: "撤销权限",
        },
    )
