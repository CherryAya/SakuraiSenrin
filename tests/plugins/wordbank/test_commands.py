from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.message import Message
import pytest

from src.plugins.wordbank.database.types import WordbankSearchItem, WordbankSearchPage
from src.plugins.wordbank.handlers.commands import (
    build_message_shape_from_message,
    dispatch_wordbank_command,
    handle_add_text_result,
    handle_add_with_media_result,
    handle_guided_add_shape_result,
    handle_guided_study_shape_result,
    handle_study_media_with_rule_result,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    shape_from_message,
    shape_to_summary_text,
)
from src.plugins.wordbank.services.core import WordbankAddResult, WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from tests.plugins.water.helpers import build_group_message_event


def _add_result(
    *,
    entry_id: int = 12,
    trigger_text: str = "晚安",
    response_text: str = "做个好梦",
) -> WordbankAddResult:
    return WordbankAddResult(
        entry_id=entry_id,
        trigger_text=trigger_text,
        response_text=response_text,
        trigger_mode="strict",
        scope="current_group",
        probability=1.0,
        weight=3,
    )


def _search_item(*, entry_id: int = 12) -> WordbankSearchItem:
    return WordbankSearchItem(
        entry_id=entry_id,
        status="approved",
        trigger_text=f"晚安{entry_id}",
        trigger_mode="strict",
        response_text=f"做个好梦{entry_id}",
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
    )


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_formats_search_with_locale() -> None:
    event = build_group_message_event("#wordbank search 晚安")
    service = cast(
        WordbankService,
        SimpleNamespace(
            search_page=AsyncMock(
                return_value=WordbankSearchPage(
                    items=(_search_item(),),
                    total_count=1,
                    offset=0,
                    limit=10,
                )
            )
        ),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="search 晚安",
        locale="zh-CN",
    )

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"


@pytest.mark.asyncio
async def test_handle_add_text_result_calls_add_message_entry_with_text_shapes() -> (
    None
):
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦")

    result = await handle_add_text_result(
        service,
        event=event,
        text="晚安 => 做个好梦",
    )

    assert result.entry_id == 12
    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert isinstance(kwargs["trigger_shape"], MessageShape)
    assert isinstance(kwargs["response_shape"], MessageShape)
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"
    assert kwargs["trigger_mode"] == "strict"


@pytest.mark.asyncio
async def test_handle_add_with_media_result_builds_image_response_shape() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(response_text="[图片:7]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=7))
        ),
    )
    event = build_group_message_event(
        "#wordbank add 晚安 [CQ:image,url=https://example.test/a.png]"
    )

    await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text="晚安",
        image_bytes=b"image-bytes",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "[图片:7]"


@pytest.mark.asyncio
async def test_handle_add_with_media_result_builds_image_trigger_shape() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="[图片:9]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=9))
        ),
    )
    event = build_group_message_event(
        "#wordbank add [CQ:image,url=https://example.test/a.png] => 是这张图"
    )

    await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text="=> 是这张图",
        image_bytes=b"image-bytes",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[图片:9]"
    assert shape_to_summary_text(kwargs["response_shape"]) == "是这张图"


@pytest.mark.asyncio
async def test_handle_guided_add_shape_result_uses_scope_and_strict_mode() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add")

    await handle_guided_add_shape_result(
        service,
        event=event,
        trigger_shape=shape_from_message(Message("晚安")),
        response_shape=shape_from_message(Message("做个好梦")),
        scope_text="1",
        advanced_text="跳过",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"]["scope"] == "current_group"
    assert kwargs["trigger_mode"] == "strict"


@pytest.mark.asyncio
async def test_build_message_shape_from_message_preserves_mixed_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import commands as commands_module

    async def _fetch_image_bytes(
        _url: str,
        *,
        attempts: int = 3,
        retry_delay_seconds: float = 0.8,
    ) -> bytes | None:
        _ = attempts, retry_delay_seconds
        return b"image-bytes"

    monkeypatch.setattr(
        commands_module,
        "fetch_image_bytes_with_retry",
        _fetch_image_bytes,
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=7))
        ),
    )

    shape = await build_message_shape_from_message(
        media_service,
        Message("[CQ:at,qq=10002]早安[CQ:image,url=https://example.test/a.png]"),
    )

    assert shape_to_summary_text(shape) == "[@:10002] 早安 [图片:7]"


@pytest.mark.asyncio
async def test_handle_guided_add_shape_result_accepts_message_shapes() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add")

    await handle_guided_add_shape_result(
        service,
        event=event,
        trigger_shape=shape_from_message(Message("[CQ:at,qq=10002]早安")),
        response_shape=shape_from_message(Message("做个好梦")),
        scope_text="1",
        advanced_text="跳过",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[@:10002] 早安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"


@pytest.mark.asyncio
async def test_handle_guided_study_shape_result_builds_rules_and_shapes() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#study")

    await handle_guided_study_shape_result(
        service,
        event=event,
        trig_mode_text="a",
        group_block_text="t",
        trigger_shape=shape_from_message(Message("晚安")),
        response_shape=shape_from_message(Message("做个好梦")),
        weight_text="4",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"]["weight"] == 4
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"


@pytest.mark.asyncio
async def test_handle_study_media_with_rule_result_supports_two_image_messages() -> (
    None
):
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="[图片:7]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(
                side_effect=[
                    SimpleNamespace(canonical_id=7),
                    SimpleNamespace(canonical_id=8),
                ]
            )
        ),
    )
    event = build_group_message_event("#study a t")

    await handle_study_media_with_rule_result(
        service,
        media_service,
        event=event,
        source="",
        raw_rule={"scope": "all_groups"},
        image_bytes=(b"left", b"right"),
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[图片:7]"
    assert shape_to_summary_text(kwargs["response_shape"]) == "[图片:8]"
