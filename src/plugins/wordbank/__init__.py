"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from nonebot import get_driver, on_message, on_notice, require
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupMessageEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import on_command, on_fullmatch
from nonebot.rule import to_me
from nonebot.typing import T_State

from src.config import config
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import (
    abort_if_revoke_signal,
    clear_interaction_errors,
    reject_or_abort_on_error,
)
from src.lib.interactive_recall import (
    INTERACTION_ROOT_MESSAGE_ID,
    INTERACTION_SESSION_KEY,
    cancel_state_resources,
    find_recall_session,
    get_interaction_session_key,
    is_supported_recall_notice,
    rebuild_temp_matcher,
    register_recall_checkpoint,
    register_root_message,
)
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger
from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start

from .database.types import WordbankMessageRefRecord
from .handlers import (
    APPROVAL_REPLY_ALIASES,
    GROUP_ALIASES,
    REPLY_COMMAND_ALIASES,
    PassiveResponse,
    build_add_result_message,
    build_forced_command_text,
    build_group_detail_message,
    build_message_shape_from_message,
    dispatch_wordbank_command,
    extract_image_urls,
    fetch_first_image_bytes_from_message,
    handle_add_text_result,
    handle_add_with_media_result,
    handle_approval_reply_result,
    handle_guided_add_shape_result,
    handle_passive_message,
    handle_passive_notice,
    handle_reply_command,
    is_reply,
    localize_command_error,
    parse_group_view_args,
    parse_view_reply_for_group_detail,
    parse_view_reply_for_search_result,
    record_submission_approval_message,
    schedule_pending_approval_notice,
)
from .handlers.commands import (
    ParsedSearch,
    execute_search_page,
    parse_guided_advanced_options,
    parse_guided_scope_choice,
    parse_guided_search_creator_filter,
    parse_guided_search_image_field_choice,
    parse_guided_search_mode_choice,
    parse_guided_search_page_choice,
    parse_search_args,
    render_search_page_message,
)
from .handlers.rendering import render_shape_message
from .message_model import MessageShape
from .services import wordbank_media_service, wordbank_service
from .services.core import WordbankAddResult
from .services.rules import RuleError

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

name = tr("zh-CN", "plugin.wordbank.name")
description = tr("zh-CN", "plugin.wordbank.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"
APPROVAL_DOCS_SOURCE = Path(__file__).parent / "docs" / "approval" / "README.MD"


def _build_wordbank_docs_meta() -> list[object]:
    main_docs = create_docs_meta(
        visible=True,
        category="fun",
        order=80,
        source=DOCS_SOURCE,
        slug="wordbank",
        aliases=("词库模块", "wordbank"),
    )
    main_docs["permission"] = Permission.NORMAL

    approval_docs = create_docs_meta(
        visible=False,
        category="fun",
        order=180,
        source=APPROVAL_DOCS_SOURCE,
        slug="wordbank.approval",
        parent_slug="wordbank",
        aliases=("词库审核", "wordbank approval"),
    )
    approval_docs["permission"] = Permission.GROUP_ADMIN
    return [main_docs, approval_docs]


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.wordbank.name",
            "description_key": "plugin.wordbank.description",
        },
        "docs": _build_wordbank_docs_meta(),
    },
)

_wordbank_initialized = False
GUIDED_MAX_ERRORS = 3
WORDBANK_GUIDED_STEP_TRIGGER = 1
WORDBANK_GUIDED_STEP_RESPONSE = 2
WORDBANK_GUIDED_STEP_SCOPE = 3
WORDBANK_GUIDED_STEP_ADVANCED = 4
WORDBANK_GUIDED_SEARCH_STAGE_MODE = "search_mode"
WORDBANK_GUIDED_SEARCH_STAGE_IMAGE_FIELD = "search_image_field"
WORDBANK_GUIDED_SEARCH_STAGE_KEYWORD = "search_keyword"
WORDBANK_GUIDED_SEARCH_STAGE_IMAGE = "search_image"
WORDBANK_GUIDED_SEARCH_STAGE_CREATOR = "search_creator"
WORDBANK_GUIDED_SEARCH_STAGE_PAGE = "search_page"
WORDBANK_GUIDED_RECALL_PENDING_KEYS: tuple[str, ...] = ()


async def initialize_wordbank_plugin() -> None:
    global _wordbank_initialized
    if _wordbank_initialized:
        log_perf("plugin.initialize.cached", initialized=True)
        return
    start = perf_start()
    service_start = perf_start()
    await wordbank_service.initialize()
    service_ms = elapsed_ms(service_start)
    media_start = perf_start()
    await wordbank_media_service.rebuild_cache()
    media_ms = elapsed_ms(media_start)
    _wordbank_initialized = True
    log_perf(
        "plugin.initialize.done",
        start=start,
        service_initialize_ms=f"{service_ms:.2f}",
        media_rebuild_ms=f"{media_ms:.2f}",
    )


driver = get_driver()


