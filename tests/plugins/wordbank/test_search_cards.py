from io import BytesIO
from typing import Any

from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw

from src.lib.message_plan import render_message_plan_entry
from src.plugins.water.img import _build_copyright_text
from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers.search_card_helpers import response_preview_items
from src.plugins.wordbank.handlers.search_cards import (
    CARD_RESPONSE_PADDING_X,
    CARD_TAG_ROW_GAP,
    SearchCardQuery,
    SearchResultCardRenderer,
    build_search_results_card_plan_entry,
)
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)


def _png_bytes(color: str = "#E88B8B") -> bytes:
    image = Image.new("RGB", (120, 80), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _item(
    trigger_group_id: int,
    *,
    matched_by: str = "text:trigger",
) -> WordbankSearchItem:
    return WordbankSearchItem(
        trigger_group_id=trigger_group_id,
        status="approved",
        trigger_text=f"触发{trigger_group_id}",
        response_text=f"响应{trigger_group_id}",
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        matched_by=matched_by,
    )


def _render_search_results_card(**kwargs: Any) -> Message:
    return render_message_plan_entry(build_search_results_card_plan_entry(**kwargs))


def test_render_search_results_card_returns_image_message() -> None:
    message = _render_search_results_card(
        items=(_item(12), _item(13, matched_by="image:response")),
        query=SearchCardQuery(
            keyword="晚安",
            field="all",
            creator_id="10001",
            has_image=True,
            page=1,
            total_count=2,
            limit=10,
        ),
        locale="zh-CN",
    )

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"


def test_render_search_results_card_supports_multiline_response_text() -> None:
    message = _render_search_results_card(
        items=(
            WordbankSearchItem(
                trigger_group_id=12,
                status="approved",
                trigger_text="凛凛的妙妙小工具",
                response_text="第一行\n第二行\n第三行\n第四行",
                response_summaries=("第一行\n第二行\n第三行\n第四行",),
                scope="all_groups",
                probability=1.0,
                weight=3,
                created_by="10001",
                matched_by="text:trigger",
            ),
        ),
        query=SearchCardQuery(
            keyword="妙妙小工具",
            field="trigger",
            creator_id="",
            has_image=False,
            page=1,
            total_count=1,
            limit=10,
        ),
        locale="zh-CN",
    )

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"


def test_render_search_results_card_supports_inline_preview_images() -> None:
    message = _render_search_results_card(
        items=(
            WordbankSearchItem(
                trigger_group_id=22,
                status="approved",
                trigger_text="[图片:7]",
                response_text="第一条响应 [图片:8]",
                response_summaries=("第一条响应 [图片:8]",),
                trigger_shape=shape_from_image(7),
                response_shape=combine_shapes(
                    shape_from_text("第一条响应"),
                    shape_from_image(8),
                ),
                scope="current_group",
                probability=1.0,
                weight=3,
                created_by="10001",
                matched_by="image:trigger",
            ),
        ),
        query=SearchCardQuery(
            keyword="晚安",
            field="all",
            creator_id="",
            has_image=True,
            page=1,
            total_count=1,
            limit=10,
        ),
        locale="zh-CN",
        preview_bytes={7: _png_bytes("#FBEAEA"), 8: _png_bytes("#E88B8B")},
    )

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"


def test_search_card_renderer_adds_fold_hint_for_multi_response_group() -> None:
    renderer = SearchResultCardRenderer()
    item = WordbankSearchItem(
        trigger_group_id=123,
        status="approved",
        trigger_text="晚安",
        response_text="第一条",
        response_summaries=("第一条", "第二条", "第三条"),
        response_count=5,
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        matched_by="text:trigger",
    )

    hint = renderer._fold_hint(item, "zh-CN")  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]

    assert "本组共 5 条词条" in hint
    assert "前 3 条" in hint
    assert "详情123" in hint
    assert renderer._has_folded_preview(item) is True  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    assert renderer._folded_preview_block_height() > 0  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]


