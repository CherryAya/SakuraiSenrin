"""真实退群插件。"""

from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.matcher import Matcher
from nonebot.plugin import on_command
from nonebot.typing import T_State

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

from .handlers import has_remove_permission, is_remove_confirmed, perform_remove

name = tr("zh-CN", "plugin.remove.name")
description = tr("zh-CN", "plugin.remove.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.GROUP_ADMIN,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.remove.name",
            "description_key": "plugin.remove.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=90,
            source=DOCS_SOURCE,
        ),
    },
)

remove_matcher = on_command("remove", aliases={"退群"}, priority=5, block=True)


@remove_matcher.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    if state.get("remove_stage"):
        return
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "remove.group_only"))
    if not await has_remove_permission(event):
        await matcher.finish(tr(locale, "remove.permission_denied"))
    state["remove_stage"] = "confirm"
    await matcher.reject(tr(locale, "remove.confirm.prompt"))


@remove_matcher.handle()
async def _(
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
) -> None:
    if state.get("remove_stage") != "confirm":
        return
    locale = await resolve_locale(str(event.group_id))
    confirm_text = event.message.extract_plain_text().strip()
    if not confirm_text:
        await matcher.reject(tr(locale, "remove.confirm.prompt"))
    if not is_remove_confirmed(confirm_text):
        await matcher.finish(tr(locale, "remove.cancelled"))
    state["remove_stage"] = "reason"
    await matcher.reject(tr(locale, "remove.reason.prompt"))


@remove_matcher.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
) -> None:
    if state.get("remove_stage") != "reason":
        return
    locale = await resolve_locale(str(event.group_id))
    reason = event.message.extract_plain_text()
    if not reason.strip():
        await matcher.reject(tr(locale, "remove.reason.prompt"))
    await perform_remove(
        bot,
        matcher,
        event,
        locale=locale,
        reason=reason,
    )
