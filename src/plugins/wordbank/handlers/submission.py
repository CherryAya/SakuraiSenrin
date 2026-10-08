"""Unified submission lifecycle for wordbank/study entry creation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import NoReturn, Protocol

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


def _submission_needs_approval_notice(submission: SubmissionPayload) -> bool:
    if isinstance(submission, WordbankAddResult):
        return submission.status == "pending"
    return any(
        item.ok and item.result is not None and item.result.status == "pending"
        for item in submission.items
    )


class SubmissionHandler(Protocol):
    def __call__(
        self,
        matcher: Matcher,
        bot: Bot,
        event: MessageEvent,
        submission: SubmissionPayload,
        locale: LocaleCode,
        *,
        source_event: MessageEvent | None = None,
    ) -> Awaitable[None]: ...


class _MatcherFinisher(Protocol):
    """只暴露无参 ``finish`` 的最小协议。

    nonebot 的 ``Matcher.finish`` 签名嵌套了 ``Message[Unknown]`` 等未绑定泛型，
    strict 模式下直接调用点会被判为 partially unknown。这里只用到「结束当前事件」
    语义，用协议收窄即可，无需改动 nonebot 类型定义。
    """

    async def finish(self) -> NoReturn: ...


async def _finish_event(matcher: _MatcherFinisher) -> NoReturn:
    await matcher.finish()


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
        *,
        source_event: MessageEvent | None = None,
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
            source_event=source_event,
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
    source_event: MessageEvent | None = None,
) -> None:
    approval_event = source_event or event
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
            event=approval_event,
            result=submission,
            send_result=send_result,
        )
    else:
        send_result = await send_batch_add_feedback(
            matcher,
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
            event=approval_event,
            batch=submission,
            send_result=send_result,
        )

    if _submission_needs_approval_notice(submission):
        schedule_submission_approval_notice(
            bot,
            service,
            event=approval_event,
            submission=submission,
            locale=locale,
            media_service=media_service,
        )
    await _finish_event(matcher)
