from unittest.mock import AsyncMock

import arrow
import pytest

from src.plugins.water.services.settlement import WaterSettlementService


@pytest.mark.asyncio
async def test_run_daily_settlement_skips_when_job_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WaterSettlementService()

    from src.plugins.water.services import settlement as settlement_module

    monkeypatch.setattr(
        settlement_module.water_repo,
        "try_start_settlement_job",
        AsyncMock(return_value=(False, "running")),
    )

    result = await service.run_daily_settlement(arrow.get("2026-03-02", "YYYY-MM-DD"))

    assert result.success is False
    assert result.skipped is True
    assert result.reason == "running"
    assert result.record_date == 20260302


@pytest.mark.asyncio
async def test_run_daily_settlement_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WaterSettlementService()

    from src.plugins.water.services import settlement as settlement_module

    aggregates = [
        type("Agg", (), {"user_id": "u1", "matrix_id": "m1", "msg_count": 10})(),
    ]

    monkeypatch.setattr(
        settlement_module.water_repo,
        "try_start_settlement_job",
        AsyncMock(return_value=(True, "started")),
    )
    collect_mock = AsyncMock(return_value=aggregates)
    apply_mock = AsyncMock()
    success_mock = AsyncMock()
    monkeypatch.setattr(
        settlement_module.water_repo, "collect_daily_aggregates", collect_mock
    )
    monkeypatch.setattr(
        settlement_module.water_repo, "apply_daily_settlement", apply_mock
    )
    monkeypatch.setattr(
        settlement_module.water_repo, "mark_settlement_success", success_mock
    )
    monkeypatch.setattr(service, "_trigger_achievements", AsyncMock(return_value=2))

    result = await service.run_daily_settlement(arrow.get("2026-03-02", "YYYY-MM-DD"))

    assert result.success is True
    assert result.skipped is False
    assert result.forced is False
    assert result.aggregate_rows == 1
    assert result.unlocked_achievements == 2
    collect_mock.assert_awaited_once()
    apply_mock.assert_awaited_once()
    success_mock.assert_awaited_once_with(20260302)


@pytest.mark.asyncio
async def test_run_daily_settlement_marks_failed_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WaterSettlementService()

    from src.plugins.water.services import settlement as settlement_module

    monkeypatch.setattr(
        settlement_module.water_repo,
        "try_start_settlement_job",
        AsyncMock(return_value=(True, "started")),
    )
    monkeypatch.setattr(
        settlement_module.water_repo,
        "collect_daily_aggregates",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    failed_mock = AsyncMock()
    monkeypatch.setattr(
        settlement_module.water_repo, "mark_settlement_failed", failed_mock
    )

    with pytest.raises(RuntimeError, match="boom"):
        await service.run_daily_settlement(arrow.get("2026-03-02", "YYYY-MM-DD"))

    failed_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_daily_settlement_force_result_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WaterSettlementService()

    from src.plugins.water.services import settlement as settlement_module

    aggregates = [
        type("Agg", (), {"user_id": "u1", "matrix_id": "m1", "msg_count": 10})(),
    ]

    monkeypatch.setattr(
        settlement_module.water_repo,
        "try_start_settlement_job",
        AsyncMock(return_value=(True, "forced")),
    )
    monkeypatch.setattr(
        settlement_module.water_repo,
        "collect_daily_aggregates",
        AsyncMock(return_value=aggregates),
    )
    monkeypatch.setattr(
        settlement_module.water_repo,
        "apply_daily_settlement",
        AsyncMock(),
    )
    monkeypatch.setattr(
        settlement_module.water_repo,
        "mark_settlement_success",
        AsyncMock(),
    )
    monkeypatch.setattr(service, "_trigger_achievements", AsyncMock(return_value=0))

    result = await service.run_daily_settlement(
        arrow.get("2026-03-02", "YYYY-MM-DD"),
        force=True,
    )

    assert result.success is True
    assert result.forced is True
