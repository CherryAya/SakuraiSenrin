from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.matcher import Matcher
from nonebug import App
import pytest

from src.database.consts import WritePolicy
from src.plugins.water.handlers.admin import (
    WaterAdminContext,
    format_settlement_message,
    handle_ignore,
    handle_report_dryrun,
    handle_report_push,
    handle_season,
    handle_settle,
)
from src.plugins.water.handlers.merge import (
    WaterMergeContext,
    handle_merge_no,
    handle_merge_yes,
    is_group_admin_event,
    is_water_merge_superuser_event,
)
from src.plugins.water.handlers.passive import (
    handle_group_increase_notice,
    handle_water_record,
)
from src.plugins.water.handlers.query import handle_my_water_profile
from src.plugins.water.services.report import WaterDailyReportBatchResult
from src.plugins.water.services.season import SeasonServiceError
from src.plugins.water.services.settlement import SettlementResult
from src.plugins.water.services.worker_jobs import WaterWorkerManifest
from tests.plugins.water.helpers import (
    DummyMatcher,
    MatcherFinished,
    build_group_increase_event,
    build_group_message_event,
)


def _build_admin_ctx(
    matcher: DummyMatcher,
    args: list[str],
    *,
    event_text: str = "#water.admin",
) -> WaterAdminContext:
    return WaterAdminContext(
        bot=cast(Bot, SimpleNamespace(self_id="99999", call_api=AsyncMock())),
        event=build_group_message_event(event_text),
        matcher=cast(Any, matcher),
        args=args,
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_handle_merge_yes_first_intention(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import merge as merge_module

    event = build_group_message_event("#water.merge yes", role="admin")
    monkeypatch.setattr(
        merge_module.water_repo,
        "set_matrix_merge_intention_once",
        AsyncMock(
            return_value=(
                True,
                {
                    "target_matrix_id": "abcd1234",
                    "matched_groups": ["20002"],
                    "simpson_score": 0.88,
                },
            )
        ),
    )
    monkeypatch.setattr(
        merge_module.water_repo,
        "get_pending_matrix_suggestion",
        AsyncMock(return_value={"target_matrix_id": "abcd1234"}),
    )

    matcher = on_message(priority=1, block=True)

    @matcher.handle()
    async def _(
        matcher: Matcher,
        event: GroupMessageEvent,
    ) -> None:
        await handle_merge_yes(
            WaterMergeContext(matcher=matcher, event=event, locale="zh-CN")
        )

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "已记录为“同意合并”啦 (≧▽≦)\n"
            "群号: 20001\n"
            "目标矩阵: abcd1234\n"
            "后续不会再重复询问这个群。\n"
            "后续要改，请到反馈群 10001 联系超管。",
            bot=bot,
        )
        ctx.should_finished(matcher)


@pytest.mark.asyncio
async def test_handle_merge_yes_shows_stale_target_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import merge as merge_module

    event = build_group_message_event("#water.merge yes", role="admin")
    matcher = DummyMatcher()
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event, locale="zh-CN")

    monkeypatch.setattr(
        merge_module.water_repo,
        "get_pending_matrix_suggestion",
        AsyncMock(return_value={"target_matrix_id": "mtx_a1b2c3d4"}),
    )
    monkeypatch.setattr(
        merge_module.water_repo,
        "set_matrix_merge_intention_once",
        AsyncMock(
            return_value=(
                True,
                {
                    "target_matrix_id": "mtx_live2222",
                    "stale_target_corrected": True,
                    "merge_applied": False,
                },
            )
        ),
    )

    with pytest.raises(MatcherFinished):
        await handle_merge_yes(ctx)

    assert matcher.finished is not None
    assert "自动修正" in matcher.finished
    assert "没有重复迁移数据" in matcher.finished


