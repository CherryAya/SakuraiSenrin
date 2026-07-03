"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-15 23:24:21
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-03 12:21:57
Description: 群聊管理插件
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import ActionFailed
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup

from src.database.core.consts import GroupStatus, Permission
from src.lib.cache.field import GroupCacheItem
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput, finish_with_message
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_doc_demo_plan_entry,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.repositories import group_repo
from src.services.info import resolve_group_name

name = tr("zh-CN", "plugin.admin_group.name")
description = tr("zh-CN", "plugin.admin_group.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "group" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


def _build_error_demo(
    locale: LocaleCode,
    message: str,
    feature_query: str | None,
) -> MessagePlanInput:
    return build_doc_demo_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        locale=locale,
        feature_query=feature_query,
        prefix_text=message,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.3.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.admin_group.name",
            "description_key": "plugin.admin_group.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="admin",
            order=110,
            source=DOCS_SOURCE,
            slug="admin.group",
            parent_slug="admin",
            aliases=("群组管理模块", "群组管理", "admin.group"),
        ),
    },
)

admin_command_group = CommandGroup("admin")
admin_group = admin_command_group.command(
    "group",
    aliases={"群组管理"},
    permission=SUPERUSER,
    priority=5,
    block=False,
)


@dataclass
class AdminGroupContext:
    bot: Bot
    group: GroupCacheItem
    locale: LocaleCode


async def ban_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status.is_banned:
        return tr(ctx.locale, "admin.group.already_banned")

    await group_repo.update_status(ctx.group.group_id, GroupStatus.BANNED)
    return tr(ctx.locale, "admin.group.banned")


async def unban_group(ctx: AdminGroupContext) -> str:
    if not ctx.group.status.is_banned:
        return tr(ctx.locale, "admin.group.not_banned")

    await group_repo.update_status(ctx.group.group_id, GroupStatus.UNAUTHORIZED)
    return tr(ctx.locale, "admin.group.unbanned")


async def auth_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status.is_banned:
        return tr(ctx.locale, "admin.group.need_unban_first")
    elif ctx.group.status.is_working:
        return tr(ctx.locale, "admin.group.already_authorized")

    await group_repo.update_status(ctx.group.group_id, GroupStatus.AUTHORIZED)
    return tr(ctx.locale, "admin.group.authorized")


async def unauth_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status.is_banned:
        return tr(ctx.locale, "admin.group.banned_skip_unauth")
    elif ctx.group.status.is_working:
        await group_repo.update_status(ctx.group.group_id, GroupStatus.UNAUTHORIZED)
        return tr(ctx.locale, "admin.group.unauthorized")

    return tr(ctx.locale, "admin.group.already_unauthorized")


async def leave_group(ctx: AdminGroupContext) -> str:
    await group_repo.update_status(ctx.group.group_id, GroupStatus.LEFT)
    try:
        await ctx.bot.set_group_leave(group_id=int(ctx.group.group_id))
        return tr(ctx.locale, "admin.group.left")
    except ActionFailed:
        return tr(ctx.locale, "admin.group.leave_failed")


async def status_group(ctx: AdminGroupContext) -> str:
    return tr(ctx.locale, "admin.group.status", status=ctx.group.status)


@admin_group.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    docs_message = build_docs(DocsRenderContext(locale=locale))
    docs_text = str(docs_message)
    args = arg.extract_plain_text().strip().split()
    if not args:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=docs_message,
            source_kind="admin_group",
        )

    command = args[0].lower()
    if command in ["help", "帮助"]:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=docs_message,
            source_kind="admin_group",
        )

    handler: Callable[[AdminGroupContext], Awaitable[str]]
    match command:
        case "ban" | "禁止" | "拉黑" | "封禁":
            handler = ban_group
        case "unban" | "解除" | "加白" | "解封":
            handler = unban_group
        case "auth" | "授权":
            handler = auth_group
        case "unauth" | "取消授权":
            handler = unauth_group
        case "status" | "状态":
            handler = status_group
        case "leave" | "退群":
            handler = leave_group
        case _:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_error_demo(
                    locale,
                    tr(locale, "admin.group.unknown_command", docs=docs_text),
                    None,
                ),
                source_kind="admin_group",
            )
            return

    group_ids = args[1:]
    if not group_ids:
        if isinstance(event, GroupMessageEvent):
            group_ids = [str(event.group_id)]
        else:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "admin.group.group_required"),
                source_kind="admin_group",
            )

    results = []
    for gid in set(group_ids):
        if not gid.isdigit():
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_error_demo(
                    locale,
                    tr(locale, "admin.group.group_invalid", group_id=gid),
                    command,
                ),
                source_kind="admin_group",
            )

        name = await resolve_group_name(bot, gid)
        group = await group_repo.get_group(gid)
        if not group:
            results.append(
                tr(
                    locale,
                    "admin.group.not_found",
                    group_id=gid,
                    group_name=name,
                )
            )
            continue

        ctx = AdminGroupContext(bot, group, locale)
        res_msg = await handler(ctx)
        results.append(
            tr(
                locale,
                "admin.group.result",
                group_id=gid,
                group_name=name,
                message=res_msg,
            )
        )
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message="\n".join(results),
        source_kind="admin_group",
    )
