from tests.plugins.water.test_repository_edges_support import *


async def test_archive_message_shards_delegates_to_counter_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    archive_mock = AsyncMock()
    monkeypatch.setattr(repo_module.water_message, "run_archiver_task", archive_mock)

    await repo.archive_message_shards()

    archive_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_summary_shards_delegates_to_counter_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    archive_mock = AsyncMock()
    monkeypatch.setattr(repo_module.water_summary, "run_archiver_task", archive_mock)

    await repo.archive_summary_shards()

    archive_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_prune_hot_summaries_deletes_before_hot_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    deleted_filters: list[Any] = []

    class FakeSession:
        async def execute(self, stmt: Any) -> Any:
            deleted_filters.append(stmt)
            return SimpleNamespace(rowcount=7)

    class _FakeSessionCtx:
        async def __aenter__(self) -> FakeSession:
            return FakeSession()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            _ = (exc_type, exc, tb)
            return False

    monkeypatch.setattr(
        repo_module.water_core_db,
        "session",
        lambda **kwargs: _FakeSessionCtx(),
    )
    monkeypatch.setattr(repo, "_hot_summary_start_date", lambda today_ts=None: 20260301)

    pruned = await repo.prune_hot_summaries()

    assert pruned == 7
    assert len(deleted_filters) == 1


@pytest.mark.asyncio
async def test_prune_old_messages_is_noop_in_file_lifecycle_model() -> None:
    repo = WaterRepository()

    pruned = await repo.prune_old_messages(1_700_000_000)

    assert pruned == 0


@pytest.mark.asyncio
async def test_get_group_user_rank_uses_group_stats_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    stats_mock = AsyncMock(return_value=3)

    class FakeGroupStatsOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_group_user_rank(self, group_id: str, user_id: str) -> int | None:
            return await stats_mock(group_id, user_id)

    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterGroupStatsOps", FakeGroupStatsOps)

    rank = await repo.get_group_user_rank("20001", "10001")

    assert rank == 3
    stats_mock.assert_awaited_once_with("20001", "10001")


@pytest.mark.asyncio
async def test_get_group_activity_rank_uses_group_stats_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    stats_mock = AsyncMock(return_value=7)

    class FakeGroupStatsOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_group_activity_rank(self, group_id: str) -> int | None:
            return await stats_mock(group_id)

    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterGroupStatsOps", FakeGroupStatsOps)

    rank = await repo.get_group_activity_rank("20001")

    assert rank == 7
    stats_mock.assert_awaited_once_with("20001")


def test_build_group_daily_rank_snapshot_from_rows_keeps_focus_window() -> None:
    repo = WaterRepository()
    current_rows = [
        WaterSummaryRecord(
            group_id=f"2000{idx}",
            user_id=f"1000{idx}",
            record_date=20260613,
            msg_count=60 - idx * 5,
            active_hours=idx + 2,
            hourly_counts=[1 if hour == idx else 0 for hour in range(24)],
            created_at=1,
            updated_at=2,
        )
        for idx in range(1, 8)
    ]
    previous_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260612,
            msg_count=65,
            active_hours=4,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="20002",
            user_id="10002",
            record_date=20260612,
            msg_count=50,
            active_hours=4,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="20004",
            user_id="10004",
            record_date=20260612,
            msg_count=55,
            active_hours=4,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
    ]

    snapshot = repo._build_group_daily_rank_snapshot_from_rows(
        focus_group_id="20004",
        record_date=20260613,
        current_rows=current_rows,
        previous_rows=previous_rows,
        radius=2,
    )

    assert snapshot is not None
    assert snapshot.total_groups == 7
    assert snapshot.focus_rank == 4
    assert snapshot.focus_trend == -2
    assert snapshot.has_hidden_before is True
    assert snapshot.has_hidden_after is True
    assert [item.group_id for item in snapshot.leaderboard] == [
        "20002",
        "20003",
        "20004",
        "20005",
        "20006",
    ]
    focus_item = next(item for item in snapshot.leaderboard if item.group_id == "20004")
    assert focus_item.active_user_count == 1
    assert focus_item.active_hours == 1


def test_build_group_daily_rank_snapshot_from_rows_handles_top_edge() -> None:
    repo = WaterRepository()
    current_rows = [
        WaterSummaryRecord(
            group_id=f"3000{idx}",
            user_id=f"1000{idx}",
            record_date=20260613,
            msg_count=50 - idx,
            active_hours=3,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        )
        for idx in range(1, 5)
    ]

    snapshot = repo._build_group_daily_rank_snapshot_from_rows(
        focus_group_id="30001",
        record_date=20260613,
        current_rows=current_rows,
        previous_rows=[],
        radius=2,
    )

    assert snapshot is not None
    assert snapshot.focus_rank == 1
    assert snapshot.has_hidden_before is False
    assert snapshot.has_hidden_after is True
    assert [item.current_rank for item in snapshot.leaderboard] == [1, 2, 3]


def test_group_daily_rank_snapshot_returns_none_when_focus_missing() -> None:
    repo = WaterRepository()
    current_rows = [
        WaterSummaryRecord(
            group_id="40001",
            user_id="10001",
            record_date=20260613,
            msg_count=20,
            active_hours=2,
            hourly_counts=[0] * 24,
            created_at=1,
            updated_at=2,
        )
    ]

    snapshot = repo._build_group_daily_rank_snapshot_from_rows(
        focus_group_id="49999",
        record_date=20260613,
        current_rows=current_rows,
        previous_rows=[],
        radius=2,
    )

    assert snapshot is None


@pytest.mark.asyncio
async def test_get_group_daily_rank_snapshot_queries_global_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()
    current_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260613,
            msg_count=20,
            active_hours=2,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="29999",
            user_id="10002",
            record_date=20260613,
            msg_count=25,
            active_hours=3,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
    ]
    previous_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260612,
            msg_count=18,
            active_hours=2,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        )
    ]
    get_window_mock = AsyncMock(side_effect=[current_rows, previous_rows])
    monkeypatch.setattr(repo, "get_summaries_in_window", get_window_mock)

    snapshot = await repo.get_group_daily_rank_snapshot(
        group_id="20001",
        record_date=20260613,
    )

    assert snapshot is not None
    assert snapshot.total_groups == 2
    assert snapshot.focus_rank == 2
    assert snapshot.focus_trend == -1
    assert [item.group_id for item in snapshot.leaderboard] == ["29999", "20001"]
    assert get_window_mock.await_args_list[0].args == (20260613, 20260613)
    assert get_window_mock.await_args_list[1].args == (20260612, 20260612)
