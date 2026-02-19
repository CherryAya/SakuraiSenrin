"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-15 23:24:21
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 23:19:52
Description: 群聊管理插件
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.exception import ActionFailed
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import CommandGroup, PluginMetadata

from src.database.core.consts import GroupStatus, Permission
from src.lib.cache.field import GroupCacheItem
from src.lib.consts import TriggerType
from src.repositories import group_repo

name = "群组管理模块"
description = "群组管理模块: 处理群组黑白名单 (支持批量操作)"

usage = f"""
===== {name} =====

命令前缀: #admin.group / #群组管理

1.加入黑名单
  ban / 禁止 / 拉黑 / 封禁
  示例: #admin.group ban <群号1> [群号2] ...

2.解除黑名单
  unban / 解除 / 加白 / 解封
  示例: #admin.group unban <群号1> [群号2] ...

3.授权群组
  auth / 授权
  示例: #admin.group auth <群号1> [群号2] ...

4.取消授权
  unauth / 取消授权
  示例: #admin.group unauth <群号1> [群号2] ...

5.查询状态
  status / 状态
  示例: #admin.group status <群号1> [群号2] ...

6.退群
  leave / 退群
  示例: #admin.group leave <群号1> [群号2] ...

7.帮助信息
  help / 帮助
  示例: #admin.group help

[注意事项]:
1. 需要【Senrin】管理员权限。
2. 支持同时输入多个群组 ID，用空格隔开。
3. 若不填群号，默认对当前所在群组执行。
""".strip()

__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.3",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
    },
)

group_cmd = CommandGroup("admin", permission=SUPERUSER, priority=5, block=False)
admin_group = group_cmd.command("group")


@dataclass
class AdminGroupContext:
    bot: Bot
    group: GroupCacheItem


async def ban_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status == GroupStatus.BANNED:
        return "已处于封禁状态"

    await group_repo.update_status(ctx.group.group_id, GroupStatus.BANNED)
    return "已封禁"


async def unban_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status != GroupStatus.BANNED:
        return "未被封禁，无需解封"

    await group_repo.update_status(ctx.group.group_id, GroupStatus.AUTHORIZED)
    return "已解封"


async def auth_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status == GroupStatus.BANNED:
        return "已被封禁，请先解封"
    elif ctx.group.status == GroupStatus.AUTHORIZED:
        return "已是授权状态"

    await group_repo.update_status(ctx.group.group_id, GroupStatus.AUTHORIZED)
    return "授权成功"


async def unauth_group(ctx: AdminGroupContext) -> str:
    if ctx.group.status == GroupStatus.BANNED:
        return "处于封禁状态，无需取消授权"
    elif ctx.group.status == GroupStatus.AUTHORIZED:
        await group_repo.update_status(ctx.group.group_id, GroupStatus.UNAUTHORIZED)
        return "已取消授权"

    return "当前未授权，无需操作"


async def leave_group(ctx: AdminGroupContext) -> str:
    await group_repo.update_status(ctx.group.group_id, GroupStatus.LEFT)
    try:
        await ctx.bot.set_group_leave(group_id=int(ctx.group.group_id))
        return "已退群"
    except ActionFailed:
        return "退群失败，仅更新数据库状态"


async def status_group(ctx: AdminGroupContext) -> str:
    return f"当前状态: {ctx.group.status.name}"


@admin_group.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    args = arg.extract_plain_text().strip().split()
    if not args:
        await matcher.finish(usage)

    command = args[0].lower()
    if command in ["help", "帮助"]:
        await matcher.finish(usage)

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
            await matcher.finish(f"未知的操作指令。\n\n{usage}")

    group_ids = args[1:]
    if not group_ids:
        if isinstance(event, GroupMessageEvent):
            group_ids = [str(event.group_id)]
        else:
            await matcher.finish("错误: 请在指令后提供至少一个目标群组 ID。")

    valid_group_ids = []
    for gid in set(group_ids):
        if not gid.isdigit():
            await matcher.finish(f"错误: 存在非法群组 ID [{gid}]，群号必须为纯数字。")
        valid_group_ids.append(gid)

    results = []
    for gid in valid_group_ids:
        group = await group_repo.get_group_by_id(gid)
        if not group:
            results.append(f"[{gid}] 数据库中不存在该群组记录")
            continue

        ctx = AdminGroupContext(bot, group)
        res_msg = await handler(ctx)
        results.append(f"[{gid}] {res_msg}")

    await matcher.finish("\n".join(results))
