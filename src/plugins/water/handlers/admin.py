"""Water 管理命令处理函数。"""

from dataclasses import dataclass

import arrow
from nonebot.matcher import Matcher

from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.services import SettlementResult, water_settlement_service


@dataclass
class WaterAdminContext:
    matcher: Matcher
    args: list[str]


def water_help_message() -> str:
    return (
        "===== Water Admin =====\n"
        "1. help\n"
        "   查看本帮助。\n"
        "2. settle [YYYYMMDD] [-f|--force]\n"
        "   触发日结算；不填日期时默认结算昨天，-f 可强制重结。\n"
        "3. pardon <penalty_id>\n"
        "   对惩罚日志执行事务回档。\n"
        "4. ignore <group_id>\n"
        "   将群加入智能感知忽略名单。\n"
        "5. ignored\n"
        "   查看忽略名单。\n"
        "6. state\n"
        "   查看系统幂等锁状态。"
    )


def format_settlement_message(result: SettlementResult) -> str:
    if result.success:
        mode = "强制重结算" if result.forced else "常规结算"
        return (
            "===== Water Settlement =====\n"
            f"状态: 成功\n"
            f"模式: {mode}\n"
            f"结算日期: {result.record_date}\n"
            f"处理条目: {result.aggregate_rows}\n"
            f"解锁成就: {result.unlocked_achievements}\n"
            "备注: 已执行分块落盘与流水裁剪。"
        )
    if result.skipped:
        reason_map = {
            "already_settled": "该日期已结算成功，幂等保护生效。",
            "running": "同日期任务正在执行中，避免并发双花。",
            "failed": "该日期上次任务失败，可检查后重新执行。",
            "pending": "任务状态未就绪，稍后重试。",
        }
        return (
            "===== Water Settlement =====\n"
            "状态: 已跳过\n"
            f"结算日期: {result.record_date}\n"
            f"原因: {reason_map.get(result.reason, result.reason or 'unknown')}"
        )
    return (
        "===== Water Settlement =====\n"
        "状态: 失败\n"
        f"结算日期: {result.record_date}\n"
        f"原因: {result.reason or 'unknown'}"
    )


async def handle_help(ctx: WaterAdminContext) -> None:
    await ctx.matcher.finish(water_help_message())


async def handle_settle(ctx: WaterAdminContext) -> None:
    target_day: arrow.Arrow | None = None
    force = False
    date_arg: str | None = None

    for arg in ctx.args[1:]:
        text = arg.strip().lower()
        if text in {"-f", "--force"}:
            force = True
            continue
        if date_arg is not None:
            await ctx.matcher.finish(
                "参数错误: settle 仅允许一个日期参数，格式 YYYYMMDD。"
            )
        date_arg = arg

    if date_arg is not None:
        if len(date_arg) != 8 or not date_arg.isdigit():
            await ctx.matcher.finish("日期格式错误，请使用 YYYYMMDD，例如 20260302。")
        try:
            target_day = arrow.get(date_arg, "YYYYMMDD")
        except ValueError:
            await ctx.matcher.finish("日期解析失败，请检查输入是否为有效日期。")

    await ctx.matcher.send("Water 结算任务执行中，请稍候...")
    result = await water_settlement_service.run_daily_settlement(
        target_day,
        force=force,
    )
    await ctx.matcher.finish(format_settlement_message(result))


async def handle_pardon(ctx: WaterAdminContext) -> None:
    if len(ctx.args) < 2:
        await ctx.matcher.finish("参数缺失: 用法 #water pardon <penalty_id>")
    penalty_id = ctx.args[1]
    if not penalty_id.isdigit():
        await ctx.matcher.finish("参数错误: penalty_id 必须是整数。")

    ok = await water_repo.pardon_penalty(int(penalty_id))
    if ok:
        await ctx.matcher.finish(
            "===== Water Pardon =====\n"
            "状态: 成功\n"
            f"日志 ID: {penalty_id}\n"
            "说明: 惩罚已回档并写入 revoked 标记。"
        )
    await ctx.matcher.finish(
        "===== Water Pardon =====\n"
        "状态: 失败\n"
        f"日志 ID: {penalty_id}\n"
        "原因: 日志不存在或已回档。"
    )


async def handle_ignore(ctx: WaterAdminContext) -> None:
    if len(ctx.args) < 2:
        await ctx.matcher.finish("参数缺失: 用法 #water ignore <group_id>")
    group_id = ctx.args[1]
    if not group_id.isdigit():
        await ctx.matcher.finish("参数错误: group_id 必须是纯数字。")

    ok = await water_repo.ignore_matrix_suggestion(group_id)
    if ok:
        await ctx.matcher.finish(
            "===== Matrix Suggestion Ignore =====\n"
            f"状态: 成功\n群号: {group_id}\n"
            "说明: 后续智能合并建议将不再提示该群。"
        )
    await ctx.matcher.finish(
        "===== Matrix Suggestion Ignore =====\n"
        f"状态: 未变更\n群号: {group_id}\n"
        "说明: 该群已在忽略列表中。"
    )


async def handle_ignored(ctx: WaterAdminContext) -> None:
    ignored = sorted(await water_repo.get_ignored_matrix_suggestions())
    if not ignored:
        await ctx.matcher.finish(
            "===== Ignored Suggestions =====\n当前为空，没有被忽略的群。"
        )
    await ctx.matcher.finish(
        "===== Ignored Suggestions =====\n"
        f"总数: {len(ignored)}\n" + "\n".join(f"- {gid}" for gid in ignored)
    )


async def handle_state(ctx: WaterAdminContext) -> None:
    state = await water_repo.get_settlement_state()
    started_at = int(state["latest_started_at"])
    finished_at = int(state["latest_finished_at"])
    started_text = (
        arrow.get(started_at).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")
        if started_at > 0
        else "-"
    )
    finished_text = (
        arrow.get(finished_at).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")
        if finished_at > 0
        else "-"
    )
    await ctx.matcher.finish(
        "===== Water System State =====\n"
        f"last_success_record_date: {state['last_success_record_date']}\n"
        f"latest_record_date: {state['latest_record_date']}\n"
        f"latest_status: {state['latest_status']}\n"
        f"latest_started_at: {started_text}\n"
        f"latest_finished_at: {finished_text}\n"
        f"ignored_count: {state['ignored_count']}\n"
        f"query_time: {arrow.get(get_current_time()).format('YYYY-MM-DD HH:mm:ss')}"
    )
