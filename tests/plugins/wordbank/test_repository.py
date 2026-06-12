import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import text

from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.instances import (
    wordbank_main_db,
    wordbank_message_ref_db,
    wordbank_message_route_db,
)
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.types import WordbankSearchRequest
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleContext


def _context(*, user_id: str = "10001", group_id: str = "20001") -> RuleContext:
    return RuleContext(
        group_id=group_id,
        user_id=user_id,
        message_type="group",
        sender_role="member",
    )


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def _build_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> WordbankService:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    service = WordbankService(WordbankRepository(), debounce_seconds=0.01)
    await service.initialize()
    return service


@pytest.mark.asyncio
async def test_same_trigger_appends_response_to_existing_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    first = await service.add_message_entry(
        trigger_shape=shape_from_text("晚安啦"),
        response_shape=shape_from_text("做个好梦"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    second = await service.add_message_entry(
        trigger_shape=shape_from_text("晚安啦"),
        response_shape=shape_from_text("也祝你好梦"),
        group_id="20001",
        user_id="10002",
        is_group=True,
    )
    group_detail = await service.get_group_detail(first.trigger_group_id)

    assert first.trigger_group_id == second.trigger_group_id
    assert first.response_item_id != second.response_item_id
    assert first.created_group is True
    assert second.created_group is False
    assert group_detail is not None
    assert len(group_detail.responses) == 2


@pytest.mark.asyncio
async def test_runtime_only_uses_approved_responses_inside_same_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    approved = await service.add_message_entry(
        trigger_shape=shape_from_text("晚安啦"),
        response_shape=shape_from_text("做个好梦"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    pending = await service.add_message_entry(
        trigger_shape=shape_from_text("晚安啦"),
        response_shape=shape_from_text("还没审核"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    await service.approve_response_item(
        approved.response_item_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    await service.rebuild_index()

    selected = await service.match_message(
        shape_from_text("晚安啦"),
        context=_context(),
    )

    assert pending.trigger_group_id == approved.trigger_group_id
    assert selected is not None
    assert selected.response.id == approved.response_item_id
    assert selected.response.text == "做个好梦"


@pytest.mark.asyncio
async def test_deleting_last_active_response_removes_group_from_runtime_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    first = await service.add_message_entry(
        trigger_shape=shape_from_text("权限测试"),
        response_shape=shape_from_text("ok-1"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    second = await service.add_message_entry(
        trigger_shape=shape_from_text("权限测试"),
        response_shape=shape_from_text("ok-2"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    for response_item_id in (first.response_item_id, second.response_item_id):
        await service.approve_response_item(
            response_item_id,
            actor_user_id="10001",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )

    deleted_first = await service.delete_response_item(
        first.response_item_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    await service.rebuild_index()
    still_matches = await service.match_message(
        shape_from_text("权限测试"),
        context=_context(),
    )

    deleted_second = await service.delete_response_item(
        second.response_item_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    await service.rebuild_index()
    no_match = await service.match_message(
        shape_from_text("权限测试"),
        context=_context(),
    )

    assert deleted_first is True
    assert still_matches is not None
    assert deleted_second is True
    assert no_match is None


@pytest.mark.asyncio
async def test_search_groups_multiple_responses_into_single_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    for response_text in ("做个好梦", "晚安晚安", "早点睡", "第四条响应"):
        created = await service.add_message_entry(
            trigger_shape=shape_from_text("晚安词条"),
            response_shape=shape_from_text(response_text),
            group_id="20001",
            user_id="10001",
            is_group=True,
        )
        await service.approve_response_item(
            created.response_item_id,
            actor_user_id="10001",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )

    page = await service.search_page(
        WordbankSearchRequest(keyword="晚安", field="trigger", creator_id="10001")
    )

    assert page.total_count == 1
    assert len(page.items) == 1
    item = page.items[0]
    assert item.response_count == 4
    assert item.response_summaries == ("做个好梦", "晚安晚安", "早点睡")
    assert item.remaining_response_count == 1


@pytest.mark.asyncio
async def test_creator_filter_matches_group_creator_strictly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    first = await service.add_message_entry(
        trigger_shape=shape_from_text("创建者测试"),
        response_shape=shape_from_text("A"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    second = await service.add_message_entry(
        trigger_shape=shape_from_text("另一个触发"),
        response_shape=shape_from_text("B"),
        group_id="20001",
        user_id="10002",
        is_group=True,
    )
    for response_item_id in (first.response_item_id, second.response_item_id):
        await service.approve_response_item(
            response_item_id,
            actor_user_id="10001",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )

    page = await service.search_page(
        WordbankSearchRequest(
            keyword="测试",
            field="trigger",
            creator_id="10001",
        )
    )

    assert [item.trigger_group_id for item in page.items] == [first.trigger_group_id]


@pytest.mark.asyncio
async def test_search_accepts_response_image_scores_and_marks_match_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    media_service = WordbankMediaService(
        service.repository,
        media_root=tmp_path / "media",
    )

    response_image = await media_service.ingest_image_bytes(_png((0, 0, 255)))
    created = await service.add_message_entry(
        trigger_shape=shape_from_text("文本触发"),
        response_shape=shape_from_image(response_image.canonical_id),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    await service.approve_response_item(
        created.response_item_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )

    items = await service.search(
        WordbankSearchRequest(
            field="response",
            has_image=True,
            image_scores={response_image.canonical_id: 1.0},
        )
    )

    assert len(items) == 1
    assert items[0].trigger_group_id == created.trigger_group_id
    assert items[0].matched_by == "image:response"


@pytest.mark.asyncio
async def test_search_items_expose_trigger_and_response_preview_image_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    media_service = WordbankMediaService(
        service.repository,
        media_root=tmp_path / "media",
    )

    trigger_image = await media_service.ingest_image_bytes(_png((255, 0, 0)))
    response_image = await media_service.ingest_image_bytes(_png((0, 255, 0)))
    created = await service.add_message_entry(
        trigger_shape=combine_shapes(
            shape_from_text("预览触发"),
            shape_from_image(trigger_image.canonical_id),
        ),
        response_shape=combine_shapes(
            shape_from_text("预览响应"),
            shape_from_image(response_image.canonical_id),
        ),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    await service.approve_response_item(
        created.response_item_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )

    page = await service.search_page(
        WordbankSearchRequest(keyword="预览", field="all"),
    )

    assert len(page.items) == 1
    assert page.items[0].trigger_preview_image_id == trigger_image.canonical_id
    assert page.items[0].response_preview_image_id == response_image.canonical_id


@pytest.mark.asyncio
async def test_runtime_selects_response_by_rule_and_call_count_inside_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    general = await service.add_message_entry(
        trigger_shape=shape_from_text("规则测试"),
        response_shape=shape_from_text("普通响应"),
        group_id="20001",
        user_id="10001",
        is_group=True,
        raw_rule={"scope": "self", "roles": "member"},
    )
    gated = await service.add_message_entry(
        trigger_shape=shape_from_text("规则测试"),
        response_shape=shape_from_text("管理员且调用过"),
        group_id="20001",
        user_id="10001",
        is_group=True,
        raw_rule={
            "roles": "admin",
            "call_count": {"window_seconds": 60, "min": 1, "max": 9},
        },
    )
    for response_item_id in (general.response_item_id, gated.response_item_id):
        await service.approve_response_item(
            response_item_id,
            actor_user_id="10001",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )
    await service.rebuild_index()

    member_selected = await service.match_message(
        shape_from_text("规则测试"),
        context=RuleContext(
            group_id="20001",
            user_id="10001",
            message_type="group",
            sender_role="member",
        ),
    )
    service._call_history[gated.response_item_id].append(get_current_time())
    admin_selected = await service.match_message(
        shape_from_text("规则测试"),
        context=RuleContext(
            group_id="20001",
            user_id="10002",
            message_type="group",
            sender_role="admin",
        ),
    )

    assert member_selected is not None
    assert member_selected.response.id == general.response_item_id
    assert admin_selected is not None
    assert admin_selected.response.id == gated.response_item_id


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
    assert wordbank_main_db.patch_registry.patches == []
    assert wordbank_message_route_db.patch_registry.patches == []
    assert wordbank_message_ref_db.patch_registry.patches == []
    assert "wordbank_search_trigger_fts" in main_objects
    assert "wordbank_search_response_fts" in main_objects
    assert "wordbank_response_message" not in main_objects
    assert "wordbank_approval_message" not in main_objects
    assert "wordbank_view_message" not in main_objects
    assert route_objects == {"wordbank_message_route"}
    assert ref_objects == {"wordbank_message_ref"}


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
        probability=0.75,
        weight=4,
        group_id="",
        created_by="10001",
        status="approved",
        enabled=1,
        approved_by="",
        deleted_at=0,
        created_at=1700000000,
        updated_at=1700001234,
    )
    await repository.rebuild_search_index()
    page = await repository.search_page(
        WordbankSearchRequest(keyword="旧版", field="all"),
    )

    assert imported.status == "approved"
    assert imported.probability == 0.75
    assert imported.trigger_group.trigger_variants[0].trigger_text == "旧版触发"
    assert page.items[0].trigger_group_id == imported.trigger_group_id
