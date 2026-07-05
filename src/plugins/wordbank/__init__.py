"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from nonebot import get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.plugin import on_command
from nonebot.rule import to_me
from nonebot.typing import T_State

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSink,
    LongTaskSpec,
    MatcherProgressSink,
    MessageEventProgressSink,
)
from src.lib.message_plan import MessagePlanInput
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger
from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start
from src.services.startup_sync import ensure_restore_not_in_progress

from . import guided_flow as guided_flow
from .docs_support import (
    DOCS_SOURCE,
    wordbank_docs_meta,
    wordbank_error_message,
)
from .entry_commands import register_wordbank_command_handlers
from .entry_runtime import register_wordbank_runtime_handlers
from .guided_flow import (
    WORDBANK_GUIDED_RECALL_PENDING_KEYS,
    cancel_guided_resources,
    collect_search_query_content,
    copy_guided_state,
    finish_guided_add,
    finish_guided_search,
    guided_search_stage,
    handle_search_session_event,
    record_guided_forward_response_choice,
    record_guided_response,
    record_guided_trigger,
    register_guided_checkpoint,
    reject_guided_error,
    resolve_search_delete_target_ids,
    start_guided_add,
    start_guided_add_with_trigger_image,
    start_guided_search,
    wordbank_guided_locale,
)
from .guided_flow import (
    WORDBANK_GUIDED_SEARCH_STAGE_PAGE as WORDBANK_GUIDED_SEARCH_STAGE_PAGE,
)
from .handlers import (
    SubmissionLifecycle,
    is_reply,
    localize_command_error,
    record_batch_submission_approval_message,  # noqa: F401
    record_submission_approval_message,  # noqa: F401
    schedule_pending_approval_notice,  # noqa: F401
    schedule_submission_approval_notice,  # noqa: F401
    send_pending_approval_notice,  # noqa: F401
    send_pending_batch_approval_notice,  # noqa: F401
)
from .handlers import (
    build_add_result_plan_entry as build_add_result_plan_entry,
)
from .handlers import (
    extract_image_urls as extract_image_urls,
)
from .handlers import (
    fetch_first_image_bytes_from_message as fetch_first_image_bytes_from_message,
)
from .handlers import (
    handle_add_text_result as handle_add_text_result,
)
from .handlers import (
    handle_add_with_media_result as handle_add_with_media_result,
)
from .handlers import (
    handle_delete as handle_delete,
)
from .handlers import (
    handle_passive_message as handle_passive_message,
)
from .handlers import (
    handle_passive_notice as handle_passive_notice,
)
from .handlers.commands import execute_search_page as execute_search_page
from .handlers.commands import render_search_page_message as render_search_page_message
from .pending_batch import send_pending_entries_review
from .services import wordbank_media_service, wordbank_service

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

name = tr("zh-CN", "plugin.wordbank.name")
description = tr("zh-CN", "plugin.wordbank.description")


def _wordbank_error_message(
    exc: Exception,
    locale: LocaleCode,
    *,
    default_feature: str | None = None,
    source: Path = DOCS_SOURCE,
    actor_permission: Permission = Permission.NORMAL,
) -> MessagePlanInput:
    return wordbank_error_message(
        exc,
        locale,
        default_feature=default_feature,
        prefix_text=localize_command_error(exc, locale),
        source=source,
        actor_permission=actor_permission,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#74C0FC",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.wordbank.name",
            "description_key": "plugin.wordbank.description",
        },
        "docs": wordbank_docs_meta(),
    },
)

_wordbank_initialized = False


async def _collect_search_query_content(
    message: Message,
    *,
    keyword_text: str,
    allow_image: bool = True,
) -> tuple[str, bool, dict[int, float]]:
    return await collect_search_query_content(
        message,
        keyword_text=keyword_text,
        allow_image=allow_image,
        media_service=wordbank_media_service,
    )


async def _handle_wordbank_command_message(*args: Any, **kwargs: Any) -> None:
    handler = getattr(
        register_wordbank_command_handlers,
        "_handle_wordbank_command_message",
        None,
    )
    if handler is None:
        raise RuntimeError("wordbank command handler is not registered")
    await handler(*args, **kwargs)


