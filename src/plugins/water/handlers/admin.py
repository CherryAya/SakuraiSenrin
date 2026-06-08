"""Water 管理命令处理函数。"""

from __future__ import annotations

from dataclasses import dataclass

import arrow
from nonebot.matcher import Matcher

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.renderers import render_season_list
from src.plugins.water.services.season import (
    SeasonCreateInput,
    SeasonStatus,
    season_service,
)
from src.plugins.water.services.settlement import (
    SettlementResult,
    water_settlement_service,
)


@dataclass
class WaterAdminContext:
    matcher: Matcher
    args: list[str]
    locale: LocaleCode


def water_help_message(locale: LocaleCode) -> str:
    return (
        tr(locale, "water.admin.help.title")
        + "\n"
        + tr(
            locale,
            "water.admin.help.content",
        )
    )


def format_settlement_message(result: SettlementResult, locale: LocaleCode) -> str:
    if result.success:
        mode = tr(
            locale,
            "water.admin.settlement.mode.force"
            if result.forced
            else "water.admin.settlement.mode.normal",
        )
        return tr(
            locale,
            "water.admin.settlement.success",
            mode=mode,
            record_date=result.record_date,
            aggregate_rows=result.aggregate_rows,
            unlocked_achievements=result.unlocked_achievements,
        )
    if result.skipped:
        match result.reason:
            case "already_settled":
                reason = tr(locale, "water.admin.settlement.reason.already_settled")
            case "running":
                reason = tr(locale, "water.admin.settlement.reason.running")
            case "failed":
                reason = tr(locale, "water.admin.settlement.reason.failed")
            case "pending":
                reason = tr(locale, "water.admin.settlement.reason.pending")
            case _:
                reason = result.reason or "unknown"
        return tr(
            locale,
            "water.admin.settlement.skipped",
            record_date=result.record_date,
            reason=reason,
        )
    return tr(
        locale,
        "water.admin.settlement.failed",
        record_date=result.record_date,
        reason=result.reason or "unknown",
    )


async def handle_help(ctx: WaterAdminContext) -> None:
    await ctx.matcher.finish(water_help_message(ctx.locale))


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
                tr(ctx.locale, "water.admin.settle.args_multiple_date")
            )
        date_arg = arg

    if date_arg is not None:
        if len(date_arg) != 8 or not date_arg.isdigit():
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.settle.date_invalid"))
        try:
            target_day = arrow.get(date_arg, "YYYYMMDD")
        except ValueError:
            await ctx.matcher.finish(
                tr(ctx.locale, "water.admin.settle.date_parse_failed")
            )

    await ctx.matcher.send(tr(ctx.locale, "water.admin.settle.running"))
    result = await water_settlement_service.run_daily_settlement(
        target_day,
        force=force,
    )
    await ctx.matcher.finish(format_settlement_message(result, ctx.locale))


async def handle_pardon(ctx: WaterAdminContext) -> None:
    if len(ctx.args) < 2:
        await ctx.matcher.finish(tr(ctx.locale, "water.admin.pardon.missing"))
    penalty_id = ctx.args[1]
    if not penalty_id.isdigit():
        await ctx.matcher.finish(tr(ctx.locale, "water.admin.pardon.invalid"))

    ok = await water_repo.pardon_penalty(int(penalty_id))
    if ok:
        await ctx.matcher.finish(
            tr(ctx.locale, "water.admin.pardon.success", penalty_id=penalty_id)
        )
    await ctx.matcher.finish(
        tr(ctx.locale, "water.admin.pardon.failed", penalty_id=penalty_id)
    )


async def handle_ignore(ctx: WaterAdminContext) -> None:
    if len(ctx.args) < 2:
        await ctx.matcher.finish(tr(ctx.locale, "water.admin.ignore.missing"))
    group_id = ctx.args[1]
    if not group_id.isdigit():
        await ctx.matcher.finish(tr(ctx.locale, "water.admin.ignore.invalid"))

    ok = await water_repo.ignore_matrix_suggestion(group_id)
    if ok:
        await ctx.matcher.finish(
            tr(ctx.locale, "water.admin.ignore.success", group_id=group_id)
        )
    await ctx.matcher.finish(
        tr(ctx.locale, "water.admin.ignore.unchanged", group_id=group_id)
    )


