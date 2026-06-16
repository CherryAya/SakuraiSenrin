"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 23:51:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-11 19:10:00
Description: 学习词库-传统版
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from nonebot import on_notice
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
from src.lib.plugin_docs import build_doc_demo_message, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.plugins.wordbank.handlers.commands import _default_i18n_text
from src.plugins.wordbank.message_model import MessageShape, shape_from_text
from src.plugins.wordbank.text_parsing import has_meaningful_text

name = tr("zh-CN", "plugin.study.name")
description = tr("zh-CN", "plugin.study.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


def _study_error_feature(exc: Exception, default_feature: str | None) -> str | None:
    key = str(getattr(exc, "key", "")).strip()
    if not key:
        return default_feature
    if key == "wordbank.error.study_mode_invalid":
        return "guided-flow"
    if key == "wordbank.error.study_group_block_invalid":
        return "guided-flow"
    if "weight" in key:
        return default_feature or "guided-flow"
    if key in {"wordbank.error.trigger_empty", "wordbank.error.response_empty"}:
        return default_feature or "shortcut"
    return default_feature


def _study_error_message(
    exc: Exception,
    locale: LocaleCode,
    *,
    default_feature: str | None = None,
) -> Message:
    from src.plugins.wordbank.handlers.commands import localize_command_error

    return build_doc_demo_message(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale=locale,
        feature_query=_study_error_feature(exc, default_feature),
        prefix_text=localize_command_error(exc, locale),
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#3BC9DB",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.study.name",
            "description_key": "plugin.study.description",
        },
        "docs": create_docs_meta(
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
study_recall_notice = on_notice(priority=5, block=False)
GUIDED_MAX_ERRORS = 3
STUDY_STEP_MODE = 1
STUDY_STEP_GROUP_BLOCK = 2
STUDY_STEP_TRIGGER = 3
STUDY_STEP_RESPONSE = 4
STUDY_STEP_WEIGHT = 5
STUDY_RECALL_PENDING_KEYS: tuple[str, ...] = ()


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
    message: Any,
) -> None:
    await reject_or_abort_on_error(
        matcher,
        state,
        message,
        max_errors=GUIDED_MAX_ERRORS,
        abort_message=tr(locale, "interaction.too_many_errors"),
    )


def _copy_study_state(
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
    if "study_locale" in state:
        snapshot["study_locale"] = state["study_locale"]
    if INTERACTION_ROOT_MESSAGE_ID in state:
        snapshot[INTERACTION_ROOT_MESSAGE_ID] = state[INTERACTION_ROOT_MESSAGE_ID]
    for key in keep_keys:
        if key in state:
            snapshot[key] = state[key]
    clear_interaction_errors(snapshot)
    return snapshot


def _prompt_for_step(locale: LocaleCode, step_index: int) -> str:
    prompt_by_step = {
        STUDY_STEP_MODE: tr(locale, "wordbank.guided.study.mode_prompt"),
        STUDY_STEP_GROUP_BLOCK: tr(locale, "wordbank.guided.study.group_block_prompt"),
        STUDY_STEP_TRIGGER: tr(locale, "wordbank.guided.study.trigger_prompt"),
        STUDY_STEP_RESPONSE: tr(locale, "wordbank.guided.study.response_prompt"),
        STUDY_STEP_WEIGHT: tr(locale, "wordbank.guided.study.weight_prompt"),
    }
    return prompt_by_step[step_index]


def _register_study_checkpoint(
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
        prompt=_prompt_for_step(locale, step_index),
        state_snapshot=snapshot,
        cleanup_keys=cleanup_keys,
    )


def _state_message_shape(state: Mapping[str, Any], key: str) -> MessageShape | None:
    value = state.get(key)
    return value if isinstance(value, MessageShape) else None


def _is_truthy_state_flag(state: T_State, key: str) -> bool:
    return bool(state.get(key, False))


def _contains_study_pair_separator(text: str) -> bool:
    return any(sep in text for sep in ("=>", "->", "回答", "回复"))


async def _start_guided_study_from_partial_args(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    text: str,
    *,
    has_images: bool,
) -> bool:
    from src.plugins.wordbank.handlers.commands import (
        parse_study_group_block_choice,
        parse_study_mode_choice,
    )
    from src.plugins.wordbank.services import wordbank_service
    from src.plugins.wordbank.services.rules import RuleError
    from src.plugins.wordbank.text_parsing import rest_after_token, tokenize_shell_like

    source = text
    if not source or _contains_study_pair_separator(source):
        return False
    try:
        tokens = tokenize_shell_like(source)
    except ValueError:
        return False
    if not tokens:
        return False
    try:
        trig_mode = parse_study_mode_choice(tokens[0].value)
    except RuleError:
        return False

    await wordbank_service.initialize()
    state["study_locale"] = locale
    clear_interaction_errors(state)
    register_root_message(state, event)
    state["study_trig_mode"] = trig_mode
    state["study_mode_prefilled"] = True

    if len(tokens) == 1:
        await matcher.pause(tr(locale, "wordbank.guided.study.group_block_prompt"))
        return True

    try:
        group_block = parse_study_group_block_choice(tokens[1].value)
    except RuleError as exc:
        await matcher.pause(
            _study_error_message(exc, locale, default_feature="guided-flow")
        )
        return True

    state["study_group_block"] = group_block
    state["study_group_prefilled"] = True

    if len(tokens) == 2:
        if has_images:
            return False
        await matcher.pause(tr(locale, "wordbank.guided.study.trigger_prompt"))
        return True

    trigger_text = rest_after_token(source, tokens[1])
    if not trigger_text:
        await matcher.pause(tr(locale, "wordbank.guided.study.trigger_prompt"))
        return True

    if len(tokens) == 3 and not has_images:
        state["study_trigger_shape"] = shape_from_text(trigger_text)
        state["study_trigger_preloaded"] = True
        state["study_response_after_preloaded_trigger"] = True
        await matcher.pause(tr(locale, "wordbank.guided.study.response_prompt"))
        return True

    return False


async def _cancel_study_resources(
    state: Mapping[str, Any],
    cleanup_keys: tuple[str, ...] = STUDY_RECALL_PENDING_KEYS,
) -> None:
    await cancel_state_resources(
        state,
        cleanup_keys,
        cleaners={},
    )


async def _record_study_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import build_message_shape_from_message
    from src.plugins.wordbank.services import wordbank_media_service

    shape = await build_message_shape_from_message(
        wordbank_media_service,
        event.message,
    )
    if shape.is_empty():
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.trigger_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = _study_locale(state)
    snapshot = _copy_study_state(
        state,
        keep_keys=(
            "study_trig_mode",
            "study_group_block",
            "study_trigger_preloaded",
            "study_response_after_preloaded_trigger",
        ),
    )
    state["study_trigger_shape"] = shape
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_TRIGGER,
        locale=locale,
        snapshot=snapshot,
    )
    await matcher.pause(tr(locale, "wordbank.guided.study.response_prompt"))


async def _record_study_response(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import build_message_shape_from_message
    from src.plugins.wordbank.services import wordbank_media_service

    shape = await build_message_shape_from_message(
        wordbank_media_service,
        event.message,
    )
    if shape.is_empty():
        await _reject_study_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.response_empty"),
        )
        return
    clear_interaction_errors(state)
    locale = _study_locale(state)
    snapshot = _copy_study_state(
        state,
        keep_keys=(
            "study_trig_mode",
            "study_group_block",
            "study_trigger_shape",
            "study_trigger_preloaded",
            "study_response_after_preloaded_trigger",
            "study_weight_after_preloaded_trigger",
        ),
    )
    state["study_response_shape"] = shape
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_RESPONSE,
        locale=locale,
        snapshot=snapshot,
    )
    await matcher.pause(tr(locale, "wordbank.guided.study.weight_prompt"))


