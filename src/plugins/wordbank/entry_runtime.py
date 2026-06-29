"""Reply, passive, and notice handler registration for the wordbank plugin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupMessageEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher

from src.database.core.consts import Permission
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.interaction import clear_interaction_errors
from src.lib.interactive_recall import (
    find_recall_session,
    is_supported_recall_notice,
    rebuild_temp_matcher,
    register_root_message,
)
from src.lib.messages import empty_message
from src.logger import logger

from .database.types import WordbankMessageRefRecord
from .guided_flow import WORDBANK_GUIDED_RECALL_PENDING_KEYS
from .handlers import (
    PassiveResponse,
    build_group_detail_message,
    handle_approval_reply_result,
    handle_passive_message,
    handle_passive_notice,
    handle_reply_command,
    parse_view_reply_for_group_detail,
    parse_view_reply_for_search_result,
)
from .handlers.commands import (
    ParsedSearch,
    execute_search_page,
    parse_search_args,
    render_search_page_message,
)
from .handlers.rendering import render_shape_message
from .services import wordbank_media_service, wordbank_service
from .services.rules import RuleError


def register_wordbank_runtime_handlers(
    *,
    wordbank_reply_command: Any,
    wordbank_approval_reply_command: Any,
    wordbank_view_reply_command: Any,
    wordbank_passive: Any,
    wordbank_notice: Any,
    wordbank_add_command: Any,
    wordbank_command: Any,
    initialize_plugin: Callable[[], Awaitable[None]],
    build_error_message: Callable[..., Message | str],
    cancel_guided_resources: Callable[..., Awaitable[None]],
    guided_locale: Callable[[Mapping[str, Any]], LocaleCode],
) -> dict[str, Callable[..., Awaitable[None]]]:
    async def _get_plugin_attr(name: str) -> Any:
        from src.plugins import wordbank as wordbank_plugin

        return getattr(wordbank_plugin, name)

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
            await wordbank_service.record_message_ref(
                ref_kind="response",
                message_id=message_id,
                trigger_group_id=response.trigger_group_id,
                trigger_variant_id=response.trigger_variant_id,
                response_item_id=response.response_item_id,
                group_id=response.group_id,
                user_id=response.user_id,
                message_type=response.message_type,
            )
        except Exception as exc:
            logger.warning(f"[Wordbank] response message record skipped: {exc}")

    def _event_message_type(event: MessageEvent) -> str:
        return "group" if isinstance(event, GroupMessageEvent) else "private"

    async def _record_view_message(
        *,
        send_result: Any,
        event: MessageEvent,
        context_type: str,
        trigger_group_id: int,
        current_page: int,
        keyword: str,
        field: str,
        creator_id: str,
        has_image: bool,
        group_ids: Sequence[int],
    ) -> None:
        message_id = _extract_sent_message_id(send_result)
        if message_id is None:
            return
        try:
            await wordbank_service.record_message_ref(
                ref_kind="view",
                message_id=message_id,
                context_type=context_type,
                trigger_group_id=trigger_group_id,
                current_page=current_page,
                keyword=keyword,
                field=field,
                creator_id=creator_id,
                has_image=has_image,
                group_ids=group_ids,
                group_id=str(getattr(event, "group_id", "") or ""),
                user_id=str(event.user_id),
                message_type=_event_message_type(event),
            )
        except Exception as exc:
            logger.warning(f"[Wordbank] view message record skipped: {exc}")

    async def _record_search_result_view_message(
        *,
        send_result: Any,
        event: MessageEvent,
        parsed: ParsedSearch,
        page: Any,
        has_image: bool,
    ) -> None:
        await _record_view_message(
            send_result=send_result,
            event=event,
            context_type="search_result",
            trigger_group_id=0,
            current_page=parsed.page,
            keyword=parsed.keyword,
            field=parsed.field,
            creator_id=parsed.creator_id,
            has_image=has_image,
            group_ids=[item.trigger_group_id for item in page.items],
        )

    async def _record_group_detail_view_message(
        *,
        send_result: Any,
        event: MessageEvent,
        trigger_group_id: int,
        page: int,
        has_image: bool,
    ) -> None:
        await _record_view_message(
            send_result=send_result,
            event=event,
            context_type="group_detail",
            trigger_group_id=trigger_group_id,
            current_page=page,
            keyword="",
            field="",
            creator_id="",
            has_image=has_image,
            group_ids=[trigger_group_id],
        )

    def _group_detail_has_image(detail: Any) -> bool:
        if any(atom.kind == "image" for atom in detail.trigger_shape.atoms):
            return True
        return any(
            atom.kind == "image"
            for response in detail.responses
            for atom in response.response_shape.atoms
        )

    async def send_search_result_view(
        matcher: Matcher,
        event: MessageEvent,
        locale: LocaleCode,
        *,
        keyword: str,
        image_scores: dict[int, float] | None = None,
        state: Any = None,
        finish_guided_search: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        parsed = parse_search_args(keyword)
        if state is None:
            page = await execute_search_page(
                wordbank_service,
                parsed=parsed,
                image_scores=image_scores,
            )
            message = await render_search_page_message(
                page,
                parsed=parsed,
                locale=locale,
                has_image=image_scores is not None,
                media_service=wordbank_media_service,
            )
            send_result = await matcher.send(message)
            await _record_search_result_view_message(
                send_result=send_result,
                event=event,
                parsed=parsed,
                page=page,
                has_image=image_scores is not None,
            )
            await matcher.finish()
            return

        clear_interaction_errors(state)
        state["wordbank_locale"] = locale
        state["wordbank_guided_search_field"] = parsed.field
        state["wordbank_guided_search_keyword"] = parsed.keyword
        state["wordbank_guided_search_creator_id"] = parsed.creator_id
        state["wordbank_guided_search_has_image"] = image_scores is not None
        state["wordbank_guided_search_image_scores"] = dict(image_scores or {})
        state["wordbank_guided_search_requires_creator"] = False
        register_root_message(state, event)
        if finish_guided_search is None:
            return
        await finish_guided_search(
            matcher,
            state,
            event,
            locale,
            page_number=parsed.page,
        )

    async def send_group_detail_view(
        matcher: Matcher,
        event: MessageEvent,
        locale: LocaleCode,
        *,
        trigger_group_id: int,
        page: int,
        finish_after_send: bool = True,
    ) -> None:
        message, detail, _ = await build_group_detail_message(
            wordbank_service,
            trigger_group_id=trigger_group_id,
            page=page,
            locale=locale,
            media_service=wordbank_media_service,
        )
        send_result = await matcher.send(message)
        await _record_group_detail_view_message(
            send_result=send_result,
            event=event,
            trigger_group_id=trigger_group_id,
            page=page,
            has_image=_group_detail_has_image(detail),
        )
        if finish_after_send:
            await matcher.finish()

    async def notify_approval_source(
        bot: Bot,
        approval_message: WordbankMessageRefRecord,
        message: str,
    ) -> None:
        source = empty_message()
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

    def _message_segment_stats(message: Message | str) -> tuple[int, int]:
        if isinstance(message, str):
            return (1 if message else 0, 0)
        segments = list(message)
        return (
            len(segments),
            sum(1 for segment in segments if segment.type == "image"),
        )

    def _image_payload_trace_fields(
        trace_fields: Mapping[str, object] | None,
    ) -> dict[str, object]:
        if trace_fields is None:
            return {}
        payload: dict[str, object] = {}
        for key in (
            "requested_image_ids",
            "loaded_image_ids",
            "loaded_image_sizes",
            "loaded_count",
            "missing_count",
            "image_total_bytes",
            "image_max_bytes",
        ):
            value = trace_fields.get(key)
            if value is not None:
                payload[key] = value
        return payload

    async def _build_passive_message(
        response: PassiveResponse,
        *,
        locale: LocaleCode,
    ) -> tuple[Message | str, dict[str, object]]:
        from src.plugins.wordbank.debug import log_perf, perf_start

        start = perf_start()
        if response.response_shape is None or response.response_shape.is_empty():
            log_perf(
                "plugin.build_passive_message.text_only",
                start=start,
                response_item_id=response.response_item_id,
            )
            return response.text, {}
        image_atom_count = sum(
            1 for atom in response.response_shape.atoms if atom.kind == "image"
        )
        log_perf(
            "plugin.build_passive_message.render_shape.begin",
            response_item_id=response.response_item_id,
            atom_count=len(response.response_shape.atoms),
            image_atom_count=image_atom_count,
        )
        render_trace: dict[str, object] = {}
        message = await render_shape_message(
            response.response_shape,
            wordbank_media_service,
            locale=locale,
            trace_fields={"response_item_id": response.response_item_id},
            trace_sink=render_trace,
        )
        image_trace_fields = _image_payload_trace_fields(render_trace)
        log_perf(
            "plugin.build_passive_message.rendered_shape",
            start=start,
            response_item_id=response.response_item_id,
            atoms=len(response.response_shape.atoms),
            segments=len(list(message)),
            **cast(Any, image_trace_fields),
        )
        return message, image_trace_fields

    @wordbank_reply_command.handle()
    async def _wordbank_reply(matcher: Matcher, event: MessageEvent) -> None:
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        try:
            msg = await handle_reply_command(
                wordbank_service,
                event=event,
                message=event.message,
                text=event.message.extract_plain_text(),
                locale=locale,
                media_service=wordbank_media_service,
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
        await matcher.finish(msg)

    @wordbank_approval_reply_command.handle()
    async def _wordbank_approval_reply(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
    ) -> None:
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        try:
            outcome = await handle_approval_reply_result(
                wordbank_service,
                event=event,
                text=event.message.extract_plain_text(),
                locale=locale,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(
                build_error_message(
                    exc,
                    locale,
                    default_feature="approval-reply",
                    actor_permission=Permission.GROUP_ADMIN,
                )
            )
            return
        if outcome.completed and outcome.approval_message is not None:
            await notify_approval_source(bot, outcome.approval_message, outcome.message)
        await matcher.finish(outcome.message)

    @wordbank_view_reply_command.handle()
    async def _wordbank_view_reply(matcher: Matcher, event: MessageEvent) -> None:
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        reply = event.reply
        if reply is None:
            await matcher.finish(tr(locale, "wordbank.reply.target_missing"))
            return
        reply_message_id = getattr(reply, "message_id", None)
        if reply_message_id is None:
            await matcher.finish(tr(locale, "wordbank.reply.target_missing"))
            return
        view_message = await wordbank_service.get_message_ref(
            str(reply_message_id),
            expected_kind="view",
        )
        if view_message is None:
            await matcher.finish(
                tr(
                    locale,
                    "wordbank.reply.view_target_not_found",
                    message_id=reply_message_id,
                )
            )
            return
        try:
            if view_message.context_type == "search_result":
                parsed = parse_view_reply_for_search_result(
                    event.message.extract_plain_text(),
                    available_group_ids=view_message.group_ids,
                )
            else:
                parsed = parse_view_reply_for_group_detail(
                    event.message.extract_plain_text(),
                    trigger_group_id=view_message.trigger_group_id,
                    current_page=view_message.current_page,
                )
            await (await _get_plugin_attr("_send_group_detail_view"))(
                matcher,
                event,
                locale,
                trigger_group_id=parsed.trigger_group_id,
                page=parsed.page,
            )
        except (RuleError, ValueError) as exc:
            await matcher.finish(
                build_error_message(
                    exc,
                    locale,
                    default_feature="reply-shortcut",
                )
            )

    @wordbank_passive.handle()
    async def _wordbank_passive(bot: Bot, event: MessageEvent) -> None:
        from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start

        start = perf_start()
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        try:
            handle_start = perf_start()
            response = await handle_passive_message(
                bot,
                event,
                wordbank_service,
                wordbank_media_service,
            )
            handle_ms = elapsed_ms(handle_start)
        except Exception as exc:
            logger.warning(f"[Wordbank] passive match skipped: {exc}")
            return
        if not response:
            log_perf(
                "plugin.passive.handle.no_match",
                start=start,
                handle_ms=f"{handle_ms:.2f}",
            )
            return
        build_start = perf_start()
        message, image_trace_fields = await _build_passive_message(
            response,
            locale=locale,
        )
        build_ms = elapsed_ms(build_start)
        segment_count, image_segment_count = _message_segment_stats(message)
        log_perf(
            "plugin.passive.handle.send.begin",
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        send_start = perf_start()
        send_result = await wordbank_passive.send(message)
        send_ms = elapsed_ms(send_start)
        log_perf(
            "plugin.passive.handle.send.done",
            start=send_start,
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        record_start = perf_start()
        await _record_passive_response_message(response, send_result)
        record_ms = elapsed_ms(record_start)
        log_perf(
            "plugin.passive.handle.sent",
            start=start,
            message_type=response.message_type,
            trigger_group_id=response.trigger_group_id,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
            send_ms=f"{send_ms:.2f}",
            record_ms=f"{record_ms:.2f}",
            **cast(Any, image_trace_fields),
        )

    @wordbank_notice.handle()
    async def _wordbank_notice(bot: Bot, event: NoticeEvent) -> None:
        from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start

        if is_supported_recall_notice(event):
            recall_event = cast(GroupRecallNoticeEvent | FriendRecallNoticeEvent, event)
            for matcher_source in (wordbank_add_command, wordbank_command):
                session = find_recall_session(matcher_source, recall_event)
                if session is None:
                    continue
                state = session.matcher_cls._default_state
                locale = guided_locale(state)
                checkpoint = session.checkpoint
                await cancel_guided_resources(
                    state,
                    checkpoint.cleanup_keys
                    if checkpoint is not None and not session.is_root_message
                    else WORDBANK_GUIDED_RECALL_PENDING_KEYS,
                )
                session.matcher_cls.destroy()
                if session.is_root_message or checkpoint is None:
                    await wordbank_notice.send(tr(locale, "interaction.cancelled"))
                    return
                rebuild_temp_matcher(
                    session.matcher_cls,
                    matcher_source,
                    step_index=checkpoint.step_index,
                    state=checkpoint.state_snapshot,
                )
                await wordbank_notice.send(checkpoint.prompt)
                return

        start = perf_start()
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        try:
            handle_start = perf_start()
            response = await handle_passive_notice(bot, event, wordbank_service)
            handle_ms = elapsed_ms(handle_start)
        except Exception as exc:
            logger.warning(f"[Wordbank] passive notice skipped: {exc}")
            return
        if not response:
            log_perf(
                "plugin.notice.handle.no_match",
                start=start,
                handle_ms=f"{handle_ms:.2f}",
            )
            return
        build_start = perf_start()
        message, image_trace_fields = await _build_passive_message(
            response,
            locale=locale,
        )
        build_ms = elapsed_ms(build_start)
        segment_count, image_segment_count = _message_segment_stats(message)
        log_perf(
            "plugin.notice.handle.send.begin",
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        send_start = perf_start()
        send_result = await wordbank_notice.send(message)
        send_ms = elapsed_ms(send_start)
        log_perf(
            "plugin.notice.handle.send.done",
            start=send_start,
            message_type=response.message_type,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            **cast(Any, image_trace_fields),
        )
        record_start = perf_start()
        await _record_passive_response_message(response, send_result)
        record_ms = elapsed_ms(record_start)
        log_perf(
            "plugin.notice.handle.sent",
            start=start,
            message_type=response.message_type,
            trigger_group_id=response.trigger_group_id,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
            send_ms=f"{send_ms:.2f}",
            record_ms=f"{record_ms:.2f}",
            **cast(Any, image_trace_fields),
        )

    return {
        "send_group_detail_view": send_group_detail_view,
        "send_search_result_view": send_search_result_view,
        "record_search_result_view_message": _record_search_result_view_message,
        "notify_approval_source": notify_approval_source,
    }
