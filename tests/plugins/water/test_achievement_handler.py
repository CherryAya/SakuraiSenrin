from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.plugins.water.handlers.achievement import handle_my_achievements
from tests.plugins.water.helpers import (
    DummyMatcher,
    MatcherFinished,
    build_group_message_event,
)


@pytest.mark.asyncio
async def test_handle_my_achievements_returns_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import achievement as achievement_handler_module

    matcher = DummyMatcher()
    event = build_group_message_event("我的水王成就")

    monkeypatch.setattr(
        achievement_handler_module.water_repo,
        "get_or_create_group_matrix_id",
        AsyncMock(return_value="abcd1234"),
    )
    monkeypatch.setattr(
        achievement_handler_module.achievement_service,
        "build_user_achievement_message",
        AsyncMock(return_value="ACHIEVEMENT_MESSAGE"),
    )

    with pytest.raises(MatcherFinished):
        await handle_my_achievements(cast(Any, matcher), event)

    assert matcher.finished == "ACHIEVEMENT_MESSAGE"
