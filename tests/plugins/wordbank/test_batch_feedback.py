from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
import pytest

from src.lib.message_plan import (
    MessagePlanEntry,
    RawMessageBlock,
    render_message_plan_input,
)
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
    build_message = AsyncMock(
        return_value=MessagePlanEntry(blocks=(RawMessageBlock(rich_message),))
    )
    deliver_plan = AsyncMock(
        side_effect=[
            SimpleNamespace(results=({"message_id": 1},)),
            SimpleNamespace(results=({"message_id": 2},)),
        ]
    )

    monkeypatch.setattr(
        batch_feedback_module,
        "build_add_result_plan_entry",
        build_message,
    )
    monkeypatch.setattr(
        batch_feedback_module,
        "deliver_message_plan",
        deliver_plan,
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
    assert deliver_plan.await_count == 2
    summary_call = deliver_plan.await_args_list[0]
    summary_plan = summary_call.kwargs["plan"]
    assert len(summary_plan.messages) == 1
    assert str(summary_plan.messages[0]).startswith("已处理合并转发响应导入")

    await_args = deliver_plan.await_args_list[1]
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    detail_messages = plan.messages
    first_message = detail_messages[0]
    second_message = detail_messages[1]
    rendered_first = render_message_plan_input(first_message)
    assert any(segment.type == "image" for segment in rendered_first)
    assert "boom" in str(render_message_plan_input(second_message))
