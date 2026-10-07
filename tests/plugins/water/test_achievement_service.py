from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.water.services.achievement import AchievementService


@pytest.mark.asyncio
async def test_preloaded_items_avoid_refetch_and_are_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """preloaded_items 必须既避免重复查询，又在 unlock 后回填供同用户后续轮复用。

    结算路径下同一用户会被调用 1 + len(seasons) 次；若新解锁项不回填，赛季轮
    会重复判定永久成就并重复计数。
    """
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    fetch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_achievement_items",
        fetch_mock,
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
    monkeypatch.setattr(
        achievement_module.water_repo,
        "unlock_achievements",
        AsyncMock(return_value=1),
    )

    known: list[tuple[str, str, str, int]] = []
    first = await service.check_and_unlock(
        user_id="u1",
        matrix_id="m1",
        record_date=20260302,
        today_msg_count=1,
        preloaded_items=known,
    )
    assert first == ["FIRST_BLOOD"]
    # FIRST_BLOOD 为 permanent，应已回填到调用方持有的列表
    assert [item[0] for item in known] == ["FIRST_BLOOD"]

    second = await service.check_and_unlock(
        user_id="u1",
        matrix_id="m1",
        record_date=20260302,
        today_msg_count=1,
        season_id="season-1",
        preloaded_items=known,
    )
    # 永久成就已在 known 中，赛季轮不应再次判定它
    assert second == []
    fetch_mock.assert_not_awaited()


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
async def test_unlock_seasonal_achievement_uses_passed_season_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_achievement_items",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(service, "_check_first_blood", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_check_night_owl", AsyncMock(return_value=True))
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
        season_id="spring_2026",
    )

    assert unlocked == ["NIGHT_OWL"]
    assert unlock_mock.await_args is not None
    payload = unlock_mock.await_args.args[0][0]
    assert payload["season_id"] == "spring_2026"


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
async def test_unlock_night_owl_merges_same_day_matrix_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AchievementService()

    from src.plugins.water.services import achievement as achievement_module

    summaries = [
        SimpleNamespace(
            record_date=20260301,
            hourly_counts=[0, 0, 1, 0, 0] + [0] * 19,
        ),
        SimpleNamespace(
            record_date=20260302,
            hourly_counts=[0, 0, 0, 1, 0] + [0] * 19,
        ),
        SimpleNamespace(
            record_date=20260302,
            hourly_counts=[0, 0, 0, 0, 1] + [0] * 19,
        ),
        SimpleNamespace(
            record_date=20260303,
            hourly_counts=[0, 0, 1, 0, 0] + [0] * 19,
        ),
    ]

    monkeypatch.setattr(
        achievement_module.water_repo,
        "get_user_recent_summaries",
        AsyncMock(return_value=summaries),
    )

    ok = await service._check_night_owl("u1", "m1", 20260303)

    assert ok is True


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
        locale="zh-CN",
    )

    assert "我的水王成就" in message
    assert "已解锁: 1/4" in message
    assert "萌新起步 (FIRST_BLOOD)" in message
    assert "当前进度: 全局等级 Lv3/10" in message
