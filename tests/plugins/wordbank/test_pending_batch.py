from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.lib.message_plan import render_message_plan_input
from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.pending_batch import _build_pending_detail_message
from src.plugins.wordbank.services.media import WordbankMediaService


@pytest.mark.asyncio
async def test_build_pending_detail_message_returns_image_blocks() -> None:
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=b"bytes")),
    )
    item = WordbankSearchItem(
        trigger_group_id=12,
        status="pending",
        trigger_text="[图片:8]",
        response_text="做个好梦 [图片:7]",
        trigger_shape=shape_from_image(8),
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        response_item_ids=(300,),
    )

    entry = await _build_pending_detail_message(
        item,
        index=1,
        locale="zh-CN",
        media_service=media_service,
    )

    rendered = render_message_plan_input(entry)
    assert "序号 1" in str(rendered)
    assert "[图片:8]" not in str(rendered)
    assert "[图片:7]" not in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 2
