from io import BytesIO
from typing import Any

from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw

from src.lib.message_plan import render_message_plan_entry
from src.plugins.water.img import _build_copyright_text
from src.plugins.wordbank.database.types import (
    WordbankSearchItem,
    WordbankSearchPreviewResponse,
)
from src.plugins.wordbank.handlers.search_card_helpers import (
    probability_chip_text,
    response_preview_items,
    summary_chips,
    weight_chip_text,
)
from src.plugins.wordbank.handlers.search_cards import (
    CARD_CHIP_HEIGHT,
    CARD_META_SEPARATOR_GAP,
    CARD_META_SEPARATOR_MARGIN_BOTTOM,
    CARD_META_SEPARATOR_MARGIN_TOP,
    CARD_RESPONSE_PADDING_X,
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
        response_text="第一条\n第一段",
        response_count=5,
        probability=1.0,
        preview_responses=(
            WordbankSearchPreviewResponse(
                response_item_id=401,
                status="approved",
                created_by="10001",
                scope="current_group",
                weight=3,
                rule=None,
                response_text="第一条\n第一段",
            ),
            WordbankSearchPreviewResponse(
                response_item_id=402,
                status="pending",
                created_by="10002",
                scope="all_groups",
                weight=5,
                rule={"roles": "admin"},
                response_text="第二条\n第二段",
            ),
            WordbankSearchPreviewResponse(
                response_item_id=403,
                status="rejected",
                created_by="10003",
                scope="self",
                weight=2,
                rule={"call_count": {"window_seconds": 30, "min": 2, "max": 4}},
                response_text="第三条",
            ),
        ),
    )

    previews = response_preview_items(item, "zh-CN")

    assert tuple(preview.index for preview in previews) == (1, 2, 3)
    assert tuple(preview.response_item_id for preview in previews) == (401, 402, 403)
    assert previews[0].blocks[0].text == "第一条\n第一段"
    assert previews[1].blocks[0].text == "第二条\n第二段"
    assert previews[1].created_by == "10002"
    assert previews[1].scope == "all_groups"
    assert previews[1].rule == {"roles": "admin"}
    assert previews[2].weight == 2


def test_response_preview_items_use_shape_blocks_without_image_placeholder() -> None:
    item = WordbankSearchItem(
        trigger_group_id=88,
        status="approved",
        trigger_text="晚安",
        response_text="做个好梦 [图片:7]",
        probability=1.0,
        preview_responses=(
            WordbankSearchPreviewResponse(
                response_item_id=501,
                status="approved",
                created_by="10001",
                scope="current_group",
                weight=3,
                rule=None,
                response_text="做个好梦 [图片:7]",
                response_shape=combine_shapes(
                    shape_from_text("做个好梦"),
                    shape_from_image(7),
                ),
            ),
            WordbankSearchPreviewResponse(
                response_item_id=502,
                status="approved",
                created_by="10002",
                scope="all_groups",
                weight=3,
                rule=None,
                response_text="第二条预览",
            ),
        ),
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
        probability=1.0,
        preview_responses=(
            WordbankSearchPreviewResponse(
                response_item_id=701,
                status="approved",
                created_by="10001",
                scope="current_group",
                weight=3,
                rule=None,
                response_text="文本后接图",
                response_shape=combine_shapes(
                    shape_from_text("前文"),
                    shape_from_image(7),
                ),
            ),
        ),
    )
    image_first = WordbankSearchItem(
        trigger_group_id=2,
        status="approved",
        trigger_text="晚安",
        response_text="先图后文",
        probability=1.0,
        preview_responses=(
            WordbankSearchPreviewResponse(
                response_item_id=702,
                status="approved",
                created_by="10001",
                scope="current_group",
                weight=3,
                rule=None,
                response_text="先图后文",
                response_shape=combine_shapes(
                    shape_from_image(8),
                    shape_from_text("后文"),
                ),
            ),
        ),
    )

    text_first_preview = response_preview_items(text_first, "zh-CN")[0]
    image_first_preview = response_preview_items(image_first, "zh-CN")[0]

    assert tuple(block.kind for block in text_first_preview.blocks) == ("text", "image")
    assert tuple(block.kind for block in image_first_preview.blocks) == (
        "image",
        "text",
    )


def test_search_card_renderer_drops_inner_trigger_panel() -> None:
    renderer = SearchResultCardRenderer()
    item = _item(31)
    column_width = 500

    total_height = renderer._item_block_height(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        item,
        absolute_index=1,
        locale="zh-CN",
        column_width=column_width,
    )
    width = column_width - 22 * 2
    expected = 20 * 2
    expected += renderer._item_header_height(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        item=item,
        width=width,
        locale="zh-CN",
    )
    expected += 14
    previews = response_preview_items(item, "zh-CN")
    expected += sum(
        renderer._response_panel_height(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
            response=preview,
            item=item,
            absolute_index=1,
            width=width,
        )
        for preview in previews
    )
    if len(previews) > 1:
        expected += 14 * (len(previews) - 1)

    assert total_height == expected
    assert not hasattr(renderer, "_draw_trigger_panel")


