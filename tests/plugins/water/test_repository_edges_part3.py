from tests.plugins.water.test_repository_edges_support import *


async def test_get_user_recent_summaries_uses_matrix_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    summary_row = WaterSummaryRecord(
        group_id="20001",
        user_id="10001",
        record_date=20260301,
        msg_count=6,
        active_hours=3,
        hourly_counts=[1] * 24,
        created_at=1,
        updated_at=2,
    )
    summary_mock = AsyncMock(return_value=[summary_row])

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_groups_by_matrix(self, matrix_id: str) -> list[str]:
            assert matrix_id == "mtx_1234"
            return ["20001", "20002"]

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_user_recent_summaries(
            self,
            user_id: str,
            group_ids: list[str],
            start_date: int,
            end_date: int,
        ) -> list[WaterSummaryRecord]:
            return await summary_mock(user_id, group_ids, start_date, end_date)

    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)
    monkeypatch.setattr(repo, "_hot_summary_start_date", lambda today_ts=None: 20260301)

    result = await repo.get_user_recent_summaries(
        user_id="10001",
        matrix_id="mtx_1234",
        start_date=20260301,
        end_date=20260303,
    )

    assert result == [summary_row]
    summary_mock.assert_awaited_once_with(
        "10001",
        ["20001", "20002"],
        20260301,
        20260303,
    )


@pytest.mark.asyncio
async def test_get_user_recent_summaries_merges_hot_and_archived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    archived_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260101,
            msg_count=3,
            active_hours=2,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        )
    ]
    hot_rows = [
        WaterSummaryRecord(
            group_id="20002",
            user_id="10001",
            record_date=20260310,
            msg_count=5,
            active_hours=4,
            hourly_counts=[2] * 24,
            created_at=3,
            updated_at=4,
        )
    ]

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_groups_by_matrix(self, matrix_id: str) -> list[str]:
            assert matrix_id == "mtx_1234"
            return ["20001", "20002"]

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_user_recent_summaries(
            self,
            user_id: str,
            group_ids: list[str],
            start_date: int,
            end_date: int,
        ) -> list[WaterSummaryRecord]:
            assert user_id == "10001"
            assert group_ids == ["20001", "20002"]
            assert start_date == 20260301
            assert end_date == 20260331
            return hot_rows

    archived_mock = AsyncMock(return_value=archived_rows)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)
    monkeypatch.setattr(repo, "_hot_summary_start_date", lambda today_ts=None: 20260301)
    monkeypatch.setattr(repo, "_fetch_archived_summaries_in_window", archived_mock)

    result = await repo.get_user_recent_summaries(
        user_id="10001",
        matrix_id="mtx_1234",
        start_date=20260101,
        end_date=20260331,
    )

    archived_mock.assert_awaited_once_with(
        start_date=20260101,
        end_date=20260228,
        group_ids=["20001", "20002"],
        user_id="10001",
    )
    assert [item.record_date for item in result] == [20260101, 20260310]


@pytest.mark.asyncio
async def test_get_summaries_in_window_merges_hot_and_archived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    hot_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260305,
            msg_count=7,
            active_hours=3,
            hourly_counts=[3] * 24,
            created_at=1,
            updated_at=2,
        )
    ]
    archived_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10002",
            record_date=20260220,
            msg_count=4,
            active_hours=2,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        )
    ]

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_summaries_in_window(
            self,
            start_date: int,
            end_date: int,
            *,
            group_ids: list[str] | None = None,
            user_id: str | None = None,
            preserve_order: bool = True,
        ) -> list[WaterSummaryRecord]:
            assert start_date == 20260301
            assert end_date == 20260331
            assert group_ids == ["20001"]
            assert user_id is None
            assert preserve_order is True
            return hot_rows

    archived_mock = AsyncMock(return_value=archived_rows)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)
    monkeypatch.setattr(repo, "_hot_summary_start_date", lambda today_ts=None: 20260301)
    monkeypatch.setattr(repo, "_fetch_archived_summaries_in_window", archived_mock)

    result = await repo.get_summaries_in_window(
        20260201,
        20260331,
        group_ids=["20001"],
    )

    archived_mock.assert_awaited_once_with(
        start_date=20260201,
        end_date=20260228,
        group_ids=["20001"],
        user_id=None,
        preserve_order=True,
    )
    assert [item.record_date for item in result] == [20260220, 20260305]


