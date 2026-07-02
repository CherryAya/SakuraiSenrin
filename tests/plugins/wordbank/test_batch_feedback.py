from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
import pytest

from src.plugins.wordbank import batch_feedback as batch_feedback_module
from src.plugins.wordbank.batch_feedback import send_batch_add_feedback
from src.plugins.wordbank.message_model import (
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.presentation import (
    WordbankAddResult,
    WordbankBatchAddItemResult,
    WordbankBatchAddResult,
)
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_send_batch_add_feedback_uses_rich_result_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rich_message = Message(
        [
            MessageSegment.text("词条已提交审核\n响应:\n"),
            MessageSegment.image(b"image-bytes"),
        ]
    )
    build_message = AsyncMock(return_value=rich_message)
    deliver_message = AsyncMock(return_value={"message_id": 1})
    send_forward = AsyncMock(return_value=None)

    monkeypatch.setattr(
        batch_feedback_module,
        "build_add_result_message",
        build_message,
    )
    monkeypatch.setattr(
        batch_feedback_module,
        "deliver_single_message",
        deliver_message,
    )
    monkeypatch.setattr(
        batch_feedback_module,
        "send_custom_forward",
        send_forward,
    )

    batch = WordbankBatchAddResult(
        total=2,
        success=1,
        failed=1,
        items=(
            WordbankBatchAddItemResult(
                index=1,
                ok=True,
                result=WordbankAddResult(
                    trigger_group_id=1,
                    trigger_variant_id=2,
                    response_item_id=3,
                    trigger_text="test_forward",
                    response_text="[图片:3069]",
                    scope="all_groups",
                    probability=1.0,
                    weight=3,
                    trigger_shape=shape_from_text("test_forward"),
                    response_shape=shape_from_image(3069),
                ),
            ),
            WordbankBatchAddItemResult(index=2, ok=False, error="boom"),
        ),
    )
    media_service = cast(WordbankMediaService, SimpleNamespace())
    bot = cast(Bot, SimpleNamespace())
    event = build_group_message_event("#wordbank.add", message_id=1)

    await send_batch_add_feedback(
        bot,
        event,
        batch=batch,
        locale="zh-CN",
        media_service=media_service,
        source_kind="wordbank_batch_submission",
        fallback_nickname="回 - 樱井千凛·Senrinです♡",
    )

    build_message.assert_awaited_once_with(
        batch.items[0].result,
        locale="zh-CN",
        media_service=media_service,
    )
    send_forward.assert_awaited_once()
    await_args = send_forward.await_args
    assert await_args is not None
    detail_messages = await_args.args[2]
    assert detail_messages[0] == rich_message
    assert any(segment.type == "image" for segment in detail_messages[0])
    assert "boom" in str(detail_messages[1])
