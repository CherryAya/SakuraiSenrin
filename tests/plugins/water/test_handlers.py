from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
from nonebug import App
import pytest

from src.database.consts import WritePolicy
from src.plugins.water.handlers.admin import (
    WaterAdminContext,
    format_settlement_message,
    handle_ignore,
    handle_settle,
)
from src.plugins.water.handlers.merge import (
    WaterMergeContext,
    handle_merge_no,
    handle_merge_yes,
    is_group_admin_event,
)
from src.plugins.water.handlers.passive import (
    handle_group_increase_notice,
    handle_water_record,
)
from src.plugins.water.services.settlement import SettlementResult
from tests.plugins.water.helpers import (
    DummyMatcher,
    MatcherFinished,
    build_group_increase_event,
    build_group_message_event,
)


@pytest.mark.asyncio
async def test_handle_merge_yes_first_intention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import merge as merge_module

    event = build_group_message_event("#water.merge yes", role="admin")
    matcher = DummyMatcher()
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event)

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

    with pytest.raises(MatcherFinished):
        await handle_merge_yes(ctx)

    assert matcher.finished is not None
    assert "目标矩阵: abcd1234" in matcher.finished


@pytest.mark.asyncio
async def test_handle_merge_yes_shows_stale_target_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import merge as merge_module

    event = build_group_message_event("#water.merge yes", role="admin")
    matcher = DummyMatcher()
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event)

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
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event)

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
    ctx = WaterMergeContext(matcher=cast(Any, matcher), event=event)

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

    assert is_group_admin_event(member_event) is False
    assert is_group_admin_event(admin_event) is True


@pytest.mark.asyncio
async def test_handle_ignore_param_validation_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = WaterAdminContext(matcher=cast(Any, matcher), args=["ignore", "20001"])

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
        )
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
        )
    )
    assert "模式: 强制重结算" in msg


@pytest.mark.asyncio
async def test_handle_settle_parses_force_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import admin as admin_module

    matcher = DummyMatcher()
    ctx = WaterAdminContext(matcher=cast(Any, matcher), args=["settle", "-f"])
    settle_mock = AsyncMock(
        return_value=SettlementResult(
            success=True,
            skipped=False,
            record_date=20260304,
            aggregate_rows=1,
            unlocked_achievements=0,
            reason="",
        )
    )

    monkeypatch.setattr(
        admin_module.water_settlement_service,
        "run_daily_settlement",
        settle_mock,
    )

    with pytest.raises(MatcherFinished):
        await handle_settle(ctx)

    settle_mock.assert_awaited_once()
    awaited_call = settle_mock.await_args
    assert awaited_call is not None
    kwargs = awaited_call.kwargs
    assert kwargs["force"] is True
