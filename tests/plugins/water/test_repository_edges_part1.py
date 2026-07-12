from tests.plugins.water.test_repository_edges_support import *


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", "already_settled"),
        ("running", "running"),
        ("failed", "failed"),
        ("pending", "pending"),
    ],
)
async def test_try_start_settlement_job_reason_mapping(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected: str,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    class FakeSettlementOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def try_start_job(
            self,
            record_date: int,
            now_ts: int,
            stale_after: int,
            force: bool = False,
        ) -> bool:
            _ = (record_date, now_ts, stale_after, force)
            return False

        async def get_job(self, record_date: int) -> Any:
            _ = record_date
            return SimpleNamespace(status=status)

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterSettlementJobOps", FakeSettlementOps)

    started, reason = await repo.try_start_settlement_job(20260303)

    assert started is False
    assert reason == expected


@pytest.mark.asyncio
async def test_try_start_settlement_job_unknown_when_no_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    class FakeSettlementOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def try_start_job(
            self,
            record_date: int,
            now_ts: int,
            stale_after: int,
            force: bool = False,
        ) -> bool:
            _ = (record_date, now_ts, stale_after, force)
            return False

        async def get_job(self, record_date: int) -> Any:
            _ = record_date
            return None

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterSettlementJobOps", FakeSettlementOps)

    started, reason = await repo.try_start_settlement_job(20260303)

    assert started is False
    assert reason == "unknown"


@pytest.mark.asyncio
async def test_group_matrix_id_collision_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    created: dict[str, str] = {}

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_matrix_id_by_group(self, group_id: str) -> str | None:
            _ = group_id
            return None

        async def get_all_mappings(self) -> dict[str, str]:
            return {"20002": "mtx_deadbeef"}

        async def upsert_mapping(self, payload: dict[str, Any]) -> int:
            created["matrix_id"] = str(payload["matrix_id"])
            return 1

    ids = iter(["mtx_deadbeef", "mtx_cafebabe"])

    def _next_id() -> str:
        return next(ids)

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)
    monkeypatch.setattr(repo, "_gen_matrix_id", _next_id)

    matrix_id = await repo.get_or_create_group_matrix_id("20001")

    assert matrix_id == "mtx_cafebabe"
    assert created["matrix_id"] == "mtx_cafebabe"


@pytest.mark.asyncio
async def test_set_pending_matrix_suggestion_does_not_override_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    set_pending = AsyncMock()

    class FakeMergeOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_state(self, group_id: str) -> Any:
            _ = group_id
            return SimpleNamespace(status="pending", target_matrix_id="aaaa1111")

        async def set_pending_target(
            self, group_id: str, target_matrix_id: str, now_ts: int
        ) -> int:
            _ = (group_id, target_matrix_id, now_ts)
            await set_pending(group_id, target_matrix_id, now_ts)
            return 1

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterMatrixMergeStateOps", FakeMergeOps)

    await repo.set_pending_matrix_suggestion(
        group_id="20001",
        target_matrix_id="bbbb2222",
    )

    set_pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_matrix_merge_intention_once_maps_group_on_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    set_intention = AsyncMock(return_value=True)
    map_mock = AsyncMock()

    class FakeMergeOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_state(self, group_id: str) -> Any:
            _ = group_id
            return SimpleNamespace(status="pending", target_matrix_id="faceb00c")

        async def set_intention_once(
            self,
            group_id: str,
            action: str,
            operator_id: str,
            now_ts: int,
            target_matrix_id: str | None = None,
        ) -> bool:
            _ = (group_id, action, operator_id, now_ts, target_matrix_id)
            return await set_intention()

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def count_groups_by_matrix(self, matrix_id: str) -> int:
            _ = matrix_id
            return 1

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterMatrixMergeStateOps", FakeMergeOps)
    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)
    monkeypatch.setattr(
        repo, "get_or_create_group_matrix_id", AsyncMock(return_value="mtx_live0001")
    )
    monkeypatch.setattr(repo, "map_group_to_matrix", map_mock)

    ok, decision = await repo.set_matrix_merge_intention_once(
        group_id="20001",
        action="merge",
        operator_id="10001",
    )

    assert ok is True
    assert decision["target_matrix_id"] == "faceb00c"
    set_intention.assert_awaited_once()
    map_mock.assert_awaited_once_with("20001", "faceb00c")


