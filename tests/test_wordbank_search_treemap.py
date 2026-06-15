from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw
import pytest

from src.lib.wordbank_search_treemap import (
    TREEMAP_HEIGHT,
    TREEMAP_WIDTH,
    SearchTreemapItem,
    SearchTreemapPage,
    SearchTreemapQuery,
    SearchTreemapRenderer,
    SearchTreemapResponseCard,
    SearchTreemapResponseSegment,
    SearchTreemapTile,
    TreemapRect,
    build_search_treemap_layout,
    load_search_treemap_fixture,
    render_search_results_treemap,
    render_search_results_treemap_bytes,
)

FIXTURES_ROOT = Path(__file__).parent / "plugins" / "wordbank" / "fixtures"


def test_load_search_treemap_fixture_builds_page() -> None:
    page = load_search_treemap_fixture(FIXTURES_ROOT / "search_treemap_basic.json")

    assert isinstance(page, SearchTreemapPage)
    assert page.query.keyword == "晚安"
    assert len(page.items) == 8
    assert page.items[0].response_count == 12
    assert page.items[0].remaining_response_count == 7
    assert page.items[0].responses[0].weight == 8


def test_load_search_treemap_fixture_rejects_missing_required_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "broken.json"
    source.write_text(
        '{"query":{"keyword":"x","field":"trigger","creator_id":"","has_image":false,"page":1,"total_count":1,"limit":10},"items":[{"trigger_group_id":1}]}',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"response_count|responses|trigger_text",
    ):
        load_search_treemap_fixture(source)


