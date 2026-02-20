"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:20:20
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-21 00:43:58
Description: 用户管理插件
"""

from argparse import Namespace
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
import math

import arrow
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import ShellCommandArgs, ShellCommandArgv
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup, PluginMetadata
from nonebot.rule import ArgumentParser

from src.database.core.consts import Permission
from src.lib.cache.field import BlacklistCacheItem, UserCacheItem
from src.lib.consts import GLOBAL_SCOPE, TriggerType
from src.lib.types import UNSET, Unset, is_set
from src.lib.utils import time_to_timedelta
from src.repositories import blacklist_repo, group_repo, user_repo

name = "用户管理模块"
description = "用户管理模块: 采用标准 Shell 风格解析 (argparse)"

usage = f"""
===== {name} =====
命令前缀: #admin.user / #用户管理

本模块使用标准 CLI 语法，支持 -h 或 --help 查看详细帮助。
示例:
  #admin.user ban 12345 67890 -r 恶意刷屏 -t 1d
  #admin.user unban 12345 -r 申诉通过
  #admin.user status 12345
""".strip()

__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
    },
)

# fmt: off
user_parser = ArgumentParser()
subparsers = user_parser.add_subparsers(dest="action", required=True, help="执行的操作")

ban_parser = subparsers.add_parser(name="ban", aliases=["拉黑"], help="加入黑名单")
ban_parser.add_argument("uids", nargs="+", help="目标用户 ID 列表")
ban_parser.add_argument("-g", "--group", type=str, default=GLOBAL_SCOPE, help="群组 ID")
ban_parser.add_argument("-r", "--reason", type=str, default=UNSET, help="操作原因")
ban_parser.add_argument("-t", "--time", type=str, default=UNSET, help="封禁时长 (缺省为永久)")  # noqa: E501

unban_parser = subparsers.add_parser(name="unban", aliases=["加白"], help="解除黑名单")
unban_parser.add_argument("uids", nargs="+", help="目标用户 ID 列表")
unban_parser.add_argument("-g", "--group", type=str, default=GLOBAL_SCOPE, help="群组 ID")  # noqa: E501
unban_parser.add_argument("-r", "--reason", type=str, default=UNSET, help="操作原因")

status_parser = subparsers.add_parser("status", aliases=["状态"], help="查询状态")
status_parser.add_argument("uids", nargs="+", help="目标用户 ID 列表")
# fmt: on

admin_command_group = CommandGroup(
    cmd="admin",
    permission=SUPERUSER,
    priority=5,
    block=False,
)
admin_user = admin_command_group.shell_command(
    cmd="user",
    aliases={"用户管理"},
    parser=user_parser,
)


@dataclass
class AdminUserContext:
    user: UserCacheItem
    group_id: str
    operator_id: str
    blacklist: BlacklistCacheItem | Unset = UNSET
    time_str: str | Unset = UNSET
    reason: str | Unset = UNSET


async def ban_user(ctx: AdminUserContext) -> str:
    if ctx.user.permission == Permission.SUPERUSER:
        return "无法操作超级用户"

    if is_set(ctx.blacklist) and datetime.now() < ctx.blacklist.expiry:
        return "已处于封禁状态"

    duration = math.inf
    human_time = "永久"
    if is_set(ctx.time_str):
        try:
            duration = time_to_timedelta(ctx.time_str).total_seconds()
            human_time = (
                arrow.now()
                .shift(seconds=duration)
                .humanize(
                    locale="zh",
                    only_distance=True,
                )
            )
        except ValueError:
            return "时间格式错误"

    await blacklist_repo.add_ban(
        target_user_id=ctx.user.user_id,
        group_id=ctx.group_id,
        operator_id=ctx.operator_id,
        duration=duration,
        reason=ctx.reason,
    )

    return f"已封禁 (时长: {human_time} 范围: {ctx.group_id})"


async def unban_user(ctx: AdminUserContext) -> str:
    if is_set(ctx.blacklist):
        await blacklist_repo.set_unban(
            ctx.user.user_id,
            ctx.group_id,
            ctx.operator_id,
        )
        return "已解封"
    else:
        return "未封禁"


async def status_user(ctx: AdminUserContext) -> str:
    if is_set(ctx.blacklist):
        status = "封禁"
    else:
        status = "正常"
    return f"状态: {status}"


@admin_user.handle()
async def _(
    event: MessageEvent,
    matcher: Matcher,
    args: Namespace = ShellCommandArgs(),
    argv: list[str] = ShellCommandArgv(),
) -> None:
    action = args.action
    uids = list(set(args.uids))
    group_id = getattr(args, "group", GLOBAL_SCOPE)
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
            await matcher.finish("未知的操作指令。")

    operator_id = str(event.user_id)
    results = []

    for uid in uids:
        if not uid.isdigit():
            results.append(f"[{uid}] 非法 ID，必须为纯数字")
            continue

        if not (user := await user_repo.get_user(uid)):
            results.append(f"[{uid}] 这位用户还没有和凛凛聊过哦，随意操作会挨揍哦？")
            continue

        if group_id != GLOBAL_SCOPE and not (await group_repo.get_group(group_id)):
            results.append(f"[{uid}] 群组({group_id})不存在，随意操作会挨揍哦？")
            continue

        blacklist = await blacklist_repo.get_blacklist(user.user_id, group_id) or UNSET
        ctx = AdminUserContext(
            user=user,
            group_id=group_id,
            operator_id=operator_id,
            reason=reason,
            blacklist=blacklist,
            time_str=time_str,
        )
        res_msg = await handler(ctx)
        results.append(f"[{uid}] {res_msg}")

    await matcher.finish("\n".join(results))
