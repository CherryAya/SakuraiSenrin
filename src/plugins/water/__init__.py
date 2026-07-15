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
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MessageEventProgressSink,
)
from src.lib.message_delivery import resolve_notice_delivery_target
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanInput,
    deliver_message_plan,
    finish_with_message,
    reject_with_message,
)
from src.lib.plugin_docs import build_doc_demo_plan_entry, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger
from src.services.startup_sync import ensure_restore_not_in_progress

from .database import water_repo
from .handlers import (
    WaterAdminContext,
    WaterMergeContext,
    build_my_achievements_message,
    build_my_water_profile_message,
    build_water_query_message,
    handle_group_increase_notice,
    handle_help,
    handle_ignore,
    handle_ignored,
    handle_merge_no,
    handle_merge_yes,
    handle_pardon,
    handle_season,
    handle_settle,
    handle_state,
    handle_water_query,
    handle_water_record,
    is_group_admin_event,
    is_water_merge_superuser_event,
    water_help_message,
)
from .services.matrix_suggestion import matrix_suggestion_service
from .services.query_router import WaterQuerySpec, water_query_router
from .services.rank_query import water_rank_query_service
from .services.rank_types import (
    RANK_SHORTCUT_ALIASES,
    WaterRankPeriod,
    WaterRankQuerySpec,
    WaterRankScope,
    WaterRankSubject,
)
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
        "impression_color": "#4DABF7",
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
            aliases=("吹水记录", "吹水", "water"),
        ),
    },
)

_water_plugin_initialized = False
_water_query_cooldown = MemoryCooldown(
    30,
    isolate_level=CooldownIsolateLevel.USER,
)
GUIDED_MAX_ERRORS = 3
WATER_STEP_GUIDED_INPUT = 1


def _build_water_demo_message(
    locale: LocaleCode,
    message: str,
    feature_query: str | None,
    *,
    actor_permission: Permission = Permission.NORMAL,
) -> MessagePlanInput:
    return build_doc_demo_plan_entry(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        actor_permission=actor_permission,
        locale=locale,
        feature_query=feature_query,
        prefix_text=message,
    )


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


def _water_rank_subject(state: T_State) -> WaterRankSubject | None:
    subject = state.get("water_rank_subject_value")
    return subject if subject in {"user", "group", "matrix"} else None


def _water_rank_scope(state: T_State) -> WaterRankScope | None:
    scope = state.get("water_rank_scope_value")
    return scope if scope in {"group", "matrix", "global"} else None


def _water_rank_period(state: T_State) -> WaterRankPeriod | None:
    period = state.get("water_rank_period_value")
    return (
        period
        if period in {"day", "week", "month", "season", "year", "total"}
        else None
    )


def _build_water_guided_prompt(state: T_State) -> str:
    locale = _water_rank_locale(state)
    is_superuser = bool(state.get("water_rank_is_superuser", False))
    return water_query_router.build_guided_progress_prompt(
        locale,
        subject=_water_rank_subject(state),
        scope=_water_rank_scope(state),
        period=_water_rank_period(state),
        is_superuser=is_superuser,
    )


def _store_water_guided_prompt(state: T_State) -> str:
    prompt = _build_water_guided_prompt(state)
    state["water_rank_guided_prompt"] = prompt
    return prompt


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


def _build_water_progress_sink(
    bot: Bot,
    event: MessageEvent,
) -> CompositeProgressSink:
    return CompositeProgressSink(
        LoggerProgressSink(),
        MessageEventProgressSink(bot, event),
    )


def _water_progress_task_name(spec: WaterQuerySpec) -> str:
    if spec.view == "report":
        return "water.query.report"
    if spec.view == "profile":
        return "water.query.profile"
    if spec.view == "achievement":
        return "water.query.achievement"
    if spec.scope_type == "activity":
        return "water.query.activity"
    if spec.scope_type == "rank":
        return "water.query.rank"
    return "water.query.generic"


