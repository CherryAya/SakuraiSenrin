from pathlib import Path
import sqlite3

import pytest

from scripts.fix_wordbank_face_migration import (
    _parse_args,
    repair_wordbank_face_migration,
)
from src.plugins.wordbank.database.instances import wordbank_main_db
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.message_model import (
    shape_from_payload,
    shape_from_text,
)


@pytest.mark.asyncio
async def test_repair_wordbank_face_migration_converts_legacy_face_atoms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    await repository.init_all_tables()
    await repository.create_or_append_response(
        trigger_shape=shape_from_text("触发"),
        response_shape=shape_from_text("响应"),
        rule={},
        scope="all_groups",
        priority=1,
        trigger_probability=1.0,
        weight=1,
        group_id="",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="",
        deleted_at=0,
        created_at=1,
        updated_at=1,
    )
    db_path = wordbank_main_db.base_dir / wordbank_main_db.filename
    legacy_payload = '[{"kind":"text","text":"[face:1]"}]'

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE wordbank_trigger_variant
            SET trigger_text = '[face:1]', message_json = ?
            """,
            (legacy_payload,),
        )
        connection.execute(
            """
            UPDATE wordbank_response_item
            SET text = '[face:2]', message_json = ?
            """,
            ('[{"kind":"text","text":"[face:2]"}]',),
        )
        connection.commit()

    dry_report = repair_wordbank_face_migration(db_path)
    assert dry_report.changed_variants == 1
    assert dry_report.changed_responses == 1

    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT message_json FROM wordbank_trigger_variant"
            ).fetchone()[0]
            == legacy_payload
        )

    report = repair_wordbank_face_migration(db_path, dry_run=False)
    assert report.changed_groups == {1}
    assert report.converted_faces == 2

    with sqlite3.connect(db_path) as connection:
        variant_payload = connection.execute(
            "SELECT message_json FROM wordbank_trigger_variant"
        ).fetchone()[0]
        response_payload = connection.execute(
            "SELECT message_json FROM wordbank_response_item"
        ).fetchone()[0]
        document = connection.execute(
            """
            SELECT trigger_text, response_text, trigger_structure_key
            FROM wordbank_search_document
            """
        ).fetchone()

    assert shape_from_payload(variant_payload).atoms[0].kind == "face"
    assert shape_from_payload(response_payload).atoms[0].kind == "face"
    assert document == ("[表情 ID：1]", "[表情 ID：2]", "face")


def test_fix_wordbank_face_migration_cli_defaults_to_dry_run() -> None:
    assert _parse_args([]).apply is False
    assert _parse_args(["--dry-run"]).apply is False
    assert _parse_args(["--apply"]).apply is True
