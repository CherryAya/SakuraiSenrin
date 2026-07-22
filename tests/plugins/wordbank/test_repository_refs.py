from tests.plugins.wordbank.test_repository_support import *


async def test_record_message_ref_roundtrip_writes_route_and_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    await service.record_message_ref(
        ref_kind="view",
        message_id="90001",
        context_type="search_result",
        trigger_group_id=0,
        current_page=2,
        keyword="jrlp",
        field="all",
        creator_id="10001",
        has_image=True,
        group_ids=(271, 300),
        group_id="20001",
        user_id="10001",
        message_type="group",
    )
    route = await service.repository.get_message_ref_route("90001")
    record = await service.get_message_ref("90001", expected_kind="view")

    assert route is not None
    assert route.ref_kind == "view"
    assert record is not None
    assert record.shard_key == route.shard_key
    assert record.context_type == "search_result"
    assert record.current_page == 2
    assert record.group_ids == (271, 300)
    assert record.has_image is True


async def test_record_message_ref_upsert_reuses_message_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    await service.record_message_ref(
        ref_kind="response",
        message_id="90002",
        trigger_group_id=12,
        trigger_variant_id=120,
        response_item_id=300,
        group_id="20001",
        user_id="10001",
        message_type="group",
    )
    await service.record_message_ref(
        ref_kind="response",
        message_id="90002",
        trigger_group_id=12,
        trigger_variant_id=121,
        response_item_id=301,
        group_id="20002",
        user_id="10002",
        message_type="private",
    )

    route = await service.repository.get_message_ref_route("90002")
    record = await service.get_message_ref("90002", expected_kind="response")

    assert route is not None
    assert route.ref_kind == "response"
    assert record is not None
    assert record.shard_key == route.shard_key
    assert record.trigger_variant_id == 121
    assert record.response_item_id == 301
    assert record.group_id == "20002"
    assert record.user_id == "10002"
    assert record.message_type == "private"


async def test_record_message_ref_roundtrip_supports_approval_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    await service.record_message_ref(
        ref_kind="approval",
        message_id="90003",
        trigger_group_id=88,
        response_item_id=501,
        group_id="20001",
        user_id="10001",
        source_message_id="777",
        message_type="approval",
    )

    route = await service.repository.get_message_ref_route("90003")
    record = await service.get_message_ref("90003", expected_kind="approval")

    assert route is not None
    assert route.ref_kind == "approval"
    assert record is not None
    assert record.shard_key == route.shard_key
    assert record.source_message_id == "777"
    assert record.response_item_id == 501
    assert record.message_type == "approval"


async def test_init_all_tables_creates_fts_and_clears_wordbank_patch_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    async with wordbank_main_db.read_session() as session:
        main_objects = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE name IN (
                            'wordbank_search_trigger_fts',
                            'wordbank_search_response_fts',
                            'wordbank_response_message',
                            'wordbank_approval_message',
                            'wordbank_view_message'
                        )
                        """
                    )
                )
            ).all()
        }
    async with wordbank_message_route_db.read_session() as session:
        route_objects = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE name = 'wordbank_message_route'
                        """
                    )
                )
            ).all()
        }
    async with wordbank_message_ref_db.read_session() as session:
        ref_objects = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE name = 'wordbank_message_ref'
                        """
                    )
                )
            ).all()
        }

    assert service.repository is not None
    assert {patch.patch_id for patch in wordbank_main_db.patch_registry.patches} == {
        "wordbank_response_item:add_response_mode:v1",
        "wordbank_response_item:add_forward_source_message_id:v1",
        "wordbank_response_item:add_forward_node_count:v1",
        "wordbank_response_item:add_review_history_json:v1",
    }
    assert wordbank_log_db.patch_registry.patches == []
    assert wordbank_message_route_db.patch_registry.patches == []
    assert wordbank_message_ref_db.patch_registry.patches == []
    assert "wordbank_search_trigger_fts" in main_objects
    assert "wordbank_search_response_fts" in main_objects
    assert "wordbank_response_message" not in main_objects
    assert "wordbank_approval_message" not in main_objects
    assert "wordbank_view_message" not in main_objects
    assert route_objects == {"wordbank_message_route"}
    assert ref_objects == {"wordbank_message_ref"}


async def test_runtime_incremental_refresh_only_touches_dirty_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    first = await service.add_message_entry(
        trigger_shape=shape_from_text("增量一"),
        response_shape=shape_from_text("A"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    second = await service.add_message_entry(
        trigger_shape=shape_from_text("增量二"),
        response_shape=shape_from_text("B"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    called: list[int] = []
    service._dirty_group_ids.clear()

    async def _spy_refresh(trigger_group_id: int) -> None:
        called.append(trigger_group_id)

    monkeypatch.setattr(service, "_refresh_runtime_group", _spy_refresh)

    await service.approve_response_item(
        first.response_item_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    await asyncio.sleep(0.05)

    assert called == [first.trigger_group_id]
    assert second.trigger_group_id not in called


def test_wordbank_event_stores_use_hydrate_cold_policy() -> None:
    assert wordbank_log_db.cold_policy == ColdPolicy.HYDRATE
    assert wordbank_message_ref_db.cold_policy == ColdPolicy.HYDRATE


@pytest.mark.asyncio
async def test_archive_event_shards_runs_for_log_and_message_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    calls: list[str] = []

    async def _archive_logs() -> None:
        calls.append("log")

    async def _archive_refs() -> None:
        calls.append("ref")

    monkeypatch.setattr(wordbank_log_db, "run_archiver_task", _archive_logs)
    monkeypatch.setattr(wordbank_message_ref_db, "run_archiver_task", _archive_refs)

    await service.repository.archive_event_shards()

    assert calls == ["log", "ref"]
