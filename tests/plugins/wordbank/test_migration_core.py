from tests.plugins.wordbank.test_migration_support import *


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


def test_resolve_legacy_call_window_seconds_defaults_and_clamps() -> None:
    assert (
        migration_module._resolve_legacy_call_window_seconds(None) == 60 * 60 * 24 * 90
    )
    assert migration_module._resolve_legacy_call_window_seconds({}) == 60 * 60 * 24 * 90
    assert (
        migration_module._resolve_legacy_call_window_seconds({"lifecycle": ""})
        == 60 * 60 * 24 * 90
    )
    assert (
        migration_module._resolve_legacy_call_window_seconds({"lifecycle": 3600})
        == 3600
    )
    assert (
        migration_module._resolve_legacy_call_window_seconds(
            {"lifecycle": 60 * 60 * 24 * 365}
        )
        == 60 * 60 * 24 * 90
    )


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


def test_rebuild_legacy_row_from_failure_detail_round_trips_message_payloads() -> None:
    detail = {
        "response_id": 17,
        "trigger_id": 9,
        "approval_status": "APPROVED",
        "priority": 3,
        "weight": 2,
        "created_by": "10001",
        "created_at": 1700000000,
        "trigger": {
            "kind": "message",
            "segments": [{"type": "text", "text": ""}],
        },
        "response": {
            "kind": "message",
            "segments": [{"type": "text", "text": "回放成功"}],
        },
        "response_rule_conditions": {"group_id": {"$eq": 20001}},
        "trigger_config": {"probability": 0.5},
        "extra_info": {},
    }

    row = rebuild_legacy_row_from_failure_detail(detail)

    assert row["response_id"] == 17
    assert row["trigger_id"] == 9
    assert json.loads(str(row["trigger_text"])) == [{"type": "text", "text": ""}]
    assert json.loads(str(row["response_text"])) == [
        {"type": "text", "text": "回放成功"}
    ]
    assert row["response_available"] is True
    assert row["approval_status"] == "APPROVED"


def test_rebuild_legacy_row_from_failure_detail_uses_event_extra_info() -> None:
    detail = {
        "response_id": 18,
        "trigger_id": 10,
        "approval_status": "PENDING",
        "priority": 1,
        "weight": 3,
        "created_by": "10002",
        "created_at": 1700000001,
        "trigger": {
            "kind": "event",
            "extra_info": {"action": "AT_MENTIONED"},
        },
        "response": {
            "kind": "message",
            "segments": [{"type": "text", "text": "事件命中"}],
        },
        "response_rule_conditions": {},
        "trigger_config": {},
        "extra_info": {"ignored": True},
    }

    row = rebuild_legacy_row_from_failure_detail(detail)

    assert json.loads(str(row["trigger_text"])) == []
    assert row["extra_info"] == {"action": "AT_MENTIONED"}
    assert row["response_available"] is False


def test_extract_failure_details_from_categorized_report_filters_categories() -> None:
    payload = {
        "categories": {
            "trigger_shape_empty": {
                "items": [{"response_id": 1}, {"response_id": 2}],
            },
            "image_file_missing": {
                "items": [{"response_id": 3}],
            },
        }
    }

    details = extract_failure_details_from_categorized_report(
        payload,
        categories=["trigger_shape_empty"],
    )

    assert [detail["response_id"] for detail in details] == [1, 2]


def test_infer_report_response_available_follows_approval_status() -> None:
    assert infer_report_response_available("APPROVED") is True
    assert infer_report_response_available("PENDING") is False
    assert infer_report_response_available("REJECTED") is False
