import sys
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebot.matcher import Matcher
import pytest

from src.lib.i18n.runtime import tr
from tests.plugins.water.helpers import build_group_message_event

nonebot.init(
    SUPERUSERS={"1"},
    IGNORED_USERS=set(),
    MAIN_GROUP_ID="10001",
    GITHUB_TOKEN="test-token",
    GITHUB_REPO="owner/repo",
    GITHUB_BRANCH="main",
    WORDBANK_MEDIA_PROVIDER="local",
    command_start={"#", "/"},
    command_sep={"."},
)

if nonebot.get_plugin("wordbank") is None:
    sys.modules.pop("src.plugins.wordbank", None)
    nonebot.load_plugin("src.plugins.wordbank")

from src.plugins.wordbank.forward_batch import ForwardBatchPayload
from src.plugins.wordbank.guided_flow import record_guided_forward_response_choice
from src.plugins.wordbank.message_model import shape_from_text
from src.plugins.wordbank.services import wordbank_media_service


class _PauseMatcher:
    def __init__(self) -> None:
        self.paused: list[object] = []

    async def pause(self, prompt: object) -> None:
        self.paused.append(prompt)


@pytest.mark.parametrize(
    ("choice_text", "expect_split"),
    [("1", False), ("2", True)],
)
@pytest.mark.asyncio
async def test_guided_forward_choice_uses_saved_source_message_id(
    monkeypatch: pytest.MonkeyPatch,
    choice_text: str,
    expect_split: bool,
) -> None:
    matcher = _PauseMatcher()
    bot = cast(Bot, SimpleNamespace())
    state: dict[str, object] = {
        "wordbank_guided_response_forward_pending": True,
        "wordbank_guided_response_forward_source_message_id": 54321,
    }
    event = build_group_message_event(choice_text, message_id=9)
    payload = ForwardBatchPayload(
        source_message_id=54321,
        node_count=2,
        whole_shape=shape_from_text("整体响应"),
        split_shapes=(shape_from_text("第一条"), shape_from_text("第二条")),
    )
    build_payload = AsyncMock(return_value=payload)

    monkeypatch.setattr(
        "src.plugins.wordbank.guided_flow.build_forward_batch_payload_by_source_message_id",
        build_payload,
    )

    await record_guided_forward_response_choice(
        cast(Matcher, matcher),
        event,
        state,
        "zh-CN",
        media_service=wordbank_media_service,
        bot=bot,
    )

    build_payload.assert_awaited_once_with(
        bot,
        media_service=wordbank_media_service,
        source_message_id=54321,
    )
    assert "wordbank_guided_response_forward_pending" not in state
    assert matcher.paused == [tr("zh-CN", "wordbank.guided.add.scope_prompt")]
    if expect_split:
        assert state["wordbank_guided_response_split_shapes"] == payload.split_shapes
        assert "wordbank_guided_response_shape" not in state
    else:
        assert "wordbank_guided_response_split_shapes" not in state
        assert state["wordbank_guided_response_shape"] == payload.whole_shape
