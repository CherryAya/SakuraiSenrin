"""wordbank 审核通知：把提交流程的审核结果推送给创建者。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot

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
        response_item_id for response_item_id, action in notices if action == "approve"
    ]
    rejected_ids = [
        response_item_id for response_item_id, action in notices if action == "reject"
    ]
    if approved_ids and not rejected_ids:
        entries = ", ".join(f"#{item_id}" for item_id in approved_ids)
        return f"管理员 {reviewer} 已批量通过 {len(approved_ids)} 条词条：{entries}。"
    if rejected_ids and not approved_ids:
        entries = ", ".join(f"#{item_id}" for item_id in rejected_ids)
        return f"管理员 {reviewer} 已批量拒绝 {len(rejected_ids)} 条词条：{entries}。"
    lines = [
        f"管理员 {reviewer} 已处理 {len(notices)} 条词条。",
    ]
    if approved_ids:
        lines.append("通过: " + ", ".join(f"#{item_id}" for item_id in approved_ids))
    if rejected_ids:
        lines.append("拒绝: " + ", ".join(f"#{item_id}" for item_id in rejected_ids))
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
