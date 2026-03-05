"""水王插件入口。"""

import asyncio
from collections.abc import Awaitable, Callable

from nonebot import on_message, on_notice, require
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    MessageEvent,
)
from nonebot.adapters.onebot.v11.helpers import Cooldown, CooldownIsolateLevel
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata, on_command
from nonebot.rule import is_type

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.logger import logger
from src.plugins.water.img import (
    WaterProfileCardData,
    build_my_water_fallback_text,
    build_my_water_image,
    build_water_rank_image,
)
from src.services.info import resolve_group_card, resolve_group_name

from .database import water_repo
from .handlers import (
    WaterAdminContext,
    WaterMergeContext,
    handle_group_increase_notice,
    handle_help,
    handle_ignore,
    handle_ignored,
    handle_merge_no,
    handle_merge_yes,
    handle_my_achievements,
    handle_pardon,
    handle_settle,
    handle_state,
    handle_water_record,
    is_group_admin_event,
    water_help_message,
)
from .services import matrix_suggestion_service, water_settlement_service

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

name = "吹水记录"
description = """
吹水记录模块
""".strip()

usage = f"""
===== {name} =====

用户命令:
1. 我有多水
2. 水王排行榜 / 水王
3. 我的水王成就 / 水王成就
4. #water.merge yes / #water.merge no (群管理员)

超管命令:
1. #water help
2. #water settle [YYYYMMDD] [-f|--force]
3. #water pardon <penalty_id>
4. #water ignore <group_id>
5. #water ignored
6. #water state
""".strip()

__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.3.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
    },
)
asyncio.run(water_repo.init_all_tables())
asyncio.run(matrix_suggestion_service.warm_up_first_record_cache())
asyncio.run(water_repo.warm_up_group_matrix_cache())

