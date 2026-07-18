from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import fix_wordbank_trigger_migration as fix_script
from src.plugins.wordbank.database.instances import wordbank_main_db
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.message_model import shape_from_text


def _legacy_rows() -> list[dict[str, object]]:
    return [
        {
            "response_id": 11,
            "trigger_id": 7,
            "response_text": json.dumps(
                [{"type": "text", "text": "晚安"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({}, ensure_ascii=False),
            "weight": 3,
            "priority": 3,
            "created_by": "10001",
            "created_at": 1,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "今天：好啊"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}, ensure_ascii=False),
            "extra_info": None,
            "approval_status": "APPROVED",
        }
    ]


async def _prepare_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    db_path = wordbank_main_db.base_dir / wordbank_main_db.filename
    repository = WordbankRepository()
    await repository.init_all_tables()
    await repository.create_or_append_response(
        trigger_shape=shape_from_text("今天:好啊"),
        response_shape=shape_from_text("晚安"),
        rule={},
        scope="all_groups",
        priority=3,
        trigger_probability=1.0,
        weight=3,
        group_id="",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="",
        deleted_at=0,
        created_at=1,
        updated_at=1,
    )
    return db_path


@pytest.mark.asyncio
async def test_fix_wordbank_trigger_migration_updates_trigger_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = await _prepare_database(tmp_path, monkeypatch)
    image_root = tmp_path / "images"
    image_root.mkdir()
    config = fix_script.LegacyPgConfig(
        host="127.0.0.1",
        port=5432,
        user="tester",
        password="secret",
    )

    async def fake_fetch_rows(
        incoming_config: fix_script.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return _legacy_rows()

    monkeypatch.setattr(
        fix_script,
        "fetch_legacy_response_rows",
        fake_fetch_rows,
    )

    report = await asyncio.to_thread(
        fix_script.fix_wordbank_trigger_migration,
        db_path=db_path,
        image_root=image_root,
        mapping_path=None,
        pg_config=config,
        dry_run=False,
    )

    assert report.scanned_rows == 1
    assert report.matched_rows == 1
    assert report.updated_groups == 1
    assert report.updated_variants == 1
    assert report.skipped_rows == 0

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                tv.trigger_text,
                tv.message_json,
                sd.trigger_text
            FROM wordbank_trigger_variant tv
            JOIN wordbank_search_document sd
              ON sd.trigger_group_id = tv.trigger_group_id
            """
        ).fetchone()

    assert row is not None
    assert row[0] == "今天：好啊"
    assert row[1] == '[{"kind":"text","text":"今天：好啊"}]'
    assert row[2] == "今天：好啊"


@pytest.mark.asyncio
async def test_fix_wordbank_trigger_migration_dry_run_keeps_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = await _prepare_database(tmp_path, monkeypatch)
    image_root = tmp_path / "images"
    image_root.mkdir()
    config = fix_script.LegacyPgConfig(
        host="127.0.0.1",
        port=5432,
        user="tester",
        password="secret",
    )

    async def fake_fetch_rows(
        incoming_config: fix_script.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return _legacy_rows()

    monkeypatch.setattr(
        fix_script,
        "fetch_legacy_response_rows",
        fake_fetch_rows,
    )

    report = await asyncio.to_thread(
        fix_script.fix_wordbank_trigger_migration,
        db_path=db_path,
        image_root=image_root,
        mapping_path=None,
        pg_config=config,
        dry_run=True,
    )

    assert report.updated_groups == 1
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT trigger_text, message_json FROM wordbank_trigger_variant"
        ).fetchone()

    assert row is not None
    assert row[0] == "今天:好啊"
    assert row[1] == '[{"kind":"text","text":"今天:好啊"}]'