@driver.on_startup
async def _initialize_wordbank_plugin() -> None:
    await initialize_wordbank_plugin()


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=30,
    id="wordbank_event_archive",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _wordbank_event_archive_job() -> None:
    try:
        await wordbank_service.repository.archive_event_shards()
        logger.success("[Wordbank] cron archive done")
    except Exception as e:
        logger.exception(f"[Wordbank] cron archive failed: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=1,
    minute=15,
    id="wordbank_media_maintenance",
    coalesce=True,
    misfire_grace_time=300,
    max_instances=1,
)
async def _wordbank_media_maintenance_job() -> None:
    try:
        report = await wordbank_media_service.run_scheduled_maintenance(
            batch_size=config.WORDBANK_MEDIA_MIGRATION_BATCH_SIZE
        )
        logger.success(f"[Wordbank] media maintenance done: {report}")
    except Exception as e:
        logger.exception(f"[Wordbank] media maintenance failed: {e}")


async def is_wordbank_approval_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return (
        await wordbank_service.get_message_ref(
            str(message_id),
            expected_kind="approval",
        )
        is not None
    )


async def is_wordbank_response_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return (
        await wordbank_service.get_message_ref(
            str(message_id),
            expected_kind="response",
        )
        is not None
    )


async def is_wordbank_view_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return (
        await wordbank_service.get_message_ref(
            str(message_id),
            expected_kind="view",
        )
        is not None
    )


wordbank_command = on_command(
    "wordbank",
    aliases={"词库", "wordbank.help"},
    priority=5,
    block=True,
)
wordbank_add_command = on_command(
    ("wordbank", "add"),
    aliases={"添加词条"},
    priority=5,
    block=True,
)
wordbank_search_command = on_command(
    ("wordbank", "search"),
    aliases={"搜索词条"},
    priority=5,
    block=True,
)
wordbank_pending_command = on_command(
    ("wordbank", "pending"),
    aliases={"待审核词条"},
    priority=5,
    block=True,
)
wordbank_approve_command = on_command(
    ("wordbank", "approve"),
    aliases={"通过词条", "审核通过词条"},
    priority=5,
    block=True,
)
wordbank_reject_command = on_command(
    ("wordbank", "reject"),
    aliases={"拒绝词条", "驳回词条"},
    priority=5,
    block=True,
)
wordbank_delete_command = on_command(
    ("wordbank", "delete"),
    aliases={("wordbank", "del"), "删除词条"},
    priority=5,
    block=True,
)
wordbank_restore_command = on_command(
    ("wordbank", "restore"),
    aliases={"恢复词条"},
    priority=5,
    block=True,
)
wordbank_support_command = on_command(
    ("wordbank", "support"),
    aliases={"支持删除"},
    priority=5,
    block=True,
)
wordbank_vote_command = on_command(
    ("wordbank", "vote"),
    aliases={
        "查看投票状态",
        "查看投票结果",
    },
    priority=5,
    block=True,
)
wordbank_reply_command = on_fullmatch(
    tuple(REPLY_COMMAND_ALIASES),
    ignorecase=True,
    rule=to_me() & is_reply & is_wordbank_response_reply,
    priority=5,
    block=True,
)
wordbank_approval_reply_command = on_fullmatch(
    tuple(APPROVAL_REPLY_ALIASES),
    ignorecase=True,
    rule=to_me() & is_reply & is_wordbank_approval_reply,
    priority=5,
    block=True,
)
wordbank_view_reply_command = on_message(
    rule=to_me() & is_reply & is_wordbank_view_reply,
    priority=6,
    block=True,
)
wordbank_passive = on_message(priority=95, block=False)
wordbank_notice = on_notice(priority=95, block=False)


async def _finish_add_result(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    result: WordbankAddResult,
    locale: LocaleCode,
) -> None:
    send_result = await matcher.send(
        await build_add_result_message(
            result,
            locale=locale,
            media_service=wordbank_media_service,
        )
    )
    await record_submission_approval_message(
        wordbank_service,
        event=event,
        result=result,
        send_result=send_result,
    )
    schedule_pending_approval_notice(
        bot,
        wordbank_service,
        event=event,
        result=result,
        locale=locale,
        media_service=wordbank_media_service,
    )
    await matcher.finish()


def _should_send_media_processing_notice(
    *,
    image_count: int,
) -> bool:
    return image_count > 0


async def _notify_approval_source(
    bot: Bot,
    approval_message: WordbankMessageRefRecord,
    message: str,
) -> None:
    source = Message()
    if approval_message.source_message_id.isdigit():
        source += MessageSegment.reply(int(approval_message.source_message_id))
    source += MessageSegment.text(message)

    try:
        if approval_message.group_id:
            await bot.send_group_msg(
                group_id=int(approval_message.group_id),
                message=source,
            )
            return
        if approval_message.user_id:
            await bot.send_private_msg(
                user_id=int(approval_message.user_id),
                message=source,
            )
    except Exception as exc:
        logger.warning(f"[Wordbank] approval source notice skipped: {exc}")


