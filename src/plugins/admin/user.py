"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:20:20
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 19:25:29
Description: 用户管理插件
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import ParserExit
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup
from nonebot.rule import ArgumentParser

from src.database.core.consts import Permission
from src.lib.cache.field import BlacklistCacheItem, UserCacheItem
from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG, TriggerType
from src.lib.i18n.runtime import format_duration, resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import finish_with_message
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_doc_demo_message,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.lib.types import UNSET, Unset, is_set, resolve_unset
from src.lib.utils.common import get_current_time, time_to_timedelta
from src.repositories import blacklist_repo, group_repo, user_repo
from src.services.info import resolve_user_name

name = tr("zh-CN", "plugin.admin_user.name")
description = tr("zh-CN", "plugin.admin_user.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "user" / "README.MD"


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
) -> Message:
    return build_doc_demo_message(
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
            "name_key": "plugin.admin_user.name",
            "description_key": "plugin.admin_user.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="admin",
            order=120,
            source=DOCS_SOURCE,
            slug="admin.user",
            parent_slug="admin",
            aliases=("用户管理模块", "用户管理", "admin.user"),
        ),
    },
)

# fmt: off
user_parser = ArgumentParser()
subparsers = user_parser.add_subparsers(dest="action", required=True, help="执行的操作")

ban_parser = subparsers.add_parser(name="ban", aliases=["拉黑"], help="加入黑名单")
ban_parser.add_argument("uids", nargs="+", help="目标用户 ID 列表")
ban_parser.add_argument("-g", "--group", type=str, default=GLOBAL_GROUP_FLAG, help="群组 ID")  # noqa: E501
ban_parser.add_argument("-r", "--reason", type=str, default=UNSET, help="操作原因")
ban_parser.add_argument("-t", "--time", type=str, default=UNSET, help="封禁时长 (缺省为永久)")  # noqa: E501

unban_parser = subparsers.add_parser(name="unban", aliases=["加白"], help="解除黑名单")
unban_parser.add_argument("uids", nargs="+", help="目标用户 ID 列表")
unban_parser.add_argument("-g", "--group", type=str, default=GLOBAL_GROUP_FLAG, help="群组 ID")  # noqa: E501
unban_parser.add_argument("-r", "--reason", type=str, default=UNSET, help="操作原因")

status_parser = subparsers.add_parser("status", aliases=["状态"], help="查询状态")
status_parser.add_argument("uids", nargs="+", help="目标用户 ID 列表")
# fmt: on

admin_command_group = CommandGroup("admin")
admin_user = admin_command_group.command(
    "user",
    aliases={"用户管理"},
    permission=SUPERUSER,
    priority=5,
    block=False,
)


@dataclass
class AdminUserContext:
    user: UserCacheItem
    group_id: str
    operator_id: str
    locale: LocaleCode
    blacklist: BlacklistCacheItem | Unset = UNSET
    time_str: str | Unset = UNSET
    reason: str | Unset = UNSET


async def ban_user(ctx: AdminUserContext) -> str:
    if ctx.user.permission == Permission.SUPERUSER:
        return tr(ctx.locale, "admin.user.action_superuser")

    if is_set(ctx.blacklist) and get_current_time() < ctx.blacklist.expiry:
        return tr(ctx.locale, "admin.user.action_banned")

    duration = PERMANENT_BAN_FLAG
    human_time = tr(ctx.locale, "admin.user.duration.permanent")
    if is_set(ctx.time_str):
        try:
            duration = int(time_to_timedelta(ctx.time_str).total_seconds())
            human_time = format_duration(ctx.locale, duration)
        except ValueError:
            return tr(ctx.locale, "admin.user.time_invalid")

    await blacklist_repo.add_ban(
        target_user_id=ctx.user.user_id,
        group_id=ctx.group_id,
        operator_id=ctx.operator_id,
        duration=duration,
        reason=resolve_unset(ctx.reason, None),
    )

    return tr(
        ctx.locale,
        "admin.user.banned",
        duration=human_time,
        group_id=ctx.group_id,
    )


async def unban_user(ctx: AdminUserContext) -> str:
    if is_set(ctx.blacklist):
        await blacklist_repo.set_unban(
            ctx.user.user_id,
            ctx.group_id,
            ctx.operator_id,
        )
        return tr(ctx.locale, "admin.user.unbanned")
    else:
        return tr(ctx.locale, "admin.user.not_banned")


async def status_user(ctx: AdminUserContext) -> str:
    if is_set(ctx.blacklist):
        status = tr(ctx.locale, "admin.user.status.banned")
    else:
        status = tr(ctx.locale, "admin.user.status.normal")
    return tr(ctx.locale, "admin.user.status", status=status)


@admin_user.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    argv = arg.extract_plain_text().strip().split()
    if not argv:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=build_docs(DocsRenderContext(locale=locale)),
            source_kind="admin_user",
        )
    try:
        args: Namespace | ParserExit = user_parser.parse_args(argv)
    except ParserExit as exc:
        args = exc
    if isinstance(args, ParserExit):
        if args.status == 0:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_docs(DocsRenderContext(locale=locale)),
                source_kind="admin_user",
            )
            return
        else:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_error_demo(
                    locale,
                    tr(
                        locale,
                        "admin.user.args_error",
                        message=tr(locale, "admin.user.args_error.detail"),
                    ),
                    argv[0].lower() if argv else None,
                ),
                source_kind="admin_user",
            )
            return
    assert not isinstance(args, ParserExit)

    action = args.action
    uids = list(set(args.uids))
    group_id = getattr(args, "group", GLOBAL_GROUP_FLAG)
    reason = getattr(args, "reason", UNSET)
    time_str = getattr(args, "time", UNSET)

    handler: Callable[[AdminUserContext], Awaitable[str]]
    match action:
        case "ban" | "禁止" | "拉黑":
            handler = ban_user
        case "unban" | "解除" | "加白":
            handler = unban_user
        case "status" | "状态":
            handler = status_user
        case _:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_error_demo(
                    locale,
                    tr(locale, "admin.user.unknown_command"),
                    None,
                ),
                source_kind="admin_user",
            )
            return

    operator_id = str(event.user_id)
    results = []

    for uid in uids:
        if not uid.isdigit():
            results.append(tr(locale, "admin.user.uid_invalid", user_id=uid))
            continue

        name = await resolve_user_name(bot, uid)
        if not (user := await user_repo.get_user(uid)):
            results.append(
                tr(
                    locale,
                    "admin.user.user_missing",
                    user_id=uid,
                    user_name=name,
                )
            )
            continue

        if group_id != GLOBAL_GROUP_FLAG and not (await group_repo.get_group(group_id)):
            results.append(
                tr(
                    locale,
                    "admin.user.group_missing",
                    user_id=uid,
                    user_name=name,
                    group_id=group_id,
                )
            )
            continue

        blacklist = await blacklist_repo.get_blacklist(user.user_id, group_id) or UNSET
        ctx = AdminUserContext(
            user=user,
            group_id=group_id,
            operator_id=operator_id,
            locale=locale,
            reason=reason,
            blacklist=blacklist,
            time_str=time_str,
        )
        res_msg = await handler(ctx)
        results.append(
            tr(
                locale,
                "admin.user.result",
                user_id=uid,
                user_name=name,
                message=res_msg,
            )
        )

    await finish_with_message(
        bot,
        matcher,
        event=event,
        message="\n".join(results),
        source_kind="admin_user",
    )
