from tests.plugins.wordbank.test_migration_support import *


def test_normalize_legacy_rules_widens_priority_two_empty_or_branch_to_global() -> None:
    targets = normalize_legacy_rules(
        priority=2,
        response_rule_conditions={
            "$or": [
                {"group_id": {"$eq": 879823693}},
                {},
            ]
        },
    )

    assert len(targets) == 1
    assert targets[0].scope == "all_groups"
    assert targets[0].group_id == ""
    assert targets[0].rule == {}


def test_migration_report_groups_failures_by_category() -> None:
    report = WordbankMigrationReport(
        skipped_rows=2,
        failure_details=[
            {"reason": "image file empty: abc.jpg"},
            {"reason": "image file not found: def.jpg"},
        ],
    )

    categorized = report.to_failure_categories_dict()
    categories = categorized["categories"]

    assert categorized["total_failed"] == 2
    assert isinstance(categories, dict)
    assert categories["image_file_empty"]["count"] == 1
    assert categories["image_file_missing"]["count"] == 1


@pytest.mark.asyncio
async def test_migrate_legacy_rows_recreates_target_namespace_without_patches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    db_dir = tmp_path / "db" / "wordbank_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "wordbank_main.db"
    sentinel = db_dir / "legacy-sentinel.txt"
    sentinel.write_text("legacy", encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE wordbank_response_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_group_id INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                enabled INTEGER NOT NULL DEFAULT 1,
                scope VARCHAR(32) NOT NULL,
                priority INTEGER NOT NULL,
                weight INTEGER NOT NULL DEFAULT 3,
                probability FLOAT NOT NULL DEFAULT 1.0,
                rule JSON NOT NULL DEFAULT '{}',
                group_id VARCHAR(64) NOT NULL DEFAULT '',
                created_by VARCHAR(64) NOT NULL,
                approved_by VARCHAR(64) NOT NULL DEFAULT '',
                deleted_at INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                message_json TEXT NOT NULL DEFAULT '[]',
                exact_md5 VARCHAR(32) NOT NULL DEFAULT '',
                structure_key TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT '',
                search_tokens TEXT NOT NULL DEFAULT '',
                image_keys TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wordbank_response_item (
                trigger_group_id,
                status,
                enabled,
                scope,
                priority,
                weight,
                probability,
                rule,
                group_id,
                created_by,
                approved_by,
                deleted_at,
                text,
                message_json,
                exact_md5,
                structure_key,
                search_text,
                search_tokens,
                image_keys,
                created_at,
                updated_at
            ) VALUES (
                1,
                'approved',
                1,
                'self',
                40,
                3,
                0.5,
                '{}',
                '',
                'legacy-user',
                '',
                0,
                'legacy',
                '[]',
                'legacy-md5',
                'text',
                'legacy',
                'legacy',
                '',
                1,
                1
            )
            """
        )
        conn.commit()

    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    image_root = tmp_path / "images"
    image_root.mkdir()
    catalog = build_legacy_image_catalog(image_root, None)
    rows = [
        {
            "response_id": 1,
            "trigger_id": 1,
            "response_text": json.dumps(
                [{"type": "text", "text": "新回复"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 4,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "新触发"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 0.8}),
            "extra_info": None,
            "approval_status": "APPROVED",
        }
    ]

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        reset_target=True,
    )

    assert report.imported_rows == 1
    assert not sentinel.exists()

    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(wordbank_response_item)")
        }
        response_count = conn.execute(
            "SELECT COUNT(*) FROM wordbank_response_item"
        ).fetchone()[0]
        created_by = conn.execute(
            "SELECT created_by FROM wordbank_response_item"
        ).fetchone()[0]

    assert "probability" not in columns
    assert response_count == 1
    assert created_by == "10001"


def _segment_list(value: dict[str, object]) -> list[dict[str, Any]]:
    segments = value.get("segments")
    assert isinstance(segments, list)
    normalized: list[dict[str, Any]] = []
    for item in segments:
        assert isinstance(item, dict)
        normalized.append(item)
    return normalized
