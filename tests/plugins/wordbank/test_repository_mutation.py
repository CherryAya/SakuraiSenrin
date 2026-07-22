from tests.plugins.wordbank.test_repository_support import *


async def test_repository_import_message_entry_preserves_group_and_response_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    imported = await repository.import_message_entry(
        trigger_shape=shape_from_text("旧版触发"),
        response_shape=shape_from_text("旧版响应"),
        rule={},
        scope="all_groups",
        priority=10,
        trigger_probability=0.75,
        weight=4,
        group_id="",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="",
        deleted_at=0,
        created_at=1700000000,
        updated_at=1700001234,
        response_mode="forward_whole",
        forward_source_message_id="456",
        forward_node_count=2,
    )
    await repository.rebuild_search_index()
    page = await repository.search_page(
        WordbankSearchRequest(keyword="旧版", field="all"),
    )
    detail = await repository.get_group_detail(
        imported.trigger_group_id,
        response_item_id=imported.response_item_id,
    )

    assert imported.status == "approved"
    assert imported.probability == 0.75
    assert imported.trigger_group.trigger_variants[0].trigger_text == "旧版触发"
    assert page.items[0].trigger_group_id == imported.trigger_group_id
    assert detail is not None
    assert detail.selected_response is not None
    assert detail.selected_response.response_mode == "forward_whole"
    assert detail.selected_response.forward_source_message_id == "456"
    assert detail.selected_response.forward_node_count == 2


