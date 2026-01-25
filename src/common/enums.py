from enum import Enum, IntFlag, StrEnum, auto
from types import MappingProxyType


class LocalizedMixin:
    @classmethod
    def get_label(cls, member) -> str:
        labels = getattr(cls, "__labels__", {})
        val = member.value if isinstance(member, Enum) else member
        if val in labels:
            return labels[val]
        if isinstance(labels, Enum):
            labels = labels.value
        elif isinstance(member, IntFlag) and member.value != 0:
            decomposed_labels = []
            for m in member.__class__:
                is_atomic = (m.value & (m.value - 1)) == 0
                if m.value == 0 or not is_atomic:
                    continue
                if (val & m.value) == m.value:
                    decomposed_labels.append(cls.get_label(m))
            if decomposed_labels:
                return " | ".join(decomposed_labels)
        return str(val)

    @property
    def label(self) -> str:
        return self.get_label(self)

    def __str__(self) -> str:
        return self.label


class ExceptionStatus(LocalizedMixin, StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"

    __labels__ = MappingProxyType(
        {
            PENDING: "待处理",
            RESOLVED: "已解决",
            IGNORED: "已忽略",
        },
    )


class UserStatus(LocalizedMixin, StrEnum):
    NORMAL = "NORMAL"
    BANNED = "BANNED"

    __labels__ = MappingProxyType(
        {
            NORMAL: "正常",
            BANNED: "封禁",
        },
    )


class GroupStatus(LocalizedMixin, StrEnum):
    NORMAL = "NORMAL"
    BANNED = "BANNED"
    LEFT = "LEFT"
    UNAUTHORIZED = "UNAUTHORIZED"

    __labels__ = MappingProxyType(
        {
            NORMAL: "正常",
            BANNED: "封禁",
            LEFT: "已退群",
            UNAUTHORIZED: "未授权",
        },
    )


class InvitationStatus(LocalizedMixin, StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"

    __labels__ = MappingProxyType(
        {
            PENDING: "待审批",
            APPROVED: "已同意",
            REJECTED: "已拒绝",
            IGNORED: "已忽略",
        },
    )


class Permission(LocalizedMixin, IntFlag):
    NONE = 0
    NORMAL = auto()
    WHITE_LIST = auto()
    GROUP_ADMIN = auto()
    GROUP_OWNER = auto()
    SUPERUSER = auto()

    __labels__ = MappingProxyType(
        {
            NONE: "无权限",
            NORMAL: "普通用户",
            WHITE_LIST: "白名单",
            GROUP_ADMIN: "群管理员",
            GROUP_OWNER: "群主",
            SUPERUSER: "超级管理员",
        },
    )

    def has(self, perm: "Permission") -> bool:
        """位运算判断权限"""
        return (self & perm) == perm


class TriggerType(LocalizedMixin, StrEnum):
    """插件触发方式"""

    COMMAND = "COMMAND"
    PASSIVE = "PASSIVE"
    EVENT = "EVENT"
    CRON = "CRON"

    __labels__ = MappingProxyType(
        {
            COMMAND: "指令触发",
            PASSIVE: "被动触发",
            EVENT: "事件触发",
            CRON: "定时任务",
        },
    )


class VoteOption(LocalizedMixin, StrEnum):
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    ABSTAIN = "ABSTAIN"

    __labels__ = MappingProxyType(
        {
            SUPPORT: "支持",
            OPPOSE: "反对",
            ABSTAIN: "弃权",
        },
    )


class ApprovalStatus(LocalizedMixin, StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"

    __labels__ = MappingProxyType(
        {
            PENDING: "待审批",
            APPROVED: "已通过",
            REJECTED: "已拒绝",
            WITHDRAWN: "已撤回",
        },
    )


class WordBankAction(LocalizedMixin, StrEnum):
    REPLY = "REPLY"
    AT_USER = "AT_USER"
    POKE_USER = "POKE_USER"
    BAN_USER = "BAN_USER"
    WITHDRAW = "WITHDRAW"

    __labels__ = MappingProxyType(
        {
            REPLY: "普通回复",
            AT_USER: "艾特用户",
            POKE_USER: "戳一戳",
            BAN_USER: "禁言陷阱",
            WITHDRAW: "撤回消息",
        },
    )


class OneBotV11Event(LocalizedMixin, StrEnum):
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


if __name__ == "__main__":
    import os
    from pathlib import Path
    import sys

    current_file_path = Path(__file__).resolve()
    project_root = current_file_path.parent.parent.parent
    sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    s = (
        Permission.NORMAL
        | Permission.GROUP_ADMIN
        | Permission.GROUP_OWNER
        | Permission.SUPERUSER
    )
