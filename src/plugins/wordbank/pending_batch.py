from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_delivery import deliver_single_message, resolve_delivery_target
from src.lib.messages import empty_message, text_message
from src.lib.onebot_forward import send_custom_forward
from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers.approval import extract_sent_message_id
from src.plugins.wordbank.handlers.mutation import build_mutation_actor
from src.plugins.wordbank.handlers.parsers import actor_can_review, parse_search_args
from src.plugins.wordbank.handlers.rendering import render_shape_message
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService


def _response_item_id(item: WordbankSearchItem) -> int:
    if item.response_item_ids:
        return item.response_item_ids[0]
    return item.trigger_group_id


def _has_non_text_shape(shape: MessageShape | None) -> bool:
    if shape is None:
        return False
    return any(atom.kind != "text" for atom in shape.atoms)


def _build_pending_batch_summary(
    *,
    items: list[WordbankSearchItem] | tuple[WordbankSearchItem, ...],
    locale: LocaleCode,
    page: int,
    limit: int,
    has_more: bool,
) -> Message:
    lines = [
        tr(locale, "wordbank.approval.pending_title", page=page),
        tr(locale, "wordbank.approval.pending_batch_instruction"),
    ]
    for index, item in enumerate(items, start=1):
        lines.append(
            f"{index}. #{_response_item_id(item)} [{item.scope}] "
            f"{item.trigger_text} => {item.response_text}"
        )
    if has_more:
        lines.append(
            tr(
                locale,
                "wordbank.approval.pending_more",
                next_page=page + 1,
                limit=limit,
            )
        )
    return text_message("\n".join(lines))


async def _build_pending_detail_message(
    item: WordbankSearchItem,
    *,
    index: int,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
    message = empty_message()
    message += MessageSegment.text(
        tr(locale, "wordbank.batch.index", index=index)
        + "\n"
        + tr(
            locale,
            "wordbank.approval.pending_item",
            entry_id=_response_item_id(item),
            scope=item.scope,
            trigger_text=item.trigger_text,
            response_text=item.response_text,
            created_by=item.created_by,
        )
    )
    if _has_non_text_shape(item.trigger_shape):
        assert item.trigger_shape is not None
        message += MessageSegment.text(
            "\n" + tr(locale, "wordbank.group.trigger_label") + "\n"
        )
        message += await render_shape_message(
            item.trigger_shape,
            media_service,
            locale=locale,
        )
    if _has_non_text_shape(item.response_shape):
        assert item.response_shape is not None
        message += MessageSegment.text(
            "\n" + tr(locale, "wordbank.approval.response_label") + "\n"
        )
        message += await render_shape_message(
            item.response_shape,
            media_service,
            locale=locale,
        )
    return message


async def send_pending_entries_review(
    bot: Bot,
    event: MessageEvent,
    *,
    text: str,
    locale: LocaleCode,
    service: WordbankService,
    media_service: WordbankMediaService,
    source_kind: str,
    fallback_nickname: str,
) -> None:
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        await deliver_single_message(
            bot,
            target=resolve_delivery_target(event),
            message=text_message(tr(locale, "wordbank.approval.permission_denied")),
            source_kind=source_kind,
        )
        return

    parsed = parse_search_args(text)
    offset = (parsed.page - 1) * parsed.limit
    pending_items = await service.list_pending_entries(
        keyword=parsed.keyword,
        limit=parsed.limit + 1,
        offset=offset,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    )
    has_more = len(pending_items) > parsed.limit
    items = pending_items[: parsed.limit]
    if not items:
        await deliver_single_message(
            bot,
            target=resolve_delivery_target(event),
            message=text_message(
                tr(locale, "wordbank.approval.pending_empty", page=parsed.page)
            ),
            source_kind=source_kind,
        )
        return

    summary = _build_pending_batch_summary(
        items=items,
        locale=locale,
        page=parsed.page,
        limit=parsed.limit,
        has_more=has_more,
    )
    send_result = await deliver_single_message(
        bot,
        target=resolve_delivery_target(event),
        message=summary,
        source_kind=source_kind,
    )
    message_id = extract_sent_message_id(send_result)
    if message_id is not None:
        await service.record_message_ref(
            ref_kind="approval",
            message_id=message_id,
            trigger_group_id=items[0].trigger_group_id,
            response_item_id=_response_item_id(items[0]),
            group_id=str(getattr(event, "group_id", "") or ""),
            user_id=str(event.user_id),
            source_message_id=str(getattr(event, "message_id", "") or ""),
            context_type="pending_batch",
            message_type="approval_batch",
            group_ids=tuple(_response_item_id(item) for item in items),
        )

    detail_messages = [
        await _build_pending_detail_message(
            item,
            index=index,
            locale=locale,
            media_service=media_service,
        )
        for index, item in enumerate(items, start=1)
    ]
    await send_custom_forward(
        bot,
        event,
        detail_messages,
        fallback_nickname=fallback_nickname,
    )
