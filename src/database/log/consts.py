"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 14:10:22
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:33:19
Description: log db 常量
"""

from typing import ClassVar

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

    __label_keys__: ClassVar[dict[str, str]] = {
        EVENT: "enum.onebot.event",
        MESSAGE_EVENT: "enum.onebot.message_event",
        PRIVATE_MESSAGE_EVENT: "enum.onebot.private_message_event",
        GROUP_MESSAGE_EVENT: "enum.onebot.group_message_event",
        NOTICE_EVENT: "enum.onebot.notice_event",
        GROUP_UPLOAD_NOTICE_EVENT: "enum.onebot.group_upload_notice_event",
        GROUP_ADMIN_NOTICE_EVENT: "enum.onebot.group_admin_notice_event",
        GROUP_DECREASE_NOTICE_EVENT: "enum.onebot.group_decrease_notice_event",
        GROUP_INCREASE_NOTICE_EVENT: "enum.onebot.group_increase_notice_event",
        GROUP_BAN_NOTICE_EVENT: "enum.onebot.group_ban_notice_event",
        FRIEND_ADD_NOTICE_EVENT: "enum.onebot.friend_add_notice_event",
        GROUP_RECALL_NOTICE_EVENT: "enum.onebot.group_recall_notice_event",
        FRIEND_RECALL_NOTICE_EVENT: "enum.onebot.friend_recall_notice_event",
        NOTIFY_EVENT: "enum.onebot.notify_event",
        POKE_NOTIFY_EVENT: "enum.onebot.poke_notify_event",
        LUCKY_KING_NOTIFY_EVENT: "enum.onebot.lucky_king_notify_event",
        HONOR_NOTIFY_EVENT: "enum.onebot.honor_notify_event",
        REQUEST_EVENT: "enum.onebot.request_event",
        FRIEND_REQUEST_EVENT: "enum.onebot.friend_request_event",
        GROUP_REQUEST_EVENT: "enum.onebot.group_request_event",
        META_EVENT: "enum.onebot.meta_event",
        LIFECYCLE_META_EVENT: "enum.onebot.lifecycle_meta_event",
        HEARTBEAT_META_EVENT: "enum.onebot.heartbeat_meta_event",
    }


class AuditContext(BaseAuditEnum):
    GLOBAL = "GLOBAL"
    GROUP = "GROUP"
    USER = "USER"
    GUILD = "GUILD"
    SYSTEM = "SYSTEM"

    __label_keys__: ClassVar[dict[str, str]] = {
        GLOBAL: "enum.audit_context.global",
        GROUP: "enum.audit_context.group",
        USER: "enum.audit_context.user",
        GUILD: "enum.audit_context.guild",
        SYSTEM: "enum.audit_context.system",
    }


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

    __label_keys__: ClassVar[dict[str, str]] = {
        ACCESS: "enum.audit_category.access",
        PERMISSION: "enum.audit_category.permission",
        PLUGIN: "enum.audit_category.plugin",
        SYSTEM: "enum.audit_category.system",
        TASK: "enum.audit_category.task",
        FILE: "enum.audit_category.file",
    }


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

    __label_keys__: ClassVar[dict[str, str]] = {
        CREATE: "enum.audit_action.create",
        UPDATE: "enum.audit_action.update",
        DELETE: "enum.audit_action.delete",
        LOGIN: "enum.audit_action.login",
        LOGOUT: "enum.audit_action.logout",
        CONNECT: "enum.audit_action.connect",
        KICK: "enum.audit_action.kick",
        BAN: "enum.audit_action.ban",
        UNBAN: "enum.audit_action.unban",
        MUTE: "enum.audit_action.mute",
        UNMUTE: "enum.audit_action.unmute",
        APPROVE: "enum.audit_action.approve",
        REJECT: "enum.audit_action.reject",
        ENABLE: "enum.audit_action.enable",
        DISABLE: "enum.audit_action.disable",
        TRIGGER: "enum.audit_action.trigger",
        RELOAD: "enum.audit_action.reload",
        CHANGE: "enum.audit_action.change",
        GRANT: "enum.audit_action.grant",
        REVOKE: "enum.audit_action.revoke",
    }
