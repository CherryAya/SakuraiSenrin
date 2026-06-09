"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from pathlib import Path

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger

from .handlers import (
    IMAGE_ALIASES,
    dispatch_wordbank_command,
    extract_image_urls,
    handle_add_image,
    handle_passive_message,
)
from .services import wordbank_media_service, wordbank_service
from .services.rules import RuleError

name = tr("zh-CN", "plugin.wordbank.name")
description = "低资源优先的群聊词库，支持文本与图片触发。"
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
wordbank_passive = on_message(priority=95, block=False)


@wordbank_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    await initialize_wordbank_plugin()
    text = arg.extract_plain_text().strip()
    action = text.split(maxsplit=1)[0].lower() if text else ""
    if action in IMAGE_ALIASES:
        urls = extract_image_urls(arg)
        if not urls:
            await matcher.finish("图片词条需要在命令消息中附带图片。")
        from .handlers.passive import fetch_image_bytes

        data = await fetch_image_bytes(urls[0])
        if data is None:
            await matcher.finish("图片下载失败，无法加入词库。")
        try:
            _, _, rest = text.partition(" ")
            msg = await handle_add_image(
                wordbank_service,
                wordbank_media_service,
                event=event,
                image_bytes=data,
                text=rest,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(str(exc))
        await matcher.finish(msg)

    try:
        msg = await dispatch_wordbank_command(
            wordbank_service,
            event=event,
            text=text,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(str(exc))
    await matcher.finish(msg)


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
        await wordbank_passive.send(response)