def test_load_search_treemap_fixture_rejects_incomplete_response_card(
    tmp_path: Path,
) -> None:
    source = tmp_path / "broken-response.json"
    source.write_text(
        '{"query":{"keyword":"x","field":"trigger","creator_id":"","has_image":false,"page":1,"total_count":1,"limit":10},"items":[{"trigger_group_id":1,"trigger_text":"t","status":"approved","created_by":"10001","response_count":1,"responses":[{"text":"r","created_by":"10002","weight":3}]}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"rule"):
        load_search_treemap_fixture(source)


def test_load_search_treemap_fixture_accepts_response_segments(tmp_path: Path) -> None:
    source = tmp_path / "segments.json"
    source.write_text(
        '{"query":{"keyword":"x","field":"trigger","creator_id":"","has_image":false,"page":1,"total_count":1,"limit":10},"items":[{"trigger_group_id":1,"trigger_text":"t","status":"approved","created_by":"10001","response_count":1,"responses":[{"text":"摘要","created_by":"10002","weight":3,"rule":"默认","segments":[{"kind":"text","text":"前半句"},{"kind":"image","image_path":"/tmp/a.webp"},{"kind":"text","text":"后半句"}]}]}]}',
        encoding="utf-8",
    )

    page = load_search_treemap_fixture(source)

    assert len(page.items[0].responses[0].segments) == 3
    assert page.items[0].responses[0].segments[1].kind == "image"


def test_build_search_treemap_layout_scales_area_with_response_count() -> None:
    page = load_search_treemap_fixture(FIXTURES_ROOT / "search_treemap_basic.json")

    layout = build_search_treemap_layout(
        page,
        content_x=0,
        content_y=0,
        content_width=1200,
        content_height=720,
    )

    tile_by_group = {tile.item.trigger_group_id: tile for tile in layout}
    assert tile_by_group[101].rect.area > tile_by_group[104].rect.area
    assert tile_by_group[104].rect.area > tile_by_group[108].rect.area
    assert tile_by_group[101].normalized_weight > tile_by_group[104].normalized_weight
    assert tile_by_group[107].normalized_weight > tile_by_group[108].normalized_weight


def test_build_search_treemap_layout_is_stable_for_same_fixture() -> None:
    page = load_search_treemap_fixture(FIXTURES_ROOT / "search_treemap_stress.json")

    first = build_search_treemap_layout(
        page,
        content_x=0,
        content_y=0,
        content_width=1200,
        content_height=720,
    )
    second = build_search_treemap_layout(
        page,
        content_x=0,
        content_y=0,
        content_width=1200,
        content_height=720,
    )

    assert first == second


def test_render_search_results_treemap_bytes_outputs_png() -> None:
    page = load_search_treemap_fixture(FIXTURES_ROOT / "search_treemap_basic.json")

    image_bytes = render_search_results_treemap_bytes(page=page, locale="zh-CN")

    with Image.open(BytesIO(image_bytes)) as image:
        assert image.size == (TREEMAP_WIDTH, TREEMAP_HEIGHT)
        assert image.format == "PNG"


def test_render_search_results_treemap_returns_image_message() -> None:
    page = load_search_treemap_fixture(FIXTURES_ROOT / "search_treemap_basic.json")

    message = render_search_results_treemap(page=page, locale="zh-CN")

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"


def test_renderer_handles_long_and_multiline_response_cards() -> None:
    page = load_search_treemap_fixture(FIXTURES_ROOT / "search_treemap_stress.json")

    image_bytes = render_search_results_treemap_bytes(page=page, locale="zh-CN")

    assert len(image_bytes) > 10000


def test_renderer_can_fall_back_to_minimal_tiles() -> None:
    page = SearchTreemapPage(
        query=SearchTreemapQuery(
            keyword="极小块",
            field="trigger",
            creator_id="",
            has_image=False,
            page=1,
            total_count=12,
            limit=10,
        ),
        items=tuple(
            SearchTreemapItem(
                trigger_group_id=1000 + index,
                trigger_text=f"块{index}",
                status="approved",
                created_by="10001",
                response_count=1 if index < 11 else 8,
                responses=(
                    SearchTreemapResponseCard(
                        text="测试摘要",
                        created_by="10001",
                        weight=1,
                        rule="默认",
                    ),
                ),
            )
            for index in range(12)
        ),
    )

    image_bytes = SearchTreemapRenderer().render(page, locale="zh-CN")

    assert len(image_bytes) > 8000


def test_renderer_prefers_three_columns_for_wide_dense_tiles() -> None:
    renderer = SearchTreemapRenderer()
    responses = tuple(
        SearchTreemapResponseCard(
            text=f"响应 {index}",
            created_by="10001",
            weight=3,
            rule="默认",
            image_path="/tmp/example.webp",
            segments=(
                SearchTreemapResponseSegment(kind="text", text=f"响应 {index}"),
                SearchTreemapResponseSegment(
                    kind="image", image_path="/tmp/example.webp"
                ),
            ),
        )
        for index in range(8)
    )

    cols, shown = renderer._choose_card_layout(  # pyright: ignore[reportPrivateUsage]
        width=720,
        height=360,
        responses=responses,
        response_count=len(responses),
    )

    assert cols == 3
    assert shown >= 3


def test_renderer_uses_single_column_for_single_response() -> None:
    renderer = SearchTreemapRenderer()
    responses = (
        SearchTreemapResponseCard(
            text="晚安啦",
            created_by="10001",
            weight=3,
            rule="默认",
        ),
    )

    cols, shown = renderer._choose_card_layout(  # pyright: ignore[reportPrivateUsage]
        width=420,
        height=260,
        responses=responses,
        response_count=1,
    )

    assert cols == 1
    assert shown == 1


def test_renderer_fits_long_trigger_title_without_ellipsis() -> None:
    renderer = SearchTreemapRenderer()

    font, lines = renderer._fit_tile_title_layout(  # pyright: ignore[reportPrivateUsage]
        "晚安不许复读测试一下超长触发词布局",
        max_width=160,
        max_height=140,
    )

    assert font is not None
    assert "".join(lines) == "晚安不许复读测试一下超长触发词布局"
    assert all("..." not in line for line in lines)


def test_renderer_prefers_fewer_lines_for_short_trigger_title() -> None:
    renderer = SearchTreemapRenderer()

    _, lines = renderer._fit_tile_title_layout(  # pyright: ignore[reportPrivateUsage]
        "晚安，不许复读",
        max_width=100,
        max_height=160,
    )

    assert len(lines) <= 3


def test_renderer_uses_smaller_response_font_for_narrow_cards() -> None:
    renderer = SearchTreemapRenderer()

    compact_font = renderer._choose_response_title_font(  # pyright: ignore[reportPrivateUsage]
        "晚安不许复读",
        width=96,
        spacious=False,
        has_image=False,
    )
    regular_font = renderer._choose_response_title_font(  # pyright: ignore[reportPrivateUsage]
        "晚安不许复读",
        width=220,
        spacious=False,
        has_image=False,
    )

    assert renderer._line_height(compact_font) < renderer._line_height(regular_font)  # pyright: ignore[reportPrivateUsage]


def test_renderer_detects_single_text_response() -> None:
    renderer = SearchTreemapRenderer()
    response = SearchTreemapResponseCard(
        text="晚安啦",
        created_by="10001",
        weight=3,
        rule="默认",
    )

    assert (
        renderer._single_text_response_text(  # pyright: ignore[reportPrivateUsage]
            response,
            "zh-CN",
        )
        == "晚安啦"
    )


def test_renderer_ignores_multi_segment_response_for_single_text_fit() -> None:
    renderer = SearchTreemapRenderer()
    response = SearchTreemapResponseCard(
        text="晚安啦",
        created_by="10001",
        weight=3,
        rule="默认",
        segments=(
            SearchTreemapResponseSegment(kind="text", text="晚安啦"),
            SearchTreemapResponseSegment(kind="image", image_path="/tmp/a.webp"),
        ),
    )

    assert (
        renderer._single_text_response_text(  # pyright: ignore[reportPrivateUsage]
            response,
            "zh-CN",
        )
        is None
    )


def test_renderer_single_text_layout_prefers_larger_font_when_space_allows() -> None:
    renderer = SearchTreemapRenderer()

    sparse_font, sparse_lines = renderer._fit_single_text_response_layout(  # pyright: ignore[reportPrivateUsage]
        "晚安啦",
        max_width=220,
        max_height=180,
    )
    regular_font = renderer._choose_response_title_font(  # pyright: ignore[reportPrivateUsage]
        "晚安啦",
        width=220,
        spacious=True,
        has_image=False,
    )

    assert sparse_lines
    assert renderer._line_height(sparse_font) >= renderer._line_height(regular_font)  # pyright: ignore[reportPrivateUsage]


def test_renderer_single_text_layout_uses_larger_font_for_taller_cards() -> None:
    renderer = SearchTreemapRenderer()

    regular_font, _ = renderer._fit_single_text_response_layout(  # pyright: ignore[reportPrivateUsage]
        "晚安呢！",
        max_width=220,
        max_height=160,
    )
    large_font, _ = renderer._fit_single_text_response_layout(  # pyright: ignore[reportPrivateUsage]
        "晚安呢！",
        max_width=320,
        max_height=260,
    )

    assert renderer._line_height(large_font) > renderer._line_height(regular_font)  # pyright: ignore[reportPrivateUsage]


def test_renderer_single_text_initial_font_size_scales_with_available_space() -> None:
    renderer = SearchTreemapRenderer()

    compact = renderer._single_text_initial_font_size(  # pyright: ignore[reportPrivateUsage]
        text="晚安呢！",
        max_width=180,
        max_height=120,
    )
    large = renderer._single_text_initial_font_size(  # pyright: ignore[reportPrivateUsage]
        text="晚安呢！",
        max_width=320,
        max_height=260,
    )

    assert large > compact


def test_renderer_single_text_layout_fits_long_text_into_available_height() -> None:
    renderer = SearchTreemapRenderer()

    font, lines = renderer._fit_single_text_response_layout(  # pyright: ignore[reportPrivateUsage]
        "我很确定你没有睡，只是不想理我，我不想你带着情绪睡，那样对身体不好。",
        max_width=220,
        max_height=180,
    )

    assert lines
    assert len(lines) * renderer._line_height(font) <= 180  # pyright: ignore[reportPrivateUsage]


def test_renderer_regular_text_block_layout_falls_back_font_to_fit_height() -> None:
    renderer = SearchTreemapRenderer()

    font, lines = renderer._fit_lxgw_text_block_layout(  # pyright: ignore[reportPrivateUsage]
        "今日同您携手共进的是:（地狱把妹王）",
        max_width=120,
        max_height=72,
        preferred_size=24,
    )

    assert lines
    assert len(lines) * renderer._line_height(font) <= 72  # pyright: ignore[reportPrivateUsage]


def test_renderer_expands_masonry_layout_to_fill_column_height() -> None:
    renderer = SearchTreemapRenderer()
    responses = (
        SearchTreemapResponseCard(
            text="晚安",
            created_by="10001",
            weight=3,
            rule="默认",
        ),
        SearchTreemapResponseCard(
            text="好梦",
            created_by="10001",
            weight=3,
            rule="默认",
        ),
    )

    placements = renderer._build_masonry_layout(  # pyright: ignore[reportPrivateUsage]
        responses=responses,
        locale="zh-CN",
        x=10,
        y=20,
        width=220,
        height=320,
        cols=1,
    )
    expanded = renderer._expand_masonry_layout(  # pyright: ignore[reportPrivateUsage]
        placements,
        responses=responses,
        x=10,
        y=20,
        height=320,
        cols=1,
    )

    assert placements
    assert expanded[-1][1].y + expanded[-1][1].height == 340


def test_renderer_draws_fallback_card_when_masonry_cannot_place_response() -> None:
    renderer = SearchTreemapRenderer()
    response = SearchTreemapResponseCard(
        text="我很确定你没有睡，只是不想理我，我不想你带着情绪睡。",
        created_by="10001",
        weight=3,
        rule="默认",
    )
    tile = SearchTreemapTile(
        item=SearchTreemapItem(
            trigger_group_id=1,
            trigger_text="晚安小朋友",
            status="approved",
            created_by="10001",
            response_count=1,
            responses=(response,),
        ),
        rect=TreemapRect(x=0, y=0, width=180, height=120),
        raw_weight=1,
        normalized_weight=1,
    )
    image = Image.new("RGB", (220, 180), "#ffffff")
    draw = ImageDraw.Draw(image)

    renderer._draw_response_card_grid(  # pyright: ignore[reportPrivateUsage]
        image,
        draw,
        tile,
        "zh-CN",
        x=10,
        y=10,
        width=150,
        height=70,
    )

    assert image.getpixel((12, 12)) != (255, 255, 255)


def test_renderer_draws_dual_response_cards_side_by_side_when_wide() -> None:
    renderer = SearchTreemapRenderer()
    responses = (
        SearchTreemapResponseCard(
            text="晚安啦！",
            created_by="10001",
            weight=3,
            rule="默认",
        ),
        SearchTreemapResponseCard(
            text="明天见！",
            created_by="10002",
            weight=3,
            rule="默认",
        ),
    )
    image = Image.new("RGB", (420, 240), "#ffffff")
    draw = ImageDraw.Draw(image)

    renderer._draw_dual_response_cards(  # pyright: ignore[reportPrivateUsage]
        image,
        draw,
        responses=responses,
        locale="zh-CN",
        x=10,
        y=10,
        width=360,
        height=140,
    )

    assert image.getpixel((12, 12)) != (255, 255, 255)
    assert image.getpixel((200, 12)) != (255, 255, 255)


def test_renderer_draws_dual_response_cards_stacked_when_tall() -> None:
    renderer = SearchTreemapRenderer()
    responses = (
        SearchTreemapResponseCard(
            text="晚安啦！",
            created_by="10001",
            weight=3,
            rule="默认",
        ),
        SearchTreemapResponseCard(
            text="明天见！",
            created_by="10002",
            weight=3,
            rule="默认",
        ),
    )
    image = Image.new("RGB", (260, 360), "#ffffff")
    draw = ImageDraw.Draw(image)

    renderer._draw_dual_response_cards(  # pyright: ignore[reportPrivateUsage]
        image,
        draw,
        responses=responses,
        locale="zh-CN",
        x=10,
        y=10,
        width=180,
        height=220,
    )

    assert image.getpixel((12, 12)) != (255, 255, 255)
    assert image.getpixel((12, 130)) != (255, 255, 255)


def test_renderer_rejects_dual_response_layout_when_cards_do_not_fit() -> None:
    renderer = SearchTreemapRenderer()
    responses = (
        SearchTreemapResponseCard(
            text="今日同您携手共进的是:（弥命）",
            created_by="10001",
            weight=3,
            rule="用户 10001",
            image_path="/tmp/a.webp",
            segments=(
                SearchTreemapResponseSegment(
                    kind="text",
                    text="今日同您携手共进的是:（弥命）",
                ),
                SearchTreemapResponseSegment(kind="image", image_path="/tmp/a.webp"),
            ),
        ),
        SearchTreemapResponseCard(
            text="今日同您携手共进的是:（弥命）",
            created_by="10001",
            weight=3,
            rule="用户 10001",
            image_path="/tmp/b.webp",
            segments=(
                SearchTreemapResponseSegment(
                    kind="text",
                    text="今日同您携手共进的是:（弥命）",
                ),
                SearchTreemapResponseSegment(kind="image", image_path="/tmp/b.webp"),
            ),
        ),
    )
    rects = renderer._dual_response_rects(  # pyright: ignore[reportPrivateUsage]
        width=280,
        height=180,
    )

    assert rects is not None
    assert not renderer._can_use_dual_response_layout(  # pyright: ignore[reportPrivateUsage]
        responses=responses,
        locale="zh-CN",
        rects=rects,
    )


def test_renderer_strips_image_placeholder_when_preview_exists() -> None:
    renderer = SearchTreemapRenderer()

    normalized = renderer._normalize_response_text(  # pyright: ignore[reportPrivateUsage]
        "今天陪你的是 [图片x1]",
        "zh-CN",
        has_image_preview=True,
    )

    assert normalized == "今天陪你的是"


def test_renderer_keeps_image_only_cards_without_none_placeholder() -> None:
    renderer = SearchTreemapRenderer()

    normalized = renderer._normalize_response_text(  # pyright: ignore[reportPrivateUsage]
        "[图片x1]",
        "zh-CN",
        has_image_preview=True,
    )

    assert normalized == ""


def test_renderer_fits_preview_image_without_cropping(tmp_path: Path) -> None:
    renderer = SearchTreemapRenderer()
    source = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), "#ff6699").save(source)

    preview = renderer._fit_preview_image(  # pyright: ignore[reportPrivateUsage]
        str(source),
        max_width=120,
        max_height=120,
    )

    assert preview is not None
    assert preview.width == 120
    assert preview.height == 30


