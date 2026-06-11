from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.types import WordbankSearchRequest
from src.plugins.wordbank.message_model import shape_from_image, shape_from_text
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
async def test_repository_round_trip_and_exact_runtime_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    result = await service.add_message_entry(
        trigger_shape=shape_from_text("晚安啦"),
        response_shape=shape_from_text("做个好梦"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    approved = await service.approve_entry(
        result.entry_id,
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
    missed = await service.match_message(
        shape_from_text("晚安"),
        context=_context(),
    )

    assert approved is True
    assert selected is not None
    assert selected.response.text == "做个好梦"
    assert missed is None


@pytest.mark.asyncio
async def test_pending_and_delete_vote_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    result = await service.add_message_entry(
        trigger_shape=shape_from_text("权限测试"),
        response_shape=shape_from_text("ok"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    pending = await service.list_pending_entries(
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    approved = await service.approve_entry(
        result.entry_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    vote = await service.request_delete_vote(
        entry_id=result.entry_id,
        group_id="20001",
        user_id="10002",
        threshold=2,
    )
    support = await service.support_delete_vote(
        vote_id=vote.vote_id if vote is not None else 0,
        group_id="20001",
        user_id="10003",
    )

    assert [item.entry_id for item in pending] == [result.entry_id]
    assert approved is True
    assert vote is not None
    assert support is not None
    assert support.entry_deleted is True


@pytest.mark.asyncio
async def test_search_uses_creator_filter_and_text_fts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)

    first = await service.add_message_entry(
        trigger_shape=shape_from_text("晚安词条"),
        response_shape=shape_from_text("做个好梦"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    second = await service.add_message_entry(
        trigger_shape=shape_from_text("早安词条"),
        response_shape=shape_from_text("早上好"),
        group_id="20001",
        user_id="10002",
        is_group=True,
    )
    for entry_id in (first.entry_id, second.entry_id):
        await service.approve_entry(
            entry_id,
            actor_user_id="10001",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )

    items = await service.search(
        WordbankSearchRequest(
            keyword="晚安",
            field="trigger",
            creator_id="10001",
        )
    )

    assert [item.entry_id for item in items] == [first.entry_id]


@pytest.mark.asyncio
async def test_search_accepts_image_scores_for_trigger_and_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _build_service(tmp_path, monkeypatch)
    media_service = WordbankMediaService(
        service.repository,
        media_root=tmp_path / "media",
    )

    trigger_image = await media_service.ingest_image_bytes(_png((255, 0, 0)))
    response_image = await media_service.ingest_image_bytes(_png((0, 0, 255)))

    trigger_entry = await service.add_message_entry(
        trigger_shape=shape_from_image(trigger_image.canonical_id),
        response_shape=shape_from_text("图触发"),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    response_entry = await service.add_message_entry(
        trigger_shape=shape_from_text("文本触发"),
        response_shape=shape_from_image(response_image.canonical_id),
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    for entry_id in (trigger_entry.entry_id, response_entry.entry_id):
        await service.approve_entry(
            entry_id,
            actor_user_id="10001",
            actor_group_id="20001",
            can_moderate_group=True,
            is_superuser=False,
        )

    trigger_items = await service.search(
        WordbankSearchRequest(
            field="trigger",
            has_image=True,
            image_scores={trigger_image.canonical_id: 1.0},
        )
    )
    response_items = await service.search(
        WordbankSearchRequest(
            field="response",
            has_image=True,
            image_scores={response_image.canonical_id: 1.0},
        )
    )

    assert [item.entry_id for item in trigger_items] == [trigger_entry.entry_id]
    assert [item.entry_id for item in response_items] == [response_entry.entry_id]