@pytest.mark.asyncio
async def test_handle_merge_no_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.water.handlers import merge as merge_module

    event = build_group_message_event("#water.merge no", role="admin")
    matcher = DummyMatcher()
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event, locale="zh-CN")

    monkeypatch.setattr(
        merge_module.water_repo,
        "set_matrix_merge_intention_once",
        AsyncMock(return_value=(False, {"action": "merge"})),
    )
    monkeypatch.setattr(
        merge_module.water_repo,
        "get_pending_matrix_suggestion",
        AsyncMock(return_value={"target_matrix_id": "abcd1234"}),
    )

    with pytest.raises(MatcherFinished):
        await handle_merge_no(ctx)

    assert matcher.finished is not None
    assert "首次选择已经生效" in matcher.finished


@pytest.mark.asyncio
async def test_handle_merge_yes_no_need(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.water.handlers import merge as merge_module

    event = build_group_message_event("#water.merge yes", role="admin")
    matcher = DummyMatcher()
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event, locale="zh-CN")

    set_intention_mock = AsyncMock(return_value=(False, {"action": "no_need"}))
    monkeypatch.setattr(
        merge_module.water_repo,
        "set_matrix_merge_intention_once",
        set_intention_mock,
    )
    monkeypatch.setattr(
        merge_module.water_repo,
        "get_pending_matrix_suggestion",
        AsyncMock(return_value=None),
    )

    with pytest.raises(MatcherFinished):
        await handle_merge_yes(ctx)

    assert matcher.finished is not None
    assert "不用合并" in matcher.finished
    set_intention_mock.assert_not_awaited()


def test_is_group_admin_event() -> None:
    member_event = build_group_message_event("hello", role="member")
    admin_event = build_group_message_event("hello", role="admin")
    owner_event = build_group_message_event("hello", role="owner")
    superuser_event = build_group_message_event("hello", user_id=1, role="member")

    assert is_group_admin_event(member_event) is False
    assert is_group_admin_event(admin_event) is True
    assert is_group_admin_event(owner_event) is True
    assert is_group_admin_event(superuser_event) is True


def test_is_water_merge_superuser_event() -> None:
    member_event = build_group_message_event("hello", role="member")
    admin_event = build_group_message_event("hello", role="admin")
    owner_event = build_group_message_event("hello", role="owner")
    superuser_event = build_group_message_event("hello", user_id=1, role="member")

    assert is_water_merge_superuser_event(member_event) is False
    assert is_water_merge_superuser_event(admin_event) is False
    assert is_water_merge_superuser_event(owner_event) is False
    assert is_water_merge_superuser_event(superuser_event) is True


@pytest.mark.asyncio
async def test_handle_ignore_param_validation_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = _build_admin_ctx(matcher, ["ignore", "20001"])

    monkeypatch.setattr(
        admin_module.water_repo,
        "ignore_matrix_suggestion",
        AsyncMock(return_value=True),
    )

    with pytest.raises(MatcherFinished):
        await handle_ignore(ctx)

    assert matcher.finished is not None
    assert "状态: 成功" in matcher.finished


@pytest.mark.asyncio
async def test_handle_report_dryrun_uses_report_service_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = _build_admin_ctx(
        matcher,
        ["report-dryrun"],
        event_text="#water report-dryrun",
    )

    monkeypatch.setattr(
        admin_module.water_report_service,
        "build_daily_report_dry_run_summary",
        AsyncMock(return_value="DRYRUN_OK"),
    )

    with pytest.raises(MatcherFinished):
        await handle_report_dryrun(ctx)

    assert matcher.finished == "DRYRUN_OK"