def _water_progress_stage(spec: WaterQuerySpec) -> str:
    if spec.view == "report":
        return "building_report"
    if spec.view == "profile":
        return "building_profile"
    if spec.view == "achievement":
        return "building_achievement"
    if spec.scope_type == "activity":
        return "building_activity"
    if spec.scope_type == "rank":
        return "building_rank"
    return "processing_items"


async def _run_water_query_long_task(
    bot: Bot,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    spec: WaterQuerySpec,
    build_message: Callable[[LongTaskRunner], Awaitable[MessagePlanInput]],
) -> MessagePlanInput:
    async with LongTaskRunner(
        LongTaskSpec(
            task_name=_water_progress_task_name(spec),
            source_kind="water_query",
            prompt=tr(locale, "water.common.working"),
            threshold_ms=800,
        ),
        sink=_build_water_progress_sink(bot, event),
    ) as long_task:
        await long_task.advance(_water_progress_stage(spec))
        return await build_message(long_task)


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
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="water.daily_settlement",
                source_kind="water_daily_settlement",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="water_daily_settlement")
            await long_task.advance("processing_items")
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
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="water.message_archive",
                source_kind="water_message_archive",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="water_message_archive")
            await long_task.advance("archiving")
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
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="water.summary_archive",
                source_kind="water_summary_archive",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="water_summary_archive")
            await long_task.advance("archiving")
            await water_repo.archive_summary_shards()
            await long_task.advance("processing_items")
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
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="water.daily_report_push",
                source_kind="water_daily_report_push",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="water_daily_report_push")
            bots = list(get_bots().values())
            if not bots:
                logger.warning("[Water][ReportPush] skipped: no bot connected")
                return
            await long_task.advance("sending")
            result = await water_report_service.run_daily_group_report_push(
                bot=cast(Bot, bots[0]),
                locale="zh-CN",
                task=long_task,
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
async def _(bot: Bot, matcher: Matcher, event: NoticeEvent) -> None:
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
        await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(tr(locale, "interaction.cancelled"),),
                source_kind="water_notice",
            ),
            target=resolve_notice_delivery_target(event),
        )
        return

    rebuild_temp_matcher(
        session.matcher_cls,
        water_query,
        step_index=checkpoint.step_index,
        state=checkpoint.state_snapshot,
    )
    await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=(checkpoint.prompt,),
            source_kind="water_notice",
        ),
        target=resolve_notice_delivery_target(event),
    )


@water_query.handle(
    parameterless=[
        water_query_cooldown(30, skip_today_report=True),
    ]
)
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    is_superuser = _is_water_superuser(event)
    if not isinstance(event, GroupMessageEvent):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(locale, "water.common.group_only"),
            source_kind="water_query",
        )
        return
    assert isinstance(event, GroupMessageEvent)
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
        state.pop("water_rank_subject_value", None)
        state.pop("water_rank_scope_value", None)
        state.pop("water_rank_period_value", None)
        state["water_rank_guided_prompt"] = water_query_router.build_guided_intro(
            locale,
            is_superuser=is_superuser,
        )
        return
    else:
        spec = water_query_router.parse(text)
    if spec.view == "report":
        if not is_group_admin_event(event):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "water.common.admin_confirm"),
                source_kind="water_query",
            )
            return
        acquired, remain_seconds = (
            water_report_service.try_acquire_today_report_cooldown(str(event.group_id))
        )
        if not acquired:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "water.report.cooldown", seconds=remain_seconds),
                source_kind="water_query",
            )
            return
    if water_query_router.should_send_working(spec) and (
        spec.rank_spec is None
        or water_query_router.is_rank_period_allowed(
            spec.rank_spec.period,
            is_superuser=is_superuser,
        )
    ):
        message = await _run_water_query_long_task(
            bot,
            event,
            locale,
            spec=spec,
            build_message=lambda task: build_water_query_message(
                event,
                arg,
                locale,
                is_superuser=is_superuser,
                spec=spec,
                task=task,
            ),
        )
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=message,
            source_kind="water_query",
        )
        return
    await handle_water_query(
        matcher,
        event,
        arg,
        locale,
        is_superuser=is_superuser,
        spec=spec,
    )


