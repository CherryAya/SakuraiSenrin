from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pytest

from scripts import migrate_water as migrate_water_script
from src.database.system_migration import LegacyPgConfig
from src.plugins.water import migration as water_migration_module
from src.plugins.water.migration import WaterMigrationReport


def test_migrate_water_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["migrate_water.py"],
    )

    args = migrate_water_script.parse_args()

    assert args.old_repo == "../sakuraisenrin-old"
    assert args.pg_database == "senrin_water"
    assert args.report == "./data/db/water-migration-report.json"
    assert args.chunk_size == 2000
    assert args.no_reset_target is False


def test_migrate_water_build_pg_config_uses_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = LegacyPgConfig(
        host="127.0.0.1",
        port=5432,
        user="legacy",
        password="secret",
        database="senrin_system",
    )
    monkeypatch.setattr(
        migrate_water_script, "load_legacy_pg_config", lambda _: defaults
    )
    args = argparse.Namespace(
        old_repo="../old",
        pg_host="db.example.com",
        pg_port=15432,
        pg_user="water",
        pg_password="pw",
        pg_database="senrin_water",
    )

    config = migrate_water_script.build_pg_config(args)

    assert config == LegacyPgConfig(
        host="db.example.com",
        port=15432,
        user="water",
        password="pw",
        database="senrin_water",
    )


def test_migrate_water_parse_day_arg_validates_format() -> None:
    assert migrate_water_script._parse_day_arg("20260612") == 20260612
    with pytest.raises(argparse.ArgumentTypeError):
        migrate_water_script._parse_day_arg("2026-06-12")


@pytest.mark.asyncio
async def test_migrate_water_main_fetches_imports_and_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "water-report.json"
    args = argparse.Namespace(
        old_repo="../old",
        pg_host=None,
        pg_port=None,
        pg_user=None,
        pg_password=None,
        pg_database="senrin_water",
        report=str(report_path),
        no_reset_target=False,
        from_date=20260610,
        to_date=20260612,
        chunk_size=500,
    )
    raw_rows = [
        {
            "id": 1,
            "user_id": "10001",
            "group_id": "20001",
            "created_at": datetime(2026, 6, 11, 8, 0, 0),
        }
    ]
    built_rows = ["row"]
    report = WaterMigrationReport(
        source_rows=1,
        imported_messages=1,
        imported_counter_rows=1,
        settled_days=1,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(migrate_water_script.nonebot, "init", lambda: None)
    monkeypatch.setattr(migrate_water_script, "parse_args", lambda: args)
    monkeypatch.setattr(
        migrate_water_script,
        "build_pg_config",
        lambda incoming_args: LegacyPgConfig(
            host="127.0.0.1",
            port=5432,
            user="legacy",
            password="secret",
            database=incoming_args.pg_database,
        ),
    )

    async def _fake_fetch(
        config: LegacyPgConfig,
        *,
        from_date: int | None = None,
        to_date: int | None = None,
    ) -> list[dict[str, object]]:
        captured["fetch"] = {
            "config": config,
            "from_date": from_date,
            "to_date": to_date,
        }
        return raw_rows

    async def _fake_migrate(
        rows: list[object],
        *,
        reset_target: bool = True,
        chunk_size: int = 1000,
    ) -> WaterMigrationReport:
        captured["migrate"] = {
            "rows": rows,
            "reset_target": reset_target,
            "chunk_size": chunk_size,
        }
        return report

    def _fake_write_report(path: Path, incoming_report: WaterMigrationReport) -> None:
        captured["report"] = {"path": path, "report": incoming_report}

    monkeypatch.setattr(water_migration_module, "fetch_legacy_water_rows", _fake_fetch)
    monkeypatch.setattr(
        water_migration_module,
        "build_legacy_water_rows",
        lambda rows: built_rows if rows == raw_rows else [],
    )
    monkeypatch.setattr(
        water_migration_module,
        "migrate_legacy_water",
        _fake_migrate,
    )
    monkeypatch.setattr(water_migration_module, "write_report", _fake_write_report)

    await migrate_water_script.main()

    assert captured["fetch"] == {
        "config": LegacyPgConfig(
            host="127.0.0.1",
            port=5432,
            user="legacy",
            password="secret",
            database="senrin_water",
        ),
        "from_date": 20260610,
        "to_date": 20260612,
    }
    assert captured["migrate"] == {
        "rows": built_rows,
        "reset_target": True,
        "chunk_size": 500,
    }
    assert captured["report"] == {"path": report_path, "report": report}