self_global_water_status = on_command(
    "我有多水",
    priority=5,
    block=True,
)
water_rank = on_command(
    "水王排行榜",
    aliases={"水王"},
    priority=5,
    block=True,
)
water_achievement = on_command(
    "我的水王成就",
    aliases={"水王成就"},
    priority=5,
    block=True,
)
water_admin = on_command(
    "water",
    aliases={"水王管理"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
water_merge = on_command("water.merge", aliases={"water合并"}, priority=5, block=True)
water_recorder = on_message(priority=99, block=False)


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=5,
    id="water_daily_settlement",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_daily_settlement_job() -> None:
    try:
        result = await water_settlement_service.run_daily_settlement()
        if result.success:
            logger.success(
                "[Water] cron settlement done: "
                f"date={result.record_date} "
                f"rows={result.aggregate_rows} "
                f"achievements={result.unlocked_achievements}"
            )
        else:
            logger.warning(
                "[Water] cron settlement skipped: "
                f"date={result.record_date} reason={result.reason}"
            )
    except Exception as e:
        logger.exception(f"[Water] cron settlement failed: {e}")


@water_recorder.handle()
async def _(bot: Bot, event: GroupMessageEvent) -> None:
    await handle_water_record(bot, event)


@on_notice(priority=5, rule=is_type(GroupIncreaseNoticeEvent), block=False).handle()
async def _(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    await handle_group_increase_notice(bot, event)


@self_global_water_status.handle()
async def _(event: GroupMessageEvent) -> None:
    user_id = str(event.user_id)
    group_id = str(event.group_id)
    matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
    matrix_group_ids = await water_repo.get_groups_by_matrix_id(matrix_id)
    if not matrix_group_ids:
        matrix_group_ids = [group_id]
    matrix_group_names = await asyncio.gather(
        *(resolve_group_name(None, gid) for gid in matrix_group_ids)
    )
    matrix_groups = list(
        zip(
            matrix_group_ids,
            [
                name or f"群聊_{gid[-4:]}"
                for gid, name in zip(matrix_group_ids, matrix_group_names, strict=False)
            ],
            strict=False,
        )
    )

    global_level, matrix_level, matrix_total_level = await asyncio.gather(
        water_repo.get_user_global_level(user_id),
        water_repo.get_user_matrix_level(user_id, matrix_id),
        water_repo.get_matrix_total_level(matrix_id),
    )
    (
        global_rank,
        group_user_rank,
        matrix_user_rank,
        matrix_rank,
        group_activity_rank,
        achievement_items,
    ) = await asyncio.gather(
        water_repo.get_user_global_rank(user_id),
        water_repo.get_group_user_rank(group_id, user_id),
        water_repo.get_user_matrix_rank(user_id, matrix_id),
        water_repo.get_matrix_rank(matrix_id),
        water_repo.get_group_activity_rank(group_id),
        water_repo.get_user_achievement_items(user_id),
    )

    if global_level is None and matrix_level is None:
        await self_global_water_status.finish(
            "你当前还没有水王资产记录，先多聊几句再来查吧。"
        )

    profile_data = WaterProfileCardData(
        user_id=user_id,
        group_id=group_id,
        matrix_id=matrix_id,
        group_name=await resolve_group_name(None, group_id),
        username=await resolve_group_card(None, user_id, group_id),
        global_level=global_level,
        matrix_level=matrix_level,
        global_rank=global_rank,
        group_user_rank=group_user_rank,
        matrix_user_rank=matrix_user_rank,
        matrix_rank=matrix_rank,
        group_rank=group_activity_rank,
        matrix_total_level=matrix_total_level,
        matrix_groups=matrix_groups,
        achievement_items=achievement_items,
    )

    await self_global_water_status.send("凛凛制图中，请稍候……")
    card = await build_my_water_image(profile_data)
    if card:
        await self_global_water_status.finish(MessageSegment.image(card))

    fallback = await build_my_water_fallback_text(profile_data)
    await self_global_water_status.finish(fallback)


@water_rank.handle(
    parameterless=[
        Cooldown(
            cooldown=30,
            isolate_level=CooldownIsolateLevel.USER,
            prompt="冷却时间 30s，请耐心等待 qwq",
        )
    ]
)
async def _(event: GroupMessageEvent) -> None:
    await water_rank.send("凛凛统计中，请稍后喔……")
    res = await build_water_rank_image(str(event.group_id))
    if res:
        await water_rank.finish(MessageSegment.image(res))
    else:
        await water_rank.finish("凛凛，凛凛凛凛！无水无水！🏳️")


@water_achievement.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("这个命令要在群里用喔~")
    await handle_my_achievements(matcher, event)


@water_merge.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("这个命令要在群里用喔~")
    if not is_group_admin_event(event):
        await matcher.finish("这条要群管理员或群主来确认喔~")

    choice = arg.extract_plain_text().strip().lower()
    handler: Callable[[WaterMergeContext], Awaitable[None]]
    match choice:
        case "yes" | "同意":
            handler = handle_merge_yes
        case "no" | "拒绝":
            handler = handle_merge_no
        case _:
            await matcher.finish(
                "凛凛没看懂，你可以发 #water.merge yes 或 #water.merge no"
            )

    await handler(WaterMergeContext(matcher=matcher, event=event))


@water_admin.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    text = arg.extract_plain_text().strip()
    if not text:
        await matcher.finish(water_help_message())

    args = text.split()
    action = args[0].lower().removeprefix(".")
    _ = event

    handler: Callable[[WaterAdminContext], Awaitable[None]]
    match action:
        case "help" | "帮助":
            handler = handle_help
        case "settle" | "结算":
            handler = handle_settle
        case "pardon" | "回档":
            handler = handle_pardon
        case "ignore" | "忽略":
            handler = handle_ignore
        case "ignored" | "忽略列表":
            handler = handle_ignored
        case "state" | "状态":
            handler = handle_state
        case _:
            await matcher.finish(f"未知子命令: {action}\n\n{water_help_message()}")

    await handler(WaterAdminContext(matcher=matcher, args=args))