@water_query.got(
    "water_rank_guided_input",
    prompt=MessageTemplate("{water_rank_guided_prompt}"),
)
async def _water_query_guided_step(
    bot: Bot,
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
    guided_arg: Message = Arg("water_rank_guided_input"),
) -> None:
    locale = _water_rank_locale(state)
    is_superuser = bool(state.get("water_rank_is_superuser", False))
    await _abort_water_on_revoke(matcher, event, locale)
    text = guided_arg.extract_plain_text().strip()
    if water_query_router.is_guided_cancel(text):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=water_query_router.build_guided_cancel_message(locale),
            source_kind="water_query",
        )
        return
    draft = water_query_router.parse_rank_input(text)

    if draft.errors:
        prompt = water_query_router.build_guided_error_prompt(
            locale,
            draft.errors,
            subject=_water_rank_subject(state),
            scope=_water_rank_scope(state),
            period=_water_rank_period(state),
            is_superuser=is_superuser,
        )
        state["water_rank_guided_prompt"] = prompt
        _register_water_checkpoint(
            state,
            event,
            step_index=WATER_STEP_GUIDED_INPUT,
            prompt=prompt,
            snapshot=_copy_water_state(
                state,
                keep_keys=(
                    "water_rank_subject_value",
                    "water_rank_scope_value",
                    "water_rank_period_value",
                    "water_rank_guided_prompt",
                ),
            ),
        )
        await _reject_water_error(matcher, state, locale, prompt)
        return

    subject = _water_rank_subject(state) or draft.subject
    scope = _water_rank_scope(state) or draft.scope
    period = _water_rank_period(state) or draft.period

    if subject is not None:
        state["water_rank_subject_value"] = subject
    if scope is not None:
        state["water_rank_scope_value"] = scope
    if period is not None:
        state["water_rank_period_value"] = period

    if subject is not None and scope is not None:
        if scope not in water_query_router.valid_scopes_for_subject(subject):
            error_text = water_query_router.build_invalid_combo_error_text(
                locale,
                subject=subject,
                period=period,
            )
            prompt = "\n".join(
                [
                    error_text,
                    water_query_router.build_guided_progress_prompt(
                        locale,
                        subject=subject,
                        scope=None,
                        period=period,
                        is_superuser=is_superuser,
                    ),
                ]
            )
            state["water_rank_scope_value"] = None
            state["water_rank_guided_prompt"] = prompt
            _register_water_checkpoint(
                state,
                event,
                step_index=WATER_STEP_GUIDED_INPUT,
                prompt=prompt,
                snapshot=_copy_water_state(
                    state,
                    keep_keys=(
                        "water_rank_subject_value",
                        "water_rank_scope_value",
                        "water_rank_period_value",
                        "water_rank_guided_prompt",
                    ),
                ),
            )
            await _reject_water_error(matcher, state, locale, prompt)
            return

    if period is not None and not water_query_router.is_rank_period_allowed(
        period,
        is_superuser=is_superuser,
    ):
        state["water_rank_period_value"] = None
        prompt = water_query_router.build_guided_error_prompt(
            locale,
            ("invalid_period",),
            subject=subject,
            scope=scope,
            period=None,
            is_superuser=is_superuser,
        )
        state["water_rank_guided_prompt"] = prompt
        _register_water_checkpoint(
            state,
            event,
            step_index=WATER_STEP_GUIDED_INPUT,
            prompt=prompt,
            snapshot=_copy_water_state(
                state,
                keep_keys=(
                    "water_rank_subject_value",
                    "water_rank_scope_value",
                    "water_rank_period_value",
                    "water_rank_guided_prompt",
                ),
            ),
        )
        await _reject_water_error(matcher, state, locale, prompt)
        return

    if subject is None or scope is None or period is None:
        clear_interaction_errors(state)
        prompt = _store_water_guided_prompt(state)
        _register_water_checkpoint(
            state,
            event,
            step_index=WATER_STEP_GUIDED_INPUT,
            prompt=prompt,
            snapshot=_copy_water_state(
                state,
                keep_keys=(
                    "water_rank_subject_value",
                    "water_rank_scope_value",
                    "water_rank_period_value",
                    "water_rank_guided_prompt",
                ),
            ),
        )
        await reject_with_message(matcher, message=prompt)
        return

    clear_interaction_errors(state)
    rank_spec = WaterRankQuerySpec(subject=subject, scope=scope, period=period)
    await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=(water_query_router.build_guided_summary(locale, rank_spec),),
            source_kind="water_query",
        ),
        event=event,
    )
    message = await _run_water_query_long_task(
        bot,
        event,
        locale,
        spec=WaterQuerySpec(
            subject="personal",
            scope_type="rank",
            scope_value=rank_spec.period,
            view="rank",
            mode="simple",
            rank_spec=rank_spec,
        ),
        build_message=lambda _task: water_rank_query_service.build_rank_message(
            subject=rank_spec.subject,
            scope=rank_spec.scope,
            period=rank_spec.period,
            group_id=str(event.group_id),
            locale=locale,
        ),
    )
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=message,
        source_kind="water_query",
    )


