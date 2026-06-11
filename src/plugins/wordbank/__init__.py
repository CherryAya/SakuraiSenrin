"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
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

from .database.types import WordbankApprovalMessageRecord
from .handlers import (
    APPROVAL_REPLY_ALIASES,
    REPLY_COMMAND_ALIASES,
    PassiveResponse,
    build_add_result_message,
    build_forced_command_text,
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
    record_submission_approval_message,
    send_pending_approval_notice,
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
    render_search_page_message,
)
from .message_model import MessageShape
from .services import wordbank_media_service, wordbank_service
from .services.core import WordbankAddResult
from .services.rules import RuleError

name = tr("zh-CN", "plugin.wordbank.name")
description = tr("zh-CN", "plugin.wordbank.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"
APPROVAL_DOCS_SOURCE = (
    Path(__file__).parent.parent / "wordbank_approval" / "docs" / "README.MD"
)


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
        return
    await wordbank_service.initialize()
    await wordbank_media_service.rebuild_cache()
    _wordbank_initialized = True


driver = get_driver()


@driver.on_startup
async def _initialize_wordbank_plugin() -> None:
    await initialize_wordbank_plugin()


async def is_wordbank_approval_reply(event: MessageEvent) -> bool:
    if event.reply is None:
        return False
    message_id = getattr(event.reply, "message_id", None)
    if message_id is None:
        return False
    await initialize_wordbank_plugin()
    return await wordbank_service.get_approval_message(str(message_id)) is not None


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
    rule=to_me() & is_reply,
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
wordbank_passive = on_message(priority=95, block=False)
wordbank_notice = on_notice(priority=95, block=False)


async def _finish_add_result(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    result: WordbankAddResult,
    locale: LocaleCode,
) -> None:
    await send_pending_approval_notice(
        bot,
        wordbank_service,
        event=event,
        result=result,
        locale=locale,
        media_service=wordbank_media_service,
    )
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
    await matcher.finish()


async def _notify_approval_source(
    bot: Bot,
    approval_message: WordbankApprovalMessageRecord,
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
        msg = await dispatch_wordbank_command(
            wordbank_service,
            event=event,
            text=text,
            locale=locale,
            search_image_scores=search_image_scores,
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
    message = render_search_page_message(
        page,
        parsed=parsed,
        locale=locale,
        has_image=bool(state.get("wordbank_guided_search_has_image")),
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
        await matcher.finish(message)
        return
    state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_PAGE
    await matcher.send(message)
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
        await wordbank_service.record_response_message(
            message_id=message_id,
            entry_id=response.entry_id,
            trigger_id=response.trigger_id,
            response_id=response.response_id,
            group_id=response.group_id,
            user_id=response.user_id,
            message_type=response.message_type,
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] response message record skipped: {exc}")


async def _build_passive_message(response: PassiveResponse) -> Message | str:
    if response.response_shape is None or response.response_shape.is_empty():
        return response.text

    message = Message()
    for atom in response.response_shape.atoms:
        if atom.kind == "text" and atom.text:
            message += MessageSegment.text(atom.text)
        elif atom.kind == "at" and atom.target_id:
            message += MessageSegment.at(atom.target_id)
        elif atom.kind == "image" and atom.canonical_image_id is not None:
            image_bytes = await wordbank_media_service.load_canonical_storage_bytes(
                atom.canonical_image_id
            )
            if image_bytes is None:
                return tr("zh-CN", "wordbank.error.image_storage_missing")
            message += MessageSegment.image(image_bytes)
    return message


@wordbank_passive.handle()
async def _(bot: Bot, event: MessageEvent) -> None:
    await initialize_wordbank_plugin()
    try:
        response = await handle_passive_message(
            bot,
            event,
            wordbank_service,
            wordbank_media_service,
        )
    except Exception as exc:
        logger.warning(f"[Wordbank] passive match skipped: {exc}")
        return
    if response:
        message = await _build_passive_message(response)
        send_result = await wordbank_passive.send(message)
        await _record_passive_response_message(response, send_result)


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

    await initialize_wordbank_plugin()
    try:
        response = await handle_passive_notice(bot, event, wordbank_service)
    except Exception as exc:
        logger.warning(f"[Wordbank] passive notice skipped: {exc}")
        return
    if response:
        send_result = await wordbank_notice.send(await _build_passive_message(response))
        await _record_passive_response_message(response, send_result)
