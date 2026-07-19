from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import export_wordbank_trigger_fix_plan as export_script
from scripts import fix_wordbank_trigger_migration as fix_script
from scripts.migrations.wordbank_types import LegacyPgConfig
from src.plugins.wordbank.database.instances import wordbank_main_db
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.message_model import shape_from_text


def _legacy_row(
    *,
    response_id: int = 11,
    trigger_id: int = 7,
    trigger_text: str = "今天：好啊",
) -> dict[str, object]:
    return {
        "response_id": response_id,
        "trigger_id": trigger_id,
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
            [{"type": "text", "text": trigger_text}],
            ensure_ascii=False,
        ),
        "trigger_config": json.dumps({"probability": 1.0}, ensure_ascii=False),
        "extra_info": None,
        "approval_status": "APPROVED",
    }


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
async def test_export_trigger_fix_plan_dedupes_same_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = await _prepare_database(tmp_path, monkeypatch)
    image_root = tmp_path / "images"
    image_root.mkdir()
    output_path = tmp_path / ".devtest" / "wordbank-trigger-fix-plan.json"
    config = LegacyPgConfig(
        host="127.0.0.1",
        port=5432,
        user="tester",
        password="secret",
    )

    async def fake_fetch_rows(
        incoming_config: LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return [
            _legacy_row(response_id=11, trigger_id=7),
            _legacy_row(response_id=12, trigger_id=7),
        ]

    monkeypatch.setattr(
        export_script,
        "fetch_legacy_response_rows",
        fake_fetch_rows,
    )

    report = await asyncio.to_thread(
        export_script.export_trigger_fix_plan,
        db_path=db_path,
        output_path=output_path,
        image_root=image_root,
        mapping_path=None,
        pg_config=config,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["planned_groups"] == 1
    assert report["planned_variants"] == 1
    assert len(payload["operations"]) == 1
    assert payload["operations"][0]["trigger_group_id"] == 1
    assert payload["operations"][0]["legacy_response_ids"] == [11, 12]
    assert payload["operations"][0]["legacy_trigger_ids"] == [7]
    assert payload["operations"][0]["desired_variant"]["trigger_text"] == "今天：好啊"


@pytest.mark.asyncio
async def test_apply_trigger_fix_plan_updates_trigger_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = await _prepare_database(tmp_path, monkeypatch)
    image_root = tmp_path / "images"
    image_root.mkdir()

    plan_payload, _ = await asyncio.to_thread(
        fix_script.build_trigger_fix_plan,
        db_path=db_path,
        legacy_rows=[_legacy_row()],
        image_root=image_root,
        mapping_path=None,
    )

    report = await asyncio.to_thread(
        fix_script.apply_trigger_fix_plan,
        db_path=db_path,
        plan_payload=plan_payload,
        dry_run=False,
    )

    assert report.updated_groups == 1
    assert report.updated_variants == 1
    assert report.skipped_operations == 0

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
async def test_apply_trigger_fix_plan_dry_run_keeps_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = await _prepare_database(tmp_path, monkeypatch)
    image_root = tmp_path / "images"
    image_root.mkdir()

    plan_payload, _ = await asyncio.to_thread(
        fix_script.build_trigger_fix_plan,
        db_path=db_path,
        legacy_rows=[_legacy_row()],
        image_root=image_root,
        mapping_path=None,
    )

    report = await asyncio.to_thread(
        fix_script.apply_trigger_fix_plan,
        db_path=db_path,
        plan_payload=plan_payload,
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


@pytest.mark.asyncio
async def test_apply_trigger_fix_plan_skips_when_sqlite_state_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = await _prepare_database(tmp_path, monkeypatch)
    image_root = tmp_path / "images"
    image_root.mkdir()

    plan_payload, _ = await asyncio.to_thread(
        fix_script.build_trigger_fix_plan,
        db_path=db_path,
        legacy_rows=[_legacy_row()],
        image_root=image_root,
        mapping_path=None,
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE wordbank_trigger_variant
            SET trigger_text = ?, message_json = ?
            WHERE id = 1
            """,
            ("现在已经被改过", '[{"kind":"text","text":"现在已经被改过"}]'),
        )
        connection.commit()

    report = await asyncio.to_thread(
        fix_script.apply_trigger_fix_plan,
        db_path=db_path,
        plan_payload=plan_payload,
        dry_run=False,
    )

    assert report.updated_groups == 0
    assert report.skipped_operations == 1
    assert report.skipped_reasons == {"group 1: current_state_mismatch": 1}

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT trigger_text FROM wordbank_trigger_variant WHERE id = 1"
        ).fetchone()

    assert row is not None
    assert row[0] == "现在已经被改过"
