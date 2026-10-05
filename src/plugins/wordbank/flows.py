"""wordbank 引导流程与视图的装配层。

把 guided_flow 的纯逻辑与 services / views 绑定为可直接调用的入口。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.typing import T_State

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import LongTaskRunner, LongTaskSpec

from . import views
from .bootstrap import _initialize_wordbank_plugin
from .errors import build_wordbank_error_message
from .guided_flow import (
    WORDBANK_GUIDED_RECALL_PENDING_KEYS,
    cancel_guided_resources,
    collect_search_query_content,
    finish_guided_add,
    handle_search_session_event,
    record_guided_forward_response_choice,
    record_guided_response,
    record_guided_trigger,
    start_guided_add,
    start_guided_add_with_trigger_image,
    start_guided_search,
)
from .handlers import SubmissionLifecycle
from .pending_batch import send_pending_entries_review
from .services import wordbank_media_service, wordbank_service


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
        initialize_plugin=_initialize_wordbank_plugin,
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
        initialize_plugin=_initialize_wordbank_plugin,
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
        sink=views.build_progress_sink(bot=bot, event=event),
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
        send_group_detail_view=views.send_group_detail_view,
        finish_guided_search_fn=views.finish_guided_search_view,
        build_error_message=build_wordbank_error_message,
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
        initialize_plugin=_initialize_wordbank_plugin,
    )


async def _finish_guided_add(
    bot: Bot,
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