@pytest.mark.asyncio
async def test_handle_report_push_runs_prepare_and_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = _build_admin_ctx(
        matcher,
        ["report-push", "20260725"],
        event_text="#water report-push 20260725",
    )

    worker_mock = AsyncMock(
        return_value=SimpleNamespace(
            manifest=WaterWorkerManifest(
                job_name="daily_report_prepare",
                job_id="job-1",
                started_at=1,
                finished_at=2,
                status="success",
                record_date=20260725,
                metrics={
                    "candidate_groups": 2,
                    "rendered_groups": 2,
                    "skipped_groups": 0,
                    "failed_groups": 0,
                    "total_elapsed_ms": 12.0,
                },
                report_items=(),
            ),
            output_dir=Path("/tmp/water-report-push-job-1"),
            exit_code=0,
            timed_out=False,
        )
    )
    send_mock = AsyncMock(
        return_value=WaterDailyReportBatchResult(
            record_date=20260725,
            candidate_groups=2,
            rendered_groups=2,
            sent_groups=2,
            skipped_groups=0,
            failed_groups=0,
            total_elapsed_ms=3456.0,
        )
    )
    monkeypatch.setattr(admin_module, "run_water_subprocess_job", worker_mock)
    monkeypatch.setattr(
        admin_module.water_report_service,
        "send_prepared_daily_group_report_push",
        send_mock,
    )

    with pytest.raises(MatcherFinished):
        await handle_report_push(ctx)

    worker_mock.assert_awaited_once_with(
        "daily_report_prepare",
        record_date=20260725,
        locale="zh-CN",
    )
    send_mock.assert_awaited_once()
    assert matcher.finished is not None
    assert "状态: 完成" in matcher.finished
    assert "日报日期: 20260725" in matcher.finished
    assert "已发送: 2" in matcher.finished


@pytest.mark.asyncio
async def test_handle_report_push_requires_valid_single_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = _build_admin_ctx(
        matcher,
        ["report-push", "20260725", "20260724"],
        event_text="#water report-push 20260725 20260724",
    )
    worker_mock = AsyncMock()
    monkeypatch.setattr(admin_module, "run_water_subprocess_job", worker_mock)

    with pytest.raises(MatcherFinished):
        await handle_report_push(ctx)

    worker_mock.assert_not_awaited()
    assert matcher.finished is not None
    assert "参数错误: report-push 仅允许一个日期参数，格式 YYYYMMDD。" in str(
        matcher.finished
    )


@pytest.mark.asyncio
async def test_handle_my_water_profile_uses_profile_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import query as query_module

    matcher = DummyMatcher()
    event = build_group_message_event("我有多水")

    profile_mock = AsyncMock(return_value="PROFILE")
    monkeypatch.setattr(
        query_module.water_query_router,
        "build_profile_message",
        profile_mock,
    )

    with pytest.raises(MatcherFinished):
        await handle_my_water_profile(cast(Any, matcher), event, "zh-CN")

    profile_mock.assert_awaited_once()
    assert matcher.finished == "PROFILE"


@pytest.mark.asyncio
async def test_handle_season_create_and_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    create_matcher = DummyMatcher()
    create_ctx = _build_admin_ctx(
        create_matcher,
        [
            "season",
            "create",
            "spring_2026",
            "20260301",
            "20260331",
            "2026",
            "春日特别季",
        ],
        event_text="#water.admin season create",
    )

    monkeypatch.setattr(
        admin_module.season_service,
        "create",
        AsyncMock(
            return_value=type(
                "Season",
                (),
                {
                    "season_id": "spring_2026",
                    "name": "2026 春日特别季",
                    "start_date": 20260301,
                    "end_date": 20260331,
                },
            )()
        ),
    )

    with pytest.raises(MatcherFinished):
        await handle_season(create_ctx)

    assert "已创建 draft" in str(create_matcher.finished)

    list_matcher = DummyMatcher()
    list_ctx = _build_admin_ctx(
        list_matcher,
        ["season", "list", "published"],
        event_text="#water.admin season list",
    )
    monkeypatch.setattr(
        admin_module.season_service,
        "list",
        AsyncMock(
            return_value=[
                type(
                    "Season",
                    (),
                    {
                        "season_id": "spring_2026",
                        "name": "2026 春日特别季",
                        "start_date": 20260301,
                        "end_date": 20260331,
                        "status": "published",
                    },
                )()
            ]
        ),
    )

    with pytest.raises(MatcherFinished):
        await handle_season(list_ctx)

    assert "spring_2026" in str(list_matcher.finished)


