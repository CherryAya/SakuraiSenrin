from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from PIL import Image
import pytest
from sqlalchemy import select

from src.plugins.wordbank import migration as migration_module
from src.plugins.wordbank.database.instances import wordbank_log_db
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.tables import WordbankLog
from src.plugins.wordbank.migration import (
    WordbankMigrationReport,
    build_legacy_image_catalog,
    legacy_message_to_shape,
    migrate_legacy_rows,
    migrate_legacy_wordbank,
    normalize_legacy_rules,
    normalize_legacy_scope,
    normalize_legacy_state,
    parse_legacy_env_file,
)
from src.plugins.wordbank.services.media import WordbankMediaService


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (20, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_legacy_message_to_shape_uses_local_image_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()
    source_path = image_root / "ABCDEF1234567890ABCDEF1234567890.png"
    source_path.write_bytes(_png_bytes((255, 0, 0)))

    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "img/original.bin": {
                    "saved_as": source_path.name,
                }
            }
        ),
        encoding="utf-8",
    )

    catalog = build_legacy_image_catalog(image_root, mapping_path)
    repository = WordbankRepository()
    await repository.init_all_tables()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")

    shape = await legacy_message_to_shape(
        [
            {"type": "text", "text": "晚安"},
            {"type": "image", "file": "img/original.bin"},
            {"type": "at", "qq": "10001"},
        ],
        image_catalog=catalog,
        media_service=media_service,
    )

    assert [atom.kind for atom in shape.atoms] == ["text", "image", "at"]
    assert shape.atoms[1].canonical_image_id == 1
    assert list((tmp_path / "media").glob("*.webp"))


@pytest.mark.asyncio
async def test_legacy_message_to_shape_prefers_file_mapping_saved_as(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()
    mapped_path = image_root / "REALMD5000000000000000000000000001.gif"
    mapped_path.write_bytes(_png_bytes((1, 2, 3)))
    misleading_path = image_root / "legacy-name.jpg"
    misleading_path.write_bytes(_png_bytes((0, 0, 255)))

    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "img/legacy-name.jpg": {
                    "saved_as": mapped_path.name,
                    "md5": "REALMD5000000000000000000000000001",
                }
            }
        ),
        encoding="utf-8",
    )

    catalog = build_legacy_image_catalog(image_root, mapping_path)
    repository = WordbankRepository()
    await repository.init_all_tables()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    report = WordbankMigrationReport()

    shape = await legacy_message_to_shape(
        [{"type": "image", "file": "img/legacy-name.jpg"}],
        image_catalog=catalog,
        media_service=media_service,
        report=report,
    )

    assert [atom.kind for atom in shape.atoms] == ["image"]
    assert shape.atoms[0].canonical_image_id == 1
    assert report.image_resolution_counts["mapping"] == 1


@pytest.mark.asyncio
async def test_legacy_message_to_shape_resolves_trailing_md5_image_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()
    source_path = image_root / "D510679C6C463C58A5E62B216467600E.jpg"
    source_path.write_bytes(_png_bytes((0, 255, 0)))

    catalog = build_legacy_image_catalog(image_root, None)
    repository = WordbankRepository()
    await repository.init_all_tables()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    report = WordbankMigrationReport()

    shape = await legacy_message_to_shape(
        [
            {
                "type": "image",
                "file": (
                    "000001464e61704361744f6e65426f747c4d736746696c657c327c"
                    "383133333737363130.D510679C6C463C58A5E62B216467600E.jpg"
                ),
            }
        ],
        image_catalog=catalog,
        media_service=media_service,
        report=report,
    )

    assert [atom.kind for atom in shape.atoms] == ["image"]
    assert shape.atoms[0].canonical_image_id == 1
    assert report.image_resolution_counts["md5_index"] == 1


