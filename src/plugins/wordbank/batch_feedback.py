from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_delivery import deliver_single_message, resolve_delivery_target
from src.lib.messages import text_message
from src.lib.onebot_forward import send_custom_forward
from src.plugins.wordbank.services import format_add_result
from src.plugins.wordbank.services.presentation import WordbankBatchAddResult


async def send_batch_add_feedback(
    bot: Bot,
    event: MessageEvent,
    *,
    batch: WordbankBatchAddResult,
    locale: LocaleCode,
    source_kind: str,
    fallback_nickname: str,
) -> None:
    summary = text_message(
        tr(
            locale,
            "wordbank.batch_add.summary",
            total=batch.total,
            success=batch.success,
            failed=batch.failed,
        )
    )
    await deliver_single_message(
        bot,
        target=resolve_delivery_target(event),
        message=summary,
        source_kind=source_kind,
    )
    detail_messages = []
    for item in batch.items:
        if item.ok and item.result is not None:
            detail_messages.append(
                text_message(
                    tr(locale, "wordbank.batch.index", index=item.index)
                    + "\n"
                    + format_add_result(item.result, locale=locale)
                )
            )
        else:
            detail_messages.append(
                text_message(
                    tr(
                        locale,
                        "wordbank.batch_add.detail_failed",
                        index=item.index,
                        error=item.error
                        or tr(locale, "wordbank.batch_add.unknown_error"),
                    )
                )
            )
    if detail_messages:
        await send_custom_forward(
            bot,
            event,
            tuple(detail_messages),
            fallback_nickname=fallback_nickname,
        )
