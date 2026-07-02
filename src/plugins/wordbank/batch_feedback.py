from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanEntry,
    TextBlock,
    deliver_message_plan,
)
from src.plugins.wordbank.handlers.approval import build_add_result_plan_entry
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import WordbankBatchAddResult


async def send_batch_add_feedback(
    bot: Bot,
    event: MessageEvent,
    *,
    batch: WordbankBatchAddResult,
    locale: LocaleCode,
    media_service: WordbankMediaService,
    source_kind: str,
    fallback_nickname: str,
) -> None:
    summary = tr(
        locale,
        "wordbank.batch_add.summary",
        total=batch.total,
        success=batch.success,
        failed=batch.failed,
    )
    await deliver_message_plan(
        bot,
        plan=DeliveryPlan(
            messages=(summary,),
            source_kind=source_kind,
        ),
        event=event,
    )
    detail_messages = []
    for item in batch.items:
        if item.ok and item.result is not None:
            detail_messages.append(
                await build_add_result_plan_entry(
                    item.result,
                    locale=locale,
                    media_service=media_service,
                )
            )
        else:
            detail_messages.append(
                MessagePlanEntry(
                    blocks=(
                        TextBlock(
                            tr(
                                locale,
                                "wordbank.batch_add.detail_failed",
                                index=item.index,
                                error=item.error
                                or tr(locale, "wordbank.batch_add.unknown_error"),
                            )
                        ),
                    )
                )
            )
    if detail_messages:
        await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=tuple(detail_messages),
                source_kind=source_kind,
                fallback_nickname=fallback_nickname,
                force_forward=True,
            ),
            event=event,
        )
