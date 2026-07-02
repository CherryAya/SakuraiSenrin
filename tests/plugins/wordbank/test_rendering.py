from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.lib.message_plan import render_message_plan_entry
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankResponseItemDetail,
    WordbankSearchItem,
)
from src.plugins.wordbank.handlers.rendering import (
    build_group_detail_page_plan_entry,
    build_pending_items_plan_entry,
    build_reply_detail_plan_entry,
    build_search_items_text_plan_entry,
)
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.services.media import WordbankMediaService


def _media_service() -> WordbankMediaService:
    async def _load(image_id: int) -> bytes:
        return f"image-{image_id}".encode()

    return cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(side_effect=_load)),
    )


def _search_item() -> WordbankSearchItem:
    return WordbankSearchItem(
        trigger_group_id=12,
        status="approved",
        trigger_text="[图片:8]",
        response_text="做个好梦 [图片:7]",
        response_summaries=("做个好梦 [图片:7]",),
        response_count=1,
        trigger_shape=shape_from_image(8),
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        response_item_ids=(300,),
    )


def _group_detail() -> WordbankGroupDetail:
    return WordbankGroupDetail(
        trigger_group_id=12,
        status="approved",
        enabled=1,
        probability=1.0,
        group_id="20001",
        created_by="10001",
        deleted_at=0,
        trigger_text="[图片:8]",
        trigger_shape=shape_from_image(8),
        trigger_variant_id=120,
        responses=(
            WordbankResponseItemDetail(
                response_item_id=300,
                status="approved",
                enabled=1,
                scope="current_group",
                weight=3,
                rule={},
                group_id="20001",
                created_by="10001",
                approved_by="10002",
                deleted_at=0,
                response_text="做个好梦 [图片:7]",
                response_shape=combine_shapes(
                    shape_from_text("做个好梦"),
                    shape_from_image(7),
                ),
            ),
        ),
        selected_response_item_id=300,
    )


@pytest.mark.asyncio
async def test_build_search_items_text_plan_entry_renders_rich_shapes() -> None:
    entry = await build_search_items_text_plan_entry(
        items=[_search_item()],
        locale="zh-CN",
        media_service=_media_service(),
    )

    rendered = render_message_plan_entry(entry)
    assert "词库搜索结果" in str(rendered)
    assert "[图片:8]" not in str(rendered)
    assert "[图片:7]" not in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 2


@pytest.mark.asyncio
async def test_build_pending_items_plan_entry_renders_rich_shapes() -> None:
    entry = await build_pending_items_plan_entry(
        items=[_search_item()],
        locale="zh-CN",
        media_service=_media_service(),
    )

    rendered = render_message_plan_entry(entry)
    assert "待审核词条" in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 2


@pytest.mark.asyncio
async def test_build_reply_detail_plan_entry_renders_selected_response() -> None:
    entry = await build_reply_detail_plan_entry(
        detail=_group_detail(),
        locale="zh-CN",
        media_service=_media_service(),
        message_id="123",
        message_type="group",
    )

    rendered = render_message_plan_entry(entry)
    assert "来源消息: 123 (group)" in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 2


@pytest.mark.asyncio
async def test_build_group_detail_page_plan_entry_renders_trigger_and_responses(
) -> None:
    entry = await build_group_detail_page_plan_entry(
        detail=_group_detail(),
        page=1,
        total_pages=1,
        locale="zh-CN",
        media_service=_media_service(),
    )

    rendered = render_message_plan_entry(entry)
    assert "Trigger Group #12" in str(rendered)
    assert "响应 #300" in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 2
