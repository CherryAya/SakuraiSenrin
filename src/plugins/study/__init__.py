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
from nonebot.typing import T_State

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.study.name")
description = tr("zh-CN", "plugin.study.description")
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
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    from src.plugins.wordbank.handlers import handle_study_shortcut
    from src.plugins.wordbank.handlers.commands import (
        abort_if_revoke_signal,
        localize_command_error,
    )
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.rules import RuleError

    await abort_if_revoke_signal(event, matcher)
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not arg.extract_plain_text().strip():
        await wordbank_service.initialize()
        state["study_locale"] = locale
        await matcher.pause(tr(locale, "wordbank.guided.study.mode_prompt"))
        return
    await wordbank_service.initialize()
    try:
        msg = await handle_study_shortcut(
            wordbank_service,
            event=event,
            text=arg.extract_plain_text(),
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
    await matcher.finish(msg)


def _study_locale(state: T_State) -> LocaleCode:
    locale = state.get("study_locale", "zh-CN")
    return locale if locale in {"zh-CN", "lzh", "x-meme"} else "zh-CN"


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import abort_if_revoke_signal

    await abort_if_revoke_signal(event, matcher)
    locale = _study_locale(state)
    state["study_trig_mode"] = event.message.extract_plain_text()
    await matcher.pause(tr(locale, "wordbank.guided.study.group_block_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import abort_if_revoke_signal

    await abort_if_revoke_signal(event, matcher)
    locale = _study_locale(state)
    state["study_group_block"] = event.message.extract_plain_text()
    await matcher.pause(tr(locale, "wordbank.guided.study.trigger_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import abort_if_revoke_signal

    await abort_if_revoke_signal(event, matcher)
    locale = _study_locale(state)
    state["study_trigger"] = event.message.extract_plain_text()
    await matcher.pause(tr(locale, "wordbank.guided.study.response_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import abort_if_revoke_signal

    await abort_if_revoke_signal(event, matcher)
    locale = _study_locale(state)
    state["study_response"] = event.message.extract_plain_text()
    await matcher.pause(tr(locale, "wordbank.guided.study.weight_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers import handle_guided_study_shortcut
    from src.plugins.wordbank.handlers.commands import (
        abort_if_revoke_signal,
        localize_command_error,
    )
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.rules import RuleError

    await abort_if_revoke_signal(event, matcher)
    locale = _study_locale(state)
    try:
        msg = await handle_guided_study_shortcut(
            wordbank_service,
            event=event,
            trig_mode_text=str(state.get("study_trig_mode", "")),
            group_block_text=str(state.get("study_group_block", "")),
            trigger_text=str(state.get("study_trigger", "")),
            response_text=str(state.get("study_response", "")),
            weight_text=event.message.extract_plain_text(),
            locale=locale,
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
    await matcher.finish(msg)
