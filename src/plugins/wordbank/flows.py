"""wordbank 引导流程与视图的装配层。

把 guided_flow 的纯逻辑与 services / 单例绑定为可直接调用的入口。
纯转发包装已移除：注入型参数在 guided_flow 顶层直接依赖 services，入口直接调用。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher
from nonebot.typing import T_State

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import LongTaskRunner, LongTaskSpec

from . import views
from .guided_flow import finish_guided_add
from .handlers import SubmissionLifecycle
from .handlers.commands import (
    RANK_ALIASES,
    RESPONSE_ALIASES,
    SET_ALIASES,
    TRIGGER_ALIASES,
    split_command_text,
)
from .pending_batch import send_pending_entries_review
from .services import wordbank_media_service, wordbank_service

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


def _build_wordbank_command_progress_spec(
    action: str,
    *,
    rest: str,
    locale: LocaleCode,
) -> LongTaskSpec | None:
    if action in RANK_ALIASES:
        return LongTaskSpec(
            task_name="wordbank.rank.view",
            source_kind="wordbank_command",
            prompt=tr(locale, "wordbank.view.processing"),
            threshold_ms=800,
        )
    sub_action, _ = split_command_text(rest)
    if action in TRIGGER_ALIASES and sub_action in SET_ALIASES:
        return LongTaskSpec(
            task_name="wordbank.trigger.set",
            source_kind="wordbank_command",
            prompt=tr(locale, "wordbank.mutation.processing"),
            threshold_ms=800,
        )
    if action in RESPONSE_ALIASES and sub_action in SET_ALIASES:
        return LongTaskSpec(
            task_name="wordbank.response.set",
            source_kind="wordbank_command",
            prompt=tr(locale, "wordbank.mutation.processing"),
            threshold_ms=800,
        )
    return None