@pytest.mark.asyncio
async def test_repository_updates_trigger_probability_and_response_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    imported = await repository.import_message_entry(
        trigger_shape=shape_from_text("旧版触发"),
        response_shape=shape_from_text("旧版响应"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="10002",
        deleted_at=0,
        created_at=1700000000,
        updated_at=1700001234,
        response_mode="forward_whole",
        forward_source_message_id="456",
        forward_node_count=2,
    )

    updated_probability = await repository.update_trigger_probability(
        imported.trigger_group_id,
        probability=0.25,
        actor_user_id="1",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=True,
    )
    updated_content = await repository.update_response_content(
        imported.response_item_id,
        response_shape=shape_from_text("已修改响应"),
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    detail = await repository.get_group_detail(
        imported.trigger_group_id,
        response_item_id=imported.response_item_id,
    )

    assert updated_probability is True
    assert updated_content is True
    assert detail is not None
    assert detail.probability == 0.25
    assert detail.selected_response is not None
    assert detail.selected_response.response_text == "已修改响应"
    assert detail.selected_response.status == "pending"
    assert detail.selected_response.response_mode == "normal"
    assert detail.selected_response.forward_source_message_id is None
    assert detail.selected_response.forward_node_count == 0


@pytest.mark.asyncio
async def test_repository_trigger_updates_require_superuser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    imported = await repository.import_message_entry(
        trigger_shape=shape_from_text("旧版触发"),
        response_shape=shape_from_text("旧版响应"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="10002",
        deleted_at=0,
        created_at=1700000000,
        updated_at=1700001234,
    )

    updated_probability = await repository.update_trigger_probability(
        imported.trigger_group_id,
        probability=0.25,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    updated_content = await repository.update_trigger_content(
        imported.trigger_group_id,
        trigger_shape=shape_from_text("新版触发"),
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    detail = await repository.get_group_detail(imported.trigger_group_id)

    assert updated_probability is False
    assert updated_content is False
    assert detail is not None
    assert detail.probability == 1.0
    assert detail.trigger_text == "旧版触发"


@pytest.mark.asyncio
async def test_repository_response_edits_require_creator_or_superuser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    imported = await repository.import_message_entry(
        trigger_shape=shape_from_text("旧版触发"),
        response_shape=shape_from_text("旧版响应"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="10002",
        deleted_at=0,
        created_at=1700000000,
        updated_at=1700001234,
    )

    updated_weight = await repository.update_response_weight(
        imported.response_item_id,
        weight=5,
        actor_user_id="10002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    updated_content = await repository.update_response_content(
        imported.response_item_id,
        response_shape=shape_from_text("管理员改词"),
        actor_user_id="10002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    detail = await repository.get_group_detail(
        imported.trigger_group_id,
        response_item_id=imported.response_item_id,
    )

    assert updated_weight is False
    assert updated_content is False
    assert detail is not None
    assert detail.selected_response is not None
    assert detail.selected_response.weight == 3
    assert detail.selected_response.response_text == "旧版响应"
    assert detail.selected_response.status == "approved"


@pytest.mark.asyncio
async def test_repository_update_trigger_content_resets_active_responses_and_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    first = await repository.import_message_entry(
        trigger_shape=shape_from_text("旧版触发"),
        response_shape=shape_from_text("旧版响应一"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="10002",
        deleted_at=0,
        created_at=1700000000,
        updated_at=1700001234,
    )
    second = await repository.import_message_entry(
        trigger_shape=shape_from_text("旧版触发"),
        response_shape=shape_from_text("旧版响应二"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=4,
        group_id="20001",
        created_by="10002",
        status="approved",
        enabled=1,
        approved_by="10003",
        deleted_at=0,
        created_at=1700000001,
        updated_at=1700001235,
    )

    updated = await repository.update_trigger_content(
        first.trigger_group_id,
        trigger_shape=shape_from_text("新版触发"),
        actor_user_id="1",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=True,
    )
    detail = await repository.get_group_detail(first.trigger_group_id)
    search_page = await repository.search_page(
        WordbankSearchRequest(keyword="新版", field="trigger"),
    )

    assert first.trigger_group_id == second.trigger_group_id
    assert updated is True
    assert detail is not None
    assert detail.trigger_text == "新版触发"
    assert tuple(response.status for response in detail.responses) == (
        "pending",
        "pending",
    )
    assert search_page.total_count == 1
    assert search_page.items[0].trigger_group_id == first.trigger_group_id


@pytest.mark.asyncio
async def test_repository_creator_leaderboard_month_aggregates_current_month_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    now = arrow.get(get_current_time()).to("Asia/Shanghai")
    month_start = now.floor("month").int_timestamp
    previous_month_start = now.shift(months=-1).floor("month").int_timestamp

    await repository.import_message_entry(
        trigger_shape=shape_from_text("触发一"),
        response_shape=shape_from_text("响应一"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="1",
        deleted_at=0,
        created_at=month_start + 10,
        updated_at=month_start + 10,
    )
    await repository.import_message_entry(
        trigger_shape=shape_from_text("触发二"),
        response_shape=shape_from_text("响应二"),
        rule={},
        scope="all_groups",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20002",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="1",
        deleted_at=0,
        created_at=month_start + 20,
        updated_at=month_start + 20,
    )
    await repository.import_message_entry(
        trigger_shape=shape_from_text("触发三"),
        response_shape=shape_from_text("响应三"),
        rule={},
        scope="private_only",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="",
        created_by="10002",
        status="approved",
        enabled=1,
        approved_by="1",
        deleted_at=0,
        created_at=month_start + 30,
        updated_at=month_start + 30,
    )
    await repository.import_message_entry(
        trigger_shape=shape_from_text("旧月触发"),
        response_shape=shape_from_text("旧月响应"),
        rule={},
        scope="self",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20003",
        created_by="10003",
        status="approved",
        enabled=1,
        approved_by="1",
        deleted_at=0,
        created_at=previous_month_start + 10,
        updated_at=previous_month_start + 10,
    )
    await repository.import_message_entry(
        trigger_shape=shape_from_text("待审触发"),
        response_shape=shape_from_text("待审响应"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20004",
        created_by="10004",
        status="pending",
        enabled=1,
        approved_by="",
        deleted_at=0,
        created_at=month_start + 40,
        updated_at=month_start + 40,
    )

    snapshot = await repository.get_creator_leaderboard(
        period="month",
        limit=10,
        now_ts=month_start + 3600,
    )

    assert snapshot.period == "month"
    assert snapshot.range_start == month_start
    assert snapshot.total_creator_count == 2
    assert snapshot.total_approved_count == 3
    assert [item.created_by for item in snapshot.items] == ["10001", "10002"]
    assert snapshot.items[0].approved_count == 2
    assert snapshot.items[0].group_count == 2
    assert snapshot.items[0].current_group_count == 1
    assert snapshot.items[0].all_groups_count == 1
    assert snapshot.items[1].private_only_count == 1


@pytest.mark.asyncio
async def test_repository_creator_leaderboard_total_includes_previous_periods(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    repository = WordbankRepository()
    await repository.init_all_tables()

    now = arrow.get(get_current_time()).to("Asia/Shanghai")
    month_start = now.floor("month").int_timestamp
    previous_month_start = now.shift(months=-1).floor("month").int_timestamp

    await repository.import_message_entry(
        trigger_shape=shape_from_text("本月触发"),
        response_shape=shape_from_text("本月响应"),
        rule={},
        scope="current_group",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20001",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="1",
        deleted_at=0,
        created_at=month_start + 10,
        updated_at=month_start + 10,
    )
    await repository.import_message_entry(
        trigger_shape=shape_from_text("上月触发"),
        response_shape=shape_from_text("上月响应"),
        rule={},
        scope="all_groups",
        priority=30,
        trigger_probability=1.0,
        weight=3,
        group_id="20002",
        created_by="10002",
        status="approved",
        enabled=1,
        approved_by="1",
        deleted_at=0,
        created_at=previous_month_start + 10,
        updated_at=previous_month_start + 10,
    )

    snapshot = await repository.get_creator_leaderboard(
        period="total",
        limit=10,
        now_ts=month_start + 3600,
    )

    assert snapshot.period == "total"
    assert snapshot.range_start == previous_month_start + 10
    assert snapshot.total_creator_count == 2
    assert snapshot.total_approved_count == 2
    assert [item.created_by for item in snapshot.items] == ["10001", "10002"]
