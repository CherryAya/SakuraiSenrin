"""Guided add/search flow helpers for wordbank plugin."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.typing import T_State

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import clear_interaction_errors, reject_or_abort_on_error
from src.lib.interactive_recall import (
    INTERACTION_ROOT_MESSAGE_ID,
    INTERACTION_SESSION_KEY,
    cancel_state_resources,
    get_interaction_session_key,
    register_recall_checkpoint,
    register_root_message,
)
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MatcherProgressSink,
)
from src.lib.message_plan import (
    DeliveryPlan,
    deliver_message_plan,
    finish_with_message,
    pause_with_message,
)
from src.logger import logger
from src.plugins.wordbank.debug import (
    describe_batch_errors,
    describe_message_segments,
    describe_shape,
)
from src.plugins.wordbank.forward_batch import (
    build_response_input_payload,
    is_forward_input,
)
from src.plugins.wordbank.handlers import (
    build_message_shape_from_message,
    handle_guided_add_shape_result,
)
from src.plugins.wordbank.handlers.commands import (
    ParsedSearch,
    _default_i18n_text,
)
from src.plugins.wordbank.handlers.media_helpers import (
    build_response_shape_from_message,
    shape_from_trigger_text_value,
)
from src.plugins.wordbank.handlers.parsers import (
    parse_search_session_command,
)
from src.plugins.wordbank.handlers.reply import parse_view_reply_for_search_result
from src.plugins.wordbank.handlers.submission import SubmissionHandler
from src.plugins.wordbank.message_model import MessageShape
from src.plugins.wordbank.services.rules import RuleError
from src.plugins.wordbank.text_parsing import (
    has_meaningful_text,
    normalize_cq_plain_text,
)

GUIDED_MAX_ERRORS = 3
WORDBANK_GUIDED_STEP_TRIGGER = 1
WORDBANK_GUIDED_STEP_RESPONSE = 2
WORDBANK_GUIDED_STEP_SCOPE = 3
WORDBANK_GUIDED_STEP_ADVANCED = 4
WORDBANK_GUIDED_SEARCH_STAGE_DIMENSIONS = "search_dimensions"
WORDBANK_GUIDED_SEARCH_STAGE_QUERY = "search_query"
WORDBANK_GUIDED_SEARCH_STAGE_CREATOR = "search_creator"
WORDBANK_GUIDED_SEARCH_STAGE_PAGE = "search_page"
WORDBANK_GUIDED_RECALL_PENDING_KEYS = (
    "wordbank_guided_trigger_shape",
    "wordbank_guided_response_shape",
    "wordbank_guided_response_forward_pending",
    "wordbank_guided_response_forward_event",
    "wordbank_guided_response_forward_source_message_id",
    "wordbank_guided_response_forward_messages",
    "wordbank_guided_response_forward_node_count",
    "wordbank_guided_response_split_shapes",
    "wordbank_guided_scope",
    "wordbank_guided_search_keyword",
    "wordbank_guided_search_field",
    "wordbank_guided_search_creator_id",
    "wordbank_guided_search_image_scores",
    "wordbank_guided_search_group_ids",
    "wordbank_guided_search_delete_target_ids",
)
PRIVATE_SCOPE_PROMPT = (
    "请选择生效范围：\n"
    "1. 仅自己（默认）\n"
    "2. 全局响应\n"
    "输入 revoke / recall / exit 可取消本次操作。"
)


def _resolve_guided_bot(bot: Bot | None, matcher: Matcher) -> Bot | None:
    if bot is not None and hasattr(bot, "call_api") and hasattr(bot, "self_id"):
        return bot
    fallback = getattr(matcher, "bot", None)
    if (
        fallback is not None
        and hasattr(fallback, "call_api")
        and hasattr(
            fallback,
            "self_id",
        )
    ):
        return fallback
    return None


def _require_guided_bot(bot: Bot | None, matcher: Matcher) -> Bot:
    resolved = _resolve_guided_bot(bot, matcher)
    if resolved is None:
        raise RuntimeError("wordbank guided flow requires a delivery-capable bot")
    return resolved


def state_message_shape(state: Mapping[str, Any], key: str) -> MessageShape | None:
    value = state.get(key)
    return value if isinstance(value, MessageShape) else None


def _guided_state_keys(state: Mapping[str, Any]) -> list[str]:
    return sorted(str(key) for key in state.keys() if str(key).startswith("wordbank_"))


def _guided_forward_response_event(state: Mapping[str, Any]) -> MessageEvent | None:
    value = state.get("wordbank_guided_response_forward_event")
    return value if isinstance(value, MessageEvent) else None


def _guided_submission_source_event(state: Mapping[str, Any]) -> MessageEvent | None:
    value = state.get("wordbank_guided_submission_source_event")
    return value if isinstance(value, MessageEvent) else None


async def reject_guided_error(
    matcher: Matcher,
    state: T_State,
    locale: LocaleCode,
    message: Any,
) -> None:
    await reject_or_abort_on_error(
        matcher,
        state,
        message,
        max_errors=GUIDED_MAX_ERRORS,
        abort_message=tr(locale, "interaction.too_many_errors"),
    )


def wordbank_guided_locale(state: Mapping[str, Any]) -> LocaleCode:
    locale = state.get("wordbank_locale", "zh-CN")
    return locale if locale in {"zh-CN", "lzh", "x-meme"} else "zh-CN"


def copy_guided_state(
    state: Mapping[str, Any],
    *,
    keep_keys: tuple[str, ...],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key, value in state.items():
        if key.startswith("__nonebug"):
            snapshot[key] = value
    session_key = get_interaction_session_key(state)
    if session_key is not None:
        snapshot[INTERACTION_SESSION_KEY] = session_key
    if "wordbank_locale" in state:
        snapshot["wordbank_locale"] = state["wordbank_locale"]
    if INTERACTION_ROOT_MESSAGE_ID in state:
        snapshot[INTERACTION_ROOT_MESSAGE_ID] = state[INTERACTION_ROOT_MESSAGE_ID]
    if "wordbank_guided_submission_source_event" in state:
        snapshot["wordbank_guided_submission_source_event"] = state[
            "wordbank_guided_submission_source_event"
        ]
    for key in keep_keys:
        if key in state:
            snapshot[key] = state[key]
    clear_interaction_errors(snapshot)
    return snapshot


def guided_response_state_keys(state: Mapping[str, Any]) -> tuple[str, ...]:
    keys: list[str] = []
    for key in (
        "wordbank_guided_response_shape",
        "wordbank_guided_response_split_shapes",
        "wordbank_guided_response_forward_pending",
        "wordbank_guided_response_forward_event",
        "wordbank_guided_submission_source_event",
        "wordbank_guided_response_forward_source_message_id",
        "wordbank_guided_response_forward_messages",
        "wordbank_guided_response_forward_node_count",
    ):
        if key in state:
            keys.append(key)
    return tuple(keys)


def guided_prompt_for_step(
    locale: LocaleCode,
    step_index: int,
    *,
    is_group: bool,
) -> str:
    prompt_by_step = {
        WORDBANK_GUIDED_STEP_TRIGGER: tr(locale, "wordbank.guided.add.trigger_prompt"),
        WORDBANK_GUIDED_STEP_RESPONSE: tr(
            locale,
            "wordbank.guided.add.response_prompt",
        ),
        WORDBANK_GUIDED_STEP_SCOPE: guided_scope_prompt(
            locale=locale,
            is_group=is_group,
        ),
        WORDBANK_GUIDED_STEP_ADVANCED: tr(
            locale,
            "wordbank.guided.add.advanced_prompt",
        ),
    }
    return prompt_by_step[step_index]


def guided_scope_prompt(*, locale: LocaleCode, is_group: bool) -> str:
    if is_group:
        return tr(locale, "wordbank.guided.add.scope_prompt")
    _ = locale
    return PRIVATE_SCOPE_PROMPT


def register_guided_checkpoint(
    state: T_State,
    event: MessageEvent,
    *,
    step_index: int,
    locale: LocaleCode,
    snapshot: Mapping[str, Any],
    cleanup_keys: tuple[str, ...] = (),
) -> None:
    register_recall_checkpoint(
        state,
        message_id=getattr(event, "message_id", ""),
        step_index=step_index,
        prompt=guided_prompt_for_step(
            locale,
            step_index,
            is_group=bool(getattr(event, "group_id", "")),
        ),
        state_snapshot=snapshot,
        cleanup_keys=cleanup_keys,
    )


async def cancel_guided_resources(
    state: Mapping[str, Any],
    cleanup_keys: tuple[str, ...] = WORDBANK_GUIDED_RECALL_PENDING_KEYS,
) -> None:
    await cancel_state_resources(
        state,
        cleanup_keys,
        cleaners={},
    )


async def record_guided_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    *,
    media_service: Any,
) -> None:
    plain_text = event.message.extract_plain_text()
    if has_meaningful_text(plain_text) and len(event.message) == 1:
        shape = shape_from_trigger_text_value(plain_text)
    else:
        long_task = LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.guided.trigger_shape",
                source_kind="wordbank_guided",
                prompt=tr(locale, "wordbank.add.processing_with_media"),
                threshold_ms=800,
            ),
            sink=CompositeProgressSink(
                LoggerProgressSink(),
                MatcherProgressSink(matcher),
            ),
        )
        async with long_task:
            shape = await build_message_shape_from_message(
                media_service,
                event.message,
                task=long_task,
            )
    if shape.is_empty():
        await reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = wordbank_guided_locale(state)
    snapshot = copy_guided_state(state, keep_keys=())
    state["wordbank_guided_trigger_shape"] = shape
    register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_TRIGGER,
        locale=locale,
        snapshot=snapshot,
    )
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.add.response_prompt"),
    )


async def record_guided_response(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    *,
    media_service: Any,
) -> None:
    if is_forward_input(event):
        state["wordbank_guided_response_forward_pending"] = True
        state["wordbank_guided_response_forward_event"] = event
        logger.debug(
            "[Wordbank][guided] forward response detected | "
            f"{describe_message_segments(event.message)}"
        )
        clear_interaction_errors(state)
        locale = wordbank_guided_locale(state)
        register_guided_checkpoint(
            state,
            event,
            step_index=WORDBANK_GUIDED_STEP_RESPONSE,
            locale=locale,
            snapshot=copy_guided_state(
                state,
                keep_keys=("wordbank_guided_trigger_shape",),
            ),
        )
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.forward_response_prompt"),
        )
        return
    long_task = LongTaskRunner(
        LongTaskSpec(
            task_name="wordbank.guided.response_shape",
            source_kind="wordbank_guided",
            prompt=tr(locale, "wordbank.add.processing_with_media"),
            threshold_ms=800,
        ),
        sink=CompositeProgressSink(
            LoggerProgressSink(),
            MatcherProgressSink(matcher),
        ),
    )
    async with long_task:
        shape = await build_response_shape_from_message(
            media_service,
            event.message,
            task=long_task,
        )
    if shape.is_empty():
        await reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = wordbank_guided_locale(state)
    snapshot = copy_guided_state(
        state,
        keep_keys=("wordbank_guided_trigger_shape",),
    )
    state["wordbank_guided_response_shape"] = shape
    state["wordbank_guided_submission_source_event"] = event
    register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_RESPONSE,
        locale=locale,
        snapshot=snapshot,
    )
    await pause_with_message(
        matcher,
        message=guided_scope_prompt(
            locale=locale,
            is_group=bool(getattr(event, "group_id", "")),
        ),
    )


async def record_guided_forward_response_choice(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    *,
    media_service: Any,
    bot: Bot,
) -> None:
    if not bool(state.get("wordbank_guided_response_forward_pending", False)):
        return
    choice = event.message.extract_plain_text().strip().lower()
    state_keys = _guided_state_keys(state)
    logger.debug(
        "[Wordbank][guided] forward response choice | "
        f"choice={choice or '-'} state_keys={state_keys}"
    )
    response_event = _guided_forward_response_event(state)
    if response_event is None:
        logger.debug(
            "[Wordbank][guided] forward response choice missing response_event | "
            f"choice={choice or '-'}"
        )
        await reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.forward_message_not_found"),
        )
        return
    if choice in {"1", "whole", "整体"}:
        state.pop("wordbank_guided_response_forward_pending", None)
        long_task = LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.guided.forward_response_whole",
                source_kind="wordbank_guided",
                prompt=tr(locale, "wordbank.add.processing_with_media"),
                threshold_ms=800,
            ),
            sink=CompositeProgressSink(
                LoggerProgressSink(),
                MatcherProgressSink(matcher),
            ),
        )
        async with long_task:
            payload = await build_response_input_payload(
                bot,
                response_event,
                media_service=media_service,
                task=long_task,
            )
        if payload.input_kind != "forward":
            await reject_guided_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.forward_message_not_found"),
            )
            return
        state["wordbank_guided_response_shape"] = payload.whole_shape
        state["wordbank_guided_submission_source_event"] = response_event
        state["wordbank_guided_response_forward_source_message_id"] = (
            payload.source_message_id or ""
        )
        state["wordbank_guided_response_forward_messages"] = payload.messages
        state["wordbank_guided_response_forward_node_count"] = len(payload.messages)
        state.pop("wordbank_guided_response_forward_event", None)
        whole_description = describe_shape(payload.whole_shape)
        logger.debug(
            "[Wordbank][guided] forward response imported whole | "
            f"source_message_id={payload.source_message_id or '-'} "
            f"node_count={len(payload.split_shapes)} whole={whole_description}"
        )
        clear_interaction_errors(state)
        await pause_with_message(
            matcher,
            message=guided_scope_prompt(
                locale=locale,
                is_group=bool(getattr(event, "group_id", "")),
            ),
        )
        return
    if choice in {"2", "split", "拆开"}:
        state.pop("wordbank_guided_response_forward_pending", None)
        long_task = LongTaskRunner(
            LongTaskSpec(
                task_name="wordbank.guided.forward_response_split",
                source_kind="wordbank_guided",
                prompt=tr(locale, "wordbank.add.processing_with_media"),
                threshold_ms=800,
            ),
            sink=CompositeProgressSink(
                LoggerProgressSink(),
                MatcherProgressSink(matcher),
            ),
        )
        async with long_task:
            payload = await build_response_input_payload(
                bot,
                response_event,
                media_service=media_service,
                task=long_task,
            )
        if payload.input_kind != "forward":
            await reject_guided_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.forward_message_not_found"),
            )
            return
        state["wordbank_guided_response_split_shapes"] = payload.split_shapes
        state["wordbank_guided_submission_source_event"] = response_event
        state["wordbank_guided_response_forward_source_message_id"] = (
            payload.source_message_id or ""
        )
        state["wordbank_guided_response_forward_node_count"] = len(payload.messages)
        state.pop("wordbank_guided_response_forward_event", None)
        first_shape = payload.split_shapes[0] if payload.split_shapes else None
        logger.debug(
            "[Wordbank][guided] forward response imported split | "
            f"source_message_id={payload.source_message_id or '-'} "
            f"node_count={len(payload.split_shapes)} "
            f"split_count={len(payload.split_shapes)} "
            f"first={describe_shape(first_shape)}"
        )
        clear_interaction_errors(state)
        await pause_with_message(
            matcher,
            message=guided_scope_prompt(
                locale=locale,
                is_group=bool(getattr(event, "group_id", "")),
            ),
        )
        return
    await reject_guided_error(
        matcher,
        state,
        locale,
        tr(locale, "wordbank.error.forward_response_choice_invalid"),
    )


async def start_guided_add_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
    *,
    media_service: Any,
    initialize_plugin: Any,
) -> None:
    await initialize_plugin()
    state["wordbank_locale"] = locale
    shape = await build_message_shape_from_message(media_service, arg)
    if shape.is_empty():
        await start_guided_add(
            matcher,
            event,
            state,
            locale,
            initialize_plugin=initialize_plugin,
        )
        return
    clear_interaction_errors(state)
    register_root_message(state, event)
    state["wordbank_guided_trigger_shape"] = shape
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.add.response_prompt"),
    )


async def start_guided_add(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    *,
    initialize_plugin: Any,
) -> None:
    await initialize_plugin()
    state["wordbank_locale"] = locale
    register_root_message(state, event)
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.add.trigger_prompt"),
    )


async def finish_guided_add(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    *,
    finalize_submission: SubmissionHandler,
    wordbank_service: Any,
) -> None:
    locale = wordbank_guided_locale(state)
    source_event = _guided_submission_source_event(state)
    try:
        state_keys = _guided_state_keys(state)
        logger.debug(f"[Wordbank][guided] finish add start | state_keys={state_keys}")
        trigger_shape = state_message_shape(state, "wordbank_guided_trigger_shape")
        if trigger_shape is None or trigger_shape.is_empty():
            raise RuleError(
                _default_i18n_text("wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        if "wordbank_guided_response_split_shapes" in state:
            split_shapes = tuple(
                shape
                for shape in state.get("wordbank_guided_response_split_shapes", ())
                if isinstance(shape, MessageShape)
            )
            raw_split_count = len(
                tuple(state.get("wordbank_guided_response_split_shapes", ()) or ())
            )
            logger.debug(
                "[Wordbank][guided] finish add split branch | "
                f"raw_split_count={raw_split_count} "
                f"filtered_split_count={len(split_shapes)} "
                f"trigger={describe_shape(trigger_shape)}"
            )
            if not split_shapes:
                raise RuleError(
                    _default_i18n_text("wordbank.error.response_empty"),
                    key="wordbank.error.response_empty",
                )
            from src.plugins.wordbank.handlers.commands import (
                parse_guided_advanced_options,
                parse_guided_scope_choice,
            )

            scope = parse_guided_scope_choice(
                str(state.get("wordbank_guided_scope", "")),
                is_group=isinstance(event, GroupMessageEvent),
            )
            advanced = parse_guided_advanced_options(event.message.extract_plain_text())
            raw_rule = {"scope": scope, **advanced.raw_rule}
            batch = await wordbank_service.add_message_entries(
                trigger_shape=trigger_shape,
                response_shapes=split_shapes,
                raw_rule=raw_rule,
                group_id=str(getattr(event, "group_id", "")),
                user_id=str(event.user_id),
                is_group=isinstance(event, GroupMessageEvent),
            )
            batch_errors = describe_batch_errors(
                [item.error for item in batch.items if not item.ok]
            )
            logger.debug(
                "[Wordbank][guided] finish add split result | "
                f"total={batch.total} success={batch.success} failed={batch.failed} "
                f"errors={batch_errors}"
            )
            if batch.success == 0:
                raise RuleError(
                    _default_i18n_text("wordbank.error.response_empty"),
                    key="wordbank.error.response_empty",
                )
            source_message_id = str(
                state.get("wordbank_guided_response_forward_source_message_id", "")
                or ""
            )
            node_count = int(
                state.get("wordbank_guided_response_forward_node_count", 0) or 0
            )
            batch = replace(
                batch,
                items=tuple(
                    replace(
                        item,
                        result=(
                            replace(
                                item.result,
                                response_mode="forward_split",
                                forward_source_message_id=source_message_id or None,
                                forward_node_count=node_count,
                            )
                            if item.result is not None
                            else None
                        ),
                    )
                    for item in batch.items
                ),
            )
            await finalize_submission(
                matcher,
                bot,
                event,
                batch,
                locale,
                source_event=source_event,
            )
            return
        else:
            response_shape = state_message_shape(
                state, "wordbank_guided_response_shape"
            )
            if response_shape is None or response_shape.is_empty():
                raise RuleError(
                    _default_i18n_text("wordbank.error.response_empty"),
                    key="wordbank.error.response_empty",
                )
            result = await handle_guided_add_shape_result(
                wordbank_service,
                event=event,
                trigger_shape=trigger_shape,
                response_shape=response_shape,
                scope_text=str(state.get("wordbank_guided_scope", "")),
                advanced_text=event.message.extract_plain_text(),
            )
            forward_messages = state.get("wordbank_guided_response_forward_messages")
            if isinstance(forward_messages, tuple) and forward_messages:
                result = replace(
                    result,
                    response_mode="forward_whole",
                    forward_source_message_id=str(
                        state.get(
                            "wordbank_guided_response_forward_source_message_id", ""
                        )
                        or ""
                    )
                    or None,
                    forward_node_count=int(
                        state.get("wordbank_guided_response_forward_node_count", 0) or 0
                    ),
                )
    except (RuleError, ValueError) as exc:
        raise exc
    await finalize_submission(
        matcher,
        bot,
        event,
        result,
        locale,
        source_event=source_event,
    )


def guided_search_stage(state: Mapping[str, Any]) -> str:
    value = state.get("wordbank_guided_search_stage", "")
    return value if isinstance(value, str) else ""


def guided_search_image_scores(state: Mapping[str, Any]) -> dict[int, float]:
    value = state.get("wordbank_guided_search_image_scores")
    if not isinstance(value, dict):
        return {}
    return {
        int(key): float(score) for key, score in value.items() if str(key).isdigit()
    }


async def collect_search_query_content(
    message: Message,
    *,
    keyword_text: str,
    allow_image: bool = True,
    media_service: Any,
) -> tuple[str, bool, dict[int, float]]:
    from src.plugins import wordbank as wordbank_plugin
    from src.plugins.wordbank.text_parsing import has_meaningful_text

    normalized_keyword = normalize_cq_plain_text(
        keyword_text,
        collapse_cq_only_text=True,
    )
    keyword = keyword_text if has_meaningful_text(normalized_keyword) else ""
    if not allow_image:
        return keyword, False, {}
    data = await wordbank_plugin.fetch_first_image_bytes_from_message(message)
    if data is None:
        return keyword, False, {}
    image_scores = {
        match.canonical_id: match.score
        for match in media_service.search_similar_images(data)
    }
    return keyword, True, image_scores


def build_guided_search_parsed(
    state: Mapping[str, Any],
    *,
    page: int = 1,
) -> ParsedSearch:
    return ParsedSearch(
        keyword=str(state.get("wordbank_guided_search_keyword", "")),
        page=page,
        limit=10,
        field=str(state.get("wordbank_guided_search_field", "all")).strip() or "all",
        creator_id=str(state.get("wordbank_guided_search_creator_id", "")),
    )


def guided_search_current_page(state: Mapping[str, Any]) -> int:
    value = state.get("wordbank_guided_search_current_page", 1)
    return int(value) if isinstance(value, int) and value > 0 else 1


def guided_search_total_pages(state: Mapping[str, Any]) -> int:
    value = state.get("wordbank_guided_search_total_pages", 1)
    return int(value) if isinstance(value, int) and value > 0 else 1


def guided_search_group_ids(state: Mapping[str, Any]) -> tuple[int, ...]:
    value = state.get("wordbank_guided_search_group_ids")
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        int(item) for item in value if isinstance(item, int) or str(item).isdigit()
    )


def guided_search_delete_target_ids(state: Mapping[str, Any]) -> tuple[int, ...]:
    value = state.get("wordbank_guided_search_delete_target_ids")
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        int(item) for item in value if isinstance(item, int) or str(item).isdigit()
    )


def representative_detail_response_item_id(detail: Any) -> int | None:
    responses = tuple(getattr(detail, "responses", ()) or ())
    if not responses:
        return None

    def _sort_key(response: Any) -> tuple[int, int, int]:
        deleted_at = int(getattr(response, "deleted_at", 0) or 0)
        status = str(getattr(response, "status", "") or "")
        enabled = int(getattr(response, "enabled", 0) or 0)
        response_item_id = int(getattr(response, "response_item_id", 0) or 0)
        active_rank = (
            0 if deleted_at == 0 and status == "approved" and enabled == 1 else 1
        )
        deleted_rank = 0 if deleted_at == 0 else 1
        return (active_rank, deleted_rank, response_item_id)

    representative = min(responses, key=_sort_key)
    response_item_id = int(getattr(representative, "response_item_id", 0) or 0)
    return response_item_id if response_item_id > 0 else None


async def resolve_search_delete_target_ids(
    page: Any, *, wordbank_service: Any
) -> tuple[int, ...]:
    target_ids: list[int] = []
    for item in page.items:
        if item.response_item_ids:
            target_ids.append(int(item.response_item_ids[0]))
            continue
        detail = await wordbank_service.get_group_detail(item.trigger_group_id)
        target_ids.append(representative_detail_response_item_id(detail) or 0)
    return tuple(target_ids)


def search_session_prompt(locale: LocaleCode, *, total_pages: int) -> str:
    return tr(
        locale,
        "wordbank.guided.search.page_prompt",
        total_pages=total_pages,
    )


async def start_guided_search(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    *,
    initialize_plugin: Any,
) -> None:
    await initialize_plugin()
    clear_interaction_errors(state)
    state["wordbank_locale"] = locale
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_DIMENSIONS
    state["wordbank_guided_search_field"] = "all"
    state["wordbank_guided_search_keyword"] = ""
    state["wordbank_guided_search_creator_id"] = ""
    state["wordbank_guided_search_has_image"] = False
    state["wordbank_guided_search_image_scores"] = {}
    state["wordbank_guided_search_requires_creator"] = False
    register_root_message(state, event)
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.search.mode_prompt"),
    )


async def finish_guided_search(
    bot: Bot | None,
    matcher: Matcher,
    state: T_State,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    page_number: int,
    clamp_page: bool = False,
    wordbank_service: Any,
    media_service: Any,
    record_search_result_view_message: Any,
) -> None:
    from src.plugins import wordbank as wordbank_plugin

    image_scores = (
        guided_search_image_scores(state)
        if bool(state.get("wordbank_guided_search_has_image"))
        else None
    )
    parsed = build_guided_search_parsed(state, page=page_number)
    page = await wordbank_plugin.execute_search_page(
        wordbank_service,
        parsed=parsed,
        image_scores=image_scores,
    )
    total_pages = max(1, (page.total_count + parsed.limit - 1) // parsed.limit)
    if page.total_count > 0 and page_number > total_pages:
        if not clamp_page:
            await reject_guided_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.guided_search_page_out_of_range"),
            )
            return
        parsed = build_guided_search_parsed(state, page=total_pages)
        page = await wordbank_plugin.execute_search_page(
            wordbank_service,
            parsed=parsed,
            image_scores=image_scores,
        )
    message = await wordbank_plugin.render_search_page_message(
        page,
        parsed=parsed,
        locale=locale,
        has_image=bool(state.get("wordbank_guided_search_has_image")),
        media_service=media_service,
    )
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_PAGE
    state["wordbank_guided_search_current_page"] = parsed.page
    state["wordbank_guided_search_total_pages"] = max(
        1, (page.total_count + parsed.limit - 1) // parsed.limit
    )
    state["wordbank_guided_search_group_ids"] = tuple(
        item.trigger_group_id for item in page.items
    )
    state[
        "wordbank_guided_search_delete_target_ids"
    ] = await wordbank_plugin._resolve_search_delete_target_ids(
        page,
        wordbank_service=wordbank_service,
    )
    delivery_bot = _require_guided_bot(bot, matcher)
    plan_result = await deliver_message_plan(
        delivery_bot,
        plan=DeliveryPlan(
            messages=(message,),
            source_kind="wordbank_view",
        ),
        event=event,
    )
    send_result = plan_result.results[0]
    await record_search_result_view_message(
        send_result=send_result,
        event=event,
        parsed=parsed,
        page=page,
        has_image=bool(state.get("wordbank_guided_search_has_image")),
    )
    if page.total_count <= 0:
        await matcher.finish()
        return
    await pause_with_message(
        matcher,
        message=search_session_prompt(
            locale,
            total_pages=guided_search_total_pages(state),
        ),
    )


async def handle_search_session_event(
    bot: Any,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    *,
    wordbank_service: Any,
    send_group_detail_view: Any,
    finish_guided_search_fn: Any,
    build_error_message: Any,
) -> None:
    from src.plugins import wordbank as wordbank_plugin

    if guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_PAGE:
        return
    try:
        parsed = parse_search_session_command(event.message.extract_plain_text())
    except RuleError as exc:
        await reject_guided_error(
            matcher,
            state,
            locale,
            build_error_message(exc, locale, default_feature="search"),
        )
        return

    if parsed.action == "exit":
        await finish_with_message(
            _require_guided_bot(bot if isinstance(bot, Bot) else None, matcher),
            matcher,
            event=event,
            message=tr(locale, "wordbank.guided.search.finished"),
            source_kind="wordbank_view",
        )
        return

    if parsed.action == "detail":
        try:
            view = parse_view_reply_for_search_result(
                event.message.extract_plain_text(),
                available_group_ids=guided_search_group_ids(state),
            )
        except RuleError as exc:
            await reject_guided_error(
                matcher,
                state,
                locale,
                build_error_message(exc, locale, default_feature="search"),
            )
            return
        clear_interaction_errors(state)
        await send_group_detail_view(
            bot,
            matcher,
            event,
            locale,
            trigger_group_id=view.trigger_group_id,
            page=view.page,
            finish_after_send=False,
        )
        await pause_with_message(
            matcher,
            message=search_session_prompt(
                locale,
                total_pages=guided_search_total_pages(state),
            ),
        )
        return

    if parsed.action == "delete":
        delete_target_ids = guided_search_delete_target_ids(state)
        if (
            not delete_target_ids
            or any(index > len(delete_target_ids) for index in parsed.delete_indexes)
            or any(delete_target_ids[index - 1] <= 0 for index in parsed.delete_indexes)
        ):
            await reject_guided_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.search_delete_index_invalid"),
            )
            return
        messages = [
            await wordbank_plugin.handle_delete(
                wordbank_service,
                event=event,
                response_item_id_text=str(delete_target_ids[index - 1]),
                locale=locale,
            )
            for index in parsed.delete_indexes
        ]
        if messages:
            merged_message = "\n".join(messages)
            await deliver_message_plan(
                _require_guided_bot(bot, matcher),
                plan=DeliveryPlan(
                    messages=(merged_message,),
                    source_kind="wordbank_command",
                ),
                event=event,
            )
        clear_interaction_errors(state)
        await finish_guided_search_fn(
            bot,
            matcher,
            state,
            event,
            locale,
            page_number=guided_search_current_page(state),
            clamp_page=True,
        )
        return

    page_number = parsed.page
    if page_number is None:
        await finish_with_message(
            _require_guided_bot(bot if isinstance(bot, Bot) else None, matcher),
            matcher,
            event=event,
            message=tr(locale, "wordbank.guided.search.finished"),
            source_kind="wordbank_view",
        )
        return
    clear_interaction_errors(state)
    await finish_guided_search_fn(
        bot,
        matcher,
        state,
        event,
        locale,
        page_number=page_number,
    )