@pytest.mark.asyncio
async def test_legacy_message_to_shape_reports_empty_image_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()
    source_path = image_root / "EMPTY0000000000000000000000000001.jpg"
    source_path.write_bytes(b"")

    catalog = build_legacy_image_catalog(image_root, None)
    repository = WordbankRepository()
    await repository.init_all_tables()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")

    with pytest.raises(Exception, match="image file empty"):
        await legacy_message_to_shape(
            [{"type": "image", "file": source_path.name}],
            image_catalog=catalog,
            media_service=media_service,
        )


def test_normalize_legacy_scope_and_state() -> None:
    scope, group_id, rule = normalize_legacy_scope(
        priority=1,
        response_rule_conditions={
            "group_id": {"$eq": 20001},
            "user_id": {"$eq": 10001},
        },
    )
    state = normalize_legacy_state(
        approval_status="APPROVED",
        response_available=False,
        migration_time=123456,
    )

    assert (scope, group_id, rule) == ("self_in_current_group", "20001", {})
    assert state.status == "approved"
    assert state.enabled == 0
    assert state.deleted_at == 123456


def test_normalize_legacy_rules_expands_or_role_and_call_count() -> None:
    targets = normalize_legacy_rules(
        priority=3,
        response_rule_conditions={
            "$or": [
                {"group_id": {"$eq": 20001}},
                {
                    "$and": [
                        {"role": {"$eq": "member"}},
                        {"call_count": {"$range": [6, 10]}},
                    ]
                },
            ]
        },
        trigger_config={"lifecycle": 3600},
    )

    assert len(targets) == 2
    assert targets[0].scope == "current_group"
    assert targets[0].group_id == "20001"
    assert targets[0].rule == {}
    assert targets[1].scope == "all_groups"
    assert targets[1].rule == {
        "roles": "member",
        "call_count": {
            "window_seconds": 3600,
            "min": 6,
            "max": 10,
        },
    }


def test_normalize_legacy_rules_prunes_redundant_specific_branch() -> None:
    targets = normalize_legacy_rules(
        priority=3,
        response_rule_conditions={
            "$or": [
                {},
                {"role": {"$eq": "member"}},
            ]
        },
    )

    assert len(targets) == 1
    assert targets[0].scope == "all_groups"
    assert targets[0].rule == {}


