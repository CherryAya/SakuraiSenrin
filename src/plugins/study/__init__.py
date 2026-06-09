"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 23:51:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:52
Description: 学习词库-传统版
"""

from pathlib import Path

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

name = tr("zh-CN", "plugin.study.name")
description = "词库快捷学习入口，内部复用 wordbank 固定规则模型。"
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
            "name_key": "plugin.study.name",
            "description_key": "plugin.study.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="fun",
            order=85,
            source=DOCS_SOURCE,
        ),
    },
)

study_command = on_command(
    "study",
    aliases={"学习"},
    priority=5,
    block=True,
)


@study_command.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    from src.plugins.wordbank.handlers import handle_study_shortcut
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.rules import RuleError

    await wordbank_service.initialize()
    try:
        msg = await handle_study_shortcut(
            wordbank_service,
            event=event,
            text=arg.extract_plain_text(),
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(str(exc))
    await matcher.finish(msg)
