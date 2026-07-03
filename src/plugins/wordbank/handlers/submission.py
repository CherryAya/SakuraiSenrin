"""Unified submission lifecycle for wordbank/study entry creation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.matcher import Matcher

from src.lib.i18n.types import LocaleCode
from src.lib.message_delivery import DeliveryResult
from src.lib.message_plan import DeliveryPlan, deliver_message_plan
from src.plugins.wordbank.batch_feedback import send_batch_add_feedback
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    WordbankAddResult,
    WordbankBatchAddResult,
)

from .approval import (
    build_add_result_plan_entry,
    record_batch_submission_approval_message,
    record_submission_approval_message,
    schedule_submission_approval_notice,
)

type SubmissionPayload = WordbankAddResult | WordbankBatchAddResult
type BatchFeedbackNicknameBuilder = Callable[[LocaleCode], str]
type SubmissionHandler = Callable[
    [Matcher, Bot, MessageEvent, SubmissionPayload, LocaleCode],
    Awaitable[None],
]


@dataclass(slots=True, frozen=True)
class SubmissionLifecycle:
    service: WordbankService
    media_service: WordbankMediaService
    submission_source_kind: str
    batch_submission_source_kind: str
    batch_feedback_nickname_builder: BatchFeedbackNicknameBuilder

    async def finalize(
        self,
        matcher: Matcher,
        bot: Bot,
        event: MessageEvent,
        submission: SubmissionPayload,
        locale: LocaleCode,
    ) -> None:
        await finalize_submission(
            matcher,
            bot,
            event,
            submission,
            locale=locale,
            service=self.service,
            media_service=self.media_service,
            submission_source_kind=self.submission_source_kind,
            batch_submission_source_kind=self.batch_submission_source_kind,
            batch_feedback_nickname=self.batch_feedback_nickname_builder(locale),
        )


async def finalize_submission(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    submission: SubmissionPayload,
    *,
    locale: LocaleCode,
    service: WordbankService,
    media_service: WordbankMediaService,
    submission_source_kind: str,
    batch_submission_source_kind: str,
    batch_feedback_nickname: str,
) -> None:
    send_result: DeliveryResult | None = None

    if isinstance(submission, WordbankAddResult):
        message = await build_add_result_plan_entry(
            submission,
            locale=locale,
            media_service=media_service,
        )
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(message,),
                source_kind=submission_source_kind,
            ),
            event=event,
        )
        send_result = plan_result.results[0]
        await record_submission_approval_message(
            service,
            event=event,
            result=submission,
            send_result=send_result,
        )
    else:
        send_result = await send_batch_add_feedback(
            bot,
            event,
            batch=submission,
            locale=locale,
            media_service=media_service,
            source_kind=batch_submission_source_kind,
            fallback_nickname=batch_feedback_nickname,
        )
        await record_batch_submission_approval_message(
            service,
            event=event,
            batch=submission,
            send_result=send_result,
        )

    schedule_submission_approval_notice(
        bot,
        service,
        event=event,
        submission=submission,
        locale=locale,
        media_service=media_service,
    )
    await matcher.finish()
