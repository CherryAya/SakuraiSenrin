import sys
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
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

from src.plugins.wordbank.forward_batch import ResponseInputPayload
from src.plugins.wordbank.guided_flow import (
    PRIVATE_SCOPE_PROMPT,
    copy_guided_state,
    finish_guided_add,
    guided_response_state_keys,
    guided_scope_prompt,
    record_guided_forward_response_choice,
)
from src.plugins.wordbank.message_model import shape_from_text
from src.plugins.wordbank.services import wordbank_media_service
from src.plugins.wordbank.services.presentation import WordbankBatchAddResult


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
async def test_guided_forward_choice_uses_saved_response_event(
    monkeypatch: pytest.MonkeyPatch,
    choice_text: str,
    expect_split: bool,
) -> None:
    matcher = _PauseMatcher()
    bot = cast(Bot, SimpleNamespace())
    response_event = build_group_message_event("", message_id=8)
    response_event.message = Message([MessageSegment.forward("54321")])
    state: dict[str, object] = {
        "wordbank_guided_response_forward_pending": True,
        "wordbank_guided_response_forward_event": response_event,
    }
    event = build_group_message_event(choice_text, message_id=9)
    payload = ResponseInputPayload(
        input_kind="forward",
        source_message_id="54321",
        whole_shape=shape_from_text("整体响应"),
        split_shapes=(shape_from_text("第一条"), shape_from_text("第二条")),
    )
    build_payload = AsyncMock(return_value=payload)

    monkeypatch.setattr(
        "src.plugins.wordbank.guided_flow.build_response_input_payload",
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

    build_payload.assert_awaited_once()
    await_args = build_payload.await_args
    assert await_args is not None
    assert await_args.args == (bot, response_event)
    assert await_args.kwargs["media_service"] is wordbank_media_service
    assert await_args.kwargs["task"] is not None
    assert "wordbank_guided_response_forward_pending" not in state
    assert "wordbank_guided_response_forward_event" not in state
    assert state["wordbank_guided_submission_source_event"] is response_event
    assert matcher.paused == [tr("zh-CN", "wordbank.guided.add.scope_prompt")]
    if expect_split:
        assert state["wordbank_guided_response_split_shapes"] == payload.split_shapes
        assert "wordbank_guided_response_shape" not in state
    else:
        assert "wordbank_guided_response_split_shapes" not in state
        assert state["wordbank_guided_response_shape"] == payload.whole_shape


def test_guided_response_state_keys_keeps_forward_response_state() -> None:
    split_shape = shape_from_text("第一条")
    state: dict[str, object] = {
        "wordbank_guided_response_split_shapes": (split_shape,),
        "wordbank_guided_response_forward_source_message_id": "54321",
        "wordbank_guided_response_forward_node_count": 2,
        "wordbank_guided_response_forward_messages": ("node-1", "node-2"),
        "wordbank_guided_submission_source_event": build_group_message_event(
            "响应词",
            message_id=7,
        ),
        "wordbank_guided_scope": "1",
    }

    snapshot = copy_guided_state(
        state,
        keep_keys=("wordbank_guided_trigger_shape", *guided_response_state_keys(state)),
    )

    assert snapshot["wordbank_guided_response_split_shapes"] == (split_shape,)
    assert snapshot["wordbank_guided_response_forward_source_message_id"] == "54321"
    assert snapshot["wordbank_guided_response_forward_node_count"] == 2
    assert snapshot["wordbank_guided_response_forward_messages"] == (
        "node-1",
        "node-2",
    )
    assert (
        snapshot["wordbank_guided_submission_source_event"]
        is state["wordbank_guided_submission_source_event"]
    )


@pytest.mark.asyncio
async def test_finish_guided_add_uses_saved_submission_source_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = cast(Matcher, SimpleNamespace())
    bot = cast(Bot, SimpleNamespace())
    source_event = build_group_message_event("第一条响应", message_id=8)
    weight_event = build_group_message_event("跳过", message_id=9)
    state: dict[str, object] = {
        "wordbank_locale": "zh-CN",
        "wordbank_guided_trigger_shape": shape_from_text("晚安"),
        "wordbank_guided_response_split_shapes": (
            shape_from_text("第一条"),
            shape_from_text("第二条"),
        ),
        "wordbank_guided_submission_source_event": source_event,
        "wordbank_guided_scope": "1",
    }
    batch = WordbankBatchAddResult(total=2, success=2, failed=0, items=())
    add_message_entries = AsyncMock(return_value=batch)
    finalize_submission = AsyncMock(return_value=None)

    service = SimpleNamespace(add_message_entries=add_message_entries)

    await finish_guided_add(
        bot,
        matcher,
        weight_event,
        state,
        finalize_submission=finalize_submission,
        wordbank_service=service,
    )

    finalize_submission.assert_awaited_once()
    assert finalize_submission.await_args is not None
    assert finalize_submission.await_args.kwargs["source_event"] is source_event


def test_guided_scope_prompt_uses_private_variant_in_private_chat() -> None:
    assert guided_scope_prompt(locale="zh-CN", is_group=True) == tr(
        "zh-CN",
        "wordbank.guided.add.scope_prompt",
    )
    assert guided_scope_prompt(locale="zh-CN", is_group=False) == PRIVATE_SCOPE_PROMPT


def test_guided_response_prompt_mentions_response_only_placeholders() -> None:
    prompt = tr("zh-CN", "wordbank.guided.add.response_prompt")

    assert "仅响应词生效" in prompt
    assert "[@触发者]" in prompt
    assert "[账号]" in prompt
    assert "[昵称]" in prompt
    assert "[群名片]" in prompt
    assert "[xx]" in prompt
    assert "触发者头像" in prompt
