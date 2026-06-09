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
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent, NoticeEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import on_command, on_fullmatch
from nonebot.rule import to_me

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

from .handlers import (
    IMAGE_ALIASES,
    REPLY_COMMAND_ALIASES,
    PassiveResponse,
    build_forced_command_text,
    dispatch_wordbank_command,
    extract_image_urls,
    handle_add_image,
    handle_passive_message,
    handle_passive_notice,
    handle_reply_command,
    is_reply,
    localize_command_error,
)
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
    aliases={"词库"},
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
wordbank_image_command = on_command(
    ("wordbank", "image"),
    aliases={("wordbank", "img"), "图片词条"},
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
    aliases={"查看投票状态", "查看投票结果"},
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
    if action in IMAGE_ALIASES:
        urls = extract_image_urls(arg)
        if not urls:
            await matcher.finish(tr(locale, "wordbank.error.image_missing"))
        from .handlers.passive import fetch_image_bytes

        data = await fetch_image_bytes(urls[0])
        if data is None:
            await matcher.finish(tr(locale, "wordbank.error.image_download_failed"))
        try:
            _, _, rest = text.partition(" ")
            msg = await handle_add_image(
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


@wordbank_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg)


@wordbank_add_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="add")


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


@wordbank_image_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await _handle_wordbank_command_message(matcher, event, arg, forced_action="image")


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
        send_result = await wordbank_passive.send(response.text)
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
        send_result = await wordbank_notice.send(response.text)
        await _record_passive_response_message(response, send_result)
