from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scripts import archive_event_shards as archive_script


def test_archive_script_parse_args_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["archive_event_shards.py"])

    args = archive_script.parse_args()

    assert args.target == "all"
    assert args.include_water_summary is False
    assert args.cleanup_stale_sidecars is False


@pytest.mark.asyncio
async def test_archive_targets_wordbank_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    wordbank_log_db = archive_script.wordbank_log_db
    wordbank_ref_db = archive_script.wordbank_message_ref_db
    water_message = archive_script.water_message

    async def _archive_wordbank_logs() -> None:
        called.append("wordbank_logs")

    async def _archive_wordbank_refs() -> None:
        called.append("wordbank_message_ref")

    async def _archive_water_message() -> None:
        called.append("water_message")

    monkeypatch.setattr(
        wordbank_log_db,
        "run_archiver_task",
        _archive_wordbank_logs,
    )
    monkeypatch.setattr(
        wordbank_ref_db,
        "run_archiver_task",
        _archive_wordbank_refs,
    )
    monkeypatch.setattr(
        water_message,
        "run_archiver_task",
        _archive_water_message,
    )

    completed = await archive_script.archive_targets(target="wordbank")

    assert completed[0] == ["wordbank_logs", "wordbank_message_ref"]
    assert called == ["wordbank_logs", "wordbank_message_ref"]


@pytest.mark.asyncio
async def test_archive_targets_water_with_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    async def _archive_water_message() -> None:
        called.append("water_message")

    async def _archive_water_summary() -> None:
        called.append("water_summary")

    monkeypatch.setattr(
        archive_script.water_message,
        "run_archiver_task",
        _archive_water_message,
    )
    monkeypatch.setattr(
        archive_script.water_summary,
        "run_archiver_task",
        _archive_water_summary,
    )

    completed = await archive_script.archive_targets(
        target="water",
        include_water_summary=True,
    )

    assert completed[0] == ["water_message", "water_summary"]
    assert called == ["water_message", "water_summary"]


def test_cleanup_stale_sidecars_only_removes_archived_shards(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "wordbank_db"
    namespace_dir.mkdir(parents=True, exist_ok=True)
    archived = namespace_dir / "wordbank_logs_2026_01.db.zst"
    archived.write_bytes(b"zstd")
    hot_db = namespace_dir / "wordbank_logs_2026_06.db"
    hot_db.write_bytes(b"db")
    stale_wal = namespace_dir / "wordbank_logs_2026_01.db-wal"
    stale_wal.write_bytes(b"wal")
    stale_shm = namespace_dir / "wordbank_logs_2026_01.db-shm"
    stale_shm.write_bytes(b"shm")
    hot_wal = namespace_dir / "wordbank_logs_2026_06.db-wal"
    hot_wal.write_bytes(b"wal")

    store = argparse.Namespace(base_dir=namespace_dir, prefix="wordbank_logs")

    cleaned = archive_script.cleanup_stale_sidecars([("wordbank_logs", store)])

    assert cleaned == {"wordbank_logs": 2}
    assert not stale_wal.exists()
    assert not stale_shm.exists()
    assert hot_wal.exists()


@pytest.mark.asyncio
async def test_archive_script_main_logs_completed_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(
        target="all",
        include_water_summary=True,
        cleanup_stale_sidecars=True,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(archive_script, "parse_args", lambda: args)

    async def _archive_targets(
        *,
        target: str,
        include_water_summary: bool = False,
    ) -> tuple[list[str], list[tuple[str, object]]]:
        captured["archive"] = {
            "target": target,
            "include_water_summary": include_water_summary,
        }
        return (
            ["wordbank_logs", "water_message", "water_summary"],
            [("wordbank_logs", object())],
        )

    monkeypatch.setattr(archive_script, "archive_targets", _archive_targets)
    monkeypatch.setattr(
        archive_script,
        "cleanup_stale_sidecars",
        lambda stores: {"wordbank_logs": len(stores)},
    )
    monkeypatch.setattr(
        archive_script.logger,
        "info",
        lambda message: captured.__setitem__("info", message),
    )
    monkeypatch.setattr(
        archive_script.logger,
        "success",
        lambda message: captured.__setitem__("success", message),
    )

    await archive_script.main()

    assert captured["archive"] == {
        "target": "all",
        "include_water_summary": True,
    }
    assert "target=all" in str(captured["info"])
    assert "cleanup_stale_sidecars=True" in str(captured["info"])
    assert "wordbank_logs,water_message,water_summary" in str(captured["success"])
    assert "cleaned={'wordbank_logs': 1}" in str(captured["success"])
