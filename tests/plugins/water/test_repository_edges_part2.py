from tests.plugins.water.test_repository_edges_support import *


async def test_get_first_summary_record_date_uses_scope_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    class _ShardSessionCtx:
        def __init__(self, shard_key: str) -> None:
            self._session = SimpleNamespace(shard_key=shard_key)

        async def __aenter__(self) -> object:
            return self._session

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_groups_by_matrix(self, matrix_id: str) -> list[str]:
            assert matrix_id == "mtx_test"
            return ["20001", "20002"]

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_first_summary_record_date(
            self,
            *,
            group_ids: list[str] | None = None,
            user_id: str | None = None,
        ) -> int | None:
            assert group_ids == ["20001", "20002"]
            assert user_id is None
            return 20260305

    class FakeArchivedSummaryOps:
        def __init__(self, session: SimpleNamespace) -> None:
            self._session = session

        async def get_first_summary_record_date(
            self,
            *,
            group_ids: list[str] | None = None,
            user_id: str | None = None,
        ) -> int | None:
            assert group_ids == ["20001", "20002"]
            assert user_id is None
            if self._session.shard_key == "2026_01":
                return None
            if self._session.shard_key == "2026_02":
                return 20260201
            raise AssertionError(f"unexpected shard {self._session.shard_key}")

    def _fake_summary_read_session(**kwargs: Any) -> _ShardSessionCtx:
        shard_key = repo_module.arrow.get(kwargs["time_ctx"]).format("YYYY_MM")
        return _ShardSessionCtx(shard_key)

    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(
        repo_module.water_summary, "read_session", _fake_summary_read_session
    )
    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)
    monkeypatch.setattr(repo_module, "WaterArchivedSummaryOps", FakeArchivedSummaryOps)
    monkeypatch.setattr(repo, "_hot_summary_start_date", lambda today_ts=None: 20260301)
    monkeypatch.setattr(
        repo, "_iter_month_keys", lambda start_date, end_date: ["2026_01", "2026_02"]
    )
    monkeypatch.setattr(
        repo,
        "get_or_create_group_matrix_id",
        AsyncMock(return_value="mtx_test"),
    )

    first_date = await repo.get_first_summary_record_date(
        subject="user",
        scope="matrix",
        group_id="20001",
    )

    assert first_date == 20260201


@pytest.mark.asyncio
async def test_get_today_leaderboard_flushes_realtime_buffer_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    flush_mock = AsyncMock()

    class FakeMessageOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_top_users(
            self,
            group_id: str,
            start_ts: int,
            end_ts: int,
            limit: int,
        ) -> list[tuple[str, int]]:
            _ = (group_id, start_ts, end_ts, limit)
            return [("10001", 5)]

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_ranks_by_date(
            self,
            group_id: str,
            record_date: int,
        ) -> dict[str, int]:
            _ = (group_id, record_date)
            return {"10001": 2}

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_781_913_600)
    monkeypatch.setattr(repo_module.water_writer, "flush_now", flush_mock)
    monkeypatch.setattr(repo_module.water_message, "read_session", _fake_session)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterMessageOps", FakeMessageOps)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)

    rows = await repo.get_today_leaderboard("20001", limit=10)

    flush_mock.assert_awaited_once()
    assert len(rows) == 1
    assert rows[0].user_id == "10001"
    assert rows[0].msg_count == 5
    assert rows[0].trend == 1


@pytest.mark.asyncio
async def test_get_today_leaderboard_uses_shanghai_local_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    called: dict[str, int] = {}

    class FakeMessageOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_top_users(
            self,
            group_id: str,
            start_ts: int,
            end_ts: int,
            limit: int,
        ) -> list[tuple[str, int]]:
            _ = (group_id, end_ts, limit)
            called["record_date"] = int(
                repo_module.arrow.get(start_ts).to("Asia/Shanghai").format("YYYYMMDD")
            )
            return [("10001", 3)]

    class FakeSummaryOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_ranks_by_date(
            self,
            group_id: str,
            record_date: int,
        ) -> dict[str, int]:
            _ = group_id
            called["yesterday_date"] = record_date
            return {}

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_781_323_200)
    monkeypatch.setattr(repo_module.water_writer, "flush_now", AsyncMock())
    monkeypatch.setattr(repo_module.water_message, "read_session", _fake_session)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterMessageOps", FakeMessageOps)
    monkeypatch.setattr(repo_module, "WaterSummaryOps", FakeSummaryOps)

    rows = await repo.get_today_leaderboard("20001", limit=10)

    assert len(rows) == 1
    assert called["record_date"] == 20260613
    assert called["yesterday_date"] == 20260612


@pytest.mark.asyncio
async def test_get_global_period_overview_reads_previous_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()
    get_window_mock = AsyncMock(
        side_effect=[
            [
                WaterSummaryRecord(
                    group_id="20001",
                    user_id="10001",
                    record_date=20260510,
                    msg_count=120,
                    active_hours=18,
                    hourly_counts=[2] * 24,
                    created_at=1,
                    updated_at=2,
                )
            ],
            [
                WaterSummaryRecord(
                    group_id="20002",
                    user_id="10002",
                    record_date=20260410,
                    msg_count=90,
                    active_hours=16,
                    hourly_counts=[1] * 24,
                    created_at=1,
                    updated_at=2,
                )
            ],
        ]
    )
    monkeypatch.setattr(repo, "get_summaries_in_window", get_window_mock)

    overview = await repo.get_global_period_overview(
        20260501,
        20260523,
        20260401,
        20260423,
    )

    assert overview.total_msg_count == 120
    assert overview.active_user_count == 1
    assert overview.previous_total_msg_count == 90
    assert overview.delta_total_msg_count == 30
    assert overview.hourly_counts == [2] * 24


@pytest.mark.asyncio
async def test_map_group_to_matrix_updates_mapping_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    upsert_mock = AsyncMock(return_value=1)

    monkeypatch.setattr(
        repo, "get_or_create_group_matrix_id", AsyncMock(return_value="oldm1111")
    )
    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def upsert_mapping(self, payload: dict[str, Any]) -> int:
            _ = payload
            return await upsert_mock()

    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)

    await repo.map_group_to_matrix("20001", "newm2222")

    upsert_mock.assert_awaited_once()
    assert repo._group_matrix_cache["20001"] == "newm2222"


@pytest.mark.asyncio
async def test_map_group_to_matrix_noop_when_same_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    upsert_mock = AsyncMock()
    monkeypatch.setattr(
        repo, "get_or_create_group_matrix_id", AsyncMock(return_value="same0001")
    )
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def upsert_mapping(self, payload: dict[str, Any]) -> int:
            _ = payload
            return await upsert_mock()

    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)

    await repo.map_group_to_matrix("20001", "same0001")

    upsert_mock.assert_not_awaited()