def test_response_preview_items_preserve_response_boundaries() -> None:
    item = WordbankSearchItem(
        trigger_group_id=123,
        status="approved",
        trigger_text="晚安",
        response_text="第一条",
        response_summaries=("第一条\n第一段", "第二条\n第二段", "第三条"),
        response_item_ids=(401, 402, 403),
        response_count=5,
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        matched_by="text:trigger",
    )

    previews = response_preview_items(item, "zh-CN")

    assert tuple(preview.index for preview in previews) == (1, 2, 3)
    assert tuple(preview.response_item_id for preview in previews) == (401, 402, 403)
    assert previews[0].blocks[0].text == "第一条\n第一段"
    assert previews[1].blocks[0].text == "第二条\n第二段"


def test_response_preview_items_use_shape_blocks_without_image_placeholder() -> None:
    item = WordbankSearchItem(
        trigger_group_id=88,
        status="approved",
        trigger_text="晚安",
        response_text="做个好梦 [图片:7]",
        response_summaries=("做个好梦 [图片:7]", "第二条预览"),
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
        response_item_ids=(501, 502),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
    )

    previews = response_preview_items(item, "zh-CN")

    assert tuple(block.kind for block in previews[0].blocks) == ("text", "image")
    assert "[图片:7]" not in "".join(block.text for block in previews[0].blocks)
    assert tuple(block.kind for block in previews[1].blocks) == ("text",)


def test_response_preview_items_preserve_shape_order_for_mixed_content() -> None:
    text_first = WordbankSearchItem(
        trigger_group_id=1,
        status="approved",
        trigger_text="晚安",
        response_text="文本后接图",
        response_summaries=("文本后接图",),
        response_shape=combine_shapes(shape_from_text("前文"), shape_from_image(7)),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
    )
    image_first = WordbankSearchItem(
        trigger_group_id=2,
        status="approved",
        trigger_text="晚安",
        response_text="先图后文",
        response_summaries=("先图后文",),
        response_shape=combine_shapes(shape_from_image(8), shape_from_text("后文")),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
    )

    text_first_preview = response_preview_items(text_first, "zh-CN")[0]
    image_first_preview = response_preview_items(image_first, "zh-CN")[0]

    assert tuple(block.kind for block in text_first_preview.blocks) == ("text", "image")
    assert tuple(block.kind for block in image_first_preview.blocks) == (
        "image",
        "text",
    )


def test_search_card_renderer_uses_distinct_trigger_and_response_text_styles() -> None:
    renderer = SearchResultCardRenderer()

    assert (
        renderer.trigger_body_font != renderer.response_body_font
        or renderer.trigger_text_fill != renderer.response_text_fill
    )


def test_search_card_renderer_adds_tag_row_gap_to_multiline_tags() -> None:
    renderer = SearchResultCardRenderer()
    tags = ("特别特别长的标签一", "特别特别长的标签二", "特别特别长的标签三")
    width = 220

    height = renderer._tags_height(tags=tags, width=width)  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    single_row_height = (
        renderer._tags_height(tags=("单行标签",), width=width)  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    )

    assert height > single_row_height
    assert height >= single_row_height * 2 + CARD_TAG_ROW_GAP - 4


def test_search_card_renderer_response_body_keeps_uniform_content_start_x() -> None:
    renderer = SearchResultCardRenderer(preview_bytes={7: _png_bytes()})
    response = response_preview_items(
        WordbankSearchItem(
            trigger_group_id=77,
            status="approved",
            trigger_text="晚安",
            response_text="[图片:7]",
            response_summaries=("[图片:7]",),
            response_shape=shape_from_image(7),
            scope="current_group",
            probability=1.0,
            weight=3,
            created_by="10001",
        ),
        "zh-CN",
    )[0]
    image = Image.new("RGB", (400, 240), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    recorded: list[int] = []

    original = renderer._draw_content_blocks  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]

    def _recorded_draw_content_blocks(*args: Any, **kwargs: Any) -> int:
        recorded.append(int(kwargs["x"]))
        return original(*args, **kwargs)

    renderer._draw_content_blocks = _recorded_draw_content_blocks  # type: ignore[method-assign]  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    renderer._draw_response_panel(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        image,
        draw,
        response=response,
        item=_item(77),
        absolute_index=4,
        x=30,
        y=80,
        width=300,
        height=120,
    )

    assert recorded == [30 + CARD_RESPONSE_PADDING_X]


def test_search_card_copyright_text_matches_water_style() -> None:
    assert _build_copyright_text(2026) == "© 2020-2026 SakuraiSenrin"
