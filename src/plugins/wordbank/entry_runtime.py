"""Reply, passive, and notice handler registration for the wordbank plugin."""

from __future__ import annotations

from typing import cast

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.matcher import Matcher

from src.database.core.consts import Permission
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.interactive_recall import (
    find_recall_session,
    is_supported_recall_notice,
    rebuild_temp_matcher,
)
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanInput,
    deliver_message_plan,
    finish_with_message,
)
from src.lib.reply_router import (
    ReplyRoute,
    ResolvedReplyTarget,
    dispatch_reply_route,
    register_reply_route,
)
from src.logger import logger

from . import (
    views,
    wordbank_add_command,
    wordbank_approval_reply_command,
    wordbank_command,
    wordbank_notice,
    wordbank_passive,
    wordbank_reply_command,
    wordbank_view_reply_command,
)
from .database.types import WordbankMessageRefRecord
from .errors import build_wordbank_error_message
from .guided_flow import (
    WORDBANK_GUIDED_RECALL_PENDING_KEYS,
    cancel_guided_resources,
    wordbank_guided_locale,
)
from .handlers import (
    ApprovalReplyOutcome,
    get_reply_message_ids,
    group_detail_page_response_item_ids,
    handle_approval_reply_result,
    handle_reply_command,
    parse_group_detail_delete_reply,
    parse_view_reply_for_group_detail,
    parse_view_reply_for_search_result,
    wordbank_message_ref_from_reply_target,
)
from .handlers import mutation as handlers_mutation
from .handlers import passive as handlers_passive
from .lifecycle import initialize_wordbank_plugin
from .notify import (
    notify_approval_source,
    notify_creator_review_result,
    notify_creator_review_results,
)
from .services import wordbank_media_service, wordbank_service
from .services.rules import RuleError


async def _handle_registered_wordbank_response_reply(
    bot: Bot,
    event: MessageEvent,
    target: ResolvedReplyTarget,
) -> MessagePlanInput | None:
    _ = bot
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    service = wordbank_service
    media_service = wordbank_media_service
    return await handle_reply_command(
        service,
        event=event,
        message=event.message,
        text=event.message.extract_plain_text(),
        locale=locale,
        media_service=media_service,
        response_message=wordbank_message_ref_from_reply_target(target),
    )


async def _handle_registered_wordbank_approval_reply(
    bot: Bot,
    event: MessageEvent,
    target: ResolvedReplyTarget,
) -> ApprovalReplyOutcome:
    _ = bot
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    service = wordbank_service
    return await handle_approval_reply_result(
        service,
        event=event,
        text=event.message.extract_plain_text(),
        locale=locale,
        approval_message=wordbank_message_ref_from_reply_target(target),
    )


async def _handle_registered_wordbank_view_reply(
    bot: Bot,
    event: MessageEvent,
    target: ResolvedReplyTarget,
) -> WordbankMessageRefRecord:
    _ = (bot, event)
    return wordbank_message_ref_from_reply_target(target)


register_reply_route(
    ReplyRoute(
        name="wordbank.response",
        context_kinds=("wordbank.response",),
        text_matcher=lambda _text: True,
        handler=_handle_registered_wordbank_response_reply,
    )
)
register_reply_route(
    ReplyRoute(
        name="wordbank.approval",
        context_kinds=("wordbank.approval",),
        text_matcher=lambda _text: True,
        handler=_handle_registered_wordbank_approval_reply,
    )
)
register_reply_route(
    ReplyRoute(
        name="wordbank.view",
        context_kinds=("wordbank.view",),
        text_matcher=lambda _text: True,
        handler=_handle_registered_wordbank_view_reply,
    )
)


@wordbank_reply_command.handle()
async def _wordbank_reply(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    try:
        msg = await dispatch_reply_route("wordbank.response", bot, event)
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
    if msg is None:
        await matcher.finish()
        return
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=msg,
        source_kind="wordbank_command",
    )


@wordbank_approval_reply_command.handle()
async def _wordbank_approval_reply(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    try:
        outcome = await dispatch_reply_route("wordbank.approval", bot, event)
    except (RuleError, ValueError) as exc:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=build_wordbank_error_message(
                exc,
                locale,
                default_feature="approval-reply",
                actor_permission=Permission.GROUP_ADMIN,
            ),
            source_kind="wordbank_command",
        )
        return
    if outcome is None:
        await matcher.finish()
        return
    if outcome.message is None:
        await matcher.finish()
        return
    if outcome.completed and outcome.approval_message is not None:
        if outcome.action:
            delivered = await notify_creator_review_result(
                bot,
                response_item_id=outcome.approval_message.response_item_id,
                action=outcome.action,
                locale=locale,
                approval_message=outcome.approval_message,
                reviewer_id=str(event.user_id),
                message=outcome.message,
            )
            if not delivered:
                await notify_approval_source(
                    bot,
                    outcome.approval_message,
                    outcome.message,
                )
        else:
            await notify_approval_source(
                bot,
                outcome.approval_message,
                outcome.message,
            )
    elif outcome.completed and outcome.batch_notices:
        await notify_creator_review_results(
            bot,
            notices=outcome.batch_notices,
            locale=locale,
            reviewer_id=str(event.user_id),
        )
    await finish_with_message(
        bot,
        matcher,
        event=event,
        message=outcome.message,
        source_kind="wordbank_command",
    )


