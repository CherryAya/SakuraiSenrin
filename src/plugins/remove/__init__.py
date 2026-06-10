"""真实退群插件。"""

from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.plugin import on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

from .handlers import has_remove_permission, is_remove_confirmed, perform_remove

name = tr("zh-CN", "plugin.remove.name")
description = tr("zh-CN", "plugin.remove.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
        ctx=ctx,
    )


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
            build_docs,
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
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "remove.group_only"))
    if not await has_remove_permission(event):
        await matcher.finish(tr(locale, "remove.permission_denied"))


@remove_matcher.got(
    "confirm",
    prompt=Message(tr("zh-CN", "remove.confirm.prompt")),
)
async def _confirm_step(
    matcher: Matcher,
    event: GroupMessageEvent,
    confirm: Message = Arg(),
) -> None:
    locale = await resolve_locale(str(event.group_id))
    if not is_remove_confirmed(confirm.extract_plain_text()):
        await matcher.finish(tr(locale, "remove.cancelled"))


@remove_matcher.got(
    "reason",
    prompt=Message(tr("zh-CN", "remove.reason.prompt")),
)
async def _reason_step(
    bot: Bot,
    matcher: Matcher,
    event: GroupMessageEvent,
    reason: Message = Arg(),
) -> None:
    locale = await resolve_locale(str(event.group_id))
    await perform_remove(
        bot,
        matcher,
        event,
        locale=locale,
        reason=reason.extract_plain_text(),
    )
