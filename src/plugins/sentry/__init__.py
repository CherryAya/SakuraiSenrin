"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 01:39:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07
Description: sentry 异常记录插件
"""

import asyncio
from collections.abc import MutableMapping
from pathlib import Path
from typing import cast

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

# ActionFailed 代表平台侧拒绝了本次调用（限流、重复处理、非好友、权限不足等）。
# 这类异常同样要通报管理员，只是它们通常不是代码缺陷，
# 因此在 Sentry 侧降级为 warning，避免污染 error 列表。
_ACTION_FAILED_TAG = {"key": "action_failed", "value": "true"}


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


def _should_downgrade_level(exc_value: BaseException | None) -> bool:
    """是否需要在 Sentry 侧降级为 warning。

    仅依据异常类型判定，不做文案匹配：文案会随平台版本变化，
    且任何新增的业务异常都不应被静默归类。
    注意这只影响 Sentry 的 level，管理员通报不受影响，仍然全量发送。
    """
    return isinstance(exc_value, ActionFailed)


def _event_payload(event: Event) -> MutableMapping[str, object]:
    """Sentry Event 本身是 dict，就地改写前先收窄成可变映射。"""
    return cast(MutableMapping[str, object], event)


def _set_tags(event: Event, tags: list[dict[str, str]]) -> None:
    """整体替换 event tags，规避 TypedDict 逐项赋值的类型限制。"""
    _event_payload(event)["tags"] = tags


def _downgrade_level_to_warning(event: Event) -> None:
    """把 ActionFailed 在 Sentry 侧降级为 warning，避免污染 error 列表。"""
    payload = _event_payload(event)
    payload["level"] = "warning"
    contexts = cast(dict[str, object], event.get("contexts") or {})
    default = cast(dict[str, object], contexts.get("default") or {})
    default["level"] = "warning"
    contexts["default"] = default
    payload["contexts"] = contexts
    _set_tags(event, [*_normalized_tags(event), _ACTION_FAILED_TAG])


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
    contexts = cast(dict[str, object], event.get("contexts") or {})
    contexts["senrin"] = payload
    _event_payload(event)["contexts"] = contexts
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
    # 仅丢弃三方库心跳噪声（websockets keepalive 的空 AssertionError）。
    # 业务异常一律不丢弃：平台拒绝、限流、重复处理等同样需要管理员知情。
    if _should_drop_event(hint):
        logger.debug("Skip reporting websocket keepalive AssertionError noise.")
        return None

    if "exc_info" in hint:
        exc_type, exc_value, _ = hint["exc_info"]
        if not isinstance(exc_type, type):  # pragma: no cover - 防御
            return event

        # 顺序有讲究：先补上下文，再按类型决定 Sentry level，最后无条件通报管理员。
        _attach_context_to_event(event)

        if _should_downgrade_level(exc_value):
            logger.debug(
                "[Sentry] downgrade ActionFailed to warning "
                f"exc_type={exc_type.__name__}"
            )
            _downgrade_level_to_warning(event)

        _schedule_admin_notify(_build_error_message(exc_type, exc_value))

    return event


def _schedule_admin_notify(error_msg: str) -> None:
    """把管理员通报投递出去；没有运行中的事件循环时跳过。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[Sentry] no running loop, skip admin notify.")
        return
    task = loop.create_task(notify_admin(error_msg))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


sentry_sdk.init(
    dsn=config.SENTRY_DSN,
    send_default_pii=True,
    before_send=before_send_handler,
    enable_logs=True,
)
