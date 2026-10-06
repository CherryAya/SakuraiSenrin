"""Command and guided-flow handler registration for the wordbank plugin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.typing import T_State

from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import abort_if_revoke_signal, clear_interaction_errors
from src.lib.long_task import (
    CompositeProgressSink,
    LoggerProgressSink,
    LongTaskRunner,
    LongTaskSpec,
    MessageEventProgressSink,
)
from src.lib.message_plan import (
    MessagePlanInput,
    finish_with_message,
    pause_with_message,
)

from . import guided_flow as guided_flow_module
from . import (
    views,
    wordbank_add_command,
    wordbank_approve_command,
    wordbank_command,
    wordbank_delete_command,
    wordbank_pending_command,
    wordbank_rank_command,
    wordbank_reject_command,
    wordbank_restore_command,
    wordbank_search_command,
)
from .errors import build_wordbank_error_message
from .flows import (
    _build_wordbank_command_progress_spec,
    _finish_guided_add,
    _send_pending_entries_view,
    _wordbank_submission_lifecycle,
)
from .guided_flow import (
    WORDBANK_GUIDED_SEARCH_STAGE_CREATOR,
    WORDBANK_GUIDED_SEARCH_STAGE_DIMENSIONS,
    WORDBANK_GUIDED_SEARCH_STAGE_QUERY,
    WORDBANK_GUIDED_STEP_ADVANCED,
    WORDBANK_GUIDED_STEP_SCOPE,
    copy_guided_state,
    guided_response_state_keys,
    guided_search_stage,
    register_guided_checkpoint,
    reject_guided_error,
    wordbank_guided_locale,
)
from .handlers import (
    GROUP_ALIASES,
    build_forced_command_text,
    dispatch_wordbank_command_with_outcome,
    parse_group_view_args,
)
from .handlers import commands as handlers_commands
from .handlers import media_helpers as handlers_media_helpers
from .handlers.commands import (
    PENDING_ALIASES,
    parse_guided_advanced_options,
    parse_guided_scope_choice,
)
from .handlers.parsers import (
    parse_guided_search_creator_filter,
    parse_guided_search_mode_choice,
    parse_search_session_command,
)
from .lifecycle import _initialize_wordbank_plugin
from .notify import notify_creator_review_result
from .services import wordbank_media_service, wordbank_service
from .services.rules import RuleError
from .text_parsing import (
    has_meaningful_text,
    rest_after_token,
    split_command_text,
    tokenize_shell_like,
)

ErrorBuilder = Callable[..., MessagePlanInput]
SearchQueryCollector = Callable[..., Awaitable[tuple[str, bool, dict[int, float]]]]


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


def _raw_rest_after_first_token(text: str) -> str:
    source = text.lstrip()
    if not source:
        return ""
    tokens = tokenize_shell_like(source)
    if not tokens:
        return ""
    return rest_after_token(source, tokens[0]).lstrip()


async def _run_wordbank_command_with_optional_progress(
    action: str,
    *,
    rest: str,
    bot: Bot,
    event: MessageEvent,
    locale: LocaleCode,
    work: Callable[[], Awaitable[MessagePlanInput]],
) -> MessagePlanInput:
    spec = _build_wordbank_command_progress_spec(action, rest=rest, locale=locale)
    if spec is None:
        return await work()
    async with LongTaskRunner(
        spec,
        sink=CompositeProgressSink(
            LoggerProgressSink(),
            MessageEventProgressSink(bot, event),
        ),
    ) as long_task:
        await long_task.advance("rendering")
        return await work()


async def handle_wordbank_command_message(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message,
    *,
    forced_action: str | None = None,
    state: T_State | None = None,
) -> None:
    await _initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    text = build_forced_command_text(forced_action, arg.extract_plain_text())
    action, rest = split_command_text(text)
    search_image_scores: dict[int, float] | None = None
    try:
        parsed_session_command = parse_search_session_command(text)
    except RuleError:
        parsed_session_command = None
    if (
        parsed_session_command is not None
        and parsed_session_command.action == "detail"
        and parsed_session_command.trigger_group_id is not None
    ):
        await views.send_group_detail_view(
            bot,
            matcher,
            event,
            locale,
            trigger_group_id=parsed_session_command.trigger_group_id,
            page=parsed_session_command.page or 1,
        )
        return
    if action in {"add", "添加", "学习"}:
        try:
            has_images = bool(handlers_media_helpers.extract_image_urls(arg))
            if not has_images:
                result = await handlers_commands.handle_add_text_result(
                    wordbank_service,
                    event=event,
                    text=rest,
                )
            else:
                long_task = LongTaskRunner(
                    LongTaskSpec(
                        task_name="wordbank.add.media_submission",
                        source_kind="wordbank_command",
                        prompt=tr(locale, "wordbank.add.processing_with_media"),
                        threshold_ms=800,
                    ),
                    sink=CompositeProgressSink(
                        LoggerProgressSink(),
                        MessageEventProgressSink(bot, event),
                    ),
                )
                async with long_task:
                    data = await (
                        handlers_media_helpers.fetch_first_image_bytes_from_message(
                            arg,
                            task=long_task,
                        )
                    )
                    if data is None:
                        result = await handlers_commands.handle_add_text_result(
                            wordbank_service,
                            event=event,
                            text=rest,
                        )
                    else:
                        result = await handlers_commands.handle_add_with_media_result(
                            wordbank_service,
                            wordbank_media_service,
                            event=event,
                            image_bytes=data,
                            text=rest,
                            task=long_task,
                        )
                    await long_task.advance("submitting")
        except (RuleError, ValueError) as exc:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_wordbank_error_message(
                    exc, locale, default_feature="add"
                ),
                source_kind="wordbank_command",
            )
            return
        await _wordbank_submission_lifecycle.finalize(
            matcher, bot, event, result, locale
        )
        return
    if action in {"search", "find", "查询", "搜索"}:
        try:
            (
                keyword,
                has_image,
                search_image_scores,
            ) = await guided_flow_module.collect_search_query_content(
                arg, keyword_text=rest
            )
        except (RuleError, ValueError) as exc:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_wordbank_error_message(
                    exc,
                    locale,
                    default_feature="search",
                ),
                source_kind="wordbank_command",
            )
            return
        try:
            await views.send_search_result_view(
                bot,
                matcher,
                event,
                locale,
                keyword=keyword,
                image_scores=search_image_scores if has_image else None,
                state=state,
            )
        except (RuleError, ValueError) as exc:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_wordbank_error_message(
                    exc,
                    locale,
                    default_feature="search",
                ),
                source_kind="wordbank_command",
            )
        return
    if action in {"详情", *GROUP_ALIASES}:
        try:
            parsed_group = parse_group_view_args(rest)
            await views.send_group_detail_view(
                bot,
                matcher,
                event,
                locale,
                trigger_group_id=parsed_group.trigger_group_id,
                page=parsed_group.page,
            )
        except (RuleError, ValueError) as exc:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_wordbank_error_message(
                    exc,
                    locale,
                    default_feature="reply-shortcut",
                ),
                source_kind="wordbank_command",
            )
        return
    if action in PENDING_ALIASES:
        await _send_pending_entries_view(bot, event, rest, locale)
        await matcher.finish()
        return

    async def _dispatch_command() -> MessagePlanInput:
        message, outcome = await dispatch_wordbank_command_with_outcome(
            wordbank_service,
            event=event,
            text=text,
            locale=locale,
            raw_message=arg,
            search_image_scores=search_image_scores,
            media_service=wordbank_media_service,
        )
        if outcome is not None and outcome.completed and outcome.action:
            await notify_creator_review_result(
                bot,
                response_item_id=outcome.response_item_id,
                action=outcome.action,
                locale=locale,
                reviewer_id=str(event.user_id),
            )
        return message

    try:
        msg = await _run_wordbank_command_with_optional_progress(
            action,
            rest=rest,
            bot=bot,
            event=event,
            locale=locale,
            work=_dispatch_command,
        )
    except (RuleError, ValueError) as exc:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=build_wordbank_error_message(exc, locale),
            source_kind="wordbank_command",
        )
        return
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=msg,
        source_kind="wordbank_command",
    )


@wordbank_command.handle()
async def _wordbank_root(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_guided_on_revoke(matcher, event, locale)
    text = arg.extract_plain_text()
    if has_meaningful_text(text):
        first, tail = split_command_text(text)
        if first in {"add", "添加", "学习"} and not has_meaningful_text(tail):
            if handlers_media_helpers.extract_image_urls(arg):
                await guided_flow_module.start_guided_add_with_trigger_image(
                    matcher,
                    event,
                    state,
                    locale,
                    arg,
                )
            else:
                await guided_flow_module.start_guided_add(matcher, event, state, locale)
            return
    await _initialize_wordbank_plugin()
    handler = handle_wordbank_command_message
    await handler(bot, matcher, event, arg, state=state)


@wordbank_command.handle()
async def _wordbank_guided_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    if guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await guided_flow_module.record_guided_trigger(matcher, event, state, locale)


@wordbank_command.handle()
async def _wordbank_guided_response(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    if guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if state.get("wordbank_guided_response_forward_pending"):
        await guided_flow_module.record_guided_forward_response_choice(
            matcher,
            event,
            state,
            locale,
            bot=bot,
        )
        return
    await guided_flow_module.record_guided_response(matcher, event, state, locale)


async def _handle_scope_step(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    text = event.message.extract_plain_text()
    try:
        parse_guided_scope_choice(
            text,
            is_group=bool(getattr(event, "group_id", "")),
        )
    except RuleError as exc:
        await reject_guided_error(
            matcher,
            state,
            locale,
            build_wordbank_error_message(exc, locale, default_feature="add-scope"),
        )
        return
    clear_interaction_errors(state)
    locale = wordbank_guided_locale(state)
    register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_SCOPE,
        locale=locale,
        snapshot=copy_guided_state(
            state,
            keep_keys=(
                "wordbank_guided_trigger_shape",
                *guided_response_state_keys(state),
            ),
        ),
    )
    state["wordbank_guided_scope"] = text
    await pause_with_message(
        matcher,
        message=tr(locale, "wordbank.guided.add.advanced_prompt"),
    )


async def _handle_advanced_step(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    locale: LocaleCode,
) -> None:
    try:
        parse_guided_advanced_options(event.message.extract_plain_text())
    except RuleError as exc:
        await reject_guided_error(
            matcher,
            state,
            locale,
            build_wordbank_error_message(exc, locale, default_feature="add"),
        )
        return
    clear_interaction_errors(state)
    locale = wordbank_guided_locale(state)
    register_guided_checkpoint(
        state,
        event,
        step_index=WORDBANK_GUIDED_STEP_ADVANCED,
        locale=locale,
        snapshot=copy_guided_state(
            state,
            keep_keys=(
                "wordbank_guided_trigger_shape",
                "wordbank_guided_scope",
                *guided_response_state_keys(state),
            ),
        ),
    )
    await _finish_guided_add(bot, matcher, event, state)


@wordbank_command.handle()
async def _wordbank_scope_step(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    if guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _handle_scope_step(matcher, event, state, locale)


@wordbank_command.handle()
async def _wordbank_advanced_step(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    if guided_search_stage(state):
        return
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _handle_advanced_step(bot, matcher, event, state, locale)


@wordbank_command.handle()
async def _wordbank_session(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await guided_flow_module.handle_search_session_event(
        bot,
        matcher,
        event,
        state,
        locale,
    )


@wordbank_add_command.handle()
async def _wordbank_add_root(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    await _initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_guided_on_revoke(matcher, event, locale)
    plain_text = arg.extract_plain_text()
    has_images = bool(handlers_media_helpers.extract_image_urls(arg))
    if not has_meaningful_text(plain_text) and not has_images:
        await guided_flow_module.start_guided_add(matcher, event, state, locale)
        return
    if not has_meaningful_text(plain_text) and has_images:
        await guided_flow_module.start_guided_add_with_trigger_image(
            matcher,
            event,
            state,
            locale,
            arg,
        )
        return
    handler = handle_wordbank_command_message
    await handler(
        bot,
        matcher,
        event,
        arg,
        forced_action="add",
        state=state,
    )


@wordbank_add_command.handle()
async def _wordbank_add_trigger(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await guided_flow_module.record_guided_trigger(matcher, event, state, locale)


@wordbank_add_command.handle()
async def _wordbank_add_response(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if state.get("wordbank_guided_response_forward_pending"):
        await guided_flow_module.record_guided_forward_response_choice(
            matcher,
            event,
            state,
            locale,
            bot=bot,
        )
        return
    await guided_flow_module.record_guided_response(matcher, event, state, locale)


@wordbank_add_command.handle()
async def _wordbank_add_scope(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _handle_scope_step(matcher, event, state, locale)


@wordbank_add_command.handle()
async def _wordbank_add_advanced(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await _handle_advanced_step(bot, matcher, event, state, locale)


@wordbank_search_command.handle()
async def _wordbank_search_root(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
    arg: Message = CommandArg(),
) -> None:
    await _initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    await _abort_guided_on_revoke(matcher, event, locale)
    has_images = bool(handlers_media_helpers.extract_image_urls(arg))
    if not has_meaningful_text(arg.extract_plain_text()) and not has_images:
        await guided_flow_module.start_guided_search(
            matcher,
            event,
            state,
            locale,
        )
        return
    handler = handle_wordbank_command_message
    if not has_images:
        arg_for_handler = Message()
        arg_for_handler += MessageSegment.text(
            _raw_rest_after_first_token(event.raw_message)
        )
    else:
        arg_for_handler = arg
    await handler(
        bot,
        matcher,
        event,
        arg_for_handler,
        forced_action="search",
        state=state,
    )


@wordbank_search_command.handle()
async def _wordbank_search_dimensions(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_DIMENSIONS:
        return
    try:
        selection = parse_guided_search_mode_choice(event.message.extract_plain_text())
    except RuleError as exc:
        await reject_guided_error(
            matcher,
            state,
            locale,
            build_wordbank_error_message(exc, locale, default_feature="search"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_field"] = selection.field
    state["wordbank_guided_search_requires_creator"] = selection.requires_creator
    if selection.requires_query:
        state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_QUERY
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.search.keyword_prompt"),
        )
        return
    if selection.requires_creator:
        state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.search.creator_prompt"),
        )
        return
    await views.finish_guided_search_view(
        bot,
        matcher,
        state,
        event,
        locale,
        page_number=1,
    )


@wordbank_search_command.handle()
async def _wordbank_search_query(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_QUERY:
        return
    try:
        (
            keyword,
            has_image,
            image_scores,
        ) = await guided_flow_module.collect_search_query_content(
            event.message,
            keyword_text=event.message.extract_plain_text(),
        )
    except (RuleError, ValueError) as exc:
        await reject_guided_error(
            matcher,
            state,
            locale,
            build_wordbank_error_message(exc, locale, default_feature="search"),
        )
        return
    if not keyword and not has_image:
        await reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.guided_search_keyword_empty"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_keyword"] = keyword
    state["wordbank_guided_search_has_image"] = has_image
    state["wordbank_guided_search_image_scores"] = image_scores
    if bool(state.get("wordbank_guided_search_requires_creator")):
        state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
        await pause_with_message(
            matcher,
            message=tr(locale, "wordbank.guided.search.creator_prompt"),
        )
        return
    await views.finish_guided_search_view(
        bot,
        matcher,
        state,
        event,
        locale,
        page_number=1,
    )


@wordbank_search_command.handle()
async def _wordbank_search_creator(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    if guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_CREATOR:
        return
    try:
        creator_id = parse_guided_search_creator_filter(
            event.message.extract_plain_text()
        )
    except RuleError as exc:
        await reject_guided_error(
            matcher,
            state,
            locale,
            build_wordbank_error_message(exc, locale, default_feature="search"),
        )
        return
    clear_interaction_errors(state)
    state["wordbank_guided_search_creator_id"] = creator_id
    if bool(state.get("wordbank_guided_search_requires_creator")) and not creator_id:
        await reject_guided_error(
            matcher,
            state,
            locale,
            tr(locale, "wordbank.error.guided_search_creator_empty"),
        )
        return
    await views.finish_guided_search_view(
        bot,
        matcher,
        state,
        event,
        locale,
        page_number=1,
    )


@wordbank_search_command.handle()
async def _wordbank_search_session(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    locale = state.get("wordbank_locale", "zh-CN")
    await _abort_guided_on_revoke(matcher, event, locale)
    await guided_flow_module.handle_search_session_event(
        bot,
        matcher,
        event,
        state,
        locale,
    )


def _register_forced_command(matcher_obj: Any, action: str) -> None:
    @matcher_obj.handle()
    async def _forced_command(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        arg: Message = CommandArg(),
    ) -> None:
        handler = handle_wordbank_command_message
        await handler(
            bot,
            matcher,
            event,
            arg,
            forced_action=action,
        )


_register_forced_command(wordbank_pending_command, "pending")


_register_forced_command(wordbank_rank_command, "rank")


_register_forced_command(wordbank_approve_command, "approve")


_register_forced_command(wordbank_reject_command, "reject")


_register_forced_command(wordbank_delete_command, "delete")


_register_forced_command(wordbank_restore_command, "restore")