async def _record_study_weight_and_finish(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers.commands import (
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
            _study_error_message(exc, locale, default_feature="guided-flow"),
        )
        return
    clear_interaction_errors(state)
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_WEIGHT,
        locale=locale,
        snapshot=_copy_study_state(
            state,
            keep_keys=(
                "study_trig_mode",
                "study_group_block",
                "study_trigger_shape",
                "study_response_shape",
                "study_trigger_preloaded",
                "study_response_after_preloaded_trigger",
                "study_weight_after_preloaded_trigger",
            ),
        ),
    )
    await _finish_guided_study(bot, matcher, event, state, locale)


async def _finish_guided_study(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    from src.plugins.wordbank.handlers import (
        build_add_result_message,
        handle_guided_study_shape_result,
        record_submission_approval_message,
        schedule_pending_approval_notice,
    )
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service
    from src.plugins.wordbank.services.rules import RuleError

    try:
        trigger_shape = _state_message_shape(state, "study_trigger_shape")
        response_shape = _state_message_shape(state, "study_response_shape")
        if trigger_shape is None or trigger_shape.is_empty():
            raise RuleError(
                _default_i18n_text("wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        if response_shape is None or response_shape.is_empty():
            raise RuleError(
                _default_i18n_text("wordbank.error.response_empty"),
                key="wordbank.error.response_empty",
            )
        result = await handle_guided_study_shape_result(
            wordbank_service,
            event=event,
            trig_mode_text=str(state.get("study_trig_mode", "")),
            group_block_text=str(state.get("study_group_block", "")),
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            weight_text=event.message.extract_plain_text(),
        )
    except (RuleError, ValueError) as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_message(exc, locale, default_feature="guided-flow"),
        )
        return
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


async def _start_guided_study_with_trigger_image(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
    arg: Message,
) -> None:
    from src.plugins.wordbank.handlers import build_message_shape_from_message
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service

    await wordbank_service.initialize()
    state["study_locale"] = locale
    shape = await build_message_shape_from_message(wordbank_media_service, arg)
    if shape.is_empty():
        await matcher.pause(tr(locale, "wordbank.guided.study.mode_prompt"))
        return
    clear_interaction_errors(state)
    register_root_message(state, event)
    state["study_trigger_shape"] = shape
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
        build_add_result_message,
        extract_image_urls,
        fetch_image_bytes_from_message,
        handle_study_with_media_result,
        record_submission_approval_message,
        schedule_pending_approval_notice,
    )
    from src.plugins.wordbank.services import wordbank_media_service, wordbank_service
    from src.plugins.wordbank.services.rules import RuleError

    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_study_on_revoke(matcher, event, locale)
    arg_text = arg.extract_plain_text()
    has_images = bool(extract_image_urls(arg))
    if not has_meaningful_text(arg_text):
        if has_images:
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
        register_root_message(state, event)
        await matcher.pause(tr(locale, "wordbank.guided.study.mode_prompt"))
        return
    if await _start_guided_study_from_partial_args(
        matcher,
        event,
        state,
        locale,
        arg_text,
        has_images=has_images,
    ):
        return
    await wordbank_service.initialize()
    try:
        if has_images:
            await matcher.send(tr(locale, "wordbank.add.processing_with_media"))
        image_items = await fetch_image_bytes_from_message(arg, limit=2)
        result = await handle_study_with_media_result(
            wordbank_service,
            wordbank_media_service,
            event=event,
            text=arg.extract_plain_text(),
            image_bytes=image_items[0] if image_items else None,
            extra_image_bytes=image_items[1:],
        )
    except (RuleError, ValueError) as exc:
        await matcher.finish(
            _study_error_message(exc, locale, default_feature="shortcut")
        )
        return
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


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import (
        parse_study_mode_choice,
    )
    from src.plugins.wordbank.services.rules import RuleError

    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if _is_truthy_state_flag(state, "study_mode_prefilled"):
        state.pop("study_mode_prefilled", None)
        return
    text = event.message.extract_plain_text()
    try:
        parse_study_mode_choice(text)
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_message(exc, locale, default_feature="guided-flow"),
        )
        return
    clear_interaction_errors(state)
    state["study_trig_mode"] = text
    mode_keep_keys = ("study_trigger_preloaded",)
    mode_cleanup_keys = STUDY_RECALL_PENDING_KEYS
    if _is_truthy_state_flag(state, "study_trigger_preloaded"):
        mode_keep_keys = (
            "study_trigger_preloaded",
            "study_trigger_shape",
        )
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_MODE,
        locale=locale,
        snapshot=_copy_study_state(state, keep_keys=mode_keep_keys),
        cleanup_keys=mode_cleanup_keys,
    )
    await matcher.pause(tr(locale, "wordbank.guided.study.group_block_prompt"))


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    from src.plugins.wordbank.handlers.commands import (
        parse_study_group_block_choice,
    )
    from src.plugins.wordbank.services.rules import RuleError

    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if _is_truthy_state_flag(state, "study_group_prefilled"):
        state.pop("study_group_prefilled", None)
        return
    text = event.message.extract_plain_text()
    try:
        parse_study_group_block_choice(text)
    except RuleError as exc:
        await _reject_study_error(
            matcher,
            state,
            locale,
            _study_error_message(exc, locale, default_feature="guided-flow"),
        )
        return
    clear_interaction_errors(state)
    state["study_group_block"] = text
    keep_keys = (
        "study_trig_mode",
        "study_trigger_preloaded",
    )
    cleanup_keys = STUDY_RECALL_PENDING_KEYS
    if _is_truthy_state_flag(state, "study_trigger_preloaded"):
        keep_keys = (
            "study_trig_mode",
            "study_trigger_preloaded",
            "study_trigger_shape",
        )
    _register_study_checkpoint(
        state,
        event,
        step_index=STUDY_STEP_GROUP_BLOCK,
        locale=locale,
        snapshot=_copy_study_state(state, keep_keys=keep_keys),
        cleanup_keys=cleanup_keys,
    )
    if _is_truthy_state_flag(state, "study_trigger_preloaded"):
        state["study_response_after_preloaded_trigger"] = True
        await matcher.pause(tr(locale, "wordbank.guided.study.response_prompt"))
        return
    await matcher.pause(tr(locale, "wordbank.guided.study.trigger_prompt"))