def test_renderer_estimates_sequence_image_height_from_aspect_ratio(
    tmp_path: Path,
) -> None:
    renderer = SearchTreemapRenderer()
    wide = tmp_path / "wide.png"
    tall = tmp_path / "tall.png"
    Image.new("RGB", (480, 160), "#ff6699").save(wide)
    Image.new("RGB", (160, 480), "#66ccff").save(tall)

    wide_height = renderer._preferred_sequence_image_height(  # pyright: ignore[reportPrivateUsage]
        180,
        image_path=str(wide),
    )
    tall_height = renderer._preferred_sequence_image_height(  # pyright: ignore[reportPrivateUsage]
        180,
        image_path=str(tall),
    )

    assert wide_height < tall_height
    assert wide_height >= 44


def test_renderer_uses_shorter_layout_image_height_for_mixed_content(
    tmp_path: Path,
) -> None:
    renderer = SearchTreemapRenderer()
    source = tmp_path / "tall.png"
    Image.new("RGB", (160, 480), "#66ccff").save(source)

    pure_height = renderer._estimate_layout_image_height(  # pyright: ignore[reportPrivateUsage]
        180,
        image_path=str(source),
        mixed_content=False,
    )
    mixed_height = renderer._estimate_layout_image_height(  # pyright: ignore[reportPrivateUsage]
        180,
        image_path=str(source),
        mixed_content=True,
    )

    assert mixed_height < pure_height