@water_profile.handle(
    parameterless=[
        water_query_cooldown(30),
    ]
)
async def _(bot: Bot, matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(locale, "water.common.group_only"),
            source_kind="water_query",
        )
        return
    assert isinstance(event, GroupMessageEvent)
    message = await _run_water_query_long_task(
        bot,
        event,
        locale,
        spec=WaterQuerySpec(
            subject="personal",
            scope_type="activity",
            scope_value="profile",
            view="profile",
            mode="full",
        ),
        build_message=lambda _task: build_my_water_profile_message(event, locale),
    )
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=message,
        source_kind="water_query",
    )


@water_achievement.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(locale, "water.common.group_only"),
            source_kind="water_achievement",
        )
        return
    assert isinstance(event, GroupMessageEvent)
    message = await _run_water_query_long_task(
        bot,
        event,
        locale,
        spec=WaterQuerySpec(
            subject="personal",
            scope_type="activity",
            scope_value="achievement",
            view="achievement",
            mode="full",
        ),
        build_message=lambda _task: build_my_achievements_message(event, locale),
    )
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=message,
        source_kind="water_achievement",
    )


@water_merge.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(locale, "water.common.group_only"),
            source_kind="water_merge",
        )
        return
    assert isinstance(event, GroupMessageEvent)
    if not is_water_merge_superuser_event(event):
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(locale, "water.merge.superuser_only"),
            source_kind="water_merge",
        )
        return

    choice = arg.extract_plain_text().strip().lower()
    handler: Callable[[WaterMergeContext], Awaitable[None]]
    match choice:
        case "yes" | "同意":
            handler = handle_merge_yes
        case "no" | "拒绝":
            handler = handle_merge_no
        case _:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "water.common.merge_choice_invalid"),
                source_kind="water_merge",
            )
            return

    await handler(
        WaterMergeContext(
            matcher=matcher,
            event=event,
            locale=locale,
        )
    )


@water_admin.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    text = arg.extract_plain_text().strip()
    if not text:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=water_help_message(locale),
            source_kind="water_admin",
        )
        return

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
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=_build_water_demo_message(
                    locale,
                    tr(
                        locale,
                        "water.common.unknown_subcommand",
                        action=action,
                        docs=water_help_message(locale),
                    ),
                    "admin-maintenance",
                    actor_permission=Permission.SUPERUSER,
                ),
                source_kind="water_admin",
            )
            return

    await handler(
        WaterAdminContext(
            bot=bot,
            event=event,
            matcher=matcher,
            args=args,
            locale=locale,
        )
    )
