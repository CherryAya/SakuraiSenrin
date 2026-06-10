from pathlib import Path

import pytest

from src.plugins.wordbank.database import instances
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.rules import RuleContext


@pytest.mark.asyncio
async def test_repository_round_trip_and_index_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    repo = WordbankRepository()
    service = WordbankService(repo, debounce_seconds=0.01)
    await service.initialize()

    result = await service.add_text_entry(
        trigger_text="晚安啦",
        response_text="做个好梦",
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    assert result.status == "pending"
    await service.rebuild_index()

    selected = await service.match_text(
        "大家晚安啦",
        context=RuleContext(
            group_id="20001",
            user_id="10001",
            message_type="group",
        ),
    )

    assert selected is None
    assert await service.approve_entry(
        result.entry_id,
        actor_user_id="20002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    await service.rebuild_index()

    selected = await service.match_text(
        "大家晚安啦",
        context=RuleContext(
            group_id="20001",
            user_id="10001",
            message_type="group",
        ),
    )

    assert selected is not None
    assert selected.response.text == "做个好梦"

    await service.record_response_message(
        message_id="90001",
        entry_id=selected.candidate.entry.id,
        trigger_id=selected.candidate.trigger.id,
        response_id=selected.response.id,
        group_id="20001",
        user_id="10001",
        message_type="text",
    )
    response_message = await service.get_response_message("90001")
    assert response_message is not None
    assert response_message.entry_id == result.entry_id
    assert response_message.trigger_id == selected.candidate.trigger.id
    assert response_message.response_id == selected.response.id

    detail = await service.get_entry_detail(
        response_message.entry_id,
        trigger_id=response_message.trigger_id,
        response_id=response_message.response_id,
    )
    assert detail is not None
    assert detail.trigger_text == "晚安啦"
    assert detail.response_text == "做个好梦"

    assert await service.delete_entry(
        result.entry_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=False,
        is_superuser=False,
    )
    await service.rebuild_index()
    assert service.index.find_text("大家晚安啦") == []

    assert await service.restore_entry(
        result.entry_id,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=False,
        is_superuser=False,
    )
    await service.rebuild_index()
    assert service.index.find_text("大家晚安啦")

    assert (
        tmp_path / instances.wordbank_main_db.namespace / "wordbank_main.db"
    ).is_file()


@pytest.mark.asyncio
async def test_mutation_permissions_for_owner_group_admin_and_superuser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    service = WordbankService(WordbankRepository(), debounce_seconds=0.01)
    await service.initialize()
    result = await service.add_text_entry(
        trigger_text="权限测试",
        response_text="ok",
        group_id="20001",
        user_id="10001",
        is_group=True,
    )

    assert not await service.delete_entry(
        result.entry_id,
        actor_user_id="20002",
        actor_group_id="20001",
        can_moderate_group=False,
        is_superuser=False,
    )
    assert await service.delete_entry(
        result.entry_id,
        actor_user_id="20002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    assert not await service.restore_entry(
        result.entry_id,
        actor_user_id="20002",
        actor_group_id="20002",
        can_moderate_group=True,
        is_superuser=False,
    )
    assert await service.restore_entry(
        result.entry_id,
        actor_user_id="1",
        actor_group_id="",
        can_moderate_group=False,
        is_superuser=True,
    )


@pytest.mark.asyncio
async def test_pending_review_permissions_and_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    service = WordbankService(WordbankRepository(), debounce_seconds=0.01)
    await service.initialize()
    first = await service.add_text_entry(
        trigger_text="待审一",
        response_text="ok",
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    await service.add_text_entry(
        trigger_text="待审二",
        response_text="ok",
        group_id="20002",
        user_id="10002",
        is_group=True,
    )

    assert not await service.approve_entry(
        first.entry_id,
        actor_user_id="20002",
        actor_group_id="20002",
        can_moderate_group=True,
        is_superuser=False,
    )
    pending = await service.list_pending_entries(
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    assert [item.entry_id for item in pending] == [first.entry_id]

    assert await service.reject_entry(
        first.entry_id,
        actor_user_id="20002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    assert not await service.list_pending_entries(
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    detail = await service.get_entry_detail(first.entry_id)
    assert detail is not None
    assert detail.status == "rejected"


@pytest.mark.asyncio
async def test_delete_vote_reaches_threshold_and_refreshes_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)

    service = WordbankService(WordbankRepository(), debounce_seconds=0.01)
    await service.initialize()
    result = await service.add_text_entry(
        trigger_text="投票删除测试",
        response_text="ok",
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    assert await service.approve_entry(
        result.entry_id,
        actor_user_id="20002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    await service.rebuild_index()
    assert service.index.find_text("投票删除测试")

    created = await service.request_delete_vote(
        entry_id=result.entry_id,
        group_id="20001",
        user_id="20002",
        threshold=2,
    )
    assert created is not None
    assert created.created
    assert created.support_count == 1
    assert not created.entry_deleted

    duplicate = await service.support_delete_vote(
        vote_id=created.vote_id,
        group_id="20001",
        user_id="20002",
    )
    assert duplicate is not None
    assert duplicate.already_supported
    assert duplicate.support_count == 1

    passed = await service.support_delete_vote(
        vote_id=created.vote_id,
        group_id="20001",
        user_id="20003",
    )
    assert passed is not None
    assert passed.entry_deleted
    assert passed.status == "passed"
    assert passed.support_count == 2

    status = await service.get_delete_vote(created.vote_id, group_id="20001")
    assert status is not None
    assert status.status == "passed"
    assert status.support_count == 2

    await service.rebuild_index()
    assert service.index.find_text("投票删除测试") == []
