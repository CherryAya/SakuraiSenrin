from types import SimpleNamespace, TracebackType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.plugins.water.database.repo import WaterRepository
from src.plugins.water.database.types import WaterSummaryRecord


class _DummySessionCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        _ = (exc_type, exc, tb)
        return False


def _fake_session(**kwargs: Any) -> _DummySessionCtx:
    _ = kwargs
    return _DummySessionCtx()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_get_first_summary_record_date_uses_scope_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = WaterRepository()
    monkeypatch.setattr(
        repo,
        "_resolve_rank_scope_summaries",
        AsyncMock(
            return_value=[
                WaterSummaryRecord(
                    group_id="20001",
                    user_id="10001",
                    record_date=20260301,
                    msg_count=10,
                    active_hours=4,
                    hourly_counts=[0] * 24,
                    created_at=1,
                    updated_at=1,
                ),
                WaterSummaryRecord(
                    group_id="20002",
                    user_id="10002",
                    record_date=20260305,
                    msg_count=12,
                    active_hours=5,
                    hourly_counts=[0] * 24,
                    created_at=1,
                    updated_at=1,
                ),
            ]
        ),
    )

    first_date = await repo.get_first_summary_record_date(
        subject="user",
        scope="matrix",
        group_id="20001",
    )

    assert first_date == 20260301


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


@pytest.mark.asyncio
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
        ) -> list[WaterSummaryRecord]:
            assert start_date == 20260301
            assert end_date == 20260331
            assert group_ids == ["20001"]
            assert user_id is None
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


@pytest.mark.asyncio
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
