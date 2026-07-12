from tests.plugins.wordbank.test_migration_support import *


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
        }
    ]
    trigger_log_rows = [
        {
            "log_id": 101,
            "message_id": "legacy-trigger-msg-2",
            "trigger_id": 8,
            "user_id": "10011",
            "call_time": call_time,
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

    async def fake_fetch_trigger_logs(
        incoming_config: migration_module.LegacyPgConfig,
    ) -> list[dict[str, object]]:
        assert incoming_config == config
        return trigger_log_rows

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
        "fetch_legacy_trigger_log_rows",
        fake_fetch_trigger_logs,
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
    assert report.imported_trigger_log_rows == 1
    assert report.imported_approval_ref_rows == 1
    assert report.skipped_log_rows == 0
    assert len(logs) == 2
    assert logs[0].group_id == "20002"
    assert logs[0].user_id == "10010"
    assert logs[1].group_id == ""
    assert logs[1].user_id == "10011"
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
async def test_migrate_legacy_rows_emits_progress_events(
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
            "response_id": 31,
            "trigger_id": 19,
            "response_text": json.dumps(
                [{"type": "text", "text": "好耶"}],
                ensure_ascii=False,
            ),
            "response_rule_conditions": json.dumps({"group_id": {"$eq": 20001}}),
            "weight": 3,
            "priority": 2,
            "created_by": "10001",
            "created_at": 1700000000,
            "response_available": True,
            "trigger_text": json.dumps(
                [{"type": "text", "text": "测试进度"}],
                ensure_ascii=False,
            ),
            "trigger_config": json.dumps({"probability": 1.0}),
            "extra_info": None,
            "approval_status": "APPROVED",
        }
    ]
    events: list[tuple[str, int, int, dict[str, object]]] = []

    def progress(
        phase: str,
        current: int,
        total: int,
        detail: Mapping[str, object],
    ) -> None:
        events.append((phase, current, total, dict(detail)))

    report = await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=catalog,
        reset_target=True,
        progress=progress,
        progress_every=1,
    )

    assert report.imported_rows == 1
    assert ("entries", 0, 1, {"reset_target": True}) in events
    assert any(phase == "entries" and current == 1 for phase, current, _, _ in events)
    assert any(
        phase == "search_index" and current == 1 for phase, current, _, _ in events
    )


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