def test_renderer_draw_image_block_returns_real_preview_height(tmp_path: Path) -> None:
    renderer = SearchTreemapRenderer()
    source = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), "#ff6699").save(source)
    image = Image.new("RGB", (240, 240), "#ffffff")

    used = renderer._draw_image_block(  # pyright: ignore[reportPrivateUsage]
        image,
        ImageDraw.Draw(image),
        str(source),
        x=0,
        y=0,
        width=120,
        height=120,
    )

    assert used == 30


def test_renderer_estimates_taller_height_for_longer_content() -> None:
    renderer = SearchTreemapRenderer()
    short = SearchTreemapResponseCard(
        text="晚安",
        created_by="10001",
        weight=3,
        rule="默认",
    )
    long = SearchTreemapResponseCard(
        text="晚安晚安晚安晚安晚安晚安晚安晚安，今天也要早点休息，记得盖好被子。",
        created_by="10001",
        weight=3,
        rule="默认",
    )

    short_height = renderer._estimate_response_card_height(  # pyright: ignore[reportPrivateUsage]
        short,
        "zh-CN",
        width=220,
    )
    long_height = renderer._estimate_response_card_height(  # pyright: ignore[reportPrivateUsage]
        long,
        "zh-CN",
        width=220,
    )

    assert long_height > short_height