@pytest.mark.asyncio
async def test_handle_season_create_surfaces_localized_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = _build_admin_ctx(
        matcher,
        [
            "season",
            "create",
            "spring_2026",
            "20260301",
            "20260331",
            "2026",
            "春日特别季",
        ],
        event_text="#water.admin season create",
    )

    monkeypatch.setattr(
        admin_module.season_service,
        "create",
        AsyncMock(
            side_effect=SeasonServiceError(
                "water.admin.season.create.exists",
                season_id="spring_2026",
            )
        ),
    )

    with pytest.raises(MatcherFinished):
        await handle_season(ctx)

    assert matcher.finished == "season_id 已存在: spring_2026"


@pytest.mark.asyncio
async def test_handle_water_record_swallows_suggestion_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import passive as passive_module

    save_mock = AsyncMock()
    monkeypatch.setattr(passive_module.water_repo, "save_message", save_mock)
    monkeypatch.setattr(
        passive_module.matrix_suggestion_service,
        "maybe_suggest_on_first_record",
        AsyncMock(side_effect=RuntimeError("x")),
    )

    event = build_group_message_event("hello")
    await handle_water_record(
        bot=cast(Any, SimpleNamespace(self_id="99999")),
        event=event,
    )

    save_mock.assert_awaited_once_with(
        group_id=str(event.group_id),
        user_id=str(event.user_id),
        created_at=event.time,
        policy=WritePolicy.BUFFERED,
    )


@pytest.mark.asyncio
async def test_handle_group_increase_notice_ignore_bot_self(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.plugins.water.handlers import passive as passive_module

    suggest_mock = AsyncMock()
    monkeypatch.setattr(
        passive_module.matrix_suggestion_service,
        "maybe_suggest_on_new_member",
        suggest_mock,
    )

    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        event = build_group_increase_event(user_id=99999)
        await handle_group_increase_notice(bot=bot, event=event)

    suggest_mock.assert_not_awaited()


def test_format_settlement_message_reason_mapping() -> None:
    msg = format_settlement_message(
        SettlementResult(
            success=False,
            skipped=True,
            record_date=20260302,
            aggregate_rows=0,
            unlocked_achievements=0,
            reason="already_settled",
        ),
        "zh-CN",
    )
    assert "该日期已结算成功" in msg


def test_format_settlement_message_shows_force_mode() -> None:
    msg = format_settlement_message(
        SettlementResult(
            success=True,
            skipped=False,
            record_date=20260302,
            aggregate_rows=8,
            unlocked_achievements=1,
            forced=True,
        ),
        "zh-CN",
    )
    assert "模式: 强制重结算" in msg


@pytest.mark.asyncio
async def test_handle_settle_parses_force_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = _build_admin_ctx(
        matcher,
        ["settle", "-f"],
        event_text="#water.admin settle",
    )
    settle_mock = AsyncMock(
        return_value=SimpleNamespace(
            manifest=WaterWorkerManifest(
                job_name="settlement",
                job_id="job-1",
                started_at=1,
                finished_at=2,
                status="success",
                record_date=20260304,
                metrics={
                    "aggregate_rows": 1,
                    "unlocked_achievements": 0,
                    "forced": False,
                    "reason": "",
                },
            ),
            exit_code=0,
            timed_out=False,
        )
    )

    monkeypatch.setattr(
        admin_module,
        "run_water_subprocess_job",
        settle_mock,
    )

    with pytest.raises(MatcherFinished):
        await handle_settle(ctx)

    settle_mock.assert_awaited_once()
    awaited_call = settle_mock.await_args
    assert awaited_call is not None
    kwargs = awaited_call.kwargs
    assert kwargs["force"] is True
    assert kwargs["record_date"] is None
    call_api = cast(Any, ctx.bot.call_api)
    call_api.assert_not_awaited()
