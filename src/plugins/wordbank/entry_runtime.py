"""Reply, passive, and notice handler registration for the wordbank plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupMessageEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
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
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import (
    AtRefBlock,
    DeliveryPlan,
    ImageBytesBlock,
    MessagePlanEntry,
    MessagePlanInput,
    RawMessageBlock,
    ReplyRefBlock,
    TextBlock,
    deliver_message_plan,
    finish_with_message,
    normalize_message_plan_entry,
)
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.repositories import member_repo, user_repo

from .database.types import WordbankMessageRefRecord
from .guided_flow import WORDBANK_GUIDED_RECALL_PENDING_KEYS
from .handlers import (
    PassiveResponse,
    build_group_detail_message,
    handle_approval_reply_result,
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
from .handlers.rendering import (
    _build_image_payload_stats,
    _load_shape_image_bytes,
    _log_missing_image_fallbacks,
)
from .message_model import (
    PLACEHOLDER_ACCOUNT,
    PLACEHOLDER_GROUP_CARD,
    PLACEHOLDER_NICKNAME,
    PLACEHOLDER_PROFILE_COMBO,
    format_at_fallback_text,
    format_event_summary_text,
    is_response_sender_target,
    is_safe_executable_at_target,
)
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
    build_error_message: Callable[..., MessagePlanInput],
    cancel_guided_resources: Callable[..., Awaitable[None]],
    guided_locale: Callable[[Mapping[str, Any]], LocaleCode],
) -> dict[str, Any]:
    async def _get_plugin_attr(name: str) -> Any:
        from src.plugins import wordbank as wordbank_plugin

        return getattr(wordbank_plugin, name)

    async def _get_runtime_attr(name: str, fallback: Any) -> Any:
        try:
            return await _get_plugin_attr(name)
        except Exception:
            return fallback

    async def _get_wordbank_service() -> Any:
        return await _get_runtime_attr("wordbank_service", wordbank_service)

    async def _get_wordbank_media_service() -> Any:
        return await _get_runtime_attr(
            "wordbank_media_service",
            wordbank_media_service,
        )

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
            service = await _get_wordbank_service()
            await service.record_message_ref(
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

    def _notice_delivery_target(event: NoticeEvent) -> DeliveryTarget:
        group_id = str(getattr(event, "group_id", "") or "")
        if group_id:
            return DeliveryTarget(kind="group", target_id=group_id)
        return DeliveryTarget(
            kind="private",
            target_id=str(getattr(event, "user_id", "")),
        )

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
            service = await _get_wordbank_service()
            await service.record_message_ref(
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
        bot: Bot,
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
        service = await _get_wordbank_service()
        media_service = await _get_wordbank_media_service()
        if state is None:
            page = await execute_search_page(
                service,
                parsed=parsed,
                image_scores=image_scores,
            )
            message = await render_search_page_message(
                page,
                parsed=parsed,
                locale=locale,
                has_image=image_scores is not None,
                media_service=media_service,
            )
            plan_result = await deliver_message_plan(
                bot,
                plan=DeliveryPlan(
                    messages=(message,),
                    source_kind="wordbank_view",
                ),
                event=event,
            )
            send_result = plan_result.results[0]
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
            bot,
            matcher,
            state,
            event,
            locale,
            page_number=parsed.page,
        )

    async def send_group_detail_view(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        locale: LocaleCode,
        *,
        trigger_group_id: int,
        page: int,
        finish_after_send: bool = True,
    ) -> None:
        service = await _get_wordbank_service()
        media_service = await _get_wordbank_media_service()
        message, detail, _ = await build_group_detail_message(
            service,
            trigger_group_id=trigger_group_id,
            page=page,
            locale=locale,
            media_service=media_service,
        )
        plan_result = await deliver_message_plan(
            bot,
            plan=DeliveryPlan(
                messages=(message,),
                source_kind="wordbank_view",
            ),
            event=event,
        )
        send_result = plan_result.results[0]
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
        blocks: list[ReplyRefBlock | TextBlock] = []
        if approval_message.source_message_id:
            blocks.append(
                ReplyRefBlock(message_id=str(approval_message.source_message_id))
            )
        blocks.append(TextBlock(text=message))
        plan = DeliveryPlan(
            messages=(MessagePlanEntry(blocks=tuple(blocks)),),
            source_kind="wordbank_approval_source_notice",
            allow_asset_reuse=False,
        )
        try:
            if approval_message.group_id:
                await deliver_message_plan(
                    bot,
                    plan=plan,
                    target=DeliveryTarget(
                        kind="group",
                        target_id=str(approval_message.group_id),
                    ),
                )
                return
            if approval_message.user_id:
                await deliver_message_plan(
                    bot,
                    plan=plan,
                    target=DeliveryTarget(
                        kind="private",
                        target_id=str(approval_message.user_id),
                    ),
                )
        except Exception as exc:
            logger.warning(f"[Wordbank] approval source notice skipped: {exc}")

    async def _find_creator_submission_context(
        response_item_id: int,
    ) -> WordbankMessageRefRecord | None:
        service = await _get_wordbank_service()
        refs = await service.list_message_refs_by_response_item_ids(
            (response_item_id,),
            expected_kind="approval",
        )
        for record in refs:
            if record.message_type in {"submission", "submission_batch"}:
                return record
        return None

    async def _find_creator_submission_contexts(
        response_item_ids: tuple[int, ...],
    ) -> dict[int, WordbankMessageRefRecord]:
        service = await _get_wordbank_service()
        refs = await service.list_message_refs_by_response_item_ids(
            response_item_ids,
            expected_kind="approval",
        )
        contexts: dict[int, WordbankMessageRefRecord] = {}
        for response_item_id in response_item_ids:
            for record in refs:
                if record.message_type not in {"submission", "submission_batch"}:
                    continue
                if record.response_item_id == response_item_id or (
                    response_item_id in record.group_ids
                ):
                    contexts[response_item_id] = record
                    break
        return contexts

    def _build_creator_notice_message(
        *,
        action: str,
        reviewer_id: str,
    ) -> str:
        reviewer = reviewer_id or "管理员"
        if action == "approve":
            return f"管理员 {reviewer} 已通过该词条。"
        return f"管理员 {reviewer} 已拒绝该词条。"

    def _build_creator_batch_notice_message(
        *,
        notices: tuple[tuple[int, str], ...],
        reviewer_id: str,
    ) -> str:
        reviewer = reviewer_id or "管理员"
        approved_ids = [
            response_item_id
            for response_item_id, action in notices
            if action == "approve"
        ]
        rejected_ids = [
            response_item_id
            for response_item_id, action in notices
            if action == "reject"
        ]
        if approved_ids and not rejected_ids:
            entries = ", ".join(f"#{item_id}" for item_id in approved_ids)
            return (
                f"管理员 {reviewer} 已批量通过 {len(approved_ids)} 条词条：{entries}。"
            )
        if rejected_ids and not approved_ids:
            entries = ", ".join(f"#{item_id}" for item_id in rejected_ids)
            return (
                f"管理员 {reviewer} 已批量拒绝 {len(rejected_ids)} 条词条：{entries}。"
            )
        lines = [
            f"管理员 {reviewer} 已处理 {len(notices)} 条词条。",
        ]
        if approved_ids:
            lines.append(
                "通过: " + ", ".join(f"#{item_id}" for item_id in approved_ids)
            )
        if rejected_ids:
            lines.append(
                "拒绝: " + ", ".join(f"#{item_id}" for item_id in rejected_ids)
            )
        return "\n".join(lines)

    async def notify_creator_review_results(
        bot: Bot,
        *,
        notices: tuple[tuple[int, str], ...],
        locale: LocaleCode,
        reviewer_id: str = "",
    ) -> None:
        _ = locale
        if not notices:
            return
        contexts = await _find_creator_submission_contexts(
            tuple(response_item_id for response_item_id, _ in notices)
        )
        grouped_notices: dict[
            tuple[str, str, str, str],
            list[tuple[int, str]],
        ] = {}
        grouped_contexts: dict[tuple[str, str, str, str], WordbankMessageRefRecord] = {}
        for response_item_id, action in notices:
            context = contexts.get(response_item_id)
            if context is None or not context.user_id:
                continue
            context_key = (
                str(context.group_id),
                str(context.user_id),
                str(context.source_message_id),
                str(context.message_type),
            )
            grouped_contexts[context_key] = context
            grouped_notices.setdefault(context_key, []).append(
                (response_item_id, action)
            )

        for context_key, context_notices in grouped_notices.items():
            context = grouped_contexts[context_key]
            blocks: list[ReplyRefBlock | AtRefBlock | TextBlock] = []
            if context.source_message_id:
                blocks.append(ReplyRefBlock(message_id=str(context.source_message_id)))
            blocks.append(AtRefBlock(target_id=str(context.user_id)))
            blocks.append(TextBlock(text=" "))
            blocks.append(
                TextBlock(
                    text=_build_creator_batch_notice_message(
                        notices=tuple(context_notices),
                        reviewer_id=reviewer_id,
                    )
                )
            )
            plan = DeliveryPlan(
                messages=(MessagePlanEntry(blocks=tuple(blocks)),),
                source_kind="wordbank_creator_review_notice",
                allow_asset_reuse=False,
            )
            try:
                if context.group_id:
                    await deliver_message_plan(
                        bot,
                        plan=plan,
                        target=DeliveryTarget(
                            kind="group",
                            target_id=str(context.group_id),
                        ),
                    )
                    continue
                await deliver_message_plan(
                    bot,
                    plan=plan,
                    target=DeliveryTarget(
                        kind="private",
                        target_id=str(context.user_id),
                    ),
                )
            except Exception as exc:
                logger.warning(f"[Wordbank] creator review notice skipped: {exc}")

    async def notify_creator_review_result(
        bot: Bot,
        *,
        response_item_id: int,
        action: str,
        locale: LocaleCode,
        approval_message: WordbankMessageRefRecord | None = None,
        reviewer_id: str = "",
        message: str | None = None,
    ) -> bool:
        context = approval_message
        if context is None:
            context = await _find_creator_submission_context(response_item_id)
        if context is None or not context.user_id:
            return False

        blocks: list[ReplyRefBlock | AtRefBlock | TextBlock] = []
        if context.source_message_id:
            blocks.append(ReplyRefBlock(message_id=str(context.source_message_id)))
        blocks.append(AtRefBlock(target_id=str(context.user_id)))
        blocks.append(TextBlock(text=" "))
        blocks.append(
            TextBlock(
                text=message
                or _build_creator_notice_message(
                    action=action,
                    reviewer_id=reviewer_id,
                )
            )
        )
        plan = DeliveryPlan(
            messages=(MessagePlanEntry(blocks=tuple(blocks)),),
            source_kind="wordbank_creator_review_notice",
            allow_asset_reuse=False,
        )
        try:
            if context.group_id:
                await deliver_message_plan(
                    bot,
                    plan=plan,
                    target=DeliveryTarget(
                        kind="group",
                        target_id=str(context.group_id),
                    ),
                )
                return True
            await deliver_message_plan(
                bot,
                plan=plan,
                target=DeliveryTarget(
                    kind="private",
                    target_id=str(context.user_id),
                ),
            )
            return True
        except Exception as exc:
            logger.warning(f"[Wordbank] creator review notice skipped: {exc}")
            return False

    def _message_segment_stats(message: MessagePlanInput) -> tuple[int, int]:
        entry = normalize_message_plan_entry(message)
        segment_count = 0
        image_count = 0
        for block in entry.blocks:
            if isinstance(block, TextBlock):
                if block.text:
                    segment_count += 1
                continue
            if isinstance(block, ImageBytesBlock):
                segment_count += 1
                image_count += 1
                continue
            if isinstance(block, ReplyRefBlock):
                if block.message_id.isdigit():
                    segment_count += 1
                continue
            if isinstance(block, RawMessageBlock):
                raw_segments = list(block.message)
                segment_count += len(raw_segments)
                image_count += sum(
                    1 for segment in raw_segments if segment.type == "image"
                )
                continue
            segment_count += 1
        return (
            segment_count,
            image_count,
        )

    @dataclass(slots=True, frozen=True)
    class PassivePokeAction:
        target_id: str

    @dataclass(slots=True, frozen=True)
    class PassiveProfilePlaceholderData:
        account: str
        nickname: str
        group_card: str
        combo_text: str

    @dataclass(slots=True, frozen=True)
    class CompiledPassiveResponse:
        message: MessagePlanInput | None
        image_trace_fields: dict[str, object]
        post_actions: tuple[PassivePokeAction, ...] = ()

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

    def _resolve_passive_target_id(response: PassiveResponse, target_id: str) -> str:
        if is_response_sender_target(target_id):
            return str(response.user_id).strip()
        return str(target_id).strip()

    async def _resolve_passive_profile_placeholder_data(
        response: PassiveResponse,
    ) -> PassiveProfilePlaceholderData:
        account = str(response.user_id).strip()
        group_id = str(response.group_id).strip()
        nickname_task = (
            user_repo.get_name_by_uid(account)
            if account
            else asyncio.sleep(0, result=None)
        )
        group_card_task = (
            member_repo.get_card_by_uid_gid(account, group_id)
            if account and group_id
            else asyncio.sleep(0, result=None)
        )
        nickname_value, group_card_value = await asyncio.gather(
            nickname_task,
            group_card_task,
        )
        nickname = str(nickname_value or "").strip() or account
        raw_group_card = str(group_card_value or "").strip()
        group_card = raw_group_card or nickname
        combo_text = nickname
        if raw_group_card and raw_group_card != nickname:
            combo_text += f"({raw_group_card})"
        combo_text += f"[{account}]"
        return PassiveProfilePlaceholderData(
            account=account,
            nickname=nickname,
            group_card=group_card,
            combo_text=combo_text,
        )

    async def _render_profile_combo_avatar(account: str) -> bytes | None:
        if not account:
            return None
        try:
            avatar = await QQAvatar.fetch_user(account, size=160)
            buffer = await asyncio.to_thread(avatar.save, "PNG")
            if hasattr(buffer, "getvalue"):
                return bytes(buffer.getvalue())
            if isinstance(buffer, (bytes, bytearray)):
                return bytes(buffer)
        except Exception as exc:
            logger.debug(
                "[Wordbank] passive profile avatar skipped | "
                f"user_id={account} error={exc}"
            )
        return None

    async def _compile_passive_response(
        response: PassiveResponse,
        *,
        locale: LocaleCode,
    ) -> CompiledPassiveResponse:
        from src.plugins.wordbank.debug import log_perf as default_log_perf
        from src.plugins.wordbank.debug import perf_start as default_perf_start

        log_perf = await _get_runtime_attr("log_perf", default_log_perf)
        perf_start = await _get_runtime_attr("perf_start", default_perf_start)
        start = perf_start()
        media_service = await _get_wordbank_media_service()
        shape = response.response_shape
        if shape is None or shape.is_empty():
            text_value = response.text
            log_perf(
                "plugin.build_passive_message.text_only",
                start=start,
                response_item_id=response.response_item_id,
            )
            if not text_value:
                return CompiledPassiveResponse(message=None, image_trace_fields={})
            return CompiledPassiveResponse(
                message=text_value,
                image_trace_fields={},
            )
        image_atom_count = sum(1 for atom in shape.atoms if atom.kind == "image")
        log_perf(
            "plugin.build_passive_message.render_shape.begin",
            response_item_id=response.response_item_id,
            atom_count=len(shape.atoms),
            image_atom_count=image_atom_count,
        )
        image_bytes_by_id = await _load_shape_image_bytes(shape, media_service)
        payload_stats = _build_image_payload_stats(image_bytes_by_id)
        _log_missing_image_fallbacks(
            stage="compile_passive_response",
            locale=locale,
            image_bytes_by_id=image_bytes_by_id,
            media_service=media_service,
            trace_fields={"response_item_id": response.response_item_id},
        )
        log_perf(
            "plugin.build_passive_message.render_shape.images_loaded",
            start=start,
            response_item_id=response.response_item_id,
            **cast(Any, payload_stats),
        )
        blocks: list[Any] = []
        post_actions: list[PassivePokeAction] = []
        image_segments = 0
        profile_data: PassiveProfilePlaceholderData | None = None
        profile_avatar_bytes: bytes | None = None
        profile_avatar_loaded = False
        for atom in shape.atoms:
            if atom.kind == "text" and atom.text:
                blocks.append(TextBlock(atom.text))
                continue
            if atom.kind == "at" and atom.target_id:
                resolved_target_id = _resolve_passive_target_id(
                    response, atom.target_id
                )
                if resolved_target_id:
                    if is_safe_executable_at_target(resolved_target_id):
                        blocks.append(AtRefBlock(resolved_target_id))
                    else:
                        blocks.append(
                            TextBlock(format_at_fallback_text(resolved_target_id))
                        )
                continue
            if atom.kind == "image" and atom.canonical_image_id is not None:
                image_bytes = image_bytes_by_id.get(atom.canonical_image_id)
                if image_bytes is None:
                    blocks.append(
                        TextBlock(tr(locale, "wordbank.render.image_missing"))
                    )
                    continue
                blocks.append(ImageBytesBlock(image_bytes))
                image_segments += 1
                continue
            if atom.kind == "placeholder" and atom.placeholder_name:
                if profile_data is None:
                    profile_data = await _resolve_passive_profile_placeholder_data(
                        response
                    )
                if atom.placeholder_name == PLACEHOLDER_ACCOUNT:
                    if profile_data.account:
                        blocks.append(TextBlock(profile_data.account))
                    continue
                if atom.placeholder_name == PLACEHOLDER_NICKNAME:
                    if profile_data.nickname:
                        blocks.append(TextBlock(profile_data.nickname))
                    continue
                if atom.placeholder_name == PLACEHOLDER_GROUP_CARD:
                    if profile_data.group_card:
                        blocks.append(TextBlock(profile_data.group_card))
                    continue
                if atom.placeholder_name == PLACEHOLDER_PROFILE_COMBO:
                    if profile_data.combo_text:
                        blocks.append(TextBlock(profile_data.combo_text))
                    if not profile_avatar_loaded:
                        profile_avatar_bytes = await _render_profile_combo_avatar(
                            profile_data.account
                        )
                        profile_avatar_loaded = True
                    if profile_avatar_bytes is not None:
                        blocks.append(ImageBytesBlock(profile_avatar_bytes))
                        image_segments += 1
                    continue
            if atom.kind == "event" and atom.event_name == "event:poke":
                resolved_target_id = _resolve_passive_target_id(
                    response, atom.target_id
                )
                if resolved_target_id:
                    post_actions.append(PassivePokeAction(target_id=resolved_target_id))
                    continue
                logger.debug(
                    "[Wordbank] passive poke skipped | "
                    f"response_item_id={response.response_item_id} reason=empty_target"
                )
                continue
            if atom.kind == "event" and atom.event_name:
                blocks.append(
                    TextBlock(
                        format_event_summary_text(atom.event_name, atom.target_id)
                    )
                )
        message: MessagePlanInput | None = None
        if blocks:
            message = MessagePlanEntry(blocks=tuple(blocks))
        log_perf(
            "plugin.build_passive_message.render_shape.segment_built",
            segments=len(blocks),
            image_segments=image_segments,
            post_action_count=len(post_actions),
            **cast(Any, payload_stats),
            response_item_id=response.response_item_id,
        )
        image_trace_fields = _image_payload_trace_fields(payload_stats)
        log_perf(
            "plugin.build_passive_message.rendered_shape",
            start=start,
            response_item_id=response.response_item_id,
            atoms=len(shape.atoms),
            segments=len(blocks),
            post_action_count=len(post_actions),
            **cast(Any, image_trace_fields),
        )
        return CompiledPassiveResponse(
            message=message,
            image_trace_fields=image_trace_fields,
            post_actions=tuple(post_actions),
        )

    async def _build_passive_message(
        response: PassiveResponse,
        *,
        locale: LocaleCode,
    ) -> tuple[MessagePlanInput, dict[str, object]]:
        compiled = await _compile_passive_response(response, locale=locale)
        return (
            compiled.message or MessagePlanEntry(blocks=()),
            compiled.image_trace_fields,
        )

    async def _execute_passive_post_actions(
        bot: Bot,
        response: PassiveResponse,
        actions: tuple[PassivePokeAction, ...],
    ) -> None:
        if not actions:
            return
        # Temporarily disable active poke execution while keeping passive
        # event matching and normal message delivery unchanged.
        logger.info(
            "[Wordbank] passive poke disabled | "
            f"response_item_id={response.response_item_id} action_count={len(actions)}"
        )
        return

        # for action in actions:
        #     target_id = str(action.target_id).strip()
        #     if not target_id.isdigit():
        #         logger.debug(
        #             "[Wordbank] passive poke skipped | "
        #             "response_item_id="
        #             f"{response.response_item_id} reason=invalid_target"
        #         )
        #         continue
        #     group_id = str(response.group_id).strip()
        #     api_candidates: list[tuple[str, dict[str, int]]] = []
        #     if group_id.isdigit():
        #         api_candidates.extend(
        #             (
        #                 (
        #                     "group_poke",
        #                     {
        #                         "group_id": int(group_id),
        #                         "user_id": int(target_id),
        #                     },
        #                 ),
        #                 (
        #                     "send_poke",
        #                     {
        #                         "group_id": int(group_id),
        #                         "user_id": int(target_id),
        #                     },
        #                 ),
        #             )
        #         )
        #     else:
        #         api_candidates.extend(
        #             (
        #                 ("friend_poke", {"user_id": int(target_id)}),
        #                 ("send_poke", {"user_id": int(target_id)}),
        #             )
        #         )
        #
        #     last_exc: Exception | None = None
        #     for api_name, payload in api_candidates:
        #         try:
        #             logger.debug(
        #                 "[Wordbank] passive poke execute | "
        #                 f"response_item_id={response.response_item_id} "
        #                 f"group_id={response.group_id or '-'} "
        #                 f"target_id={target_id} api={api_name}"
        #             )
        #             await bot.call_api(api_name, **payload)
        #             last_exc = None
        #             break
        #         except Exception as exc:
        #             last_exc = exc
        #             logger.debug(
        #                 "[Wordbank] passive poke api failed | "
        #                 f"response_item_id={response.response_item_id} "
        #                 f"group_id={response.group_id or '-'} "
        #                 f"target_id={target_id} api={api_name} error={exc}"
        #             )
        #     if last_exc is not None:
        #         logger.warning(
        #             "[Wordbank] passive poke failed | "
        #             f"response_item_id={response.response_item_id} "
        #             f"group_id={response.group_id or '-'} "
        #             f"target_id={target_id} error={last_exc}"
        #         )

    @wordbank_reply_command.handle()
    async def _wordbank_reply(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
    ) -> None:
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        service = await _get_wordbank_service()
        media_service = await _get_wordbank_media_service()
        try:
            msg = await handle_reply_command(
                service,
                event=event,
                message=event.message,
                text=event.message.extract_plain_text(),
                locale=locale,
                media_service=media_service,
            )
        except (RuleError, ValueError) as exc:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_error_message(
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
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        service = await _get_wordbank_service()
        try:
            outcome = await handle_approval_reply_result(
                service,
                event=event,
                text=event.message.extract_plain_text(),
                locale=locale,
            )
        except (RuleError, ValueError) as exc:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_error_message(
                    exc,
                    locale,
                    default_feature="approval-reply",
                    actor_permission=Permission.GROUP_ADMIN,
                ),
                source_kind="wordbank_command",
            )
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
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        reply = event.reply
        if reply is None:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "wordbank.reply.target_missing"),
                source_kind="wordbank_command",
            )
            return
        reply_message_id = getattr(reply, "message_id", None)
        if reply_message_id is None:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "wordbank.reply.target_missing"),
                source_kind="wordbank_command",
            )
            return
        service = await _get_wordbank_service()
        view_message = await service.get_message_ref(
            str(reply_message_id),
            expected_kind="view",
        )
        if view_message is None:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(
                    locale,
                    "wordbank.reply.view_target_not_found",
                    message_id=reply_message_id,
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
                parsed = parse_view_reply_for_group_detail(
                    event.message.extract_plain_text(),
                    trigger_group_id=view_message.trigger_group_id,
                    current_page=view_message.current_page,
                )
            await (await _get_plugin_attr("_send_group_detail_view"))(
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
                message=build_error_message(
                    exc,
                    locale,
                    default_feature="reply-shortcut",
                ),
                source_kind="wordbank_command",
            )

    @wordbank_passive.handle()
    async def _wordbank_passive(bot: Bot, event: MessageEvent) -> None:
        from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start

        start = perf_start()
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        try:
            service = await _get_wordbank_service()
            media_service = await _get_wordbank_media_service()
            handle_start = perf_start()
            response = await (await _get_plugin_attr("handle_passive_message"))(
                bot,
                event,
                service,
                media_service,
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
        compiled = await _compile_passive_response(
            response,
            locale=locale,
        )
        build_ms = elapsed_ms(build_start)
        message = compiled.message
        image_trace_fields = compiled.image_trace_fields
        post_action_count = len(compiled.post_actions)
        if message is None and not compiled.post_actions:
            log_perf(
                "plugin.passive.handle.no_output",
                start=start,
                message_type=response.message_type,
                response_item_id=response.response_item_id,
                handle_ms=f"{handle_ms:.2f}",
                build_ms=f"{build_ms:.2f}",
            )
            return
        segment_count, image_segment_count = (
            _message_segment_stats(message) if message is not None else (0, 0)
        )
        send_result: Any = None
        send_ms = 0.0
        if message is not None:
            log_perf(
                "plugin.passive.handle.send.begin",
                message_type=response.message_type,
                response_item_id=response.response_item_id,
                segment_count=segment_count,
                image_segment_count=image_segment_count,
                post_action_count=post_action_count,
                **cast(Any, image_trace_fields),
            )
            send_start = perf_start()
            plan_result = await deliver_message_plan(
                bot,
                plan=DeliveryPlan(
                    messages=(message,),
                    source_kind="wordbank_response",
                ),
                event=event,
            )
            send_result = plan_result.results[0]
            send_ms = elapsed_ms(send_start)
            log_perf(
                "plugin.passive.handle.send.done",
                start=send_start,
                message_type=response.message_type,
                response_item_id=response.response_item_id,
                segment_count=segment_count,
                image_segment_count=image_segment_count,
                post_action_count=post_action_count,
                **cast(Any, image_trace_fields),
            )
        action_start = perf_start()
        await _execute_passive_post_actions(bot, response, compiled.post_actions)
        action_ms = elapsed_ms(action_start) if compiled.post_actions else 0.0
        record_start = perf_start()
        if send_result is not None:
            await (await _get_plugin_attr("_record_passive_response_message"))(
                response,
                send_result,
            )
        record_ms = elapsed_ms(record_start)
        log_perf(
            "plugin.passive.handle.sent",
            start=start,
            message_type=response.message_type,
            trigger_group_id=response.trigger_group_id,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            post_action_count=post_action_count,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
            send_ms=f"{send_ms:.2f}",
            action_ms=f"{action_ms:.2f}",
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
                    await deliver_message_plan(
                        bot,
                        plan=DeliveryPlan(
                            messages=((tr(locale, "interaction.cancelled")),),
                            source_kind="wordbank_notice",
                        ),
                        target=_notice_delivery_target(recall_event),
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
                    target=_notice_delivery_target(recall_event),
                )
                return

        start = perf_start()
        await initialize_plugin()
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        try:
            service = await _get_wordbank_service()
            handle_start = perf_start()
            response = await (await _get_plugin_attr("handle_passive_notice"))(
                bot,
                event,
                service,
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
        build_start = perf_start()
        compiled = await _compile_passive_response(
            response,
            locale=locale,
        )
        build_ms = elapsed_ms(build_start)
        message = compiled.message
        image_trace_fields = compiled.image_trace_fields
        post_action_count = len(compiled.post_actions)
        if message is None and not compiled.post_actions:
            log_perf(
                "plugin.notice.handle.no_output",
                start=start,
                message_type=response.message_type,
                response_item_id=response.response_item_id,
                handle_ms=f"{handle_ms:.2f}",
                build_ms=f"{build_ms:.2f}",
            )
            return
        segment_count, image_segment_count = (
            _message_segment_stats(message) if message is not None else (0, 0)
        )
        send_result: Any = None
        send_ms = 0.0
        if message is not None:
            log_perf(
                "plugin.notice.handle.send.begin",
                message_type=response.message_type,
                response_item_id=response.response_item_id,
                segment_count=segment_count,
                image_segment_count=image_segment_count,
                post_action_count=post_action_count,
                **cast(Any, image_trace_fields),
            )
            send_start = perf_start()
            plan_result = await deliver_message_plan(
                bot,
                plan=DeliveryPlan(
                    messages=(message,),
                    source_kind="wordbank_response",
                ),
                target=_notice_delivery_target(event),
            )
            send_result = plan_result.results[0]
            send_ms = elapsed_ms(send_start)
            log_perf(
                "plugin.notice.handle.send.done",
                start=send_start,
                message_type=response.message_type,
                response_item_id=response.response_item_id,
                segment_count=segment_count,
                image_segment_count=image_segment_count,
                post_action_count=post_action_count,
                **cast(Any, image_trace_fields),
            )
        action_start = perf_start()
        await _execute_passive_post_actions(bot, response, compiled.post_actions)
        action_ms = elapsed_ms(action_start) if compiled.post_actions else 0.0
        record_start = perf_start()
        if send_result is not None:
            await (await _get_plugin_attr("_record_passive_response_message"))(
                response,
                send_result,
            )
        record_ms = elapsed_ms(record_start)
        log_perf(
            "plugin.notice.handle.sent",
            start=start,
            message_type=response.message_type,
            trigger_group_id=response.trigger_group_id,
            response_item_id=response.response_item_id,
            segment_count=segment_count,
            image_segment_count=image_segment_count,
            post_action_count=post_action_count,
            handle_ms=f"{handle_ms:.2f}",
            build_ms=f"{build_ms:.2f}",
            send_ms=f"{send_ms:.2f}",
            action_ms=f"{action_ms:.2f}",
            record_ms=f"{record_ms:.2f}",
            **cast(Any, image_trace_fields),
        )

    return {
        "send_group_detail_view": send_group_detail_view,
        "send_search_result_view": send_search_result_view,
        "record_search_result_view_message": _record_search_result_view_message,
        "record_passive_response_message": _record_passive_response_message,
        "notify_approval_source": notify_approval_source,
        "notify_creator_review_result": notify_creator_review_result,
        "notify_creator_review_results": notify_creator_review_results,
        "_build_passive_message": _build_passive_message,
    }