@pytest.mark.asyncio
async def test_set_matrix_merge_intention_once_no_need_when_no_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    map_mock = AsyncMock()

    class FakeMergeOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_state(self, group_id: str) -> Any:
            _ = group_id
            return SimpleNamespace(status="", target_matrix_id="")

        async def set_intention_once(
            self,
            group_id: str,
            action: str,
            operator_id: str,
            now_ts: int,
            target_matrix_id: str | None = None,
        ) -> bool:
            _ = (group_id, action, operator_id, now_ts, target_matrix_id)
            return True

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterMatrixMergeStateOps", FakeMergeOps)
    monkeypatch.setattr(repo, "map_group_to_matrix", map_mock)

    ok, decision = await repo.set_matrix_merge_intention_once(
        group_id="20001",
        action="merge",
        operator_id="10001",
    )

    assert ok is False
    assert decision["action"] == "no_need"
    map_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_matrix_merge_intention_once_resolves_stale_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    set_intention = AsyncMock(return_value=True)
    map_mock = AsyncMock()

    class FakeMergeOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def get_state(self, group_id: str) -> Any:
            _ = group_id
            return SimpleNamespace(status="pending", target_matrix_id="mtx_stale1111")

        async def set_intention_once(
            self,
            group_id: str,
            action: str,
            operator_id: str,
            now_ts: int,
            target_matrix_id: str | None = None,
        ) -> bool:
            _ = (group_id, action, operator_id, now_ts)
            await set_intention(target_matrix_id)
            return True

    class FakeMapOps:
        def __init__(self, session: object) -> None:
            _ = session

        async def count_groups_by_matrix(self, matrix_id: str) -> int:
            _ = matrix_id
            return 0

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_700_000_000)
    monkeypatch.setattr(repo_module.water_core_db, "session", _fake_session)
    monkeypatch.setattr(repo_module, "WaterMatrixMergeStateOps", FakeMergeOps)
    monkeypatch.setattr(repo_module, "WaterGroupMatrixMapOps", FakeMapOps)
    monkeypatch.setattr(
        repo, "get_or_create_group_matrix_id", AsyncMock(return_value="mtx_live2222")
    )
    monkeypatch.setattr(repo, "map_group_to_matrix", map_mock)

    ok, decision = await repo.set_matrix_merge_intention_once(
        group_id="20002",
        action="merge",
        operator_id="10001",
    )

    assert ok is True
    assert decision["target_matrix_id"] == "mtx_live2222"
    set_intention.assert_awaited_once_with("mtx_live2222")
    map_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_global_period_leaderboard_builds_trends(
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
                    msg_count=20,
                    active_hours=9,
                    hourly_counts=[1] * 24,
                    created_at=1,
                    updated_at=2,
                ),
                WaterSummaryRecord(
                    group_id="20002",
                    user_id="10002",
                    record_date=20260511,
                    msg_count=16,
                    active_hours=7,
                    hourly_counts=[0, 1] * 12,
                    created_at=1,
                    updated_at=2,
                ),
            ],
            [
                WaterSummaryRecord(
                    group_id="20001",
                    user_id="10002",
                    record_date=20260410,
                    msg_count=25,
                    active_hours=8,
                    hourly_counts=[1] * 24,
                    created_at=1,
                    updated_at=2,
                ),
                WaterSummaryRecord(
                    group_id="20002",
                    user_id="10001",
                    record_date=20260411,
                    msg_count=10,
                    active_hours=5,
                    hourly_counts=[1] * 24,
                    created_at=1,
                    updated_at=2,
                ),
            ],
        ]
    )
    monkeypatch.setattr(repo, "get_summaries_in_window", get_window_mock)

    rows = await repo.get_global_period_leaderboard(
        20260501,
        20260523,
        20260401,
        20260423,
    )

    assert len(rows) == 2
    assert rows[0].trend == 1
    assert rows[0].hourly_counts == [1] * 24
    assert rows[1].trend == -1


