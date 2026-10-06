"""wordbank 审核通知：把提交流程的审核结果推送给创建者。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import (
    AtRefBlock,
    DeliveryPlan,
    MessagePlanEntry,
    ReplyRefBlock,
    TextBlock,
    deliver_message_plan,
)
from src.logger import logger

from .database.types import WordbankMessageRefRecord
from .services import wordbank_service


async def notify_approval_source(
    bot: Bot,
    approval_message: WordbankMessageRefRecord,
    message: str,
) -> None:
    blocks: list[ReplyRefBlock | TextBlock] = []
    if approval_message.source_message_id:
        blocks.append(ReplyRefBlock(message_id=str(approval_message.source_message_id)))
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
    service = wordbank_service
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
    service = wordbank_service
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
    locale: LocaleCode,
) -> str:
    reviewer = reviewer_id or tr(
        locale,
        "wordbank.creator_notice.reviewer_fallback",
    )
    return tr(
        locale,
        "wordbank.creator_notice.single.approved"
        if action == "approve"
        else "wordbank.creator_notice.single.rejected",
        reviewer=reviewer,
    )


def _build_creator_batch_notice_message(
    *,
    notices: tuple[tuple[int, str], ...],
    reviewer_id: str,
    locale: LocaleCode,
) -> str:
    reviewer = reviewer_id or tr(
        locale,
        "wordbank.creator_notice.reviewer_fallback",
    )
    approved_ids = [
        response_item_id for response_item_id, action in notices if action == "approve"
    ]
    rejected_ids = [
        response_item_id for response_item_id, action in notices if action == "reject"
    ]
    approved_entries = ", ".join(f"#{item_id}" for item_id in approved_ids)
    rejected_entries = ", ".join(f"#{item_id}" for item_id in rejected_ids)
    if approved_ids and not rejected_ids:
        return tr(
            locale,
            "wordbank.creator_notice.batch.approved",
            reviewer=reviewer,
            count=len(approved_ids),
            entries=approved_entries,
        )
    if rejected_ids and not approved_ids:
        return tr(
            locale,
            "wordbank.creator_notice.batch.rejected",
            reviewer=reviewer,
            count=len(rejected_ids),
            entries=rejected_entries,
        )
    lines = [
        tr(
            locale,
            "wordbank.creator_notice.batch.mixed",
            reviewer=reviewer,
            count=len(notices),
        ),
    ]
    if approved_ids:
        lines.append(
            tr(
                locale,
                "wordbank.creator_notice.batch.approved_line",
                entries=approved_entries,
            )
        )
    if rejected_ids:
        lines.append(
            tr(
                locale,
                "wordbank.creator_notice.batch.rejected_line",
                entries=rejected_entries,
            )
        )
    return "\n".join(lines)


async def notify_creator_review_results(
    bot: Bot,
    *,
    notices: tuple[tuple[int, str], ...],
    locale: LocaleCode,
    reviewer_id: str = "",
) -> None:
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
        grouped_notices.setdefault(context_key, []).append((response_item_id, action))

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
                    locale=locale,
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
                locale=locale,
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
