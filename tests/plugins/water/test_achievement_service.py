from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.water.services.achievement import AchievementService


@pytest.mark.asyncio
async def test_unlock_first_blood(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_achievement_items",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(service, "_check_night_owl", AsyncMock(return_value=False))
    monkeypatch.setattr(
        service, "_check_steady_companion", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_global_level",
        AsyncMock(return_value=None),
    )
    unlock_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(
        achievement_module.water_repo, "unlock_achievements", unlock_mock
    )

    unlocked = await service.check_and_unlock(
        user_id="u1",
        matrix_id="m1",
        record_date=20260302,
        today_msg_count=1,
    )

    assert unlocked == ["FIRST_BLOOD"]
    unlock_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlock_night_owl_requires_three_consecutive_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    summaries = [
        SimpleNamespace(record_date=20260301, hourly_counts=[0] * 24),
        SimpleNamespace(
            record_date=20260302,
            hourly_counts=[0, 0, 1, 0, 0] + [0] * 19,
        ),
        SimpleNamespace(
            record_date=20260303,
            hourly_counts=[0, 0, 0, 1, 0] + [0] * 19,
        ),
    ]

    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_recent_summaries",
        AsyncMock(return_value=summaries),
    )

    ok = await service._check_night_owl("u1", "m1", 20260303)

    assert ok is False


@pytest.mark.asyncio
async def test_unlock_matrix_pioneer_requires_first_lv10(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_achievement_items",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(service, "_check_night_owl", AsyncMock(return_value=False))
    monkeypatch.setattr(
        service, "_check_steady_companion", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_global_level",
        AsyncMock(return_value=(10_000, 500, 10)),
    )
    monkeypatch.setattr(
        achievement_module.water_repo,
        "exists_other_global_lv10",
        AsyncMock(return_value=False),
    )
    unlock_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(
        achievement_module.water_repo, "unlock_achievements", unlock_mock
    )

    unlocked = await service.check_and_unlock(
        user_id="u1",
        matrix_id="m1",
        record_date=20260302,
        today_msg_count=0,
    )

    assert unlocked == ["MATRIX_PIONEER"]
    unlock_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_user_achievement_message_contains_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_achievement_items",
        AsyncMock(return_value=[("FIRST_BLOOD", "permanent", "", 1_700_000_000)]),
    )
    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_recent_summaries",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_global_level",
        AsyncMock(return_value=(1000, 100, 3)),
    )

    message = await service.build_user_achievement_message(
        user_id="u1",
        matrix_id="m1",
        record_date=20260304,
    )

    assert "我的水王成就" in message
    assert "已解锁: 1/4" in message
    assert "萌新起步 (FIRST_BLOOD)" in message
    assert "当前进度: 全局等级 Lv3/10" in message
