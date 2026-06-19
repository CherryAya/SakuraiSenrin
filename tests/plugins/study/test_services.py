import sys
from typing import cast
from unittest.mock import AsyncMock

import nonebot
from nonebot.matcher import Matcher
import pytest

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

if nonebot.get_plugin("study") is None:
    sys.modules.pop("src.plugins.study", None)
    nonebot.load_plugin("src.plugins.study")

from src.plugins import study as study_plugin
from src.plugins.wordbank.services import wordbank_service

INVALID_GROUP_BLOCK_PROMPT = (
    "群组隔离开关输入错误，请输入 t 或 f。"
    "t 表示开启，仅当前群聊有效；f 表示关闭，按触发方式跨群或私聊生效。"
)


class _PauseMatcher:
    def __init__(self) -> None:
        self.paused: list[object] = []

    async def pause(self, prompt: object) -> None:
        self.paused.append(prompt)


@pytest.mark.asyncio
async def test_partial_args_short_circuit_to_missing_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _PauseMatcher()
    state: dict[str, object] = {}
    event = build_group_message_event("#study M F 不嘻嘻")

    monkeypatch.setattr(wordbank_service, "initialize", AsyncMock(return_value=None))

    handled = await study_plugin._start_guided_study_from_partial_args(
        cast(Matcher, matcher),
        event,
        state,
        "zh-CN",
        "M F 不嘻嘻",
        has_images=False,
    )

    assert handled is True
    assert state["study_trig_mode"] == "m"
    assert state["study_group_block"] == "f"
    assert state["study_trigger_preloaded"] is True
    assert matcher.paused == ["请输入响应词，或发送图片作为图片回复："]


@pytest.mark.asyncio
async def test_partial_args_short_circuit_to_invalid_group_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _PauseMatcher()
    state: dict[str, object] = {}
    event = build_group_message_event("#study M X 不嘻嘻")

    monkeypatch.setattr(wordbank_service, "initialize", AsyncMock(return_value=None))

    handled = await study_plugin._start_guided_study_from_partial_args(
        cast(Matcher, matcher),
        event,
        state,
        "zh-CN",
        "M X 不嘻嘻",
        has_images=False,
    )

    assert handled is True
    assert state["study_trig_mode"] == "m"
    assert "study_group_block" not in state
    assert len(matcher.paused) == 1
    paused_message = matcher.paused[0]
    assert str(paused_message).startswith(INVALID_GROUP_BLOCK_PROMPT)


@pytest.mark.asyncio
async def test_partial_args_preserve_trigger_whitespace_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = _PauseMatcher()
    state: dict[str, object] = {}
    event = build_group_message_event("#study M F 第一行  ")

    monkeypatch.setattr(wordbank_service, "initialize", AsyncMock(return_value=None))

    handled = await study_plugin._start_guided_study_from_partial_args(
        cast(Matcher, matcher),
        event,
        state,
        "zh-CN",
        "M F 第一行  ",
        has_images=False,
    )

    assert handled is True
    trigger_shape = state["study_trigger_shape"]
    assert isinstance(trigger_shape, study_plugin.MessageShape)
    assert trigger_shape.atoms[0].text == "第一行  "
