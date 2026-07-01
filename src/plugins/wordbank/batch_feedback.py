from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

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
        "已处理合并转发响应导入\n"
        f"总数: {batch.total}\n"
        f"成功: {batch.success}\n"
        f"失败: {batch.failed}"
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
                    f"序号 {item.index}\n"
                    + format_add_result(item.result, locale=locale)
                )
            )
        else:
            detail_messages.append(
                text_message(
                    f"序号 {item.index}\n提交失败\n原因: {item.error or '未知错误'}"
                )
            )
    if detail_messages:
        await send_custom_forward(
            bot,
            event,
            tuple(detail_messages),
            fallback_nickname=fallback_nickname,
        )
