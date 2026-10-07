"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 01:39:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07
Description: sentry 异常记录插件
"""

import asyncio
from pathlib import Path
from typing import Any, cast

from nonebot import get_bot
from nonebot.adapters.onebot.v11 import ActionFailed, Bot
import sentry_sdk
from sentry_sdk.types import Event, Hint

from src.config import config
from src.database.core.consts import Permission
from src.lib.admin_notifications import deliver_admin_notification_i18n
from src.lib.consts import TriggerType
from src.lib.error_context import (
    describe_error_context,
    snapshot_error_context,
)
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

name = tr("zh-CN", "plugin.sentry.name")
description = tr("zh-CN", "plugin.sentry.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#8D2CBD",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.sentry.name",
            "description_key": "plugin.sentry.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=30,
            source=DOCS_SOURCE,
        ),
    },
)
background_tasks: set[asyncio.Task] = set()

# 平台侧正常业务拒绝，不属于代码缺陷。
# 这些错误本身确实是异常抛出，但排查价值极低且数量庞大，统一降级为 warning。
_EXPECTED_ACTION_FAILURE_MARKERS: tuple[str, ...] = (
    # 群发言频率限制
    "本群每分钟只能发",
    "send group message rejected",
    # 重复处理同一个加群申请
    "already agree msg",
    "已经处理过该申请",
    # 对方不是好友，无法私聊
    "请先添加对方为好友",
    "send private message rejected",
    # 消息发送过于频繁
    "消息发送频率过快",
    "too frequent",
)

_EXPECTED_REJECTION_TAG = {"key": "expected_platform_rejection", "value": "true"}


def _should_drop_event(hint: Hint) -> bool:
    exc_info = hint.get("exc_info")
    if not exc_info:
        return False

    exc_type, exc_value, exc_traceback = exc_info
    if exc_type is not AssertionError:
        return False
    if exc_traceback is None:
        return False
    if str(exc_value):
        return False

    while exc_traceback is not None:
        frame = exc_traceback.tb_frame
        if (
            frame.f_code.co_name == "_drain_helper"
            and frame.f_globals.get("__name__") == "websockets.legacy.protocol"
        ):
            return True
        exc_traceback = exc_traceback.tb_next
    return False


def _is_expected_platform_rejection(exc_value: BaseException | None) -> bool:
    """判断是否为平台侧可预期的业务拒绝。"""
    if not isinstance(exc_value, ActionFailed):
        return False
    wording = str(getattr(exc_value, "wording", "") or "")
    if not wording:
        return False
    return any(marker in wording for marker in _EXPECTED_ACTION_FAILURE_MARKERS)


def _set_tags(event: Event, tags: list[dict[str, str]]) -> None:
    """整体替换 event tags，规避 TypedDict 逐项赋值的类型限制。"""
    payload: Any = event
    payload["tags"] = tags


def _downgrade_expected_platform_rejection(event: Event) -> None:
    """把平台业务拒绝降级为 warning，避免污染 error 列表。"""
    payload: Any = event
    payload["level"] = "warning"
    contexts = cast(dict[str, Any], event.get("contexts") or {})
    default = cast(dict[str, Any], contexts.get("default") or {})
    default["level"] = "warning"
    contexts["default"] = default
    payload["contexts"] = contexts
    _set_tags(event, [*_normalized_tags(event), _EXPECTED_REJECTION_TAG])


def _build_error_message(
    exc_type: type[BaseException], exc_value: BaseException
) -> str:
    """构造管理员通报正文，附带完整异常上下文。"""
    lines = [f"Type: {exc_type.__name__}\nValue: {exc_value}"]
    context_text = _describe_context()
    if context_text:
        lines.append(context_text)
    return "\n\n".join(lines)


def _describe_context() -> str:
    """渲染异常上下文为可读文本。"""
    return describe_error_context(snapshot_error_context())


def _normalized_tags(event: Event) -> list[dict[str, str]]:
    """把已有 tags 规整为 list[dict[str, str]]。"""
    tags: list[dict[str, str]] = []
    for tag in event.get("tags") or []:
        if isinstance(tag, dict):
            tags.append(
                {"key": str(tag.get("key", "")), "value": str(tag.get("value", ""))}
            )
    return tags


def _attach_context_to_event(event: Event) -> None:
    """把异常上下文写入 Sentry event 的 contexts 与 tags。"""
    context = snapshot_error_context()
    if context.is_empty():
        return
    payload = context.as_dict()
    contexts = cast(dict[str, Any], event.get("contexts") or {})
    contexts["senrin"] = payload
    mutable: Any = event
    mutable["contexts"] = contexts
    kept = [tag for tag in _normalized_tags(event) if not tag["key"].startswith("ctx_")]
    for key, value in payload.items():
        kept.append({"key": f"ctx_{key}", "value": value})
    _set_tags(event, kept)


async def notify_admin(error_message: str) -> None:
    # get_bot 在 bot 尚未连接时会抛异常，这里必须自行吞掉，
    # 否则告警失败本身会再次触发 Sentry 上报，形成自激循环。
    try:
        bot = get_bot()
    except Exception as e:
        logger.debug(f"[Sentry] admin notify skipped, no bot available: {e}")
        return
    try:
        await deliver_admin_notification_i18n(
            cast(Bot, bot),
            locale="zh-CN",
            key="sentry.alert",
            source_kind="sentry_alert",
            error_message=error_message,
        )
    except Exception as e:
        logger.error(tr("zh-CN", "sentry.alert.send_failed", error=str(e)))


def before_send_handler(event: Event, hint: Hint) -> Event | None:
    if _should_drop_event(hint):
        logger.debug("Skip reporting websocket keepalive AssertionError noise.")
        return None

    if "exc_info" in hint:
        exc_type, exc_value, _ = hint["exc_info"]
        if not isinstance(exc_type, type):  # pragma: no cover - 防御
            return event

        if _is_expected_platform_rejection(exc_value):
            logger.debug(
                "[Sentry] downgrade expected platform rejection "
                f"exc_type={exc_type.__name__}"
            )
            _downgrade_expected_platform_rejection(event)

        _attach_context_to_event(event)

        error_msg = _build_error_message(exc_type, exc_value)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(notify_admin(error_msg))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        except RuntimeError:
            pass

    return event


sentry_sdk.init(
    dsn=config.SENTRY_DSN,
    send_default_pii=True,
    before_send=before_send_handler,
    enable_logs=True,
)
