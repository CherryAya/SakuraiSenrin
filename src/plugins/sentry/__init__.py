"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-25 01:39:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-10-07
Description: sentry 异常记录插件
"""

import asyncio
from collections.abc import Mapping
import os
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
from src.lib.i18n.catalog import tr
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
background_tasks: set[asyncio.Task[None]] = set()


def _sentry_notify_disabled() -> bool:
    """测试环境关停管理员告警派发（`sentry_sdk.init()` 全局拦截异常）。"""
    return os.getenv("SAKURAI_DISABLE_SENTRY_NOTIFY") == "1"


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
    """ActionFailed 在 Sentry 侧降级为 warning（仅限 level，通报照旧）。"""
    return isinstance(exc_value, ActionFailed)


def _set_tags(event: Event, tags: Mapping[str, str]) -> None:
    """整体替换 event tags：与 SDK 的 ``Mapping[str, str]`` 形状保持一致。"""
    event["tags"] = dict(tags)


def _merge_tags(event: Event, extra: Mapping[str, str]) -> None:
    """在既有标签基础上合并 ``extra``，保留 SDK/scope 写入的标签。"""
    merged = dict(event.get("tags") or {})
    merged.update(extra)
    _set_tags(event, merged)


def _normalized_tags(event: Event) -> dict[str, str]:
    """把既有 tags 规整为 ``dict[str, str]``，非映射形态一律丢弃。"""
    raw = event.get("tags")
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


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


def _downgrade_level_to_warning(event: Event) -> None:
    """ActionFailed 降级为 warning 并打标；平台拒绝同样通报管理员。"""
    event["level"] = "warning"
    contexts = dict(event.get("contexts") or {})
    default = dict(contexts.get("default") or {})
    default["level"] = "warning"
    contexts["default"] = default
    event["contexts"] = contexts
    _merge_tags(event, {"action_failed": "true"})


def _attach_context_to_event(event: Event) -> None:
    """把异常上下文写入 Sentry event 的 contexts 与 tags。"""
    context = snapshot_error_context()
    if context.is_empty():
        return
    payload = context.as_dict()
    contexts = dict(event.get("contexts") or {})
    contexts["senrin"] = dict(payload)
    event["contexts"] = contexts
    kept = {
        key: value
        for key, value in _normalized_tags(event).items()
        if not key.startswith("ctx_")
    }
    kept.update({f"ctx_{key}": value for key, value in payload.items()})
    _set_tags(event, kept)


async def notify_admin(error_message: str) -> None:
    # get_bot 离线时抛异常，须吞掉：告警失败会上报，成自激循环。
    try:
        bot = get_bot()
    except Exception as e:
        logger.debug(f"[Sentry] admin notify skipped, no bot available: {e}")
        return
    try:
        await deliver_admin_notification_i18n(
            # get_bot() 静态返回基类 Bot；本仓库 bot.py 只注册 V11 适配器。
            cast(Bot, bot),
            locale="zh-CN",
            key="sentry.alert",
            source_kind="sentry_alert",
            error_message=error_message,
        )
    except Exception as e:
        logger.error(tr("zh-CN", "sentry.alert.send_failed", error=str(e)))


def before_send_handler(event: Event, hint: Hint) -> Event | None:
    # 仅丢弃 websockets keepalive 噪声；业务异常全量通报。
    if _should_drop_event(hint):
        logger.debug("Skip reporting websocket keepalive AssertionError noise.")
        return None

    if "exc_info" in hint:
        exc_type, exc_value, _ = hint["exc_info"]
        if not isinstance(exc_type, type):  # pragma: no cover - 防御
            return event

        # 顺序有讲究：先补上下文，再定 Sentry level，最后通报管理员。
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
    """把管理员通报投递出去；事件循环缺席时跳过。"""
    if _sentry_notify_disabled():
        return
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
