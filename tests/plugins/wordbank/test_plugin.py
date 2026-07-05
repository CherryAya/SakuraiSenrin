import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Message
import pytest

from src.lib.message_plan import render_message_plan_input

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
from src.plugins.wordbank.handlers import rendering as rendering_module
from src.plugins.wordbank.handlers.passive import PassiveResponse
from src.plugins.wordbank.handlers.rendering import MISSING_IMAGE_PLACEHOLDER
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
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

    message, image_trace_fields = await wordbank_plugin._build_passive_message(
        response,
        locale="zh-CN",
    )
    rendered = render_message_plan_input(message)

    assert isinstance(rendered, Message)
    assert [segment.type for segment in rendered] == ["text", "image"]
    assert image_trace_fields == {
        "requested_image_ids": (7,),
        "loaded_image_ids": (7,),
        "loaded_image_sizes": (11,),
        "loaded_count": 1,
        "missing_count": 0,
        "image_total_bytes": 11,
        "image_max_bytes": 11,
    }
    media_service.load_canonical_storage_bytes.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_build_passive_message_degrades_legacy_unsafe_at_target(
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
        response_shape=MessageShape(
            (
                MessageAtom(kind="text", text="提醒"),
                MessageAtom(kind="at", target_id="all"),
            )
        ),
    )

    message, _ = await wordbank_plugin._build_passive_message(
        response,
        locale="zh-CN",
    )
    rendered = render_message_plan_input(message)

    assert isinstance(rendered, Message)
    assert [segment.type for segment in rendered] == ["text", "text"]
    assert str(rendered) == "提醒@全体成员"


@pytest.mark.asyncio
async def test_build_passive_message_renders_sender_at_from_response_shape(
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
        message_type="event",
        response_shape=MessageShape(
            (
                MessageAtom(kind="at", target_id="__sender__"),
                MessageAtom(kind="text", text=" 在？"),
            )
        ),
    )

    message, _ = await wordbank_plugin._build_passive_message(
        response,
        locale="zh-CN",
    )
    rendered = render_message_plan_input(message)

    assert isinstance(rendered, Message)
    assert [segment.type for segment in rendered] == ["at", "text"]
    assert rendered[0].data["qq"] == "10001"
    assert rendered[1].data["text"] == " 在？"


@pytest.mark.asyncio
async def test_build_passive_message_logs_render_shape_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_service = SimpleNamespace(
        load_canonical_storage_bytes=AsyncMock(return_value=b"image-bytes")
    )
    monkeypatch.setattr(wordbank_plugin, "wordbank_media_service", media_service)
    events: dict[str, dict[str, object]] = {}

    def _capture(stage: str, *, start: float | None = None, **fields: object) -> None:
        _ = start
        events[stage] = fields

    monkeypatch.setattr(wordbank_plugin, "log_perf", _capture)
    monkeypatch.setattr(rendering_module, "log_perf", _capture)
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

    message, image_trace_fields = await wordbank_plugin._build_passive_message(
        response,
        locale="zh-CN",
    )
    rendered = render_message_plan_input(message)

    assert isinstance(rendered, Message)
    assert image_trace_fields["image_total_bytes"] == len(b"image-bytes")
    assert "plugin.build_passive_message.render_shape.begin" in events
    assert events["plugin.build_passive_message.render_shape.images_loaded"][
        "loaded_image_sizes"
    ] == (len(b"image-bytes"),)
    assert events["plugin.build_passive_message.render_shape.segment_built"][
        "image_total_bytes"
    ] == len(b"image-bytes")
    assert events["plugin.build_passive_message.rendered_shape"][
        "image_max_bytes"
    ] == len(b"image-bytes")


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

    message, image_trace_fields = await wordbank_plugin._build_passive_message(
        response,
        locale="zh-CN",
    )
    rendered = render_message_plan_input(message)

    assert isinstance(rendered, Message)
    assert [segment.type for segment in rendered] == ["text", "text"]
    assert rendered[0].data["text"] == "做个好梦"
    assert rendered[1].data["text"] == MISSING_IMAGE_PLACEHOLDER
    assert image_trace_fields == {
        "requested_image_ids": (7,),
        "loaded_image_ids": (),
        "loaded_image_sizes": (),
        "loaded_count": 0,
        "missing_count": 1,
        "image_total_bytes": 0,
        "image_max_bytes": 0,
    }
    media_service.load_canonical_storage_bytes.assert_awaited_once_with(7)
