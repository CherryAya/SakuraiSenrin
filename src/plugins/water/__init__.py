"""水王插件入口。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nonebot import get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    MessageEvent,
)
from nonebot.adapters.onebot.v11.helpers import Cooldown, CooldownIsolateLevel
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import on_command
from nonebot.rule import is_type

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

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
    handle_period_rank,
    handle_season,
    handle_settle,
    handle_state,
    handle_water_query,
    handle_water_record,
    is_group_admin_event,
    water_help_message,
)
from .services.matrix_suggestion import matrix_suggestion_service
from .services.settlement import water_settlement_service

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

name = "吹水记录"
description = "群聊活跃记录、画像、榜单与活动赛季。"

docs_content = """
1. 水王
   个人画像（简版）
2. 水王 完整
   个人画像（完整版）
3. 水王 日榜 / 月榜 / 季榜 / 年榜 / 总榜
4. 水王 成就
   个人成就
5. 水王 赛季
   当前活动赛季概览
6. 水王 赛季 当前
   当前活动赛季概览
7. 水王 赛季 列表
   已发布赛季
8. 水王 赛季 当前列表
   当前生效赛季
9. 水王 赛季 <season_id|名称> [个人|群聊|矩阵] [概览|积分|排名|成就]
10. #water season create <season_id> <start> <end> <name...>
11. #water season publish <season_id>
12. #water season archive <season_id>
13. #water season show <season_id>
14. #water season list [current|published|archived]
15. #water season delete <season_id>
""".strip()


def build_docs() -> Message:
    return build_static_docs(
        name=name,
        description=description,
        content=docs_content,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.4.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            build_docs,
            visible=True,
            category="fun",
            order=100,
        ),
    },
)

_water_plugin_initialized = False


async def initialize_water_plugin() -> None:
    global _water_plugin_initialized
    if _water_plugin_initialized:
        return
    await water_repo.init_all_tables()
    await matrix_suggestion_service.warm_up_first_record_cache()
    await water_repo.warm_up_group_matrix_cache()
    _water_plugin_initialized = True


driver = get_driver()


@driver.on_startup
async def _initialize_water_plugin() -> None:
    await initialize_water_plugin()


water_query = on_command(
    "水王",
    aliases={"水王排行榜", "我有多水"},
    priority=5,
    block=True,
)
water_week_rank = on_command("水王周榜", priority=5, block=True)
water_month_rank = on_command("水王月榜", priority=5, block=True)
water_season_rank = on_command("水王季榜", priority=5, block=True)
water_year_rank = on_command("水王年榜", priority=5, block=True)
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


@water_query.handle(
    parameterless=[
        Cooldown(
            cooldown=30,
            isolate_level=CooldownIsolateLevel.USER,
            prompt="冷却时间 30s，请耐心等待 qwq",
        )
    ]
)
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("这个命令要在群里用喔~")
    await matcher.send("凛凛统计中，请稍后喔……")
    await handle_water_query(matcher, event, arg)


@water_week_rank.handle()
async def _(matcher: Matcher) -> None:
    await handle_period_rank(matcher, "week")


@water_month_rank.handle()
async def _(matcher: Matcher) -> None:
    await handle_period_rank(matcher, "month")


@water_season_rank.handle()
async def _(matcher: Matcher) -> None:
    await handle_period_rank(matcher, "season")


@water_year_rank.handle()
async def _(matcher: Matcher) -> None:
    await handle_period_rank(matcher, "year")


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
        case "season":
            handler = handle_season
        case _:
            await matcher.finish(f"未知子命令: {action}\n\n{water_help_message()}")

    await handler(WaterAdminContext(matcher=matcher, args=args))