async def _handle_wordbank_command_message(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message,
    *,
    forced_action: str | None = None,
) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    text = build_forced_command_text(forced_action, arg.extract_plain_text())
    action = text.split(maxsplit=1)[0].lower() if text else ""
    search_image_scores: dict[int, float] | None = None
    if action in {"add", "添加", "学习"}:
        _, _, rest = text.partition(" ")
        try:
            data = await fetch_first_image_bytes_from_message(arg)
            if data is not None and _should_send_media_processing_notice(image_count=1):
                await matcher.send(tr(locale, "wordbank.add.processing_with_media"))
            if data is not None:
                result = await handle_add_with_media_result(
                    wordbank_service,
                    wordbank_media_service,
                    event=event,
                    image_bytes=data,
                    text=rest,
                )
            else:
                result = await handle_add_text_result(
                    wordbank_service,
                    event=event,
                    text=rest,
                )
        except (RuleError, ValueError) as exc:
            await matcher.finish(localize_command_error(exc, locale))
            return
        await _finish_add_result(matcher, bot, event, result, locale)
    elif action in {"search", "find", "查询", "搜索"}:
        try:
            data = await fetch_first_image_bytes_from_message(arg)
            if data is not None:
                search_image_scores = {
                    match.canonical_id: match.score
                    for match in wordbank_media_service.search_similar_images(data)
                }
        except (RuleError, ValueError) as exc:
            await matcher.finish(localize_command_error(exc, locale))
            return
        try:
            await _send_search_result_view(
                matcher,
                event,
                locale,
                keyword=text.partition(" ")[2] if " " in text else "",
                image_scores=search_image_scores,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(localize_command_error(exc, locale))
        return
    elif action in GROUP_ALIASES:
        try:
            parsed_group = parse_group_view_args(text.partition(" ")[2])
            await _send_group_detail_view(
                matcher,
                event,
                locale,
                trigger_group_id=parsed_group.trigger_group_id,
                page=parsed_group.page,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(localize_command_error(exc, locale))
        return

    try:
        msg = await dispatch_wordbank_command(
            wordbank_service,
            event=event,
            text=text,
            locale=locale,
            search_image_scores=search_image_scores,
            media_service=wordbank_media_service,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
        return
    await matcher.finish(msg)


def _state_message_shape(state: Mapping[str, Any], key: str) -> MessageShape | None:
    value = state.get(key)
    return value if isinstance(value, MessageShape) else None


async def _record_guided_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    shape = await build_message_shape_from_message(
        wordbank_media_service,
        event.message,
    )
    if shape.is_empty():
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = _wordbank_guided_locale(state)
    snapshot = _copy_guided_state(state, keep_keys=())
    state["wordbank_guided_trigger_shape"] = shape
    _register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_TRIGGER,
        locale=locale,
        snapshot=snapshot,
    )
    await matcher.pause(tr(locale, "wordbank.guided.add.response_prompt"))


async def _record_guided_response(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    shape = await build_message_shape_from_message(
        wordbank_media_service,
        event.message,
    )
    if shape.is_empty():
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = _wordbank_guided_locale(state)
    snapshot = _copy_guided_state(
        state,
        keep_keys=("wordbank_guided_trigger_shape",),
    )
    state["wordbank_guided_response_shape"] = shape
    _register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_RESPONSE,
        locale=locale,
        snapshot=snapshot,
    )
    await matcher.pause(tr(locale, "wordbank.guided.add.scope_prompt"))


async def _start_guided_add_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
) -> None:
    await initialize_wordbank_plugin()
    state["wordbank_locale"] = locale
    shape = await build_message_shape_from_message(wordbank_media_service, arg)
    if shape.is_empty():
        await _start_guided_add(matcher, event, state, locale)
        return
    clear_interaction_errors(state)
    register_root_message(state, event)
    state["wordbank_guided_trigger_shape"] = shape
    await matcher.pause(tr(locale, "wordbank.guided.add.response_prompt"))


async def _abort_guided_on_revoke(
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
) -> None:
    await abort_if_revoke_signal(
        event,
        matcher,
        message=tr(locale, "interaction.cancelled"),
    )


async def _reject_guided_error(
    matcher: Matcher,
    state: T_State,
    locale: LocaleCode,
    message: str,
) -> None:
    await reject_or_abort_on_error(
        matcher,
        state,
        message,
        max_errors=GUIDED_MAX_ERRORS,
        abort_message=tr(locale, "interaction.too_many_errors"),
    )


def _wordbank_guided_locale(state: Mapping[str, Any]) -> LocaleCode:
    locale = state.get("wordbank_locale", "zh-CN")
    return locale if locale in {"zh-CN", "lzh", "x-meme"} else "zh-CN"


def _copy_guided_state(
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
    for key in keep_keys:
        if key in state:
            snapshot[key] = state[key]
    clear_interaction_errors(snapshot)
    return snapshot


def _guided_prompt_for_step(locale: LocaleCode, step_index: int) -> str:
    prompt_by_step = {
        WORDBANK_GUIDED_STEP_TRIGGER: tr(locale, "wordbank.guided.add.trigger_prompt"),
        WORDBANK_GUIDED_STEP_RESPONSE: tr(
            locale,
            "wordbank.guided.add.response_prompt",
        ),
        WORDBANK_GUIDED_STEP_SCOPE: tr(locale, "wordbank.guided.add.scope_prompt"),
        WORDBANK_GUIDED_STEP_ADVANCED: tr(
            locale,
            "wordbank.guided.add.advanced_prompt",
        ),
    }
    return prompt_by_step[step_index]


def _register_guided_checkpoint(
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
        prompt=_guided_prompt_for_step(locale, step_index),
        state_snapshot=snapshot,
        cleanup_keys=cleanup_keys,
    )


async def _cancel_guided_resources(
    state: Mapping[str, Any],
    cleanup_keys: tuple[str, ...] = WORDBANK_GUIDED_RECALL_PENDING_KEYS,
) -> None:
    await cancel_state_resources(
        state,
        cleanup_keys,
        cleaners={},
    )


async def _start_guided_add(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await initialize_wordbank_plugin()
    state["wordbank_locale"] = locale
    register_root_message(state, event)
    await matcher.pause(tr(locale, "wordbank.guided.add.trigger_prompt"))


async def _finish_guided_add(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = _wordbank_guided_locale(state)
    try:
        trigger_shape = _state_message_shape(state, "wordbank_guided_trigger_shape")
        response_shape = _state_message_shape(state, "wordbank_guided_response_shape")
        if trigger_shape is None or trigger_shape.is_empty():
            raise RuleError("触发词不能为空", key="wordbank.error.trigger_empty")
        if response_shape is None or response_shape.is_empty():
            raise RuleError("响应词不能为空", key="wordbank.error.response_empty")
        result = await handle_guided_add_shape_result(
            wordbank_service,
            event=event,
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            scope_text=str(state.get("wordbank_guided_scope", "")),
            advanced_text=event.message.extract_plain_text(),
        )
    except (RuleError, ValueError) as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    await _finish_add_result(matcher, bot, event, result, locale)


def _guided_search_stage(state: Mapping[str, Any]) -> str:
    value = state.get("wordbank_guided_search_stage", "")
    return value if isinstance(value, str) else ""


def _guided_search_image_scores(state: Mapping[str, Any]) -> dict[int, float]:
    value = state.get("wordbank_guided_search_image_scores")
    if not isinstance(value, dict):
        return {}
    return {
        int(key): float(score) for key, score in value.items() if str(key).isdigit()
    }


def _build_guided_search_parsed(
    state: Mapping[str, Any],
    *,
    page: int = 1,
) -> ParsedSearch:
    return ParsedSearch(
        keyword=str(state.get("wordbank_guided_search_keyword", "")).strip(),
        page=page,
        limit=10,
        field=str(state.get("wordbank_guided_search_field", "all")).strip() or "all",
        creator_id=str(state.get("wordbank_guided_search_creator_id", "")).strip(),
    )


async def _start_guided_search(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await initialize_wordbank_plugin()
    clear_interaction_errors(state)
    state["wordbank_locale"] = locale
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_MODE
    state["wordbank_guided_search_field"] = "all"
    state["wordbank_guided_search_keyword"] = ""
    state["wordbank_guided_search_creator_id"] = ""
    state["wordbank_guided_search_has_image"] = False
    state["wordbank_guided_search_image_scores"] = {}
    state["wordbank_guided_search_creator_only"] = False
    register_root_message(state, event)
    await matcher.pause(tr(locale, "wordbank.guided.search.mode_prompt"))


async def _start_guided_search_with_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
) -> None:
    await initialize_wordbank_plugin()
    data = await fetch_first_image_bytes_from_message(arg)
    if data is None:
        await _start_guided_search(matcher, event, state, locale)
        return
    clear_interaction_errors(state)
    state["wordbank_locale"] = locale
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_IMAGE_FIELD
    state["wordbank_guided_search_field"] = "all"
    state["wordbank_guided_search_keyword"] = ""
    state["wordbank_guided_search_creator_id"] = ""
    state["wordbank_guided_search_has_image"] = True
    state["wordbank_guided_search_image_scores"] = {
        match.canonical_id: match.score
        for match in wordbank_media_service.search_similar_images(data)
    }
    state["wordbank_guided_search_creator_only"] = False
    register_root_message(state, event)
    await matcher.pause(tr(locale, "wordbank.guided.search.image_field_prompt"))


async def _finish_guided_search(
    matcher: Matcher,
    state: T_State,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    page_number: int,
) -> None:
    parsed = _build_guided_search_parsed(state, page=page_number)
    page = await execute_search_page(
        wordbank_service,
        parsed=parsed,
        image_scores=(
            _guided_search_image_scores(state)
            if bool(state.get("wordbank_guided_search_has_image"))
            else None
        ),
    )
    message = await render_search_page_message(
        page,
        parsed=parsed,
        locale=locale,
        has_image=bool(state.get("wordbank_guided_search_has_image")),
        media_service=wordbank_media_service,
    )
    total_pages = max(1, (page.total_count + parsed.limit - 1) // parsed.limit)
    if page.total_count > 0 and page_number > total_pages:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.guided_search_page_out_of_range"),
        )
        return
    if total_pages <= 1:
        send_result = await matcher.send(message)
        await _record_search_result_view_message(
            send_result=send_result,
            event=event,
            parsed=parsed,
            page=page,
            has_image=bool(state.get("wordbank_guided_search_has_image")),
        )
        await matcher.finish()
        return
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_PAGE
    send_result = await matcher.send(message)
    await _record_search_result_view_message(
        send_result=send_result,
        event=event,
        parsed=parsed,
        page=page,
        has_image=bool(state.get("wordbank_guided_search_has_image")),
    )
    await matcher.pause(
        tr(
            locale,
            "wordbank.guided.search.page_prompt",
            total_pages=total_pages,
        )
    )


@wordbank_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_guided_on_revoke(matcher, event, locale)
    text = arg.extract_plain_text().strip()
    if text:
        first, _, tail = text.partition(" ")
        if first.lower() in {"add", "添加", "学习"} and not tail.strip():
            if extract_image_urls(arg):
                await _start_guided_add_with_trigger_image(
                    matcher,
                    event,
                    state,
                    locale,
                    arg,
                )
            else:
                await _start_guided_add(matcher, event, state, locale)
            return
    await initialize_wordbank_plugin()
    await _handle_wordbank_command_message(bot, matcher, event, arg)


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    if _guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _record_guided_trigger(matcher, event, state, locale)


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    if _guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _record_guided_response(matcher, event, state, locale)


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    if _guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    try:
        parse_guided_scope_choice(
            text,
            is_group=bool(getattr(event, "group_id", "")),
        )
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    locale = _wordbank_guided_locale(state)
    _register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_SCOPE,
        locale=locale,
        snapshot=_copy_guided_state(
            state,
            keep_keys=(
                "wordbank_guided_trigger_shape",
                "wordbank_guided_response_shape",
            ),
        ),
    )
    state["wordbank_guided_scope"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.advanced_prompt"))


@wordbank_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    if _guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    try:
        parse_guided_advanced_options(event.message.extract_plain_text())
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    locale = _wordbank_guided_locale(state)
    _register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_ADVANCED,
        locale=locale,
        snapshot=_copy_guided_state(
            state,
            keep_keys=(
                "wordbank_guided_trigger_shape",
                "wordbank_guided_response_shape",
                "wordbank_guided_scope",
            ),
        ),
    )
    await _finish_guided_add(bot, matcher, event, state)


@wordbank_add_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_guided_on_revoke(matcher, event, locale)
    if not arg.extract_plain_text().strip() and not extract_image_urls(arg):
        await _start_guided_add(matcher, event, state, locale)
        return
    if not arg.extract_plain_text().strip() and extract_image_urls(arg):
        await _start_guided_add_with_trigger_image(
            matcher,
            event,
            state,
            locale,
            arg,
        )
        return
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="add",
    )


@wordbank_add_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _record_guided_trigger(matcher, event, state, locale)


@wordbank_add_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _record_guided_response(matcher, event, state, locale)


@wordbank_add_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    try:
        parse_guided_scope_choice(
            text,
            is_group=bool(getattr(event, "group_id", "")),
        )
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    locale = _wordbank_guided_locale(state)
    _register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_SCOPE,
        locale=locale,
        snapshot=_copy_guided_state(
            state,
            keep_keys=(
                "wordbank_guided_trigger_shape",
                "wordbank_guided_response_shape",
            ),
        ),
    )
    state["wordbank_guided_scope"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.advanced_prompt"))


@wordbank_add_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    try:
        parse_guided_advanced_options(event.message.extract_plain_text())
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    locale = _wordbank_guided_locale(state)
    _register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_ADVANCED,
        locale=locale,
        snapshot=_copy_guided_state(
            state,
            keep_keys=(
                "wordbank_guided_trigger_shape",
                "wordbank_guided_response_shape",
                "wordbank_guided_scope",
            ),
        ),
    )
    await _finish_guided_add(bot, matcher, event, state)


@wordbank_search_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_guided_on_revoke(matcher, event, locale)
    if not arg.extract_plain_text().strip() and not extract_image_urls(arg):
        await _start_guided_search(matcher, event, state, locale)
        return
    if not arg.extract_plain_text().strip() and extract_image_urls(arg):
        try:
            await _start_guided_search_with_image(
                matcher,
                event,
                state,
                locale,
                arg,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(localize_command_error(exc, locale))
        return
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="search",
    )


@wordbank_search_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    stage = _guided_search_stage(state)
    if stage != WORDBANK_GUIDED_SEARCH_STAGE_MODE:
        return
    try:
        selection = parse_guided_search_mode_choice(event.message.extract_plain_text())
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_field"] = selection.field
    state["wordbank_guided_search_creator_only"] = selection.creator_only
    if selection.creator_only:
        state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
        await matcher.pause(tr(locale, "wordbank.guided.search.creator_prompt"))
        return
    if selection.expects_image:
        state["wordbank_guided_search_has_image"] = True
        state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_IMAGE
        await matcher.pause(tr(locale, "wordbank.guided.search.image_prompt"))
        return
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_KEYWORD
    await matcher.pause(tr(locale, "wordbank.guided.search.keyword_prompt"))


@wordbank_search_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if _guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_IMAGE_FIELD:
        return
    try:
        state["wordbank_guided_search_field"] = parse_guided_search_image_field_choice(
            event.message.extract_plain_text()
        )
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
    await matcher.pause(tr(locale, "wordbank.guided.search.creator_filter_prompt"))


@wordbank_search_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if _guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_KEYWORD:
        return
    keyword = event.message.extract_plain_text().strip()
    if not keyword:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.guided_search_keyword_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_keyword"] = keyword
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
    await matcher.pause(tr(locale, "wordbank.guided.search.creator_filter_prompt"))


@wordbank_search_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if _guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_IMAGE:
        return
    try:
        data = await fetch_first_image_bytes_from_message(event.message)
    except (RuleError, ValueError) as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    if data is None:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.guided_search_image_missing"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_image_scores"] = {
        match.canonical_id: match.score
        for match in wordbank_media_service.search_similar_images(data)
    }
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
    await matcher.pause(tr(locale, "wordbank.guided.search.creator_filter_prompt"))