@pytest.mark.asyncio
async def test_pardon_penalty_restores_matrix_global_and_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    revoke_mock = AsyncMock(return_value=1)
    upsert_matrix_mock = AsyncMock(return_value=1)
    upsert_total_mock = AsyncMock(return_value=1)
    upsert_global_mock = AsyncMock(return_value=1)

    penalty_log = SimpleNamespace(
        id=7,
        user_id="10001",
        matrix_id="mtx_1234",
        group_id="20001",
        record_date=20260302,
        is_revoked=0,
        delta_exp=-70,
        extra={"candidate_delta": 70},
    )
    other_penalty = SimpleNamespace(
        id=8,
        user_id="10001",
        matrix_id="mtx_5678",
        group_id="20003",
        record_date=20260302,
        is_revoked=0,
        delta_exp=-60,
        extra={"candidate_delta": 60},
    )

    class FakePenaltyOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_penalty_by_id(self, penalty_id: int) -> Any:
            assert penalty_id == 7
            return penalty_log

        async def get_user_penalties_by_date(
            self,
            user_id: str,
            record_date: int,
        ) -> list[Any]:
            assert user_id == "10001"
            assert record_date == 20260302
            return [penalty_log, other_penalty]

        async def revoke_penalty(self, penalty_id: int, revoked_at: int) -> int:
            return await revoke_mock(penalty_id, revoked_at)

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_user_summary_rows_by_date(
            self,
            user_id: str,
            record_date: int,
        ) -> list[tuple[str, int, int]]:
            assert user_id == "10001"
            assert record_date == 20260302
            return [
                ("20002", 400, 4),
                ("20001", 49, 0),
                ("20003", 36, 2),
            ]

    class FakeLevelOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_matrix_level(
            self,
            matrix_id: str,
            user_id: str,
        ) -> tuple[int, int, int] | None:
            _ = (matrix_id, user_id)
            return (100, 100, 1)

        async def get_global_level(self, user_id: str) -> tuple[int, int, int] | None:
            assert user_id == "10001"
            return (200, 200, 1)

        async def get_matrix_total(
            self,
            matrix_id: str,
        ) -> tuple[int, int, int] | None:
            assert matrix_id == "mtx_1234"
            return (300, 300, 1)

        async def upsert_matrix_levels(self, data: list[dict[str, Any]]) -> int:
            return await upsert_matrix_mock(data)

        async def upsert_matrix_totals(self, data: list[dict[str, Any]]) -> int:
            return await upsert_total_mock(data)

        async def upsert_global_levels(self, data: list[dict[str, Any]]) -> int:
            return await upsert_global_mock(data)

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterPenaltyOps", FakePenaltyOps)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)
    monkeypatch.setattr(repo_module, "WaterLevelOps", FakeLevelOps)

    ok = await repo.pardon_penalty(7)

    assert ok is True
    upsert_matrix_mock.assert_awaited_once()
    upsert_total_mock.assert_awaited_once()
    upsert_global_mock.assert_awaited_once()
    assert upsert_matrix_mock.await_args is not None
    assert upsert_total_mock.await_args is not None
    assert upsert_global_mock.await_args is not None
    assert upsert_matrix_mock.await_args.args[0][0]["delta_exp"] == 170
    assert upsert_total_mock.await_args.args[0][0]["delta_exp"] == 370
    assert upsert_global_mock.await_args.args[0][0]["delta_exp"] == 235
    revoke_mock.assert_awaited_once_with(7, 1_700_000_000)