def test_renderer_builds_three_metadata_lines() -> None:
    renderer = SearchTreemapRenderer()
    response = SearchTreemapResponseCard(
        text="晚安",
        created_by="10001",
        weight=3,
        rule="默认",
    )

    lines = renderer._build_response_meta_lines(  # pyright: ignore[reportPrivateUsage]
        response,
        "zh-CN",
        font=renderer.card_meta_font,  # pyright: ignore[reportPrivateUsage]
        max_width=220,
    )

    assert lines == ["创建者 10001", "权重 3", "规则 默认"]


def test_renderer_measures_single_text_layout_height_for_card_estimate() -> None:
    renderer = SearchTreemapRenderer()
    response = SearchTreemapResponseCard(
        text="晚安啦",
        created_by="10001",
        weight=3,
        rule="默认",
    )

    measured = renderer._measure_response_content_height_for_layout(  # pyright: ignore[reportPrivateUsage]
        response,
        "zh-CN",
        font=renderer.card_title_font,  # pyright: ignore[reportPrivateUsage]
        width=220,
    )
    simple = renderer._estimate_response_content_height(  # pyright: ignore[reportPrivateUsage]
        response,
        "zh-CN",
        font=renderer.card_title_font,  # pyright: ignore[reportPrivateUsage]
        width=220,
    )

    assert measured >= simple


def test_renderer_vertical_layout_does_not_pin_single_text_metadata_to_bottom() -> None:
    renderer = SearchTreemapRenderer()

    layout = renderer._compute_response_card_vertical_layout(  # pyright: ignore[reportPrivateUsage]
        y=20,
        height=260,
        width=180,
        pad=12,
        content_height=72,
        meta_height=66,
        meta_gap=8,
        content_mode="single_text",
    )

    assert layout.meta_y < 20 + 260 - 12 - 66