@wordbank_search_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if _guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_CREATOR:
        return
    try:
        creator_id = parse_guided_search_creator_filter(
            event.message.extract_plain_text()
        )
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_creator_id"] = creator_id
    if bool(state.get("wordbank_guided_search_creator_only")) and not creator_id:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.guided_search_creator_empty"),
        )
        return
    await _finish_guided_search(
        matcher,
        state,
        event,
        locale,
        page_number=1,
    )


@wordbank_search_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if _guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_PAGE:
        return
    try:
        page_number = parse_guided_search_page_choice(
            event.message.extract_plain_text()
        )
    except RuleError as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    if page_number is None:
        await matcher.finish(tr(locale, "wordbank.guided.search.finished"))
        return
    clear_interaction_errors(state)
    await _finish_guided_search(
        matcher,
        state,
        event,
        locale,
        page_number=page_number,
    )


@wordbank_pending_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="pending",
    )


@wordbank_approve_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="approve",
    )


@wordbank_reject_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="reject",
    )


@wordbank_delete_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="delete",
    )


@wordbank_restore_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="restore",
    )


@wordbank_support_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="support",
    )


@wordbank_vote_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(
        bot,
        matcher,
        event,
        arg,
        forced_action="vote",
    )


@wordbank_reply_command.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    try:
        msg = await handle_reply_command(
            wordbank_service,
            event=event,
            text=event.message.extract_plain_text(),
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
        return
    await matcher.finish(msg)


@wordbank_approval_reply_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    try:
        outcome = await handle_approval_reply_result(
            wordbank_service,
            event=event,
            text=event.message.extract_plain_text(),
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
        return
    if outcome.completed and outcome.approval_message is not None:
        await _notify_approval_source(bot, outcome.approval_message, outcome.message)
    await matcher.finish(outcome.message)


@wordbank_view_reply_command.handle()
async def _(matcher: Matcher, event: MessageEvent) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    reply = event.reply
    if reply is None:
        await matcher.finish(tr(locale, "wordbank.reply.target_missing"))
        return
    reply_message_id = getattr(reply, "message_id", None)
    if reply_message_id is None:
        await matcher.finish(tr(locale, "wordbank.reply.target_missing"))
        return
    view_message = await wordbank_service.get_message_ref(
        str(reply_message_id),
        expected_kind="view",
    )
    if view_message is None:
        await matcher.finish(
            tr(
                locale,
                "wordbank.reply.view_target_not_found",
                message_id=reply_message_id,
            )
        )
        return

    try:
        if view_message.context_type == "search_result":
            parsed = parse_view_reply_for_search_result(
                event.message.extract_plain_text(),
                available_group_ids=view_message.group_ids,
            )
        else:
            parsed = parse_view_reply_for_group_detail(
                event.message.extract_plain_text(),
                trigger_group_id=view_message.trigger_group_id,
                current_page=view_message.current_page,
            )
        await _send_group_detail_view(
            matcher,
            event,
            locale,
            trigger_group_id=parsed.trigger_group_id,
            page=parsed.page,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))


def _extract_sent_message_id(result: Any) -> str | None:
    if isinstance(result, dict):
        value = result.get("message_id")
    else:
        value = getattr(result, "message_id", None)
    if value is None:
        return None
    return str(value)


async def _record_passive_response_message(
    response: PassiveResponse,
    send_result: Any,
) -> None:
    message_id = _extract_sent_message_id(send_result)
    if message_id is None:
        return
    try:
        await wordbank_service.record_message_ref(
            ref_kind="response",
            message_id=message_id,
            trigger_group_id=response.trigger_group_id,
            trigger_variant_id=response.trigger_variant_id,
            response_item_id=response.response_item_id,
            group_id=response.group_id,
            user_id=response.user_id,
            message_type=response.message_type,
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] response message record skipped: {exc}")


def _event_message_type(event: MessageEvent) -> str:
    return "group" if isinstance(event, GroupMessageEvent) else "private"


async def _record_search_result_view_message(
    *,
    send_result: Any,
    event: MessageEvent,
    parsed: ParsedSearch,
    page: Any,
    has_image: bool,
) -> None:
    await _record_view_message(
        send_result=send_result,
        event=event,
        context_type="search_result",
        trigger_group_id=0,
        current_page=parsed.page,
        keyword=parsed.keyword,
        field=parsed.field,
        creator_id=parsed.creator_id,
        has_image=has_image,
        group_ids=[item.trigger_group_id for item in page.items],
    )


async def _record_group_detail_view_message(
    *,
    send_result: Any,
    event: MessageEvent,
    trigger_group_id: int,
    page: int,
    has_image: bool,
) -> None:
    await _record_view_message(
        send_result=send_result,
        event=event,
        context_type="group_detail",
        trigger_group_id=trigger_group_id,
        current_page=page,
        keyword="",
        field="",
        creator_id="",
        has_image=has_image,
        group_ids=[trigger_group_id],
    )


async def _record_view_message(
    *,
    send_result: Any,
    event: MessageEvent,
    context_type: str,
    trigger_group_id: int,
    current_page: int,
    keyword: str,
    field: str,
    creator_id: str,
    has_image: bool,
    group_ids: Sequence[int],
) -> None:
    message_id = _extract_sent_message_id(send_result)
    if message_id is None:
        return
    try:
        await wordbank_service.record_message_ref(
            ref_kind="view",
            message_id=message_id,
            context_type=context_type,
            trigger_group_id=trigger_group_id,
            current_page=current_page,
            keyword=keyword,
            field=field,
            creator_id=creator_id,
            has_image=has_image,
            group_ids=group_ids,
            group_id=str(getattr(event, "group_id", "") or ""),
            user_id=str(event.user_id),
            message_type=_event_message_type(event),
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] view message record skipped: {exc}")


def _group_detail_has_image(detail: Any) -> bool:
    if any(atom.kind == "image" for atom in detail.trigger_shape.atoms):
        return True
    return any(
        atom.kind == "image"
        for response in detail.responses
        for atom in response.response_shape.atoms
    )


async def _send_search_result_view(
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    keyword: str,
    image_scores: dict[int, float] | None = None,
) -> None:
    parsed = parse_search_args(keyword)
    page = await execute_search_page(
        wordbank_service,
        parsed=parsed,
        image_scores=image_scores,
    )
    message = await render_search_page_message(
        page,
        parsed=parsed,
        locale=locale,
        has_image=image_scores is not None,
        media_service=wordbank_media_service,
    )
    send_result = await matcher.send(message)
    await _record_search_result_view_message(
        send_result=send_result,
        event=event,
        parsed=parsed,
        page=page,
        has_image=image_scores is not None,
    )
    await matcher.finish()


async def _send_group_detail_view(
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
    *,
    trigger_group_id: int,
    page: int,
) -> None:
    message, detail, _ = await build_group_detail_message(
        wordbank_service,
        trigger_group_id=trigger_group_id,
        page=page,
        locale=locale,
        media_service=wordbank_media_service,
    )
    send_result = await matcher.send(message)
    await _record_group_detail_view_message(
        send_result=send_result,
        event=event,
        trigger_group_id=trigger_group_id,
        page=page,
        has_image=_group_detail_has_image(detail),
    )
    await matcher.finish()


def _message_segment_stats(message: Message | str) -> tuple[int, int]:
    if isinstance(message, str):
        return (1 if message else 0, 0)
    segments = list(message)
    return (
        len(segments),
        sum(1 for segment in segments if segment.type == "image"),
    )


def _image_payload_trace_fields(
    trace_fields: Mapping[str, object] | None,
) -> dict[str, object]:
    if trace_fields is None:
        return {}
    payload: dict[str, object] = {}
    for key in (
        "requested_image_ids",
        "loaded_image_ids",
        "loaded_image_sizes",
        "loaded_count",
        "missing_count",
        "image_total_bytes",
        "image_max_bytes",
    ):
        value = trace_fields.get(key)
        if value is not None:
            payload[key] = value
    return payload


async def _build_passive_message(
    response: PassiveResponse,
) -> tuple[Message | str, dict[str, object]]:
    start = perf_start()
    if response.response_shape is None or response.response_shape.is_empty():
        log_perf(
            "plugin.build_passive_message.text_only",
            start=start,
            response_item_id=response.response_item_id,
        )
        return response.text, {}
    image_atom_count = sum(
        1 for atom in response.response_shape.atoms if atom.kind == "image"
    )
    log_perf(
        "plugin.build_passive_message.render_shape.begin",
        response_item_id=response.response_item_id,
        atom_count=len(response.response_shape.atoms),
        image_atom_count=image_atom_count,
    )
    render_trace: dict[str, object] = {}
    message = await render_shape_message(
        response.response_shape,
        wordbank_media_service,
        trace_fields={"response_item_id": response.response_item_id},
        trace_sink=render_trace,
    )
    image_trace_fields = _image_payload_trace_fields(render_trace)
    log_perf(
        "plugin.build_passive_message.rendered_shape",
        start=start,
        response_item_id=response.response_item_id,
        atoms=len(response.response_shape.atoms),
        segments=len(list(message)),
        **cast(Any, image_trace_fields),
    )
    return message, image_trace_fields


@wordbank_passive.handle()
async def _(bot: Bot, event: MessageEvent) -> None:
    start = perf_start()
    await initialize_wordbank_plugin()
    try:
        handle_start = perf_start()
        response = await handle_passive_message(
            bot,
            event,
            wordbank_service,
            wordbank_media_service,
        )
        handle_ms = elapsed_ms(handle_start)
    except Exception as exc:
        logger.warning(f"[Wordbank] passive match skipped: {exc}")
        return
    if response:
        build_start = perf_start()
        message, image_trace_fields = await _build_passive_message(response)
        build_ms = elapsed_ms(build_start)
        segment_count, image_segment_count = _message_segment_stats(message)
        log_perf(
            "plugin.passive.handle.send.begin",
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        send_start = perf_start()
        send_result = await wordbank_passive.send(message)
        send_ms = elapsed_ms(send_start)
        log_perf(
            "plugin.passive.handle.send.done",
            start=send_start,
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        record_start = perf_start()
        await _record_passive_response_message(response, send_result)
        record_ms = elapsed_ms(record_start)
        log_perf(
            "plugin.passive.handle.sent",
            start=start,
            message_type=response.message_type,
            trigger_group_id=response.trigger_group_id,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
            send_ms=f"{send_ms:.2f}",
            record_ms=f"{record_ms:.2f}",
            **cast(Any, image_trace_fields),
        )
        return
    log_perf(
        "plugin.passive.handle.no_match",
        start=start,
        handle_ms=f"{handle_ms:.2f}",
    )


@wordbank_notice.handle()
async def _(bot: Bot, event: NoticeEvent) -> None:
    if is_supported_recall_notice(event):
        recall_event = cast(GroupRecallNoticeEvent | FriendRecallNoticeEvent, event)
        for matcher_source in (wordbank_add_command, wordbank_command):
            session = find_recall_session(matcher_source, recall_event)
            if session is None:
                continue

            state = session.matcher_cls._default_state
            locale = _wordbank_guided_locale(state)
            checkpoint = session.checkpoint
            await _cancel_guided_resources(
                state,
                checkpoint.cleanup_keys
                if checkpoint is not None and not session.is_root_message
                else WORDBANK_GUIDED_RECALL_PENDING_KEYS,
            )
            session.matcher_cls.destroy()

            if session.is_root_message or checkpoint is None:
                await wordbank_notice.send(tr(locale, "interaction.cancelled"))
                return

            rebuild_temp_matcher(
                session.matcher_cls,
                matcher_source,
                step_index=checkpoint.step_index,
                state=checkpoint.state_snapshot,
            )
            await wordbank_notice.send(checkpoint.prompt)
            return

    start = perf_start()
    await initialize_wordbank_plugin()
    try:
        handle_start = perf_start()
        response = await handle_passive_notice(bot, event, wordbank_service)
        handle_ms = elapsed_ms(handle_start)
    except Exception as exc:
        logger.warning(f"[Wordbank] passive notice skipped: {exc}")
        return
    if response:
        build_start = perf_start()
        message, image_trace_fields = await _build_passive_message(response)
        build_ms = elapsed_ms(build_start)
        segment_count, image_segment_count = _message_segment_stats(message)
        log_perf(
            "plugin.notice.handle.send.begin",
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        send_start = perf_start()
        send_result = await wordbank_notice.send(message)
        send_ms = elapsed_ms(send_start)
        log_perf(
            "plugin.notice.handle.send.done",
            start=send_start,
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        record_start = perf_start()
        await _record_passive_response_message(response, send_result)
        record_ms = elapsed_ms(record_start)
        log_perf(
            "plugin.notice.handle.sent",
            start=start,
            message_type=response.message_type,
            trigger_group_id=response.trigger_group_id,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
            send_ms=f"{send_ms:.2f}",
            record_ms=f"{record_ms:.2f}",
            **cast(Any, image_trace_fields),
        )
        return
    log_perf(
        "plugin.notice.handle.no_match",
        start=start,
        handle_ms=f"{handle_ms:.2f}",
    )