def test_summary_chips_hide_default_filters() -> None:
    assert (
        summary_chips(
            keyword="",
            field="all",
            creator_id="",
            has_image=False,
            locale="zh-CN",
            field_label="全部",
        )
        == ()
    )


def test_probability_and_weight_default_values_are_hidden() -> None:
    assert probability_chip_text(1.0) == ""
    assert weight_chip_text(3) == ""


def test_search_card_renderer_response_body_keeps_uniform_content_start_x() -> None:
    renderer = SearchResultCardRenderer(preview_bytes={7: _png_bytes()})
    response = response_preview_items(
        WordbankSearchItem(
            trigger_group_id=77,
            status="approved",
            trigger_text="晚安",
            response_text="[图片:7]",
            probability=1.0,
            preview_responses=(
                WordbankSearchPreviewResponse(
                    response_item_id=801,
                    status="approved",
                    created_by="10001",
                    scope="current_group",
                    weight=3,
                    rule=None,
                    response_text="[图片:7]",
                    response_shape=shape_from_image(7),
                ),
            ),
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
        locale="zh-CN",
        x=30,
        y=80,
        width=300,
        height=120,
    )

    assert recorded == [30 + CARD_RESPONSE_PADDING_X]


def test_search_card_renderer_draws_response_content_before_meta_row() -> None:
    renderer = SearchResultCardRenderer()
    response = response_preview_items(
        WordbankSearchItem(
            trigger_group_id=78,
            status="approved",
            trigger_text="晚安",
            response_text="第一条\n第二行",
            probability=1.0,
            preview_responses=(
                WordbankSearchPreviewResponse(
                    response_item_id=811,
                    status="approved",
                    created_by="10001",
                    scope="current_group",
                    weight=3,
                    rule={"roles": "admin"},
                    response_text="第一条\n第二行",
                ),
            ),
        ),
        "zh-CN",
    )[0]
    image = Image.new("RGB", (400, 280), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    recorded: dict[str, int] = {}

    original_content = renderer._draw_content_blocks  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    original_meta = renderer._draw_response_meta_row  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]

    def _recorded_content(*args: Any, **kwargs: Any) -> int:
        recorded["content_y"] = int(kwargs["y"])
        return original_content(*args, **kwargs)

    def _recorded_meta(*args: Any, **kwargs: Any) -> None:
        recorded["meta_y"] = int(kwargs["y"])
        return original_meta(*args, **kwargs)

    renderer._draw_content_blocks = _recorded_content  # type: ignore[method-assign]  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    renderer._draw_response_meta_row = _recorded_meta  # type: ignore[method-assign]  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    renderer._draw_response_panel(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        image,
        draw,
        response=response,
        item=_item(78),
        absolute_index=5,
        locale="zh-CN",
        x=30,
        y=80,
        width=300,
        height=180,
    )

    assert recorded["content_y"] == 80 + 10
    assert recorded["meta_y"] > recorded["content_y"]


def test_search_card_renderer_response_panel_height_includes_bottom_meta_area() -> None:
    renderer = SearchResultCardRenderer()
    response = response_preview_items(
        WordbankSearchItem(
            trigger_group_id=79,
            status="approved",
            trigger_text="晚安",
            response_text="正文",
            probability=1.0,
            preview_responses=(
                WordbankSearchPreviewResponse(
                    response_item_id=812,
                    status="approved",
                    created_by="10001",
                    scope="current_group",
                    weight=3,
                    rule=None,
                    response_text="正文",
                ),
            ),
        ),
        "zh-CN",
    )[0]

    height = renderer._response_panel_height(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        response=response,
        item=_item(79),
        absolute_index=5,
        width=300,
    )
    content_height = renderer._content_blocks_height(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        response.blocks,
        max_width=300 - CARD_RESPONSE_PADDING_X * 2,
        text_font=renderer.response_body_font,
    )

    assert height == (
        10 * 2
        + content_height
        + CARD_META_SEPARATOR_MARGIN_TOP
        + CARD_META_SEPARATOR_GAP
        + CARD_META_SEPARATOR_MARGIN_BOTTOM
        + CARD_CHIP_HEIGHT
    )


def test_search_card_renderer_response_meta_uses_response_level_values() -> None:
    renderer = SearchResultCardRenderer()
    response = response_preview_items(
        WordbankSearchItem(
            trigger_group_id=66,
            status="approved",
            trigger_text="晚安",
            response_text="第一条",
            probability=1.0,
            preview_responses=(
                WordbankSearchPreviewResponse(
                    response_item_id=901,
                    status="pending",
                    created_by="24680",
                    scope="all_groups",
                    weight=5,
                    rule={"roles": "admin"},
                    response_text="第一条",
                ),
            ),
        ),
        "zh-CN",
    )[0]

    left_chips = renderer._response_left_chips(  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        response,
        "zh-CN",
        absolute_index=9,
    )
    right_chips = renderer._response_right_chips(response)  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]

    assert [chip.text for chip in left_chips] == ["9-1", "[pending]", "全局", "管理"]
    assert [chip.text for chip in right_chips] == ["U:24680", "W:5"]


def test_search_card_copyright_text_matches_water_style() -> None:
    assert _build_copyright_text(2026) == "© 2020-2026 SakuraiSenrin"
