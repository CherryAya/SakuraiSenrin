from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.migration import (
    build_legacy_image_catalog,
    legacy_message_to_shape,
    migrate_legacy_rows,
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
    assert report.skipped_rows == 0
    assert len(entries) == 1
    assert entries[0].status == "approved"
    assert entries[0].group_id == "20001"
    assert entries[0].triggers[0].trigger_mode == "fullmatch"
