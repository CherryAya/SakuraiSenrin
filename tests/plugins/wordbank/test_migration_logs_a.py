from tests.plugins.wordbank.test_migration_support import *


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


@pytest.mark.asyncio
async def test_migrate_legacy_rows_backfills_trigger_logs_into_wordbank_log(
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
    trigger_log_rows = [
        {
            "log_id": 199,
            "message_id": "legacy-trigger-msg-1",
            "trigger_id": 7,
            "user_id": "10087",
            "call_time": call_time,
        }
    ]

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        trigger_log_rows=trigger_log_rows,
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
    assert report.imported_trigger_log_rows == 1
    assert report.skipped_trigger_log_rows == 0
    assert len(logs) == 1
    assert logs[0].group_id == ""
    assert logs[0].user_id == "10087"
    assert logs[0].message_type == "unknown"


@pytest.mark.asyncio
async def test_migrate_legacy_rows_deduplicates_trigger_log_against_response_log(
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
            "message_id": "shared-msg-1",
            "response_id": 11,
            "user_id": "10086",
            "call_time": call_time,
            "group_id": "20001",
            "message_type": "group",
        }
    ]
    trigger_log_rows = [
        {
            "log_id": 199,
            "message_id": "shared-msg-1",
            "trigger_id": 7,
            "user_id": "10087",
            "call_time": call_time,
        }
    ]

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        response_log_rows=response_log_rows,
        trigger_log_rows=trigger_log_rows,
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

    assert report.imported_log_rows == 1
    assert report.imported_trigger_log_rows == 0
    assert report.skipped_trigger_log_rows == 0
    assert len(logs) == 1
    assert logs[0].group_id == "20001"
    assert logs[0].message_type == "group"


@pytest.mark.asyncio
async def test_migrate_legacy_rows_reports_missing_trigger_log_mapping(
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
    rows: list[dict[str, object]] = []
    trigger_log_rows = [
        {
            "log_id": 299,
            "message_id": "orphan-trigger-msg-1",
            "trigger_id": 777,
            "user_id": "10088",
            "call_time": datetime(2024, 1, 3, 8, 0, tzinfo=UTC),
        }
    ]

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        trigger_log_rows=trigger_log_rows,
        reset_target=True,
    )

    assert report.total_trigger_log_rows == 1
    assert report.imported_trigger_log_rows == 0
    assert report.skipped_trigger_log_rows == 1
    assert report.trigger_log_failures == [
        "299: missing imported trigger mapping for trigger_id=777"
    ]


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
