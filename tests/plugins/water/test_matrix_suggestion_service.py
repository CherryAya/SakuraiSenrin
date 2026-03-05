from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
from nonebug import App
import pytest

from src.plugins.water.services.matrix_suggestion import (
    MatrixSuggestionService,
    MergeCandidate,
)


@pytest.mark.asyncio
async def test_maybe_suggest_sends_group_prompt(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MatrixSuggestionService()

    from src.plugins.water.services import matrix_suggestion as suggestion_module

    monkeypatch.setattr(
        suggestion_module.water_repo,
        "get_or_create_group_matrix_id",
        AsyncMock(return_value="abcd1234"),
    )
    monkeypatch.setattr(
        suggestion_module.water_repo,
        "get_ignored_matrix_suggestions",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        suggestion_module.water_repo,
        "has_matrix_merge_decision",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        suggestion_module.water_repo,
        "get_pending_matrix_suggestion",
        AsyncMock(return_value=None),
    )
    pending_mock = AsyncMock()
    monkeypatch.setattr(
        suggestion_module.water_repo,
        "set_pending_matrix_suggestion",
        pending_mock,
    )

    candidate = MergeCandidate(
        matrix_id="dcba4321",
        matched_group_ids=["20002", "20003"],
        score=0.9,
        overlap_users=42,
        base_users=80,
        matrix_users=120,
    )
    monkeypatch.setattr(
        service, "_find_best_candidate", AsyncMock(return_value=candidate)
    )
    monkeypatch.setattr(
        service, "_build_suggestion_message", AsyncMock(return_value="SUGGEST")
    )

    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api(
            "send_group_msg",
            {"group_id": 20001, "message": "SUGGEST"},
            result={"message_id": 1},
        )
        await service._maybe_suggest(bot, "20001", trigger="first_record")

    pending_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_suggest_skips_when_ignored(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MatrixSuggestionService()

    from src.plugins.water.services import matrix_suggestion as suggestion_module

    monkeypatch.setattr(
        suggestion_module.water_repo,
        "get_or_create_group_matrix_id",
        AsyncMock(return_value="abcd1234"),
    )
    monkeypatch.setattr(
        suggestion_module.water_repo,
        "get_ignored_matrix_suggestions",
        AsyncMock(return_value={"20001"}),
    )

    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        await service._maybe_suggest(bot, "20001", trigger="first_record")


@pytest.mark.asyncio
async def test_first_record_cache_avoids_duplicate_db_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MatrixSuggestionService()

    from src.plugins.water.services import matrix_suggestion as suggestion_module

    mark_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        suggestion_module.water_repo, "mark_group_first_record_seen", mark_mock
    )
    monkeypatch.setattr(service, "_maybe_suggest", AsyncMock())

    dummy_bot = AsyncMock(spec=Bot)
    await service.maybe_suggest_on_first_record(dummy_bot, "20001")
    await service.maybe_suggest_on_first_record(dummy_bot, "20001")

    mark_mock.assert_awaited_once_with("20001")