async def initialize_wordbank_plugin() -> None:
    global _wordbank_initialized
    if _wordbank_initialized:
        log_perf("plugin.initialize.cached", initialized=True)
        return
    start = perf_start()
    service_start = perf_start()
    await wordbank_service.initialize()
    service_ms = elapsed_ms(service_start)
    media_start = perf_start()
    await wordbank_media_service.rebuild_cache()
    media_ms = elapsed_ms(media_start)
    _wordbank_initialized = True
    log_perf(
        "plugin.initialize.done",
        start=start,
        service_initialize_ms=f"{service_ms:.2f}",
        media_rebuild_ms=f"{media_ms:.2f}",
    )


async def _start_guided_add(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await start_guided_add(
        matcher,
        event,
        state,
        locale,
        initialize_plugin=initialize_wordbank_plugin,
    )


async def _start_guided_add_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
) -> None:
    await start_guided_add_with_trigger_image(
        matcher,
        event,
        state,
        locale,
        arg,
        media_service=wordbank_media_service,
        initialize_plugin=initialize_wordbank_plugin,
    )


async def _record_guided_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await record_guided_trigger(
        matcher,
        event,
        state,
        locale,
        media_service=wordbank_media_service,
    )


async def _record_guided_response(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await record_guided_response(
        matcher,
        event,
        state,
        locale,
        media_service=wordbank_media_service,
    )


async def _record_guided_forward_response_choice(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    bot: Bot,
) -> None:
    await record_guided_forward_response_choice(
        matcher,
        event,
        state,
        locale,
        media_service=wordbank_media_service,
        bot=bot,
    )


async def _cancel_guided_resources(
    state: Mapping[str, Any],
    cleanup_keys: tuple[str, ...] = WORDBANK_GUIDED_RECALL_PENDING_KEYS,
) -> None:
    await cancel_guided_resources(state, cleanup_keys=cleanup_keys)


_wordbank_submission_lifecycle = SubmissionLifecycle(
    service=wordbank_service,
    media_service=wordbank_media_service,
    submission_source_kind="wordbank_submission",
    batch_submission_source_kind="wordbank_batch_submission",
    batch_feedback_nickname_builder=lambda locale: tr(
        locale,
        "wordbank.batch_add.forward_nickname",
    ),
)


async def _send_pending_entries_view(
    bot: Bot,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> None:
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.pending.batch_view",
            source_kind="wordbank_pending_batch",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=_build_wordbank_progress_sink(bot=bot, event=event),
    ) as long_task:
        await long_task.advance("rendering")
        await send_pending_entries_review(
            bot,
            event,
            text=text,
            locale=locale,
            service=wordbank_service,
            media_service=wordbank_media_service,
            source_kind="wordbank_pending_batch",
            fallback_nickname=tr(locale, "wordbank.approval.pending_forward_nickname"),
        )


driver = get_driver()


@driver.on_startup
async def _initialize_wordbank_plugin() -> None:
    await initialize_wordbank_plugin()


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=30,
    id="wordbank_event_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _wordbank_event_archive_job() -> None:
    try:
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.event_archive",
                source_kind="wordbank_event_archive",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="wordbank_event_archive")
            await long_task.advance("archiving")
            await wordbank_service.repository.archive_event_shards()
        logger.success("[Wordbank] cron archive done")
    except Exception as exc:
        logger.exception(f"[Wordbank] cron archive failed: {exc}")


@scheduler.scheduled_job(
    "cron",
    hour=1,
    minute=15,
    id="wordbank_media_maintenance",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _wordbank_media_maintenance_job() -> None:
    try:
        async with LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.media_maintenance",
                source_kind="wordbank_media_maintenance",
                threshold_ms=0,
            ),
            sink=LoggerProgressSink(),
        ) as long_task:
            ensure_restore_not_in_progress(source="wordbank_media_maintenance")
            await long_task.advance("processing_items")
            report = await wordbank_media_service.run_scheduled_maintenance(
                batch_size=config.WORDBANK_MEDIA_MIGRATION_BATCH_SIZE
            )
        logger.success(f"[Wordbank] media maintenance done: {report}")
    except Exception as exc:
        logger.exception(f"[Wordbank] media maintenance failed: {exc}")


async def is_wordbank_approval_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return (
        await wordbank_service.get_message_ref(
            str(message_id),
            expected_kind="approval",
        )
        is not None
    )


async def is_wordbank_response_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return (
        await wordbank_service.get_message_ref(
            str(message_id),
            expected_kind="response",
        )
        is not None
    )