@pytest.mark.asyncio
async def test_get_natural_period_leaderboard_supports_group_and_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()
    rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260510,
            msg_count=20,
            active_hours=9,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="20002",
            user_id="10002",
            record_date=20260511,
            msg_count=16,
            active_hours=7,
            hourly_counts=[0, 1] * 12,
            created_at=1,
            updated_at=2,
        ),
    ]
    previous_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10002",
            record_date=20260509,
            msg_count=25,
            active_hours=8,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        )
    ]
    monkeypatch.setattr(
        repo,
        "_resolve_rank_scope_summaries",
        AsyncMock(side_effect=[rows, previous_rows, rows, previous_rows]),
    )
    monkeypatch.setattr(
        repo,
        "get_or_create_group_matrix_ids",
        AsyncMock(return_value={"20001": "mtx_1", "20002": "mtx_1"}),
    )

    group_rank = await repo.get_natural_period_leaderboard(
        subject="group",
        scope="global",
        group_id="20001",
        start_date=20260501,
        end_date=20260523,
        previous_start_date=20260401,
        previous_end_date=20260423,
    )
    matrix_rank = await repo.get_natural_period_leaderboard(
        subject="matrix",
        scope="global",
        group_id="20001",
        start_date=20260501,
        end_date=20260523,
        previous_start_date=20260401,
        previous_end_date=20260423,
    )

    assert [item.entity_id for item in group_rank] == ["20001", "20002"]
    assert matrix_rank[0].entity_id == "mtx_1"
    assert matrix_rank[0].group_count == 2


@pytest.mark.asyncio
async def test_get_natural_day_snapshot_reuses_single_fetch_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()

    from src.plugins.water.database import repo as repo_module

    current_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260614,
            msg_count=20,
            active_hours=9,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="20001",
            user_id="10002",
            record_date=20260614,
            msg_count=18,
            active_hours=7,
            hourly_counts=[0, 1] * 12,
            created_at=1,
            updated_at=2,
        ),
    ]
    previous_rows = [
        WaterSummaryRecord(
            group_id="20001",
            user_id="10003",
            record_date=20260613,
            msg_count=25,
            active_hours=8,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="20001",
            user_id="10001",
            record_date=20260613,
            msg_count=19,
            active_hours=6,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
        WaterSummaryRecord(
            group_id="20001",
            user_id="10002",
            record_date=20260613,
            msg_count=18,
            active_hours=5,
            hourly_counts=[1] * 24,
            created_at=1,
            updated_at=2,
        ),
    ]

    monkeypatch.setattr(repo_module, "get_current_time", lambda: 1_781_913_600)
    monkeypatch.setattr(repo_module.water_writer, "flush_now", AsyncMock())
    monkeypatch.setattr(
        repo,
        "_collect_realtime_daily_rows",
        AsyncMock(return_value=current_rows),
    )
    monkeypatch.setattr(
        repo,
        "_resolve_previous_day_rows",
        AsyncMock(return_value=previous_rows),
    )

    snapshot = await repo.get_natural_day_snapshot(
        subject="user",
        scope="group",
        group_id="20001",
        limit=2,
    )

    assert [item.entity_id for item in snapshot.leaderboard] == ["10001", "10002"]
    assert snapshot.leaderboard[0].trend == 1
    assert snapshot.leaderboard[1].trend == 1
    assert snapshot.overview.total_msg_count == 38
    assert snapshot.overview.active_entity_count == 2
    assert snapshot.overview.previous_total_msg_count == 62
