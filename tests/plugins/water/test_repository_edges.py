from types import SimpleNamespace, TracebackType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.plugins.water.database.repo import WaterRepository


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
