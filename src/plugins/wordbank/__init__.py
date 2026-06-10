"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from pathlib import Path
from typing import Any

from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent, NoticeEvent
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
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

from .database.types import WordbankApprovalMessageRecord
from .handlers import (
    APPROVAL_REPLY_ALIASES,
    REPLY_COMMAND_ALIASES,
    PassiveResponse,
    PendingWordbankImage,
    build_forced_command_text,
    dispatch_wordbank_command,
    extract_image_urls,
    fetch_first_image_bytes_from_message,
    handle_add_text_result,
    handle_add_with_media_result,
    handle_approval_reply_result,
    handle_guided_add_image_trigger_result,
    handle_guided_add_text_result,
    handle_passive_message,
    handle_passive_notice,
    handle_reply_command,
    is_reply,
    localize_command_error,
    record_submission_approval_message,
    resolve_pending_image,
    send_pending_approval_notice,
    start_ingest_first_image_from_message,
)
from .handlers.commands import parse_guided_advanced_options, parse_guided_scope_choice
from .services import wordbank_media_service, wordbank_service
from .services.core import WordbankAddResult, format_add_result
from .services.rules import RuleError

name = tr("zh-CN", "plugin.wordbank.name")
description = tr("zh-CN", "plugin.wordbank.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=ctx,
    )


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
        "docs": create_docs_meta(
            build_docs,
            visible=True,
            category="fun",
            order=80,
            source=DOCS_SOURCE,
        ),
    },
)

_wordbank_initialized = False
GUIDED_MAX_ERRORS = 3


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
    )
    send_result = await matcher.send(format_add_result(result, locale=locale))
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

    try:
        msg = await dispatch_wordbank_command(
            wordbank_service,
            event=event,
            text=text,
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
        return
    await matcher.finish(msg)


def _state_image_id(state: T_State, key: str) -> int | None:
    value = state.get(key)
    return int(value) if value is not None else None


def _state_pending_image(state: T_State, key: str) -> PendingWordbankImage | None:
    value = state.get(key)
    return value if isinstance(value, PendingWordbankImage) else None


async def _resolve_state_image_id(
    state: T_State,
    *,
    id_key: str,
    pending_key: str,
) -> int | None:
    image_id = _state_image_id(state, id_key)
    if image_id is not None:
        return image_id

    pending = _state_pending_image(state, pending_key)
    if pending is None:
        return None

    image = await resolve_pending_image(pending)
    state[id_key] = image.canonical_id
    state.pop(pending_key, None)
    return image.canonical_id


def _start_guided_image_task(event: MessageEvent) -> PendingWordbankImage | None:
    return start_ingest_first_image_from_message(
        wordbank_media_service,
        event.message,
    )


async def _record_guided_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    text = event.message.extract_plain_text().strip()
    pending_image = _start_guided_image_task(event)
    if pending_image is None and not text:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    if pending_image is not None:
        state["wordbank_guided_trigger"] = ""
        state["wordbank_guided_trigger_image_pending"] = pending_image
        state.pop("wordbank_guided_trigger_image_id", None)
    else:
        state["wordbank_guided_trigger"] = text
        state.pop("wordbank_guided_trigger_image_pending", None)
        state.pop("wordbank_guided_trigger_image_id", None)
    await matcher.pause(tr(locale, "wordbank.guided.add.response_prompt"))


async def _record_guided_response(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    text = event.message.extract_plain_text().strip()
    pending_image = _start_guided_image_task(event)
    if pending_image is None and not text:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_response"] = text
    if pending_image is not None:
        state["wordbank_guided_response_image_pending"] = pending_image
        state.pop("wordbank_guided_response_image_id", None)
    else:
        state.pop("wordbank_guided_response_image_pending", None)
        state.pop("wordbank_guided_response_image_id", None)
    await matcher.pause(tr(locale, "wordbank.guided.add.scope_prompt"))


async def _start_guided_add_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await initialize_wordbank_plugin()
    state["wordbank_locale"] = locale
    pending_image = _start_guided_image_task(event)
    if pending_image is None:
        await _start_guided_add(matcher, state, locale)
        return
    clear_interaction_errors(state)
    state["wordbank_guided_trigger"] = ""
    state["wordbank_guided_trigger_image_pending"] = pending_image
    state.pop("wordbank_guided_trigger_image_id", None)
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


async def _start_guided_add(
    matcher: Matcher,
    state: T_State,
    locale: LocaleCode,
) -> None:
    await initialize_wordbank_plugin()
    state["wordbank_locale"] = locale
    await matcher.pause(tr(locale, "wordbank.guided.add.trigger_prompt"))


async def _finish_guided_add(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    try:
        trigger_image_id = await _resolve_state_image_id(
            state,
            id_key="wordbank_guided_trigger_image_id",
            pending_key="wordbank_guided_trigger_image_pending",
        )
        response_image_id = await _resolve_state_image_id(
            state,
            id_key="wordbank_guided_response_image_id",
            pending_key="wordbank_guided_response_image_pending",
        )
        if trigger_image_id is not None:
            result = await handle_guided_add_image_trigger_result(
                wordbank_service,
                event=event,
                trigger_canonical_image_id=trigger_image_id,
                response_text=str(state.get("wordbank_guided_response", "")),
                response_canonical_image_id=response_image_id,
                scope_text=str(state.get("wordbank_guided_scope", "")),
                advanced_text=event.message.extract_plain_text(),
            )
        else:
            result = await handle_guided_add_text_result(
                wordbank_service,
                event=event,
                trigger_text=str(state.get("wordbank_guided_trigger", "")),
                response_text=str(state.get("wordbank_guided_response", "")),
                response_canonical_image_id=response_image_id,
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
                )
            else:
                await _start_guided_add(matcher, state, locale)
            return
    await initialize_wordbank_plugin()
    await _handle_wordbank_command_message(bot, matcher, event, arg)


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _record_guided_trigger(matcher, event, state, locale)


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _record_guided_response(matcher, event, state, locale)


@wordbank_command.handle()
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
    state["wordbank_guided_scope"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.advanced_prompt"))


@wordbank_command.handle()
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
        await _start_guided_add(matcher, state, locale)
        return
    if not arg.extract_plain_text().strip() and extract_image_urls(arg):
        await _start_guided_add_with_trigger_image(matcher, event, state, locale)
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
    await _finish_guided_add(bot, matcher, event, state)


@wordbank_search_command.handle()
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
        forced_action="search",
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
    if (
        response.response_kind != "image"
        or response.response_canonical_image_id is None
    ):
        return response.text

    image_bytes = await wordbank_media_service.load_canonical_storage_bytes(
        response.response_canonical_image_id
    )
    if image_bytes is None:
        return tr("zh-CN", "wordbank.error.image_storage_missing")

    message = Message()
    if response.text:
        message += MessageSegment.text(response.text)
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
    await initialize_wordbank_plugin()
    try:
        response = await handle_passive_notice(bot, event, wordbank_service)
    except Exception as exc:
        logger.warning(f"[Wordbank] passive notice skipped: {exc}")
        return
    if response:
        send_result = await wordbank_notice.send(await _build_passive_message(response))
        await _record_passive_response_message(response, send_result)
