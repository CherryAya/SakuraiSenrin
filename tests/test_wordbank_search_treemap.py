from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message
from PIL import Image
import pytest

from src.lib.wordbank_search_treemap import (
    TREEMAP_HEIGHT,
    TREEMAP_WIDTH,
    SearchTreemapItem,
    SearchTreemapPage,
    SearchTreemapQuery,
    SearchTreemapRenderer,
    SearchTreemapResponseCard,
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
    assert shown >= 6


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