async def handle_ignored(ctx: WaterAdminContext) -> None:
    ignored = sorted(await water_repo.get_ignored_matrix_suggestions())
    if not ignored:
        await ctx.matcher.finish(tr(ctx.locale, "water.admin.ignored.empty"))
    await ctx.matcher.finish(
        tr(
            ctx.locale,
            "water.admin.ignored.list",
            count=len(ignored),
            items="\n".join(f"- {gid}" for gid in ignored),
        )
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
        tr(
            ctx.locale,
            "water.admin.state",
            last_success_record_date=state["last_success_record_date"],
            latest_record_date=state["latest_record_date"],
            latest_status=state["latest_status"],
            latest_started_at=started_text,
            latest_finished_at=finished_text,
            ignored_count=state["ignored_count"],
            query_time=arrow.get(get_current_time()).format("YYYY-MM-DD HH:mm:ss"),
        )
    )


async def handle_season(ctx: WaterAdminContext) -> None:
    if len(ctx.args) < 2:
        await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.usage"))
    action = ctx.args[1].lower()
    if action == "create":
        if len(ctx.args) < 6:
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.create.usage"))
        season_id = ctx.args[2]
        start_date = ctx.args[3]
        end_date = ctx.args[4]
        if not (start_date.isdigit() and end_date.isdigit()):
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.date_invalid"))
        name = " ".join(ctx.args[5:]).strip()
        try:
            season = await season_service.create(
                SeasonCreateInput(
                    season_id=season_id,
                    start_date=int(start_date),
                    end_date=int(end_date),
                    name=name,
                )
            )
        except ValueError as exc:
            await ctx.matcher.finish(str(exc))
        await ctx.matcher.finish(
            tr(
                ctx.locale,
                "water.admin.season.created",
                season_id=season.season_id,
                name=season.name,
                start_date=season.start_date,
                end_date=season.end_date,
            )
        )
    if action == "publish":
        if len(ctx.args) < 3:
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.publish.usage"))
        try:
            season = await season_service.publish(ctx.args[2])
        except ValueError as exc:
            await ctx.matcher.finish(str(exc))
        await ctx.matcher.finish(
            tr(
                ctx.locale,
                "water.admin.season.published",
                season_id=season.season_id,
                name=season.name,
            )
        )
    if action == "archive":
        if len(ctx.args) < 3:
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.archive.usage"))
        try:
            season = await season_service.archive(ctx.args[2])
        except ValueError as exc:
            await ctx.matcher.finish(str(exc))
        await ctx.matcher.finish(
            tr(
                ctx.locale,
                "water.admin.season.archived",
                season_id=season.season_id,
                name=season.name,
            )
        )
    if action == "show":
        if len(ctx.args) < 3:
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.show.usage"))
        try:
            season = await season_service.require(ctx.args[2])
        except ValueError as exc:
            await ctx.matcher.finish(str(exc))
        await ctx.matcher.finish(
            tr(
                ctx.locale,
                "water.admin.season.show",
                season_id=season.season_id,
                name=season.name,
                status=season.status,
                start_date=season.start_date,
                end_date=season.end_date,
                description=season.description or "-",
            )
        )
    if action == "list":
        filter_arg = ctx.args[2].lower() if len(ctx.args) >= 3 else ""
        if filter_arg == "current":
            seasons = await season_service.list_current()
            await ctx.matcher.finish(
                render_season_list(
                    tr(ctx.locale, "water.query.season_list.admin.current"),
                    seasons,
                )
            )
        statuses: list[SeasonStatus] | None = None
        if filter_arg == "published":
            statuses = ["published"]
        elif filter_arg == "archived":
            statuses = ["archived"]
        elif filter_arg == "draft":
            statuses = ["draft"]
        seasons = await season_service.list(statuses)
        await ctx.matcher.finish(
            render_season_list(
                tr(ctx.locale, "water.query.season_list.admin.all"),
                seasons,
            )
        )
    if action == "delete":
        if len(ctx.args) < 3:
            await ctx.matcher.finish(tr(ctx.locale, "water.admin.season.delete.usage"))
        try:
            ok = await season_service.delete_draft(ctx.args[2])
        except ValueError as exc:
            await ctx.matcher.finish(str(exc))
        await ctx.matcher.finish(
            tr(
                ctx.locale,
                (
                    "water.admin.season.delete.result.success"
                    if ok
                    else "water.admin.season.delete.result.failed"
                ),
            )
        )
    await ctx.matcher.finish(
        tr(ctx.locale, "water.admin.season.unknown", action=action)
    )
