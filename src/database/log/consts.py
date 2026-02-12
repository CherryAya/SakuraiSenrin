from enum import StrEnum
from types import MappingProxyType

from src.lib.enums import LocalizedMixin


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
