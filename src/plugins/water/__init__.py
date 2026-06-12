"""水王插件入口。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import Any

from nonebot import get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    MessageEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Depends
from nonebot.permission import SUPERUSER
from nonebot.plugin import on_command
from nonebot.rule import is_type

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import create_docs_meta
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
    handle_my_water_profile,
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

name = tr("zh-CN", "plugin.water.name")
description = tr("zh-CN", "plugin.water.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.4.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.water.name",
            "description_key": "plugin.water.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="fun",
            order=100,
            source=DOCS_SOURCE,
        ),
    },
)

_water_plugin_initialized = False
_water_query_cooldowns: dict[str, float] = {}


def water_query_cooldown(cooldown: float = 30) -> Any:
    async def dependency(matcher: Matcher, event: MessageEvent) -> None:
        try:
            user_id = event.get_user_id()
        except Exception:
            return

        now = monotonic()
        expires_at = _water_query_cooldowns.get(user_id, 0.0)
        if expires_at > now:
            locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
            await matcher.finish(
                tr(locale, "water.common.cooldown", seconds=int(cooldown))
            )
        _water_query_cooldowns[user_id] = now + cooldown

    return Depends(dependency)


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
    aliases={"水王排行榜"},
    priority=5,
    block=True,
)
water_profile = on_command("我有多水", priority=5, block=True)
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
water_recorder = on_message(priority=4, block=False)


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


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=25,
    id="water_message_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_message_archive_job() -> None:
    try:
        await water_repo.archive_message_shards()
        logger.success("[Water] cron archive done")
    except Exception as e:
        logger.exception(f"[Water] cron archive failed: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=35,
    id="water_summary_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_summary_archive_job() -> None:
    try:
        await water_repo.archive_summary_shards()
        pruned = await water_repo.prune_hot_summaries()
        logger.success(f"[Water] cron summary archive done: pruned={pruned}")
    except Exception as e:
        logger.exception(f"[Water] cron summary archive failed: {e}")


@water_recorder.handle()
async def _(bot: Bot, event: GroupMessageEvent) -> None:
    await handle_water_record(bot, event)


@on_notice(priority=5, rule=is_type(GroupIncreaseNoticeEvent), block=False).handle()
async def _(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    await handle_group_increase_notice(bot, event)


@water_query.handle(
    parameterless=[
        water_query_cooldown(30),
    ]
)
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "water.common.group_only"))
    await matcher.send(tr(locale, "water.common.working"))
    await handle_water_query(matcher, event, arg, locale)


@water_profile.handle(
    parameterless=[
        water_query_cooldown(30),
    ]
)
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "water.common.group_only"))
    await matcher.send(tr(locale, "water.common.working"))
    await handle_my_water_profile(matcher, event, locale)


@water_week_rank.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await handle_period_rank(matcher, "week", locale)


@water_month_rank.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await handle_period_rank(matcher, "month", locale)


@water_season_rank.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await handle_period_rank(matcher, "season", locale)


@water_year_rank.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await handle_period_rank(matcher, "year", locale)


@water_achievement.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "water.common.group_only"))
    await handle_my_achievements(matcher, event, locale)


@water_merge.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "water.common.group_only"))
    if not is_group_admin_event(event):
        await matcher.finish(tr(locale, "water.common.admin_confirm"))

    choice = arg.extract_plain_text().strip().lower()
    handler: Callable[[WaterMergeContext], Awaitable[None]]
    match choice:
        case "yes" | "同意":
            handler = handle_merge_yes
        case "no" | "拒绝":
            handler = handle_merge_no
        case _:
            await matcher.finish(tr(locale, "water.common.merge_choice_invalid"))

    await handler(WaterMergeContext(matcher=matcher, event=event, locale=locale))


@water_admin.handle()
async def _(matcher: Matcher, event: MessageEvent, arg: Message = CommandArg()) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    text = arg.extract_plain_text().strip()
    if not text:
        await matcher.finish(water_help_message(locale))

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
            await matcher.finish(
                tr(
                    locale,
                    "water.common.unknown_subcommand",
                    action=action,
                    docs=water_help_message(locale),
                )
            )

    await handler(WaterAdminContext(matcher=matcher, args=args, locale=locale))
