"""Wordbank command handlers."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.long_task import LongTaskRunner
from src.lib.message_plan import MessagePlanInput
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankSearchPage,
    WordbankSearchRequest,
)
from src.plugins.wordbank.handlers.parsers import (
    ParsedSearch,
    actor_can_review,
    localize_wordbank_error,
    parse_add_media_args,
    parse_guided_advanced_options,
    parse_guided_scope_choice,
    parse_guided_weight,
    parse_rank_period,
    parse_response_set_args,
    parse_response_weight_args,
    parse_search_args,
    parse_study_group_block_choice,
    parse_study_media_prefix,
    parse_study_mode_choice,
    parse_text_add_args,
    parse_trigger_probability_args,
    parse_trigger_set_args,
    split_add_pair,
)
from src.plugins.wordbank.handlers.search_cards import SearchCardQuery
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_image,
)
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankService,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import (
    RuleError,
    build_legacy_study_shortcut_rule,
    parse_legacy_study_text,
)
from src.plugins.wordbank.text_parsing import has_meaningful_text, split_command_text

from .group_detail_card_helpers import display_group_detail
from .media_helpers import (
    build_response_shape_from_message,
    build_shape_from_text_and_images,
    extract_message_suffix_by_plain_text,
    ingest_image_bytes_items,
    shape_from_response_parts,
    shape_from_trigger_text_value,
)
from .mutation import (
    ApprovalMutationOutcome,
    build_mutation_actor,
    handle_approve,
    handle_approve_result,
    handle_delete,
    handle_reject,
    handle_reject_result,
    handle_restore,
)
from .mutation import (
    handle_response_weight_update as _handle_response_weight_update,
)
from .mutation import (
    handle_trigger_probability_update as _handle_trigger_probability_update,
)
from .rendering import (
    GROUP_PAGE_SIZE,
    build_creator_leaderboard_card_plan_entry,
    build_group_detail_page_message_plan_entry,
    build_pending_items_plan_entry,
    build_search_results_card_plan_entry,
)

ADD_ALIASES = {"add", "添加", "学习"}
SEARCH_ALIASES = {"search", "find", "查询", "搜索"}
GROUP_ALIASES = {"group", "grp", "展开"}
DELETE_ALIASES = {"delete", "del", "remove", "删除"}
RESTORE_ALIASES = {"restore", "恢复"}
APPROVE_ALIASES = {"approve", "pass", "通过", "审核通过"}
REJECT_ALIASES = {"reject", "deny", "拒绝", "驳回"}
PENDING_ALIASES = {"pending", "review", "待审", "待审核", "审核列表"}
RANK_ALIASES = {"rank", "排行", "榜", "苦瓜榜"}
TRIGGER_ALIASES = {"trigger", "触发", "触发词"}
RESPONSE_ALIASES = {"response", "响应", "响应词"}
SET_ALIASES = {"set", "edit", "修改"}
PROBABILITY_ALIASES = {"prob", "probability", "概率"}
WEIGHT_ALIASES = {"weight", "权重"}


def _default_i18n_text(key: MessageKey, **params: object) -> str:
    return tr("zh-CN", key, **params)


WORD_BANK_LABEL_KEYS: dict[str, MessageKey] = {
    "scope": "wordbank.label.scope",
    "probability": "wordbank.label.probability",
    "weight": "wordbank.label.weight",
    "role": "wordbank.label.role",
    "page": "wordbank.label.page",
    "limit": "wordbank.label.limit",
    "search_field": "wordbank.label.search_field",
    "creator_id": "wordbank.label.creator_id",
}


def _label(name: str) -> str:
    return tr("zh-CN", WORD_BANK_LABEL_KEYS[name])


def _split_command(text: str) -> tuple[str, str]:
    return split_command_text(text)


def combine_response_shape_with_image(
    response_shape: MessageShape,
    *,
    image_id: int,
) -> MessageShape:
    return combine_shapes(response_shape, shape_from_image(image_id))


async def build_response_shape_from_command_message(
    *,
    response_text: str,
    message: Message,
    media_service: WordbankMediaService | None = None,
) -> MessageShape:
    response_message = extract_message_suffix_by_plain_text(message, response_text)
    if response_message.extract_plain_text() != response_text:
        return shape_from_response_parts(response_text)
    shape = await build_response_shape_from_message(
        media_service,
        response_message,
    )
    if shape.is_empty():
        return shape_from_response_parts(response_text)
    return shape


async def handle_add_text(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> WordbankAddResult:
    result = await handle_add_text_result(service, event=event, text=text)
    return result


async def handle_add_text_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
) -> WordbankAddResult:
    parsed = parse_text_add_args(text)
    response_shape = await build_response_shape_from_command_message(
        response_text=parsed.response_text,
        message=event.message,
    )
    return await service.add_message_entry(
        trigger_shape=shape_from_trigger_text_value(parsed.trigger_text),
        response_shape=response_shape,
        raw_rule=parsed.raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=isinstance(event, GroupMessageEvent),
    )


async def handle_add_with_media(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    locale: LocaleCode,
) -> WordbankAddResult:
    result = await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_bytes,
    )
    return result


async def handle_add_with_media_result(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    task: LongTaskRunner | None = None,
) -> WordbankAddResult:
    if image_bytes is None:
        return await handle_add_text_result(service, event=event, text=text)

    parsed = parse_add_media_args(text)
    pair = parsed.pair
    is_group = isinstance(event, GroupMessageEvent)
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.user_id)

    if pair is None:
        trigger_text = parsed.source
        if not has_meaningful_text(trigger_text):
            raise RuleError(
                _default_i18n_text("wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        image_id = (
            await ingest_image_bytes_items(
                media_service,
                (image_bytes,),
                task=task,
            )
        )[0]
        return await service.add_message_entry(
            trigger_shape=shape_from_trigger_text_value(trigger_text),
            response_shape=shape_from_image(image_id),
            raw_rule=parsed.raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    trigger_text, response_text = pair
    if trigger_text:
        image_id = (
            await ingest_image_bytes_items(
                media_service,
                (image_bytes,),
                task=task,
            )
        )[0]
        response_shape = await build_response_shape_from_command_message(
            response_text=response_text,
            message=event.message,
        )
        if response_shape == shape_from_response_parts(response_text):
            response_shape = shape_from_response_parts(
                response_text,
                image_id=image_id,
            )
        else:
            response_shape = combine_response_shape_with_image(
                response_shape,
                image_id=image_id,
            )
        return await service.add_message_entry(
            trigger_shape=shape_from_trigger_text_value(trigger_text),
            response_shape=response_shape,
            raw_rule=parsed.raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    if response_text:
        image_id = (
            await ingest_image_bytes_items(
                media_service,
                (image_bytes,),
                task=task,
            )
        )[0]
        response_shape = await build_response_shape_from_command_message(
            response_text=response_text,
            message=event.message,
        )
        return await service.add_message_entry(
            trigger_shape=shape_from_image(image_id),
            response_shape=response_shape,
            raw_rule=parsed.raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    raise RuleError(
        _default_i18n_text("wordbank.error.add_pair_required"),
        key="wordbank.error.add_pair_required",
    )


async def handle_guided_add_shape_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_shape: MessageShape,
    response_shape: MessageShape,
    scope_text: str,
    advanced_text: str = "",
    response_mode: str = "normal",
    forward_source_message_id: str | None = None,
    forward_node_count: int = 0,
) -> WordbankAddResult:
    is_group = isinstance(event, GroupMessageEvent)
    scope = parse_guided_scope_choice(scope_text, is_group=is_group)
    advanced = parse_guided_advanced_options(advanced_text)
    raw_rule = {"scope": scope, **advanced.raw_rule}
    return await service.add_message_entry(
        trigger_shape=trigger_shape,
        response_shape=response_shape,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
        response_mode=response_mode,
        forward_source_message_id=forward_source_message_id,
        forward_node_count=forward_node_count,
    )


async def handle_study_shortcut(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> WordbankAddResult:
    result = await handle_study_shortcut_result(service, event=event, text=text)
    return result


async def handle_study_shortcut_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
) -> WordbankAddResult:
    is_group = isinstance(event, GroupMessageEvent)
    trigger, response, raw_rule = parse_legacy_study_text(text, is_group=is_group)
    response_shape = await build_response_shape_from_command_message(
        response_text=response,
        message=event.message,
    )
    return await service.add_message_entry(
        trigger_shape=shape_from_trigger_text_value(trigger),
        response_shape=response_shape,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )


async def handle_study_with_media(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    locale: LocaleCode,
) -> WordbankAddResult:
    result = await handle_study_with_media_result(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_bytes,
    )
    return result


async def handle_study_with_media_result(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    extra_image_bytes: Sequence[bytes] = (),
    task: LongTaskRunner | None = None,
) -> WordbankAddResult:
    image_items = ((image_bytes,) if image_bytes is not None else ()) + tuple(
        extra_image_bytes
    )
    if not image_items:
        return await handle_study_shortcut_result(
            service,
            event=event,
            text=text,
        )

    is_group = isinstance(event, GroupMessageEvent)
    parsed = parse_study_media_prefix(text, is_group=is_group)
    if parsed.raw_rule:
        return await handle_study_media_with_rule_result(
            service,
            media_service,
            event=event,
            source=parsed.source,
            raw_rule=parsed.raw_rule,
            image_bytes=image_items,
            task=task,
        )

    return await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_items[0],
        task=task,
    )


async def handle_study_media_with_rule_result(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    source: str,
    raw_rule: dict[str, Any],
    image_bytes: Sequence[bytes],
    task: LongTaskRunner | None = None,
) -> WordbankAddResult:
    if not image_bytes:
        return await handle_study_shortcut_result(
            service,
            event=event,
            text=source,
        )

    if not has_meaningful_text(source):
        source = ""

    is_group = isinstance(event, GroupMessageEvent)
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.user_id)
    pair = split_add_pair(source)

    if pair is None and not source:
        if len(image_bytes) < 2:
            raise RuleError(
                _default_i18n_text("wordbank.error.study_pair_required"),
                key="wordbank.error.study_pair_required",
            )
        trigger_image_id, response_image_id = await ingest_image_bytes_items(
            media_service,
            tuple(image_bytes[:2]),
            task=task,
        )
        return await service.add_message_entry(
            trigger_shape=shape_from_image(trigger_image_id),
            response_shape=shape_from_image(response_image_id),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    if pair is None:
        response_image_id = (
            await ingest_image_bytes_items(
                media_service,
                (image_bytes[0],),
                task=task,
            )
        )[0]
        return await service.add_message_entry(
            trigger_shape=shape_from_trigger_text_value(source),
            response_shape=shape_from_image(response_image_id),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    trigger_text, response_text = pair
    if trigger_text:
        response_image_id = (
            await ingest_image_bytes_items(
                media_service,
                (image_bytes[0],),
                task=task,
            )
        )[0]
        response_shape = await build_response_shape_from_command_message(
            response_text=response_text,
            message=event.message,
        )
        if response_shape == shape_from_response_parts(response_text):
            response_shape = shape_from_response_parts(
                response_text,
                image_id=response_image_id,
            )
        else:
            response_shape = combine_response_shape_with_image(
                response_shape,
                image_id=response_image_id,
            )
        return await service.add_message_entry(
            trigger_shape=shape_from_trigger_text_value(trigger_text),
            response_shape=response_shape,
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    image_ids = await ingest_image_bytes_items(
        media_service,
        tuple(image_bytes[:2]),
        task=task,
    )
    trigger_image_id = image_ids[0]
    response_image_id: int | None = None
    if len(image_ids) >= 2:
        response_image_id = image_ids[1]
    response_shape = await build_response_shape_from_command_message(
        response_text=response_text,
        message=event.message,
    )
    if response_shape == shape_from_response_parts(response_text):
        response_shape = shape_from_response_parts(
            response_text,
            image_id=response_image_id,
        )
    elif response_image_id is not None:
        response_shape = combine_response_shape_with_image(
            response_shape,
            image_id=response_image_id,
        )
    return await service.add_message_entry(
        trigger_shape=shape_from_image(trigger_image_id),
        response_shape=response_shape,
        raw_rule=raw_rule,
        group_id=group_id,
        user_id=user_id,
        is_group=is_group,
    )


async def handle_guided_study_shape_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    trig_mode_text: str,
    group_block_text: str,
    trigger_shape: MessageShape,
    response_shape: MessageShape,
    weight_text: str,
) -> WordbankAddResult:
    trig_mode = parse_study_mode_choice(trig_mode_text)
    group_block = parse_study_group_block_choice(group_block_text)
    weight = parse_guided_weight(weight_text)
    is_group = isinstance(event, GroupMessageEvent)
    raw_rule = build_legacy_study_shortcut_rule(
        trig_mode,
        group_block,
        is_group=is_group,
    )
    raw_rule["weight"] = weight
    return await service.add_message_entry(
        trigger_shape=trigger_shape,
        response_shape=response_shape,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )


async def handle_search(
    service: WordbankService,
    *,
    keyword: str,
    image_scores: dict[int, float] | None = None,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanInput:
    parsed = parse_search_args(keyword)
    page = await execute_search_page(
        service,
        parsed=parsed,
        image_scores=image_scores,
    )
    return await render_search_page_message(
        page,
        parsed=parsed,
        locale=locale,
        has_image=image_scores is not None,
        media_service=media_service,
    )


async def execute_search_page(
    service: WordbankService,
    *,
    parsed: ParsedSearch,
    image_scores: dict[int, float] | None = None,
) -> WordbankSearchPage:
    offset = (parsed.page - 1) * parsed.limit
    return await service.search_page(
        WordbankSearchRequest(
            keyword=parsed.keyword,
            field=parsed.field,
            creator_id=parsed.creator_id,
            has_image=image_scores is not None,
            image_scores=dict(image_scores or {}),
        ),
        limit=parsed.limit,
        offset=offset,
    )


async def render_search_page_message(
    page: WordbankSearchPage,
    *,
    parsed: ParsedSearch,
    locale: LocaleCode,
    has_image: bool,
    media_service: WordbankMediaService,
) -> MessagePlanInput:
    return await build_search_results_card_plan_entry(
        items=page.items,
        query=SearchCardQuery(
            keyword=parsed.keyword,
            field=parsed.field,
            creator_id=parsed.creator_id,
            has_image=has_image,
            page=parsed.page,
            total_count=page.total_count,
            limit=parsed.limit,
        ),
        locale=locale,
        media_service=media_service,
    )


async def build_group_detail_message(
    service: WordbankService,
    *,
    trigger_group_id: int,
    page: int,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> tuple[MessagePlanInput, WordbankGroupDetail, int]:
    detail = await service.get_group_detail(trigger_group_id)
    if detail is None:
        raise RuleError(
            _default_i18n_text(
                "wordbank.group.not_found",
                group_id=trigger_group_id,
            ),
            key="wordbank.group.not_found",
            group_id=trigger_group_id,
        )
    detail = display_group_detail(detail)
    total_pages = max(1, math.ceil(len(detail.responses) / max(GROUP_PAGE_SIZE, 1)))
    if page > total_pages:
        raise RuleError(
            _default_i18n_text(
                "wordbank.error.guided_search_page_out_of_range",
            ),
            key="wordbank.error.guided_search_page_out_of_range",
            total_pages=total_pages,
        )
    message, total_pages = await build_group_detail_page_message_plan_entry(
        detail=detail,
        page=page,
        locale=locale,
        media_service=media_service,
    )
    return message, detail, total_pages


async def handle_pending_entries(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> MessagePlanInput:
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return tr(locale, "wordbank.approval.permission_denied")
    parsed = parse_search_args(text)
    offset = (parsed.page - 1) * parsed.limit
    items = await service.list_pending_entries(
        keyword=parsed.keyword,
        limit=parsed.limit + 1,
        offset=offset,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    )
    has_more = len(items) > parsed.limit
    return await build_pending_items_plan_entry(
        items=items[: parsed.limit],
        locale=locale,
        media_service=media_service,
        page=parsed.page,
        limit=parsed.limit,
        has_more=has_more,
    )


async def handle_creator_leaderboard(
    service: WordbankService,
    *,
    text: str,
    locale: LocaleCode,
) -> MessagePlanInput:
    period = parse_rank_period(text)
    data = await service.build_creator_leaderboard(period=period, locale=locale)
    return await build_creator_leaderboard_card_plan_entry(
        data=data,
        locale=locale,
    )


async def handle_trigger_probability_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    probability: float,
    locale: LocaleCode,
) -> str:
    return await _handle_trigger_probability_update(
        service,
        event=event,
        trigger_group_id=trigger_group_id,
        probability=probability,
        locale=locale,
    )


async def handle_trigger_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    text: str,
    raw_message: Message,
    locale: LocaleCode,
) -> str:
    actor = build_mutation_actor(event)
    trigger_shape = await build_shape_from_text_and_images(
        media_service,
        text=text,
        message=raw_message,
        parse_trigger_text=True,
    )
    if await service.update_trigger_content(
        trigger_group_id,
        trigger_shape=trigger_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(
            locale,
            "wordbank.mutation.trigger_content_updated",
            group_id=trigger_group_id,
        )
    return tr(locale, "wordbank.mutation.trigger_not_found", group_id=trigger_group_id)


async def handle_response_weight_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id: int,
    weight: int,
    locale: LocaleCode,
) -> str:
    return await _handle_response_weight_update(
        service,
        event=event,
        response_item_id=response_item_id,
        weight=weight,
        locale=locale,
    )


async def handle_response_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    response_item_id: int,
    text: str,
    raw_message: Message,
    locale: LocaleCode,
) -> str:
    _ = locale
    actor = build_mutation_actor(event)
    response_shape = await build_shape_from_text_and_images(
        media_service,
        text=text,
        message=raw_message,
    )
    if await service.update_response_content(
        response_item_id,
        response_shape=response_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(
            locale,
            "wordbank.mutation.response_content_updated",
            entry_id=response_item_id,
        )
    return tr(locale, "wordbank.mutation.response_not_found", entry_id=response_item_id)


async def handle_trigger_command(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    raw_message: Message | None,
    locale: LocaleCode,
) -> str:
    action, rest = _split_command(text)
    if action in PROBABILITY_ALIASES:
        parsed = parse_trigger_probability_args(rest)
        return await handle_trigger_probability_update(
            service,
            event=event,
            trigger_group_id=parsed.trigger_group_id,
            probability=parsed.probability,
            locale=locale,
        )
    if action in SET_ALIASES:
        if raw_message is None:
            raise RuntimeError("wordbank raw message is required for trigger set")
        parsed = parse_trigger_set_args(rest)
        return await handle_trigger_content_update(
            service,
            media_service,
            event=event,
            trigger_group_id=parsed.trigger_group_id,
            text=parsed.text,
            raw_message=raw_message,
            locale=locale,
        )
    raise RuleError(
        _default_i18n_text(
            "wordbank.error.unknown_subcommand",
            action=f"trigger {action}".strip(),
            help=wordbank_help_text(locale),
        ),
        key="wordbank.error.unknown_subcommand",
        action=f"trigger {action}".strip(),
        help=wordbank_help_text(locale),
    )


async def handle_response_command(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    raw_message: Message | None,
    locale: LocaleCode,
) -> str:
    action, rest = _split_command(text)
    if action in WEIGHT_ALIASES:
        parsed = parse_response_weight_args(rest)
        return await handle_response_weight_update(
            service,
            event=event,
            response_item_id=parsed.response_item_id,
            weight=parsed.weight,
            locale=locale,
        )
    if action in SET_ALIASES:
        if raw_message is None:
            raise RuntimeError("wordbank raw message is required for response set")
        parsed = parse_response_set_args(rest)
        return await handle_response_content_update(
            service,
            media_service,
            event=event,
            response_item_id=parsed.response_item_id,
            text=parsed.text,
            raw_message=raw_message,
            locale=locale,
        )
    raise RuleError(
        _default_i18n_text(
            "wordbank.error.unknown_subcommand",
            action=f"response {action}".strip(),
            help=wordbank_help_text(locale),
        ),
        key="wordbank.error.unknown_subcommand",
        action=f"response {action}".strip(),
        help=wordbank_help_text(locale),
    )


def wordbank_help_text(locale: LocaleCode = "zh-CN") -> str:
    return tr(locale, "wordbank.help")


async def dispatch_wordbank_command(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
    raw_message: Message | None = None,
    search_image_scores: dict[int, float] | None = None,
    media_service: WordbankMediaService | None = None,
) -> MessagePlanInput:
    action, rest = _split_command(text)
    if not action or action in {"help", "帮助"}:
        return wordbank_help_text(locale)
    if action in ADD_ALIASES:
        raise RuntimeError(
            "add commands must be handled by the unified submission flow"
        )
    if action in SEARCH_ALIASES:
        if media_service is None:
            raise RuntimeError(
                "wordbank media service is required for search rendering"
            )
        return await handle_search(
            service,
            keyword=rest,
            image_scores=search_image_scores,
            locale=locale,
            media_service=media_service,
        )
    if action in PENDING_ALIASES:
        if media_service is None:
            raise RuntimeError(
                "wordbank media service is required for pending rendering"
            )
        return await handle_pending_entries(
            service,
            event=event,
            text=rest,
            locale=locale,
            media_service=media_service,
        )
    if action in RANK_ALIASES:
        return await handle_creator_leaderboard(
            service,
            text=rest,
            locale=locale,
        )
    if action in APPROVE_ALIASES:
        return await handle_approve(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in REJECT_ALIASES:
        return await handle_reject(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in DELETE_ALIASES:
        return await handle_delete(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in RESTORE_ALIASES:
        return await handle_restore(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in TRIGGER_ALIASES:
        if media_service is None:
            raise RuntimeError("wordbank media service is required for trigger editing")
        return await handle_trigger_command(
            service,
            media_service,
            event=event,
            text=rest,
            raw_message=raw_message,
            locale=locale,
        )
    if action in RESPONSE_ALIASES:
        if media_service is None:
            raise RuntimeError(
                "wordbank media service is required for response editing"
            )
        return await handle_response_command(
            service,
            media_service,
            event=event,
            text=rest,
            raw_message=raw_message,
            locale=locale,
        )
    return tr(
        locale,
        "wordbank.error.unknown_subcommand",
        action=action,
        help=wordbank_help_text(locale),
    )


async def dispatch_wordbank_command_with_outcome(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
    raw_message: Message | None = None,
    search_image_scores: dict[int, float] | None = None,
    media_service: WordbankMediaService | None = None,
) -> tuple[MessagePlanInput, ApprovalMutationOutcome | None]:
    action, rest = _split_command(text)
    if action in APPROVE_ALIASES:
        outcome = await handle_approve_result(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
        return outcome.message, outcome
    if action in REJECT_ALIASES:
        outcome = await handle_reject_result(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
        return outcome.message, outcome
    return (
        await dispatch_wordbank_command(
            service,
            event=event,
            text=text,
            locale=locale,
            raw_message=raw_message,
            search_image_scores=search_image_scores,
            media_service=media_service,
        ),
        None,
    )


def localize_command_error(exc: Exception, locale: LocaleCode) -> str:
    return localize_wordbank_error(exc, locale)