def test_parse_legacy_env_file_supports_assignment_styles(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.dev"
    env_path.write_text(
        "\n".join(
            [
                'pg_host = "127.0.0.1"',
                "pg_port = 5432",
                "ENVIRONMENT=dev",
            ]
        ),
        encoding="utf-8",
    )

    values = parse_legacy_env_file(env_path)

    assert values["pg_host"] == "127.0.0.1"
    assert values["pg_port"] == 5432
    assert values["ENVIRONMENT"] == "dev"


@pytest.mark.asyncio
async def test_migrate_legacy_rows_imports_images_and_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()
    image_name = "1665E797A17A20AF7AFB9DB71A27D788.jpg"
    (image_root / image_name).write_bytes(_png_bytes((0, 0, 255)))

    catalog = build_legacy_image_catalog(image_root, None)
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    rows = [
        {
            "response_id": 1,
            "trigger_id": 1,
            "response_text": json.dumps(
                [{"type": "image", "file": image_name}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "我的自拍"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
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
    entries = await repository.list_enabled_entries()

    assert report.imported_rows == 1
    assert report.imported_groups == 1
    assert report.imported_response_items == 1
    assert report.imported_entries == 1
    assert report.skipped_rows == 0
    assert len(entries) == 1
    assert entries[0].status == "approved"
    assert entries[0].group_id == "20001"
    assert entries[0].trigger_variants[0].trigger_text == "我的自拍"


@pytest.mark.asyncio
async def test_migrate_legacy_rows_imports_response_logs_into_current_log_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    image_root = tmp_path / "images"
    image_root.mkdir()
    catalog = build_legacy_image_catalog(image_root, None)
    call_time = datetime(2024, 1, 3, 8, 0, tzinfo=UTC)
    rows = [
        {
            "response_id": 11,
            "trigger_id": 7,
            "response_text": json.dumps(
                [{"type": "text", "text": "做个好梦"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "晚安"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
            "extra_info": None,
            "approval_status": "APPROVED",
        }
    ]
    response_log_rows = [
        {
            "log_id": 99,
            "message_id": "legacy-msg-1",
            "response_id": 11,
            "user_id": "10086",
            "call_time": call_time,
            "group_id": "20001",
            "message_type": "group",
        }
    ]

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        response_log_rows=response_log_rows,
        reset_target=True,
    )

    async with wordbank_log_db.read_session(time_ctx=call_time) as session:
        logs = (
            (
                await session.execute(
                    select(WordbankLog).order_by(WordbankLog.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    assert report.imported_rows == 1
    assert report.imported_log_rows == 1
    assert report.skipped_log_rows == 0
    assert len(logs) == 1
    assert logs[0].group_id == "20001"
    assert logs[0].user_id == "10086"
    assert logs[0].message_type == "group"
    assert logs[0].matched_text == "晚安"


@pytest.mark.asyncio
async def test_migrate_legacy_rows_imports_approval_message_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    image_root = tmp_path / "images"
    image_root.mkdir()
    catalog = build_legacy_image_catalog(image_root, None)
    rows = [
        {
            "response_id": 12,
            "trigger_id": 9,
            "response_text": json.dumps(
                [{"type": "text", "text": "晚点见"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20003}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10003",
            "created_at": 1700000200,
            "response_available": False,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "等会"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
            "extra_info": None,
            "approval_status": "PENDING",
        }
    ]
    addition_log_rows = [
        {
            "log_id": 7,
            "response_id": 12,
            "user_id": "10003",
            "add_time": datetime(2024, 1, 4, 9, 0, tzinfo=UTC),
            "add_source": {"group_id": "20003", "user_id": "10003"},
            "created_message_id": "source-msg-77",
            "approval_id": None,
        }
    ]
    message_approval_rows = [
        {
            "id": 13,
            "message_id": "approval-msg-12",
            "approval_id": 88,
            "response_id": 12,
            "approval_user_id": "10003",
            "approval_created_at": datetime(2024, 1, 4, 9, 1, tzinfo=UTC),
        }
    ]

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        addition_log_rows=addition_log_rows,
        message_approval_rows=message_approval_rows,
        reset_target=True,
    )
    approval_ref = await repository.get_message_ref(
        "approval-msg-12",
        expected_kind="approval",
    )

    assert report.imported_rows == 1
    assert report.imported_approval_ref_rows == 1
    assert report.skipped_approval_ref_rows == 0
    assert approval_ref is not None
    assert approval_ref.group_id == "20003"
    assert approval_ref.user_id == "10003"
    assert approval_ref.source_message_id == "source-msg-77"
    assert approval_ref.message_type == "group"


@pytest.mark.asyncio
async def test_migrate_legacy_wordbank_wrapper_imports_response_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    image_root = tmp_path / "images"
    image_root.mkdir()
    old_repo_root = tmp_path / "legacy-wordbank"
    old_repo_root.mkdir()
    call_time = datetime(2024, 2, 5, 9, 30, tzinfo=UTC)
    config = migration_module.LegacyPgConfig(
        host="127.0.0.1",
        port=5432,
        user="tester",
        password="secret",
    )
    rows = [
        {
            "response_id": 21,
            "trigger_id": 8,
            "response_text": json.dumps(
                [{"type": "text", "text": "梦里见"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20002}}),
            "weight": 2,
            "priority": 2,
            "created_by": "10002",
            "created_at": 1700000100,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "晚安安"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
            "extra_info": None,
            "approval_status": "APPROVED",
        }
    ]
    response_log_rows = [
        {
            "log_id": 100,
            "message_id": "legacy-msg-2",
            "response_id": 21,
            "user_id": "10010",
            "call_time": call_time,
            "group_id": "20002",
            "message_type": "group",
            "matched_text": "晚安安",
        }
    ]
    addition_log_rows = [
        {
            "log_id": 8,
            "response_id": 21,
            "user_id": "10002",
            "add_time": call_time,
            "add_source": {"group_id": "20002", "user_id": "10002"},
            "created_message_id": "source-msg-21",
            "approval_id": None,
        }
    ]
    message_approval_rows = [
        {
            "id": 4,
            "message_id": "approval-msg-21",
            "approval_id": 301,
            "response_id": 21,
            "approval_user_id": "10002",
            "approval_created_at": call_time,
        }
    ]

    async def fake_fetch_rows(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return rows

    async def fake_fetch_logs(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return response_log_rows

    async def fake_fetch_addition_logs(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return addition_log_rows

    async def fake_fetch_message_approvals(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return message_approval_rows

    def fake_load_legacy_pg_config(
        path: Path,
    ) -> migration_module.LegacyPgConfig:
        assert path == old_repo_root
        return config

    monkeypatch.setattr(
        migration_module,
        "load_legacy_pg_config",
        fake_load_legacy_pg_config,
    )
    monkeypatch.setattr(
        migration_module,
        "fetch_legacy_response_rows",
        fake_fetch_rows,
    )
    monkeypatch.setattr(
        migration_module,
        "fetch_legacy_response_log_rows",
        fake_fetch_logs,
    )
    monkeypatch.setattr(
        migration_module,
        "fetch_legacy_addition_log_rows",
        fake_fetch_addition_logs,
    )
    monkeypatch.setattr(
        migration_module,
        "fetch_legacy_message_approval_rows",
        fake_fetch_message_approvals,
    )

    report = await migrate_legacy_wordbank(
        old_repo_root,
        repository=repository,
        media_service=media_service,
        image_root=image_root,
    )

    async with wordbank_log_db.read_session(time_ctx=call_time) as session:
        logs = (
            (
                await session.execute(
                    select(WordbankLog).order_by(WordbankLog.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    assert report.imported_rows == 1
    assert report.imported_log_rows == 1
    assert report.imported_approval_ref_rows == 1
    assert report.skipped_log_rows == 0
    assert len(logs) == 1
    assert logs[0].group_id == "20002"
    assert logs[0].user_id == "10010"
    assert logs[0].matched_text == "晚安安"
    approval_ref = await repository.get_message_ref(
        "approval-msg-21",
        expected_kind="approval",
    )
    assert approval_ref is not None
    assert approval_ref.source_message_id == "source-msg-21"


@pytest.mark.asyncio
async def test_migrate_legacy_wordbank_defaults_to_recovered_files_and_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    old_repo_root = tmp_path / "sakuraisenrin-old"
    old_repo_root.mkdir()
    pic_root = tmp_path / "SakuraiSenrinPic"
    recovered_root = pic_root / "recovered_files"
    recovered_root.mkdir(parents=True)
    mapping_path = pic_root / "file_mapping.json"
    mapping_path.write_text("{}", encoding="utf-8")
    expected_catalog = build_legacy_image_catalog(recovered_root, mapping_path)
    config = migration_module.LegacyPgConfig(
        host="127.0.0.1",
        port=5432,
        user="tester",
        password="secret",
    )
    captured: dict[str, Path] = {}

    async def fake_fetch_rows(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return []

    def fake_load_legacy_pg_config(
        path: Path,
    ) -> migration_module.LegacyPgConfig:
        assert path == old_repo_root
        return config

    def fake_build_legacy_image_catalog(
        image_root: Path,
        maybe_mapping_path: Path | None,
    ) -> migration_module.LegacyImageCatalog:
        captured["image_root"] = image_root
        assert maybe_mapping_path is not None
        captured["mapping_path"] = maybe_mapping_path
        return expected_catalog

    monkeypatch.setattr(
        migration_module,
        "load_legacy_pg_config",
        fake_load_legacy_pg_config,
    )
    monkeypatch.setattr(
        migration_module,
        "fetch_legacy_response_rows",
        fake_fetch_rows,
    )
    monkeypatch.setattr(
        migration_module,
        "build_legacy_image_catalog",
        fake_build_legacy_image_catalog,
    )

    report = await migrate_legacy_wordbank(
        old_repo_root,
        repository=repository,
        media_service=media_service,
        import_logs=False,
    )

    assert report.imported_rows == 0
    assert captured["image_root"] == recovered_root
    assert captured["mapping_path"] == mapping_path


@pytest.mark.asyncio
async def test_migrate_legacy_wordbank_uses_explicit_pg_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    old_repo_root = tmp_path / "sakuraisenrin-old"
    old_repo_root.mkdir()
    image_root = tmp_path / "images"
    image_root.mkdir()
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text("{}", encoding="utf-8")
    config = migration_module.LegacyPgConfig(
        host="127.0.0.2",
        port=15432,
        user="override",
        password="secret",
    )
    captured: dict[str, migration_module.LegacyPgConfig] = {}

    async def fake_fetch_rows(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        captured["config"] = incoming_config
        return []

    monkeypatch.setattr(
        migration_module,
        "fetch_legacy_response_rows",
        fake_fetch_rows,
    )

    report = await migrate_legacy_wordbank(
        old_repo_root,
        repository=repository,
        media_service=media_service,
        image_root=image_root,
        mapping_path=mapping_path,
        pg_config=config,
        import_logs=False,
    )

    assert report.imported_rows == 0
    assert captured["config"] == config


@pytest.mark.asyncio
async def test_migrate_legacy_rows_merges_same_trigger_or_targets_into_one_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    image_root = tmp_path / "images"
    image_root.mkdir()
    catalog = build_legacy_image_catalog(image_root, None)
    rows = [
        {
            "response_id": 2,
            "trigger_id": 1,
            "response_text": json.dumps(
                [{"type": "text", "text": "你好"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps(
                {
                    "$or": [
                        {"group_id": {"$eq": 20001}},
                        {"role": {"$eq": "member"}},
                    ]
                },
                ensure_ascii=False,
            ),
            "weight": 3,
            "priority": 3,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "测试"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0, "lifecycle": 600}),
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
    groups = await repository.list_enabled_entries()
    response_items = await repository.list_group_response_items(
        report.imported_group_ids[2]
    )

    assert report.imported_rows == 1
    assert report.imported_groups == 1
    assert report.imported_response_items == 2
    assert report.imported_entries == 2
    assert report.skipped_rows == 0
    assert len(groups) == 1
    assert len(response_items) == 2
    assert sorted(item.scope for item in response_items) == [
        "all_groups",
        "current_group",
    ]
    assert report.response_distribution[2] == 1


@pytest.mark.asyncio
async def test_migrate_legacy_rows_reports_failed_trigger_and_response_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    image_root = tmp_path / "images"
    image_root.mkdir()
    catalog = build_legacy_image_catalog(image_root, None)
    rows = [
        {
            "response_id": 3,
            "trigger_id": 2,
            "response_text": json.dumps(
                [{"type": "text", "text": "响应"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "image", "file": "missing-file.jpg"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
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
    detail = report.failure_details[0]
    trigger_summary = detail["trigger"]
    response_summary = detail["response"]

    assert report.imported_rows == 0
    assert report.skipped_rows == 1
    assert detail["response_id"] == 3
    assert detail["trigger_id"] == 2
    assert detail["reason"] == "image file not found: missing-file.jpg"
    assert isinstance(trigger_summary, dict)
    assert isinstance(response_summary, dict)
    assert trigger_summary["kind"] == "message"
    assert _segment_list(trigger_summary)[0]["type"] == "image"
    assert _segment_list(response_summary)[0]["text"] == "响应"


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


def _segment_list(value: dict[str, object]) -> list[dict[str, Any]]:
    segments = value.get("segments")
    assert isinstance(segments, list)
    normalized: list[dict[str, Any]] = []
    for item in segments:
        assert isinstance(item, dict)
        normalized.append(item)
    return normalized
