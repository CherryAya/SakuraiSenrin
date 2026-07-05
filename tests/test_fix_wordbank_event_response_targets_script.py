from __future__ import annotations

from pathlib import Path
import sqlite3

from scripts.fix_wordbank_event_response_targets import (
    fix_wordbank_event_response_targets,
)

BOUND_SENDER_JSON = (
    '[{"kind":"at","target_id":"__sender__"},{"kind":"text","text":" 已绑定"}]'
)


def _prepare_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE wordbank_trigger_variant (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_group_id INTEGER NOT NULL,
                message_json TEXT NOT NULL
            );

            CREATE TABLE wordbank_response_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_group_id INTEGER NOT NULL,
                deleted_at INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                message_json TEXT NOT NULL DEFAULT '[]',
                exact_md5 TEXT NOT NULL DEFAULT '',
                structure_key TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT '',
                search_tokens TEXT NOT NULL DEFAULT '',
                image_keys TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE wordbank_search_document (
                trigger_group_id INTEGER PRIMARY KEY
            );

            CREATE TABLE wordbank_search_image_map (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            );

            CREATE VIRTUAL TABLE wordbank_search_trigger_fts USING fts5(tokens);
            CREATE VIRTUAL TABLE wordbank_search_response_fts USING fts5(tokens);
            """
        )
        connection.execute(
            """
            INSERT INTO wordbank_trigger_variant(trigger_group_id, message_json)
            VALUES
                (1, '[{"kind":"event","event_name":"event:at"}]'),
                (2, '[{"kind":"event","event_name":"event:poke"}]')
            """
        )
        connection.execute(
            """
            INSERT INTO wordbank_response_item(
                id, trigger_group_id, deleted_at, text, message_json
            )
            VALUES
                (10, 1, 0, '在？', '[{"kind":"text","text":"在？"}]'),
                (11, 2, 0, '别戳', '[{"kind":"text","text":"别戳"}]'),
                (
                    12,
                    1,
                    0,
                    '@触发者 已绑定',
                    ?
                )
            """,
            (BOUND_SENDER_JSON,),
        )
        connection.commit()
    finally:
        connection.close()


def test_fix_wordbank_event_response_targets_updates_event_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "wordbank_main.db"
    _prepare_db(db_path)

    stats = fix_wordbank_event_response_targets(db_path)

    assert stats.scanned == 3
    assert stats.updated == 2
    assert stats.at_updated == 1
    assert stats.poke_updated == 1

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT id, message_json, text FROM wordbank_response_item ORDER BY id ASC"
        ).fetchall()
        assert rows[0][1] == (
            '[{"kind":"at","target_id":"__sender__"},{"kind":"text","text":"在？"}]'
        )
        assert rows[0][2] == "@触发者 在？"
        assert rows[1][1] == (
            '[{"kind":"at","target_id":"__sender__"},{"kind":"text","text":"别戳"}]'
        )
        assert rows[1][2] == "@触发者 别戳"
        assert rows[2][1] == (
            '[{"kind":"at","target_id":"__sender__"},{"kind":"text","text":" 已绑定"}]'
        )
        trigger_fts_count = connection.execute(
            "SELECT COUNT(*) FROM wordbank_search_trigger_fts"
        ).fetchone()[0]
        response_fts_count = connection.execute(
            "SELECT COUNT(*) FROM wordbank_search_response_fts"
        ).fetchone()[0]
        assert trigger_fts_count == 0
        assert response_fts_count == 0
    finally:
        connection.close()