async def is_wordbank_view_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return (
        await wordbank_service.get_message_ref(
            str(message_id),
            expected_kind="view",
        )
        is not None
    )


wordbank_command = on_command(
    "wordbank",
    aliases={"词库", "wordbank.help"},
    priority=5,
    block=True,
)
wordbank_add_command = on_command(
    ("wordbank", "add"),
    aliases={"添加词条"},
    priority=5,
    block=True,
)
wordbank_search_command = on_command(
    ("wordbank", "search"),
    aliases={"搜索词条"},
    priority=5,
    block=True,
)
wordbank_pending_command = on_command(
    ("wordbank", "pending"),
    aliases={"待审核词条"},
    priority=5,
    block=True,
)
wordbank_rank_command = on_command(
    ("wordbank", "rank"),
    aliases={"苦瓜榜"},
    priority=5,
    block=True,
)
wordbank_approve_command = on_command(
    ("wordbank", "approve"),
    aliases={"通过词条", "审核通过词条"},
    priority=5,
    block=True,
)
wordbank_reject_command = on_command(
    ("wordbank", "reject"),
    aliases={"拒绝词条", "驳回词条"},
    priority=5,
    block=True,
)
wordbank_delete_command = on_command(
    ("wordbank", "delete"),
    aliases={("wordbank", "del"), "删除词条"},
    priority=5,
    block=True,
)
wordbank_restore_command = on_command(
    ("wordbank", "restore"),
    aliases={"恢复词条"},
    priority=5,
    block=True,
)
wordbank_reply_command = on_message(
    rule=to_me() & is_reply & is_wordbank_response_reply,
    priority=5,
    block=True,
)
wordbank_approval_reply_command = on_message(
    rule=to_me() & is_reply & is_wordbank_approval_reply,
    priority=5,
    block=True,
)
wordbank_view_reply_command = on_message(
    rule=to_me() & is_reply & is_wordbank_view_reply,
    priority=6,
    block=True,
)
wordbank_passive = on_message(priority=95, block=False)
wordbank_notice = on_notice(priority=95, block=False)

runtime_exports = register_wordbank_runtime_handlers(
    wordbank_reply_command=wordbank_reply_command,
    wordbank_approval_reply_command=wordbank_approval_reply_command,
    wordbank_view_reply_command=wordbank_view_reply_command,
    wordbank_passive=wordbank_passive,
    wordbank_notice=wordbank_notice,
    wordbank_add_command=wordbank_add_command,
    wordbank_command=wordbank_command,
    initialize_plugin=initialize_wordbank_plugin,
    build_error_message=_wordbank_error_message,
    cancel_guided_resources=_cancel_guided_resources,
    guided_locale=wordbank_guided_locale,
)


async def _finish_guided_search(
    bot: Bot,
    matcher: Matcher,
    state: T_State,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    page_number: int,
    clamp_page: bool = False,
) -> None:
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.search.guided_view",
            source_kind="wordbank_view",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=_build_wordbank_progress_sink(
            bot=bot,
            event=event,
            matcher=matcher,
        ),
    ) as long_task:
        await long_task.advance("rendering")
        await finish_guided_search(
            bot,
            matcher,
            state,
            event,
            locale,
            page_number=page_number,
            clamp_page=clamp_page,
            wordbank_service=wordbank_service,
            media_service=wordbank_media_service,
            record_search_result_view_message=_record_search_result_view_message,
        )


