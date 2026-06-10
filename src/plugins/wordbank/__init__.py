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

from .handlers import (
    REPLY_COMMAND_ALIASES,
    PassiveResponse,
    build_forced_command_text,
    dispatch_wordbank_command,
    extract_image_urls,
    handle_add_with_media,
    handle_guided_add_text,
    handle_passive_message,
    handle_passive_notice,
    handle_reply_command,
    is_reply,
    localize_command_error,
)
from .handlers.commands import parse_guided_advanced_options, parse_guided_scope_choice
from .services import wordbank_media_service, wordbank_service
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


wordbank_command = on_command(
    "wordbank",
    aliases={"词库", "wordbank.help"},
    priority=5,
    block=True,
)
wordbank_add_command = on_command(
    ("wordbank", "add"),
    aliases={"添加词条", "wordbank.add"},
    priority=5,
    block=True,
)
wordbank_search_command = on_command(
    ("wordbank", "search"),
    aliases={"搜索词条", "wordbank.search"},
    priority=5,
    block=True,
)
wordbank_delete_command = on_command(
    ("wordbank", "delete"),
    aliases={("wordbank", "del"), "删除词条", "wordbank.delete", "wordbank.del"},
    priority=5,
    block=True,
)
wordbank_restore_command = on_command(
    ("wordbank", "restore"),
    aliases={"恢复词条", "wordbank.restore"},
    priority=5,
    block=True,
)
wordbank_support_command = on_command(
    ("wordbank", "support"),
    aliases={"支持删除", "wordbank.support"},
    priority=5,
    block=True,
)
wordbank_vote_command = on_command(
    ("wordbank", "vote"),
    aliases={
        "查看投票状态",
        "查看投票结果",
        "wordbank.vote",
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
wordbank_passive = on_message(priority=95, block=False)
wordbank_notice = on_notice(priority=95, block=False)


async def _handle_wordbank_command_message(
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
        urls = extract_image_urls(arg)
        if urls:
            from .handlers.passive import fetch_image_bytes

            data = await fetch_image_bytes(urls[0])
            if data is None:
                await matcher.finish(tr(locale, "wordbank.error.image_download_failed"))
            try:
                _, _, rest = text.partition(" ")
                msg = await handle_add_with_media(
                    wordbank_service,
                    wordbank_media_service,
                    event=event,
                    image_bytes=data,
                    text=rest,
                    locale=locale,
                )
            except (RuleError, ValueError) as exc:
                await matcher.finish(localize_command_error(exc, locale))
            await matcher.finish(msg)

    try:
        msg = await dispatch_wordbank_command(
            wordbank_service,
            event=event,
            text=text,
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
    await matcher.finish(msg)


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
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    try:
        msg = await handle_guided_add_text(
            wordbank_service,
            event=event,
            trigger_text=str(state.get("wordbank_guided_trigger", "")),
            response_text=str(state.get("wordbank_guided_response", "")),
            scope_text=str(state.get("wordbank_guided_scope", "")),
            advanced_text=event.message.extract_plain_text(),
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    await matcher.finish(msg)


@wordbank_command.handle()
async def _(
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
        if (
            first.lower() in {"add", "添加", "学习"}
            and not tail.strip()
            and not extract_image_urls(arg)
        ):
            await _start_guided_add(matcher, state, locale)
            return
    await initialize_wordbank_plugin()
    await _handle_wordbank_command_message(matcher, event, arg)


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    if not text:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_trigger"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.response_prompt"))


@wordbank_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    if not text:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_response"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.scope_prompt"))


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
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
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
    await _finish_guided_add(matcher, event, state)


@wordbank_add_command.handle()
async def _(
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
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="add")


@wordbank_add_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    if not text:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_trigger"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.response_prompt"))


@wordbank_add_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    if not text:
        await _reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_response"] = text
    await matcher.pause(tr(locale, "wordbank.guided.add.scope_prompt"))


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
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
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
    await _finish_guided_add(matcher, event, state)


@wordbank_search_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="search")


@wordbank_delete_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="delete")


@wordbank_restore_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="restore")


@wordbank_support_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="support")


@wordbank_vote_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="vote")


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
    await matcher.finish(msg)


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


def _build_passive_message(response: PassiveResponse) -> Message | str:
    if (
        response.response_kind != "image"
        or response.response_canonical_image_id is None
    ):
        return response.text

    image_bytes = wordbank_media_service.load_canonical_storage_bytes(
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
        send_result = await wordbank_passive.send(_build_passive_message(response))
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
        send_result = await wordbank_notice.send(_build_passive_message(response))
        await _record_passive_response_message(response, send_result)
