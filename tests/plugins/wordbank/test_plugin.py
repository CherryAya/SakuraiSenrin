import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Message
import pytest

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

from src.plugins import wordbank as wordbank_plugin
from src.plugins.wordbank.handlers.passive import PassiveResponse
from src.plugins.wordbank.handlers.rendering import MISSING_IMAGE_PLACEHOLDER
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)


@pytest.mark.asyncio
async def test_initialize_wordbank_plugin_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wordbank_plugin, "_wordbank_initialized", False)
    service = SimpleNamespace(initialize=AsyncMock())
    media_service = SimpleNamespace(rebuild_cache=AsyncMock())
    monkeypatch.setattr(wordbank_plugin, "wordbank_service", service)
    monkeypatch.setattr(wordbank_plugin, "wordbank_media_service", media_service)

    await wordbank_plugin.initialize_wordbank_plugin()
    await wordbank_plugin.initialize_wordbank_plugin()

    service.initialize.assert_awaited_once()
    media_service.rebuild_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_passive_message_rebuilds_text_and_image_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_service = SimpleNamespace(
        load_canonical_storage_bytes=AsyncMock(return_value=b"image-bytes")
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_media_service", media_service)
    response = PassiveResponse(
        text="fallback",
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        group_id="20001",
        user_id="10001",
        message_type="message",
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
    )

    message = await wordbank_plugin._build_passive_message(response)

    assert isinstance(message, Message)
    assert [segment.type for segment in message] == ["text", "image"]
    media_service.load_canonical_storage_bytes.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_build_passive_message_keeps_text_when_image_storage_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_service = SimpleNamespace(
        load_canonical_storage_bytes=AsyncMock(return_value=None)
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_media_service", media_service)
    response = PassiveResponse(
        text="fallback",
        trigger_group_id=12,
        trigger_variant_id=21,
        response_item_id=22,
        group_id="20001",
        user_id="10001",
        message_type="message",
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
    )

    message = await wordbank_plugin._build_passive_message(response)

    assert isinstance(message, Message)
    assert [segment.type for segment in message] == ["text", "text"]
    assert message[0].data["text"] == "做个好梦"
    assert message[1].data["text"] == MISSING_IMAGE_PLACEHOLDER
    media_service.load_canonical_storage_bytes.assert_awaited_once_with(7)