def _study_locale(state: T_State) -> LocaleCode:
    locale = state.get("study_locale", "zh-CN")
    return locale if locale in {"zh-CN", "lzh", "x-meme"} else "zh-CN"


@study_command.handle()
async def _(matcher: Matcher, event: MessageEvent, state: T_State) -> None:
    locale = _study_locale(state)
    await _abort_study_on_revoke(matcher, event, locale)
    if _is_truthy_state_flag(state, "study_weight_after_preloaded_trigger"):
        return
    if _is_truthy_state_flag(state, "study_response_after_preloaded_trigger"):
        state.pop("study_response_after_preloaded_trigger", None)
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


@study_recall_notice.handle()
async def _(bot: Bot, matcher: Matcher, event: NoticeEvent) -> None:
    if not is_supported_recall_notice(event):
        return

    session = find_recall_session(
        study_command,
        cast(GroupRecallNoticeEvent | FriendRecallNoticeEvent, event),
    )
    if session is None:
        return

    locale = "zh-CN"
    checkpoint = session.checkpoint
    state = session.matcher_cls._default_state
    if "study_locale" in state:
        locale = _study_locale(state)

    await _cancel_study_resources(
        state,
        checkpoint.cleanup_keys
        if checkpoint is not None and not session.is_root_message
        else STUDY_RECALL_PENDING_KEYS,
    )
    session.matcher_cls.destroy()

    if session.is_root_message or checkpoint is None:
        await matcher.send(tr(locale, "interaction.cancelled"))
        return

    rebuild_temp_matcher(
        session.matcher_cls,
        study_command,
        step_index=checkpoint.step_index,
        state=checkpoint.state_snapshot,
    )
    await matcher.send(checkpoint.prompt)
