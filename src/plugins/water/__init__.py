"""水王插件入口。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from nonebot import get_bots, get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.internal.adapter import MessageTemplate
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import on_command
from nonebot.rule import is_type
from nonebot.typing import T_State

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.cooldown import (
    CooldownIsolateLevel,
    MemoryCooldown,
    build_cooldown_dependency,
)
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import (
    abort_if_revoke_signal,
    clear_interaction_errors,
    reject_or_abort_on_error,
)
from src.lib.interactive_recall import (
    INTERACTION_ROOT_MESSAGE_ID,
    INTERACTION_SESSION_KEY,
    find_recall_session,
    get_interaction_session_key,
    is_supported_recall_notice,
    rebuild_temp_matcher,
    register_recall_checkpoint,
    register_root_message,
)
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
    handle_season,
    handle_settle,
    handle_state,
    handle_water_query,
    handle_water_record,
    is_group_admin_event,
    water_help_message,
)
from .services.matrix_suggestion import matrix_suggestion_service
from .services.query_router import WaterQuerySpec, water_query_router
from .services.rank_query import water_rank_query_service
from .services.rank_types import RANK_SHORTCUT_ALIASES, WaterRankQuerySpec
from .services.report import water_report_service
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
_water_query_cooldown = MemoryCooldown(
    30,
    isolate_level=CooldownIsolateLevel.USER,
)
GUIDED_MAX_ERRORS = 3
WATER_STEP_SUBJECT = 1
WATER_STEP_SCOPE = 2
WATER_STEP_PERIOD = 3


def water_query_cooldown(
    cooldown: float = 30,
    *,
    skip_today_report: bool = False,
) -> Any:
    store = (
        _water_query_cooldown
        if cooldown == _water_query_cooldown.cooldown
        else MemoryCooldown(cooldown, isolate_level=CooldownIsolateLevel.USER)
    )

    async def prompt_builder(event: MessageEvent, remaining_seconds: int) -> str:
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        return tr(locale, "water.common.cooldown", seconds=remaining_seconds)

    async def bypass_checker(event: MessageEvent) -> bool:
        if not skip_today_report:
            return False
        raw_message = getattr(event, "raw_message", "").strip()
        text = raw_message
        if text.startswith(("#", "/", "＃", "井")):
            text = text[1:].strip()
        if text.startswith("水王"):
            text = text.removeprefix("水王").strip()
        return "".join(text.split()) in {
            "今日报告",
            "今日報告",
            "水王日报",
            "水王日報",
            "日报",
            "日報",
        }

    return build_cooldown_dependency(
        store,
        prompt_builder=prompt_builder,
        bypass_checker=bypass_checker if skip_today_report else None,
    )


def clear_water_query_cooldowns() -> None:
    _water_query_cooldown.clear()


def _is_water_superuser(event: MessageEvent) -> bool:
    try:
        return str(event.get_user_id()) in config.SUPERUSERS
    except Exception:
        return False


async def _abort_water_on_revoke(
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
) -> None:
    await abort_if_revoke_signal(
        event,
        matcher,
        message=tr(locale, "interaction.cancelled"),
    )


async def _reject_water_error(
    matcher: Matcher,
    state: T_State,
    locale: LocaleCode,
    message: str,
) -> None:
    await reject_or_abort_on_error(
        matcher,
        state,
        message,
        max_errors=GUIDED_MAX_ERRORS,
        abort_message=tr(locale, "interaction.too_many_errors"),
    )


def _copy_water_state(
    state: T_State,
    *,
    keep_keys: tuple[str, ...],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key, value in state.items():
        if key.startswith("__nonebug"):
            snapshot[key] = value
    session_key = get_interaction_session_key(state)
    if session_key is not None:
        snapshot[INTERACTION_SESSION_KEY] = session_key
    if "water_rank_locale" in state:
        snapshot["water_rank_locale"] = state["water_rank_locale"]
    if "water_rank_is_superuser" in state:
        snapshot["water_rank_is_superuser"] = state["water_rank_is_superuser"]
    if INTERACTION_ROOT_MESSAGE_ID in state:
        snapshot[INTERACTION_ROOT_MESSAGE_ID] = state[INTERACTION_ROOT_MESSAGE_ID]
    for key in keep_keys:
        if key in state:
            snapshot[key] = state[key]
    clear_interaction_errors(snapshot)
    return snapshot


def _water_rank_locale(state: T_State) -> LocaleCode:
    locale = state.get("water_rank_locale", "zh-CN")
    return locale if locale in {"zh-CN", "lzh", "x-meme"} else "zh-CN"


def _register_water_checkpoint(
    state: T_State,
    event: MessageEvent,
    *,
    step_index: int,
    prompt: str,
    snapshot: dict[str, Any],
) -> None:
    register_recall_checkpoint(
        state,
        message_id=getattr(event, "message_id", ""),
        step_index=step_index,
        prompt=prompt,
        state_snapshot=snapshot,
    )


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
    aliases={"水王排行榜", "水王日报", *RANK_SHORTCUT_ALIASES},
    priority=5,
    block=True,
)
water_profile = on_command("我有多水", priority=5, block=True)
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
water_notice = on_notice(priority=5, block=False)


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


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=40,
    id="water_daily_report_push",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _water_daily_report_push_job() -> None:
    try:
        bots = list(get_bots().values())
        if not bots:
            logger.warning("[Water][ReportPush] skipped: no bot connected")
            return
        result = await water_report_service.run_daily_group_report_push(
            bot=cast(Bot, bots[0]),
            locale="zh-CN",
        )
        logger.success(
            "[Water][ReportPush] cron done: "
            f"date={result.record_date} candidates={result.candidate_groups} "
            f"sent={result.sent_groups} failed={result.failed_groups}"
        )
    except Exception as e:
        logger.exception(f"[Water][ReportPush] cron failed: {e}")


@water_recorder.handle()
async def _(bot: Bot, event: GroupMessageEvent) -> None:
    await handle_water_record(bot, event)


@on_notice(priority=5, rule=is_type(GroupIncreaseNoticeEvent), block=False).handle()
async def _(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    await handle_group_increase_notice(bot, event)


@water_notice.handle()
async def _(matcher: Matcher, event: NoticeEvent) -> None:
    if not is_supported_recall_notice(event):
        return

    session = find_recall_session(
        water_query,
        cast(GroupRecallNoticeEvent | FriendRecallNoticeEvent, event),
    )
    if session is None:
        return

    state = session.matcher_cls._default_state
    locale = _water_rank_locale(state)
    checkpoint = session.checkpoint
    session.matcher_cls.destroy()

    if session.is_root_message or checkpoint is None:
        await matcher.send(tr(locale, "interaction.cancelled"))
        return

    rebuild_temp_matcher(
        session.matcher_cls,
        water_query,
        step_index=checkpoint.step_index,
        state=checkpoint.state_snapshot,
    )
    await matcher.send(checkpoint.prompt)


@water_query.handle(
    parameterless=[
        water_query_cooldown(30, skip_today_report=True),
    ]
)
async def _(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    is_superuser = _is_water_superuser(event)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "water.common.group_only"))
    text = arg.extract_plain_text().strip()
    shortcut_rank_spec, shortcut_errors = water_query_router.parse_shortcut_command(
        getattr(event, "raw_message", "")
    )
    raw_message = getattr(event, "raw_message", "").strip()
    today_report_aliases = {
        "#水王日报",
        "水王日报",
        "/水王日报",
        "＃水王日报",
        "井水王日报",
    }
    if raw_message in today_report_aliases:
        spec = WaterQuerySpec(
            subject="group",
            scope_type="activity",
            scope_value="today",
            view="report",
            mode="simple",
        )
    elif shortcut_rank_spec is not None:
        spec = WaterQuerySpec(
            subject="personal",
            scope_type="rank",
            scope_value=shortcut_rank_spec.period,
            view="rank",
            mode="simple",
            rank_spec=shortcut_rank_spec,
            errors=shortcut_errors,
        )
    elif not text:
        clear_interaction_errors(state)
        state["water_rank_locale"] = locale
        state["water_rank_is_superuser"] = is_superuser
        register_root_message(state, event)
        state["water_rank_subject_prompt"] = water_query_router.build_guided_intro(
            locale
        )
        return
    else:
        spec = water_query_router.parse(text)
    if spec.view == "report":
        if not is_group_admin_event(event):
            await matcher.finish(tr(locale, "water.common.admin_confirm"))
        acquired, remain_seconds = (
            water_report_service.try_acquire_today_report_cooldown(str(event.group_id))
        )
        if not acquired:
            await matcher.finish(
                tr(locale, "water.report.cooldown", seconds=remain_seconds)
            )
    if water_query_router.should_send_working(spec) and (
        spec.rank_spec is None
        or water_query_router.is_rank_period_allowed(
            spec.rank_spec.period,
            is_superuser=is_superuser,
        )
    ):
        await matcher.send(tr(locale, "water.common.working"))
    await handle_water_query(
        matcher,
        event,
        arg,
        locale,
        is_superuser=is_superuser,
        spec=spec,
    )


@water_query.got(
    "water_rank_subject",
    prompt=MessageTemplate("{water_rank_subject_prompt}"),
)
async def _water_query_subject_step(
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
    subject_arg: Message = Arg("water_rank_subject"),
) -> None:
    locale = _water_rank_locale(state)
    await _abort_water_on_revoke(matcher, event, locale)
    text = subject_arg.extract_plain_text().strip()
    if water_query_router.is_guided_cancel(text):
        await matcher.finish(water_query_router.build_guided_cancel_message(locale))
    subject = water_query_router.parse_subject_choice(text)
    if subject is None:
        await _reject_water_error(
            matcher,
            state,
            locale,
            water_query_router.build_subject_retry_prompt(locale),
        )
        return
    clear_interaction_errors(state)
    state["water_rank_subject_value"] = subject
    state["water_rank_scope_prompt"] = water_query_router.build_scope_prompt(
        locale, subject
    )
    _register_water_checkpoint(
        state,
        event,
        step_index=WATER_STEP_SCOPE,
        prompt=str(state["water_rank_scope_prompt"]),
        snapshot=_copy_water_state(
            state,
            keep_keys=(
                "water_rank_subject_value",
                "water_rank_scope_prompt",
            ),
        ),
    )


@water_query.got(
    "water_rank_scope",
    prompt=MessageTemplate("{water_rank_scope_prompt}"),
)
async def _water_query_scope_step(
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
    scope_arg: Message = Arg("water_rank_scope"),
) -> None:
    locale = _water_rank_locale(state)
    is_superuser = bool(state.get("water_rank_is_superuser", False))
    await _abort_water_on_revoke(matcher, event, locale)
    subject_value = state.get("water_rank_subject_value")
    subject = subject_value if subject_value in {"user", "group", "matrix"} else None
    if subject is None:
        await matcher.finish(
            water_query_router.build_rank_menu(locale, is_superuser=is_superuser)
        )
    text = scope_arg.extract_plain_text().strip()
    if water_query_router.is_guided_cancel(text):
        await matcher.finish(water_query_router.build_guided_cancel_message(locale))
    scope = water_query_router.parse_scope_choice(text)
    if scope is None:
        await _reject_water_error(
            matcher,
            state,
            locale,
            water_query_router.build_scope_retry_prompt(locale, subject),
        )
        return
    if scope not in water_query_router.valid_scopes_for_subject(subject):
        await _reject_water_error(
            matcher,
            state,
            locale,
            water_query_router.build_scope_retry_prompt(locale, subject, scope),
        )
        return
    clear_interaction_errors(state)
    state["water_rank_scope_value"] = scope
    state["water_rank_period_prompt"] = water_query_router.build_period_prompt_for_role(
        locale,
        is_superuser=is_superuser,
    )
    _register_water_checkpoint(
        state,
        event,
        step_index=WATER_STEP_PERIOD,
        prompt=str(state["water_rank_period_prompt"]),
        snapshot=_copy_water_state(
            state,
            keep_keys=(
                "water_rank_subject_value",
                "water_rank_scope_value",
                "water_rank_period_prompt",
            ),
        ),
    )


@water_query.got(
    "water_rank_period",
    prompt=MessageTemplate("{water_rank_period_prompt}"),
)
async def _water_query_period_step(
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
    period_arg: Message = Arg("water_rank_period"),
) -> None:
    locale = _water_rank_locale(state)
    is_superuser = bool(state.get("water_rank_is_superuser", False))
    await _abort_water_on_revoke(matcher, event, locale)
    subject_value = state.get("water_rank_subject_value")
    subject = subject_value if subject_value in {"user", "group", "matrix"} else None
    scope_value = state.get("water_rank_scope_value")
    scope = scope_value if scope_value in {"group", "matrix", "global"} else None
    if subject is None or scope is None:
        await matcher.finish(
            water_query_router.build_rank_menu(locale, is_superuser=is_superuser)
        )
    text = period_arg.extract_plain_text().strip()
    if water_query_router.is_guided_cancel(text):
        await matcher.finish(water_query_router.build_guided_cancel_message(locale))
    period = water_query_router.parse_period_choice(text)
    if period is None:
        await _reject_water_error(
            matcher,
            state,
            locale,
            water_query_router.build_period_retry_prompt(
                locale,
                is_superuser=is_superuser,
            ),
        )
        return
    if not water_query_router.is_rank_period_allowed(
        period,
        is_superuser=is_superuser,
    ):
        await _reject_water_error(
            matcher,
            state,
            locale,
            water_query_router.build_period_retry_prompt(
                locale,
                is_superuser=is_superuser,
            ),
        )
        return
    clear_interaction_errors(state)
    rank_spec = WaterRankQuerySpec(subject=subject, scope=scope, period=period)
    await matcher.send(water_query_router.build_guided_summary(locale, rank_spec))
    await matcher.send(tr(locale, "water.common.working"))
    message = await water_rank_query_service.build_rank_message(
        subject=rank_spec.subject,
        scope=rank_spec.scope,
        period=rank_spec.period,
        group_id=str(event.group_id),
        locale=locale,
    )
    await matcher.finish(message)


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
