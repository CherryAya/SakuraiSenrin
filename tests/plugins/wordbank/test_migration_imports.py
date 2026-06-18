from tests.plugins.wordbank.test_migration_support import *


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
async def test_migrate_legacy_rows_normalizes_empty_trigger_to_single_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()

    catalog = build_legacy_image_catalog(image_root, None)
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    rows = [
        {
            "response_id": 2,
            "trigger_id": 2,
            "response_text": json.dumps(
                [{"type": "text", "text": "空格也能触发"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": ""}],
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
    assert entries[0].trigger_variants[0].trigger_text == " "


@pytest.mark.asyncio
async def test_migrate_legacy_rows_preserves_response_newlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path / "db")
    image_root = tmp_path / "images"
    image_root.mkdir()

    catalog = build_legacy_image_catalog(image_root, None)
    repository = WordbankRepository()
    media_service = WordbankMediaService(repository, media_root=tmp_path / "media")
    rows = [
        {
            "response_id": 3,
            "trigger_id": 3,
            "response_text": json.dumps(
                [
                    {
                        "type": "text",
                        "text": "第一行\n第二行\n\n第三行",
                    }
                ],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "多行说明"}],
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
    assert entries[0].responses[0].text == "第一行\n第二行\n\n第三行"
    assert (
        entries[0].responses[0].message_shape.atoms[0].text
        == "第一行\n第二行\n\n第三行"
    )


@pytest.mark.asyncio
async def test_rebuilt_failure_rows_append_into_existing_wordbank(
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

    existing_rows = [
        {
            "response_id": 1,
            "trigger_id": 1,
            "response_text": json.dumps(
                [{"type": "text", "text": "已有数据"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "已有触发"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
            "extra_info": None,
            "approval_status": "APPROVED",
        }
    ]
    await migrate_legacy_rows(
        existing_rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        reset_target=True,
    )

    failure_details = [
        {
            "response_id": 2,
            "trigger_id": 2,
            "approval_status": "APPROVED",
            "priority": 2,
            "weight": 3,
            "created_by": "10002",
            "created_at": 1700000001,
            "trigger": {
                "kind": "message",
                "segments": [{"type": "text", "text": ""}],
            },
            "response": {
                "kind": "message",
                "segments": [{"type": "text", "text": "追加导入"}],
            },
            "response_rule_conditions": {"group_id": {"$eq": 20001}},
            "trigger_config": {"probability": 1.0},
            "extra_info": {},
        }
    ]

    report = await migrate_legacy_rows(
        rebuild_legacy_rows_from_failure_details(failure_details),
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        reset_target=False,
    )
    entries = await repository.list_enabled_entries()
    trigger_texts = sorted(
        variant.trigger_text for entry in entries for variant in entry.trigger_variants
    )

    assert report.imported_rows == 1
    assert report.skipped_rows == 0
    assert trigger_texts == [" ", "已有触发"]