@wordbank_view_reply_command.handle()
async def _wordbank_view_reply(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
) -> None:
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    service = wordbank_service
    view_message = cast(
        WordbankMessageRefRecord | None,
        await dispatch_reply_route("wordbank.view", bot, event),
    )
    if view_message is None:
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=tr(
                locale,
                "wordbank.reply.view_target_not_found",
                message_id=(get_reply_message_ids(event) or ("",))[0],
            ),
            source_kind="wordbank_command",
        )
        return
    try:
        if view_message.context_type == "search_result":
            parsed = parse_view_reply_for_search_result(
                event.message.extract_plain_text(),
                available_group_ids=view_message.group_ids,
            )
        else:
            parsed_delete = parse_group_detail_delete_reply(
                event.message.extract_plain_text(),
                available_response_item_ids=None,
            )
            if parsed_delete is not None:
                detail = await service.get_group_detail(view_message.trigger_group_id)
                if detail is None:
                    raise RuleError(
                        tr(
                            locale,
                            "wordbank.group.not_found",
                            group_id=view_message.trigger_group_id,
                        ),
                        key="wordbank.group.not_found",
                        group_id=view_message.trigger_group_id,
                    )
                parse_group_detail_delete_reply(
                    event.message.extract_plain_text(),
                    available_response_item_ids=group_detail_page_response_item_ids(
                        detail,
                        page=view_message.current_page,
                    ),
                )
                delete_handler = handlers_mutation.handle_delete
                messages = [
                    await delete_handler(
                        service,
                        event=event,
                        response_item_id_text=str(response_item_id),
                        locale=locale,
                    )
                    for response_item_id in parsed_delete.response_item_ids
                ]
                message = "\n".join(messages)
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=message,
                    source_kind="wordbank_command",
                )
                return
            parsed = parse_view_reply_for_group_detail(
                event.message.extract_plain_text(),
                trigger_group_id=view_message.trigger_group_id,
                current_page=view_message.current_page,
            )
        await views.send_group_detail_view(
            bot,
            matcher,
            event,
            locale,
            trigger_group_id=parsed.trigger_group_id,
            page=parsed.page,
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


@wordbank_passive.handle()
async def _wordbank_passive(bot: Bot, event: MessageEvent) -> None:
    from .debug import elapsed_ms, log_perf, perf_start

    start = perf_start()
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    try:
        handle_start = perf_start()
        response = await handlers_passive.handle_passive_message(
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
    await views.deliver_passive_response(
        bot,
        response,
        locale=locale,
        source_kind="wordbank_response",
        log_prefix="plugin.passive.handle",
        event=event,
        handle_ms=handle_ms,
    )


@wordbank_notice.handle()
async def _wordbank_notice(bot: Bot, event: NoticeEvent) -> None:
    from .debug import elapsed_ms, log_perf, perf_start

    if is_supported_recall_notice(event):
        recall_event = cast(GroupRecallNoticeEvent | FriendRecallNoticeEvent, event)
        for matcher_source in (wordbank_add_command, wordbank_command):
            session = find_recall_session(matcher_source, recall_event)
            if session is None:
                continue
            state = session.matcher_cls._default_state
            locale = wordbank_guided_locale(state)
            checkpoint = session.checkpoint
            await cancel_guided_resources(
                state,
                checkpoint.cleanup_keys
                if checkpoint is not None and not session.is_root_message
                else WORDBANK_GUIDED_RECALL_PENDING_KEYS,
            )
            session.matcher_cls.destroy()
            if session.is_root_message or checkpoint is None:
                await deliver_message_plan(
                    bot,
                    plan=DeliveryPlan(
                        messages=((tr(locale, "interaction.cancelled")),),
                        source_kind="wordbank_notice",
                    ),
                    target=views.notice_delivery_target(recall_event),
                )
                return
            rebuild_temp_matcher(
                session.matcher_cls,
                matcher_source,
                step_index=checkpoint.step_index,
                state=checkpoint.state_snapshot,
            )
            await deliver_message_plan(
                bot,
                plan=DeliveryPlan(
                    messages=((checkpoint.prompt),),
                    source_kind="wordbank_notice",
                ),
                target=views.notice_delivery_target(recall_event),
            )
            return

    start = perf_start()
    await initialize_wordbank_plugin()
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    try:
        handle_start = perf_start()
        response = await handlers_passive.handle_passive_notice(
            bot,
            event,
            wordbank_service,
        )
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
    await views.deliver_passive_response(
        bot,
        response,
        locale=locale,
        source_kind="wordbank_response",
        log_prefix="plugin.notice.handle",
        target=views.notice_delivery_target(event),
        handle_ms=handle_ms,
    )
