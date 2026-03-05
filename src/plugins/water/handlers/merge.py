"""Water merge 命令处理函数。"""

from dataclasses import dataclass

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.matcher import Matcher

from src.config import config
from src.plugins.water.database import water_repo


@dataclass
class WaterMergeContext:
    matcher: Matcher
    event: GroupMessageEvent


def is_group_admin_event(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    role = getattr(event.sender, "role", "member")
    return role in {"owner", "admin"}


async def handle_merge_locked(ctx: WaterMergeContext, decision: dict) -> None:
    group_id = str(ctx.event.group_id)
    old_action = str(decision.get("action", ""))
    if old_action == "no_need":
        await ctx.matcher.finish(
            "这个群现在不用合并喔 (•̀ᴗ•́)و\n"
            f"群号: {group_id}\n"
            "当前没有待确认的合并建议，不用发 #water.merge yes/no。"
        )
    action_label = "同意合并" if old_action == "merge" else "暂不合并"
    await ctx.matcher.finish(
        "这个群的首次选择已经生效啦 (•̀ω•́)✧\n"
        f"群号: {group_id}\n"
        f"已记录: {action_label}\n"
        "后续如果要改，请联系超管处理。\n"
        f"可到反馈群 {config.MAIN_GROUP_ID} 申请。"
    )


async def handle_merge_no_need(ctx: WaterMergeContext) -> None:
    group_id = str(ctx.event.group_id)
    await ctx.matcher.finish(
        "这个群现在不用合并喔 (•̀ᴗ•́)و\n"
        f"群号: {group_id}\n"
        "当前没有待确认的合并建议，不用发 #water.merge yes/no。"
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
        extra_lines.append("小提示: 目标已自动修正为当前有效矩阵，没有重复迁移数据。")
    elif merge_applied:
        extra_lines.append("小提示: 这个群已经并入目标矩阵，后续会按同一套统计累计。")

    await ctx.matcher.finish(
        "已记录为“同意合并”啦 (≧▽≦)\n"
        f"群号: {group_id}\n"
        f"目标矩阵: {target_text}\n"
        "后续不会再重复询问这个群。\n"
        + ("\n".join(extra_lines) + "\n" if extra_lines else "")
        + f"后续要改，请到反馈群 {config.MAIN_GROUP_ID} 联系超管。"
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
    await ctx.matcher.finish(
        "已记录为“先不合并”啦 (｡•́︿•̀｡)\n"
        f"群号: {group_id}\n"
        f"原建议目标: {target_text}\n"
        "后续不会再弹这个群的合并询问。\n"
        f"如果之后想合并，请到反馈群 {config.MAIN_GROUP_ID} 联系超管。"
    )
