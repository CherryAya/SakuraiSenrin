"""Command and guided-flow handler registration for the wordbank plugin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.typing import T_State

from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import abort_if_revoke_signal, clear_interaction_errors
from src.lib.message_plan import DeliveryPlan, deliver_message_plan

from .guided_flow import (
    WORDBANK_GUIDED_SEARCH_STAGE_CREATOR,
    WORDBANK_GUIDED_SEARCH_STAGE_DIMENSIONS,
    WORDBANK_GUIDED_SEARCH_STAGE_QUERY,
    WORDBANK_GUIDED_STEP_ADVANCED,
    WORDBANK_GUIDED_STEP_SCOPE,
)
from .handlers import (
    GROUP_ALIASES,
    SubmissionHandler,
    build_forced_command_text,
    dispatch_wordbank_command,
    parse_group_view_args,
)
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
from .services import wordbank_media_service, wordbank_service
from .services.rules import RuleError
from .text_parsing import (
    has_meaningful_text,
    rest_after_token,
    split_command_text,
    tokenize_shell_like,
)

ErrorBuilder = Callable[..., Message | str]
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


def register_wordbank_command_handlers(
    *,
    wordbank_command: Any,
    wordbank_add_command: Any,
    wordbank_search_command: Any,
    wordbank_pending_command: Any,
    wordbank_rank_command: Any,
    wordbank_approve_command: Any,
    wordbank_reject_command: Any,
    wordbank_delete_command: Any,
    wordbank_restore_command: Any,
    initialize_plugin: Callable[[], Awaitable[None]],
    build_error_message: ErrorBuilder,
    finalize_submission: SubmissionHandler,
    collect_search_query_content: SearchQueryCollector,
    start_guided_add: Callable[
        [Matcher, MessageEvent, T_State, LocaleCode], Awaitable[None]
    ],
    start_guided_add_with_trigger_image: Callable[
        [Matcher, MessageEvent, T_State, LocaleCode, Message],
        Awaitable[None],
    ],
    finish_guided_add: Callable[[Bot, Matcher, MessageEvent, T_State], Awaitable[None]],
    start_guided_search: Callable[
        [Matcher, MessageEvent, T_State, LocaleCode],
        Awaitable[None],
    ],
    finish_guided_search: Callable[..., Awaitable[None]],
    handle_search_session_event: Callable[
        [Bot, Matcher, MessageEvent, T_State, LocaleCode],
        Awaitable[None],
    ],
    record_guided_trigger: Callable[
        [Matcher, MessageEvent, T_State, LocaleCode],
        Awaitable[None],
    ],
    record_guided_response: Callable[
        [Matcher, MessageEvent, T_State, LocaleCode],
        Awaitable[None],
    ],
    guided_search_stage: Callable[[T_State], str | None],
    reject_guided_error: Callable[
        [Matcher, T_State, LocaleCode, Message | str], Awaitable[None]
    ],
    register_guided_checkpoint: Callable[..., None],
    guided_locale: Callable[[T_State], LocaleCode],
    copy_guided_state: Callable[..., dict[str, Any]],
    send_group_detail_view: Callable[..., Awaitable[None]],
    send_search_result_view: Callable[..., Awaitable[None]],
    resolve_locale_fn: Callable[[str | None], Awaitable[LocaleCode]] = resolve_locale,
    handle_wordbank_command_message_fn: Callable[..., Awaitable[None]] | None = None,
    send_pending_entries_view: Callable[
        [Bot, MessageEvent, str, LocaleCode],
        Awaitable[None],
    ]
    | None = None,
) -> None:
    async def _call_dynamic(name: str, *args: Any, **kwargs: Any) -> Any:
        from src.plugins import wordbank as wordbank_plugin

        target = getattr(wordbank_plugin, name)
        result = target(*args, **kwargs)
        if isawaitable(result):
            return await result
        return result

    async def handle_wordbank_command_message(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        arg: Message,
        *,
        forced_action: str | None = None,
        state: T_State | None = None,
    ) -> None:
        await initialize_plugin()
        locale = await resolve_locale_fn(str(getattr(event, "group_id", "")) or None)
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
            await send_group_detail_view(
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
                has_images = bool(await _call_dynamic("extract_image_urls", arg))
                if has_images:
                    await deliver_message_plan(
                        bot,
                        plan=DeliveryPlan(
                            messages=(
                                tr(locale, "wordbank.add.processing_with_media"),
                            ),
                            source_kind="wordbank_command",
                        ),
                        event=event,
                    )
                data = await _call_dynamic("fetch_first_image_bytes_from_message", arg)
                if data is not None:
                    result = await _call_dynamic(
                        "handle_add_with_media_result",
                        wordbank_service,
                        wordbank_media_service,
                        event=event,
                        image_bytes=data,
                        text=rest,
                    )
                else:
                    result = await _call_dynamic(
                        "handle_add_text_result",
                        wordbank_service,
                        event=event,
                        text=rest,
                    )
            except (RuleError, ValueError) as exc:
                await matcher.finish(
                    build_error_message(exc, locale, default_feature="add")
                )
                return
            await finalize_submission(matcher, bot, event, result, locale)
            return
        if action in {"search", "find", "查询", "搜索"}:
            try:
                (
                    keyword,
                    has_image,
                    search_image_scores,
                ) = await collect_search_query_content(arg, keyword_text=rest)
            except (RuleError, ValueError) as exc:
                await matcher.finish(
                    build_error_message(exc, locale, default_feature="search")
                )
                return
            try:
                await _call_dynamic(
                    "_send_search_result_view",
                    bot,
                    matcher,
                    event,
                    locale,
                    keyword=keyword,
                    image_scores=search_image_scores if has_image else None,
                    state=state,
                )
            except (RuleError, ValueError) as exc:
                await matcher.finish(
                    build_error_message(exc, locale, default_feature="search")
                )
            return
        if action in {"详情", *GROUP_ALIASES}:
            try:
                parsed_group = parse_group_view_args(rest)
                await _call_dynamic(
                    "_send_group_detail_view",
                    bot,
                    matcher,
                    event,
                    locale,
                    trigger_group_id=parsed_group.trigger_group_id,
                    page=parsed_group.page,
                )
            except (RuleError, ValueError) as exc:
                await matcher.finish(
                    build_error_message(
                        exc,
                        locale,
                        default_feature="reply-shortcut",
                    )
                )
            return
        if action in PENDING_ALIASES and send_pending_entries_view is not None:
            await send_pending_entries_view(bot, event, rest, locale)
            await matcher.finish()
            return

        try:
            msg = await dispatch_wordbank_command(
                wordbank_service,
                event=event,
                text=text,
                locale=locale,
                raw_message=arg,
                search_image_scores=search_image_scores,
                media_service=wordbank_media_service,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(build_error_message(exc, locale))
            return
        await matcher.finish(msg)

    setattr(
        register_wordbank_command_handlers,
        "_handle_wordbank_command_message",
        handle_wordbank_command_message,
    )

    @wordbank_command.handle()
    async def _wordbank_root(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
        arg: Message = CommandArg(),
    ) -> None:
        locale = await resolve_locale_fn(str(getattr(event, "group_id", "")) or None)
        await _abort_guided_on_revoke(matcher, event, locale)
        text = arg.extract_plain_text()
        if has_meaningful_text(text):
            first, tail = split_command_text(text)
            if first in {"add", "添加", "学习"} and not has_meaningful_text(tail):
                if await _call_dynamic("extract_image_urls", arg):
                    await _call_dynamic(
                        "_start_guided_add_with_trigger_image",
                        matcher,
                        event,
                        state,
                        locale,
                        arg,
                    )
                else:
                    await _call_dynamic(
                        "_start_guided_add", matcher, event, state, locale
                    )
                return
        await initialize_plugin()
        handler = handle_wordbank_command_message_fn or handle_wordbank_command_message
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
        await record_guided_trigger(matcher, event, state, locale)

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
            await _call_dynamic(
                "_record_guided_forward_response_choice",
                matcher,
                event,
                state,
                locale,
                bot,
            )
            return
        await record_guided_response(matcher, event, state, locale)

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
                build_error_message(exc, locale, default_feature="add-scope"),
            )
            return
        clear_interaction_errors(state)
        locale = guided_locale(state)
        register_guided_checkpoint(
            state,
            event,
            step_index=WORDBANK_GUIDED_STEP_SCOPE,
            locale=locale,
            snapshot=copy_guided_state(
                state,
                keep_keys=(
                    "wordbank_guided_trigger_shape",
                    "wordbank_guided_response_shape",
                ),
            ),
        )
        state["wordbank_guided_scope"] = text
        await matcher.pause(tr(locale, "wordbank.guided.add.advanced_prompt"))

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
                build_error_message(exc, locale, default_feature="add"),
            )
            return
        clear_interaction_errors(state)
        locale = guided_locale(state)
        register_guided_checkpoint(
            state,
            event,
            step_index=WORDBANK_GUIDED_STEP_ADVANCED,
            locale=locale,
            snapshot=copy_guided_state(
                state,
                keep_keys=(
                    "wordbank_guided_trigger_shape",
                    "wordbank_guided_response_shape",
                    "wordbank_guided_scope",
                ),
            ),
        )
        await finish_guided_add(bot, matcher, event, state)

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
        await _call_dynamic(
            "_handle_search_session_event", bot, matcher, event, state, locale
        )

    @wordbank_add_command.handle()
    async def _wordbank_add_root(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
        arg: Message = CommandArg(),
    ) -> None:
        await initialize_plugin()
        locale = await resolve_locale_fn(str(getattr(event, "group_id", "")) or None)
        await _abort_guided_on_revoke(matcher, event, locale)
        plain_text = arg.extract_plain_text()
        has_images = bool(await _call_dynamic("extract_image_urls", arg))
        if not has_meaningful_text(plain_text) and not has_images:
            await _call_dynamic("_start_guided_add", matcher, event, state, locale)
            return
        if not has_meaningful_text(plain_text) and has_images:
            await _call_dynamic(
                "_start_guided_add_with_trigger_image",
                matcher,
                event,
                state,
                locale,
                arg,
            )
            return
        handler = handle_wordbank_command_message_fn or handle_wordbank_command_message
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
        await record_guided_trigger(matcher, event, state, locale)

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
            await _call_dynamic(
                "_record_guided_forward_response_choice",
                matcher,
                event,
                state,
                locale,
                bot,
            )
            return
        await record_guided_response(matcher, event, state, locale)

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
        await initialize_plugin()
        locale = await resolve_locale_fn(str(getattr(event, "group_id", "")) or None)
        await _abort_guided_on_revoke(matcher, event, locale)
        has_images = bool(await _call_dynamic("extract_image_urls", arg))
        if not has_meaningful_text(arg.extract_plain_text()) and not has_images:
            await _call_dynamic("_start_guided_search", matcher, event, state, locale)
            return
        handler = handle_wordbank_command_message_fn or handle_wordbank_command_message
        arg_for_handler = (
            Message(_raw_rest_after_first_token(event.raw_message))
            if not has_images
            else arg
        )
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
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
    ) -> None:
        locale = state.get("wordbank_locale", "zh-CN")
        await _abort_guided_on_revoke(matcher, event, locale)
        if guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_DIMENSIONS:
            return
        try:
            selection = parse_guided_search_mode_choice(
                event.message.extract_plain_text()
            )
        except RuleError as exc:
            await reject_guided_error(
                matcher,
                state,
                locale,
                build_error_message(exc, locale, default_feature="search"),
            )
            return
        clear_interaction_errors(state)
        state["wordbank_guided_search_field"] = selection.field
        state["wordbank_guided_search_requires_creator"] = selection.requires_creator
        if selection.requires_query:
            state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_QUERY
            await matcher.pause(tr(locale, "wordbank.guided.search.keyword_prompt"))
            return
        if selection.requires_creator:
            state["wordbank_guided_search_stage"] = WORDBANK_GUIDED_SEARCH_STAGE_CREATOR
            await matcher.pause(tr(locale, "wordbank.guided.search.creator_prompt"))
            return
        await _call_dynamic(
            "_finish_guided_search",
            matcher,
            state,
            event,
            locale,
            page_number=1,
        )

    @wordbank_search_command.handle()
    async def _wordbank_search_query(
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
    ) -> None:
        locale = state.get("wordbank_locale", "zh-CN")
        await _abort_guided_on_revoke(matcher, event, locale)
        if guided_search_stage(state) != WORDBANK_GUIDED_SEARCH_STAGE_QUERY:
            return
        try:
            keyword, has_image, image_scores = await collect_search_query_content(
                event.message,
                keyword_text=event.message.extract_plain_text(),
            )
        except (RuleError, ValueError) as exc:
            await reject_guided_error(
                matcher,
                state,
                locale,
                build_error_message(exc, locale, default_feature="search"),
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
            await matcher.pause(tr(locale, "wordbank.guided.search.creator_prompt"))
            return
        await _call_dynamic(
            "_finish_guided_search",
            matcher,
            state,
            event,
            locale,
            page_number=1,
        )

    @wordbank_search_command.handle()
    async def _wordbank_search_creator(
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
                build_error_message(exc, locale, default_feature="search"),
            )
            return
        clear_interaction_errors(state)
        state["wordbank_guided_search_creator_id"] = creator_id
        if (
            bool(state.get("wordbank_guided_search_requires_creator"))
            and not creator_id
        ):
            await reject_guided_error(
                matcher,
                state,
                locale,
                tr(locale, "wordbank.error.guided_search_creator_empty"),
            )
            return
        await _call_dynamic(
            "_finish_guided_search",
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
        await _call_dynamic(
            "_handle_search_session_event", bot, matcher, event, state, locale
        )

    def _register_forced_command(matcher_obj: Any, action: str) -> None:
        @matcher_obj.handle()
        async def _forced_command(
            bot: Bot,
            matcher: Matcher,
            event: MessageEvent,
            arg: Message = CommandArg(),
        ) -> None:
            handler = (
                handle_wordbank_command_message_fn or handle_wordbank_command_message
            )
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