async def _handle_search_session_event(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await handle_search_session_event(
        bot,
        matcher,
        event,
        state,
        locale,
        wordbank_service=wordbank_service,
        send_group_detail_view=_send_group_detail_view,
        finish_guided_search_fn=_finish_guided_search,
        build_error_message=_wordbank_error_message,
    )


async def _start_guided_search(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await start_guided_search(
        matcher,
        event,
        state,
        locale,
        initialize_plugin=initialize_wordbank_plugin,
    )


def _build_wordbank_progress_sink(
    *,
    bot: Bot | None,
    event: MessageEvent,
    matcher: Matcher | None = None,
) -> CompositeProgressSink:
    sinks: list[LongTaskSink] = [LoggerProgressSink()]
    if bot is not None:
        sinks.append(MessageEventProgressSink(bot, event))
    elif matcher is not None:
        sinks.append(MatcherProgressSink(matcher))
    return CompositeProgressSink(*sinks)


async def _finish_guided_add(
    bot: Any,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    await finish_guided_add(
        bot,
        matcher,
        event,
        state,
        finalize_submission=_wordbank_submission_lifecycle.finalize,
        wordbank_service=wordbank_service,
    )


async def _send_search_result_view(
    bot: Any,
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    keyword: str,
    image_scores: dict[int, float] | None = None,
    state: T_State | None = None,
) -> None:
    if state is not None:
        await runtime_exports["send_search_result_view"](
            bot,
            matcher,
            event,
            locale,
            keyword=keyword,
            image_scores=image_scores,
            state=state,
            finish_guided_search=_finish_guided_search,
        )
        return
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.search.direct_view",
            source_kind="wordbank_view",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=_build_wordbank_progress_sink(bot=bot, event=event, matcher=matcher),
    ) as long_task:
        await long_task.advance("rendering")
        await runtime_exports["send_search_result_view"](
            bot,
            matcher,
            event,
            locale,
            keyword=keyword,
            image_scores=image_scores,
            state=None,
            finish_guided_search=_finish_guided_search,
        )


async def _send_group_detail_view(
    bot: Any,
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    trigger_group_id: int,
    page: int,
    finish_after_send: bool = True,
) -> None:
    async with LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.group.detail_view",
            source_kind="wordbank_view",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        ),
        sink=_build_wordbank_progress_sink(bot=bot, event=event, matcher=matcher),
    ) as long_task:
        await long_task.advance("rendering")
        await runtime_exports["send_group_detail_view"](
            bot,
            matcher,
            event,
            locale,
            trigger_group_id=trigger_group_id,
            page=page,
            finish_after_send=finish_after_send,
        )


async def _record_search_result_view_message(*args: Any, **kwargs: Any) -> None:
    await runtime_exports["record_search_result_view_message"](*args, **kwargs)


async def _record_passive_response_message(*args: Any, **kwargs: Any) -> None:
    await runtime_exports["record_passive_response_message"](*args, **kwargs)


async def _resolve_search_delete_target_ids(
    *args: Any, **kwargs: Any
) -> tuple[int, ...]:
    return await resolve_search_delete_target_ids(*args, **kwargs)


async def _build_passive_message(
    response: Any,
    *,
    locale: LocaleCode,
) -> tuple[MessagePlanInput, dict[str, object]]:
    result = await runtime_exports["_build_passive_message"](response, locale=locale)
    if result is None:
        raise RuntimeError("wordbank passive message builder is not registered")
    return cast(tuple[MessagePlanInput, dict[str, object]], result)


register_wordbank_command_handlers(
    wordbank_command=wordbank_command,
    wordbank_add_command=wordbank_add_command,
    wordbank_search_command=wordbank_search_command,
    wordbank_pending_command=wordbank_pending_command,
    wordbank_rank_command=wordbank_rank_command,
    wordbank_approve_command=wordbank_approve_command,
    wordbank_reject_command=wordbank_reject_command,
    wordbank_delete_command=wordbank_delete_command,
    wordbank_restore_command=wordbank_restore_command,
    initialize_plugin=initialize_wordbank_plugin,
    build_error_message=_wordbank_error_message,
    finalize_submission=_wordbank_submission_lifecycle.finalize,
    collect_search_query_content=_collect_search_query_content,
    start_guided_add=_start_guided_add,
    start_guided_add_with_trigger_image=_start_guided_add_with_trigger_image,
    finish_guided_add=_finish_guided_add,
    start_guided_search=_start_guided_search,
    finish_guided_search=_finish_guided_search,
    handle_search_session_event=_handle_search_session_event,
    record_guided_trigger=_record_guided_trigger,
    record_guided_response=_record_guided_response,
    guided_search_stage=guided_search_stage,
    reject_guided_error=reject_guided_error,
    register_guided_checkpoint=register_guided_checkpoint,
    guided_locale=wordbank_guided_locale,
    copy_guided_state=copy_guided_state,
    send_group_detail_view=_send_group_detail_view,
    send_search_result_view=_send_search_result_view,
    send_pending_entries_view=_send_pending_entries_view,
    resolve_locale_fn=resolve_locale,
    handle_wordbank_command_message_fn=lambda *args, **kwargs: (
        _handle_wordbank_command_message(*args, **kwargs)
    ),
)
