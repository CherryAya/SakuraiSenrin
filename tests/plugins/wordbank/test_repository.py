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
