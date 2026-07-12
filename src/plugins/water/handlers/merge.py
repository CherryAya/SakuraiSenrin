"""Water merge 命令处理函数。"""

from dataclasses import dataclass

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.matcher import Matcher

from src.config import config
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import finish_with_message
from src.plugins.water.database import water_repo


@dataclass
class WaterMergeContext:
    matcher: Matcher
    event: GroupMessageEvent
    locale: LocaleCode


def is_group_admin_event(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if str(event.user_id) in config.SUPERUSERS:
        return True
    role = getattr(event.sender, "role", "member")
    return role in {"owner", "admin"}


async def handle_merge_locked(ctx: WaterMergeContext, decision: dict) -> None:
    group_id = str(ctx.event.group_id)
    old_action = str(decision.get("action", ""))
    if old_action == "no_need":
        await finish_with_message(
            getattr(ctx.matcher, "bot", None),
            ctx.matcher,
            event=ctx.event,
            message=tr(ctx.locale, "water.merge.no_need", group_id=group_id),
            source_kind="water_merge",
        )
    action_label = tr(
        ctx.locale,
        "water.merge.locked.action.merge"
        if old_action == "merge"
        else "water.merge.locked.action.reject",
    )
    await finish_with_message(
        getattr(ctx.matcher, "bot", None),
        ctx.matcher,
        event=ctx.event,
        message=tr(
            ctx.locale,
            "water.merge.locked",
            group_id=group_id,
            action_label=action_label,
            main_group_id=config.MAIN_GROUP_ID,
        ),
        source_kind="water_merge",
    )


async def handle_merge_no_need(ctx: WaterMergeContext) -> None:
    group_id = str(ctx.event.group_id)
    await finish_with_message(
        getattr(ctx.matcher, "bot", None),
        ctx.matcher,
        event=ctx.event,
        message=tr(ctx.locale, "water.merge.no_need", group_id=group_id),
        source_kind="water_merge",
    )


async def handle_merge_yes(ctx: WaterMergeContext) -> None:
    group_id = str(ctx.event.group_id)
    pending = await water_repo.get_pending_matrix_suggestion(group_id)
    if pending is None:
        await handle_merge_no_need(ctx)

    ok, decision = await water_repo.set_matrix_merge_intention_once(
        group_id=group_id,
        action="merge",
        operator_id=str(ctx.event.user_id),
    )
    if not ok:
        await handle_merge_locked(ctx, decision)
        return

    target_matrix_id = str(decision.get("target_matrix_id", ""))
    target_text = target_matrix_id if target_matrix_id else "-"
    stale_target_corrected = bool(decision.get("stale_target_corrected", False))
    merge_applied = bool(decision.get("merge_applied", False))
    extra_lines: list[str] = []
    if stale_target_corrected and not merge_applied:
        extra_lines.append(tr(ctx.locale, "water.merge.yes.hint.corrected"))
    elif merge_applied:
        extra_lines.append(tr(ctx.locale, "water.merge.yes.hint.merged"))

    await finish_with_message(
        getattr(ctx.matcher, "bot", None),
        ctx.matcher,
        event=ctx.event,
        message=tr(
            ctx.locale,
            "water.merge.yes.recorded",
            group_id=group_id,
            target_text=target_text,
            extra=("\n".join(extra_lines) + "\n" if extra_lines else ""),
            main_group_id=config.MAIN_GROUP_ID,
        ),
        source_kind="water_merge",
    )


async def handle_merge_no(ctx: WaterMergeContext) -> None:
    group_id = str(ctx.event.group_id)
    pending = await water_repo.get_pending_matrix_suggestion(group_id)
    if pending is None:
        await handle_merge_no_need(ctx)

    ok, decision = await water_repo.set_matrix_merge_intention_once(
        group_id=group_id,
        action="reject",
        operator_id=str(ctx.event.user_id),
    )
    if not ok:
        await handle_merge_locked(ctx, decision)
        return

    target_matrix_id = str(decision.get("target_matrix_id", ""))
    target_text = target_matrix_id if target_matrix_id else "-"
    await finish_with_message(
        getattr(ctx.matcher, "bot", None),
        ctx.matcher,
        event=ctx.event,
        message=tr(
            ctx.locale,
            "water.merge.no.recorded",
            group_id=group_id,
            target_text=target_text,
            main_group_id=config.MAIN_GROUP_ID,
        ),
        source_kind="water_merge",
    )
