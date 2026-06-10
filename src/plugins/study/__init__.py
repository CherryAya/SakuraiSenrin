"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 23:51:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:52
Description: 学习词库-传统版
"""

from pathlib import Path
from typing import TYPE_CHECKING

from nonebot.adapters.onebot.v11.bot import Bot
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

if TYPE_CHECKING:
    from src.plugins.wordbank.handlers import PendingWordbankImage

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
GUIDED_MAX_ERRORS = 3


async def _abort_study_on_revoke(
    matcher: Matcher,
    event: MessageEvent,
    locale: LocaleCode,
) -> None:
    await abort_if_revoke_signal(
        event,
        matcher,
        message=tr(locale, "interaction.cancelled"),
    )


async def _reject_study_error(
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


def _state_image_id(state: T_State, key: str) -> int | None:
    value = state.get(key)
    return int(value) if value is not None else None


def _is_truthy_state_flag(state: T_State, key: str) -> bool:
    return bool(state.get(key, False))


def _state_pending_image(
    state: T_State,
    key: str,
) -> "PendingWordbankImage | None":
    from src.plugins.wordbank.handlers import PendingWordbankImage

    value = state.get(key)
    return value if isinstance(value, PendingWordbankImage) else None


async def _resolve_state_image_id(
    state: T_State,
    *,
    id_key: str,
    pending_key: str,
) -> int | None:
    from src.plugins.wordbank.handlers import resolve_pending_image

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


def _start_study_image_task(
    message: Message,
) -> "PendingWordbankImage | None":
    from src.plugins.wordbank.handlers import (
        start_ingest_first_image_from_message,
    )
    from src.plugins.wordbank.services import wordbank_media_service

    return start_ingest_first_image_from_message(wordbank_media_service, message)


async def _record_study_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import extract_image_urls

    text = event.message.extract_plain_text().strip()
    pending_image = _start_study_image_task(event.message)
    if pending_image is None and extract_image_urls(event.message):
        return
    if pending_image is None and not text:
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    if pending_image is not None:
        state["study_trigger"] = ""
        state["study_trigger_image_pending"] = pending_image
        state.pop("study_trigger_image_id", None)
    else:
        state["study_trigger"] = text
        state.pop("study_trigger_image_pending", None)
        state.pop("study_trigger_image_id", None)
    await matcher.pause(tr(locale, "wordbank.guided.study.response_prompt"))


async def _record_study_response(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import extract_image_urls

    text = event.message.extract_plain_text().strip()
    pending_image = _start_study_image_task(event.message)
    if pending_image is None and extract_image_urls(event.message):
        return
    if pending_image is None and not text:
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    state["study_response"] = text
    if pending_image is not None:
        state["study_response_image_pending"] = pending_image
        state.pop("study_response_image_id", None)
    else:
        state.pop("study_response_image_pending", None)
        state.pop("study_response_image_id", None)
    await matcher.pause(tr(locale, "wordbank.guided.study.weight_prompt"))


async def _record_study_weight_and_finish(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers.commands import (
        localize_command_error,
        parse_guided_weight,
    )
    from src.plugins.wordbank.services.rules import RuleError

    try:
        parse_guided_weight(event.message.extract_plain_text())
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    await _finish_guided_study(bot, matcher, event, state, locale)


async def _finish_guided_study(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import (
        handle_guided_study_image_trigger_result,
        handle_guided_study_shortcut_result,
        record_submission_approval_message,
        send_pending_approval_notice,
    )
    from src.plugins.wordbank.handlers.commands import localize_command_error
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.core import format_add_result
    from src.plugins.wordbank.services.rules import RuleError

    try:
        trigger_image_id = await _resolve_state_image_id(
            state,
            id_key="study_trigger_image_id",
            pending_key="study_trigger_image_pending",
        )
        response_image_id = await _resolve_state_image_id(
            state,
            id_key="study_response_image_id",
            pending_key="study_response_image_pending",
        )
        if trigger_image_id is not None:
            result = await handle_guided_study_image_trigger_result(
                wordbank_service,
                event=event,
                trig_mode_text=str(state.get("study_trig_mode", "")),
                group_block_text=str(state.get("study_group_block", "")),
                trigger_canonical_image_id=trigger_image_id,
                response_text=str(state.get("study_response", "")),
                response_canonical_image_id=response_image_id,
                weight_text=event.message.extract_plain_text(),
            )
        else:
            result = await handle_guided_study_shortcut_result(
                wordbank_service,
                event=event,
                trig_mode_text=str(state.get("study_trig_mode", "")),
                group_block_text=str(state.get("study_group_block", "")),
                trigger_text=str(state.get("study_trigger", "")),
                response_text=str(state.get("study_response", "")),
                response_canonical_image_id=response_image_id,
                weight_text=event.message.extract_plain_text(),
            )
    except (RuleError, ValueError) as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
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


async def _start_guided_study_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
) -> None:
    from src.plugins.wordbank.services import wordbank_service

    await wordbank_service.initialize()
    state["study_locale"] = locale
    pending_image = _start_study_image_task(arg)
    if pending_image is None:
        await matcher.pause(tr(locale, "wordbank.guided.study.mode_prompt"))
        return
    clear_interaction_errors(state)
    state["study_trigger"] = ""
    state["study_trigger_image_pending"] = pending_image
    state.pop("study_trigger_image_id", None)
    state["study_trigger_preloaded"] = True
    await matcher.pause(tr(locale, "wordbank.guided.study.mode_prompt"))


@study_command.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    from src.plugins.wordbank.handlers import (
        extract_image_urls,
        fetch_first_image_bytes_from_message,
        handle_study_with_media_result,
        record_submission_approval_message,
        send_pending_approval_notice,
    )
    from src.plugins.wordbank.handlers.commands import localize_command_error
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service
    from src.plugins.wordbank.services.core import format_add_result
    from src.plugins.wordbank.services.rules import RuleError

    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_study_on_revoke(matcher, event, locale)
    arg_text = arg.extract_plain_text().strip()
    if not arg_text:
        if extract_image_urls(arg):
            await _start_guided_study_with_trigger_image(
                matcher,
                event,
                state,
                locale,
                arg,
            )
            return
        await wordbank_service.initialize()
        state["study_locale"] = locale
        await matcher.pause(tr(locale, "wordbank.guided.study.mode_prompt"))
        return
    await wordbank_service.initialize()
    try:
        result = await handle_study_with_media_result(
            wordbank_service,
            wordbank_media_service,
            event=event,
            text=arg.extract_plain_text(),
            image_bytes=await fetch_first_image_bytes_from_message(arg),
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(localize_command_error(exc, locale))
        return
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


def _study_locale(state: T_State) -> LocaleCode:
    locale = state.get("study_locale", "zh-CN")
    return locale if locale in {"zh-CN", "lzh", "x-meme"} else "zh-CN"


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import (
        localize_command_error,
        parse_study_mode_choice,
    )
    from src.plugins.wordbank.services.rules import RuleError

    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    try:
        parse_study_mode_choice(text)
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["study_trig_mode"] = text
    await matcher.pause(tr(locale, "wordbank.guided.study.group_block_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import (
        localize_command_error,
        parse_study_group_block_choice,
    )
    from src.plugins.wordbank.services.rules import RuleError

    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    text = event.message.extract_plain_text().strip()
    try:
        parse_study_group_block_choice(text)
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            localize_command_error(exc, locale),
        )
        return
    clear_interaction_errors(state)
    state["study_group_block"] = text
    if _is_truthy_state_flag(state, "study_trigger_preloaded"):
        state["study_response_after_preloaded_trigger"] = True
        await matcher.pause(tr(locale, "wordbank.guided.study.response_prompt"))
        return
    await matcher.pause(tr(locale, "wordbank.guided.study.trigger_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if _is_truthy_state_flag(state, "study_response_after_preloaded_trigger"):
        state["study_weight_after_preloaded_trigger"] = True
        await _record_study_response(matcher, event, state, locale)
        return
    await _record_study_trigger(matcher, event, state, locale)


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if _is_truthy_state_flag(state, "study_weight_after_preloaded_trigger"):
        await _record_study_weight_and_finish(bot, matcher, event, state, locale)
        return
    await _record_study_response(matcher, event, state, locale)


@study_command.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    await _record_study_weight_and_finish(bot, matcher, event, state, locale)
