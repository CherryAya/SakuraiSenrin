"""Standalone treemap rendering for wordbank search results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
import json
import math
from pathlib import Path
import re
from typing import Any

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont
import squarify

from src.lib.consts import LXGW_FONG_PATH, MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

TREEMAP_WIDTH = 1760
TREEMAP_HEIGHT = 1280
TREEMAP_MARGIN_X = 52
TREEMAP_MARGIN_Y = 44
TREEMAP_SUMMARY_GAP = 22
TREEMAP_SUMMARY_COLUMN_GAP = 44
TREEMAP_FOOTER_HEIGHT = 42
TREEMAP_TILE_GAP = 10
TREEMAP_TILE_PADDING = 18
TREEMAP_MIN_TILE_WIDTH = 80
TREEMAP_MIN_TILE_HEIGHT = 72
TREEMAP_LAYOUT_RESPONSE_CAP = 64
TREEMAP_LAYOUT_MIN_WEIGHT = 14
TREEMAP_LAYOUT_MAX_WEIGHT = 100
TREEMAP_TILE_TITLE_SINGLE_LINE_WIDTH = 240
TREEMAP_TILE_RESPONSE_MIN_WIDTH = 180
TREEMAP_TILE_RESPONSE_MIN_HEIGHT = 120

_IMAGE_PLACEHOLDER_RE = re.compile(r"\s*\[图片x\d+\]\s*")


@dataclass(slots=True, frozen=True)
class SearchTreemapQuery:
    keyword: str
    field: str
    creator_id: str
    has_image: bool
    page: int
    total_count: int
    limit: int

    @property
    def total_pages(self) -> int:
        if self.total_count <= 0:
            return 1
        return max(1, math.ceil(self.total_count / max(self.limit, 1)))


@dataclass(slots=True, frozen=True)
class SearchTreemapResponseSegment:
    kind: str
    text: str = ""
    image_path: str = ""


@dataclass(slots=True, frozen=True)
class SearchTreemapResponseCard:
    text: str
    created_by: str
    weight: int
    rule: str
    image_path: str = ""
    segments: tuple[SearchTreemapResponseSegment, ...] = ()

    @property
    def ordered_segments(self) -> tuple[SearchTreemapResponseSegment, ...]:
        if self.segments:
            return self.segments
        built: list[SearchTreemapResponseSegment] = []
        if self.text.strip():
            built.append(SearchTreemapResponseSegment(kind="text", text=self.text))
        if self.image_path:
            built.append(
                SearchTreemapResponseSegment(kind="image", image_path=self.image_path)
            )
        return tuple(built)

    @property
    def primary_image_path(self) -> str:
        for segment in self.ordered_segments:
            if segment.kind == "image" and segment.image_path:
                return segment.image_path
        return self.image_path

    @property
    def has_image(self) -> bool:
        return bool(self.primary_image_path)

    @property
    def visible_text(self) -> str:
        text_parts = [
            segment.text.strip()
            for segment in self.ordered_segments
            if segment.kind == "text" and segment.text.strip()
        ]
        if text_parts:
            return " ".join(text_parts)
        return self.text.strip()


@dataclass(slots=True, frozen=True)
class SearchTreemapItem:
    trigger_group_id: int
    trigger_text: str
    status: str
    created_by: str
    response_count: int
    responses: tuple[SearchTreemapResponseCard, ...]
    remaining_response_count: int = 0
    matched_by: str = ""

    @property
    def hidden_response_count(self) -> int:
        return max(0, self.response_count - len(self.responses))


@dataclass(slots=True, frozen=True)
class SearchTreemapPage:
    query: SearchTreemapQuery
    items: tuple[SearchTreemapItem, ...]


@dataclass(slots=True, frozen=True)
class TreemapRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(slots=True, frozen=True)
class SearchTreemapTile:
    item: SearchTreemapItem
    rect: TreemapRect
    raw_weight: int
    normalized_weight: int


def load_search_treemap_fixture(path: str | Path) -> SearchTreemapPage:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Search treemap fixture must be a JSON object")

    query_payload = payload.get("query")
    items_payload = payload.get("items")
    if not isinstance(query_payload, dict):
        raise ValueError("Search treemap fixture is missing object field: query")
    if not isinstance(items_payload, list):
        raise ValueError("Search treemap fixture is missing array field: items")

    query = SearchTreemapQuery(
        keyword=_require_str(query_payload, "keyword"),
        field=_require_str(query_payload, "field"),
        creator_id=_require_str(query_payload, "creator_id"),
        has_image=_require_bool(query_payload, "has_image"),
        page=_require_int(query_payload, "page", min_value=1),
        total_count=_require_int(query_payload, "total_count", min_value=0),
        limit=_require_int(query_payload, "limit", min_value=1),
    )
    items = tuple(
        _parse_treemap_item(item, index) for index, item in enumerate(items_payload)
    )
    return SearchTreemapPage(query=query, items=items)


def build_search_treemap_layout(
    page: SearchTreemapPage,
    *,
    content_x: int,
    content_y: int,
    content_width: int,
    content_height: int,
) -> tuple[SearchTreemapTile, ...]:
    if not page.items:
        return ()

    weights = [max(item.response_count, 1) for item in page.items]
    normalized = [_layout_weight_from_response_count(weight) for weight in weights]
    sizes = squarify.normalize_sizes(
        normalized,
        content_width,
        content_height,
    )
    rects = _build_rects_from_squarify(
        squarify.padded_squarify(
            sizes,
            content_x,
            content_y,
            content_width,
            content_height,
        )
    )
    return tuple(
        SearchTreemapTile(
            item=item,
            rect=rect,
            raw_weight=weight,
            normalized_weight=normalized_weight,
        )
        for item, rect, weight, normalized_weight in zip(
            page.items,
            rects,
            weights,
            normalized,
            strict=True,
        )
    )


class SearchTreemapRenderer:
    BG = "#FFF8FA"
    PANEL = "#FFF1F5"
    PANEL_ALT = (
        "#FFEAF1",
        "#FFF1E8",
        "#EEF6FF",
        "#F4F1FF",
        "#EFFBF5",
        "#FFF7DE",
    )
    BORDER = "#F0D5E0"
    HEADER = "#2E2533"
    BODY = "#4F4554"
    MUTED = "#867884"
    ACCENT = "#D96E95"
    BADGE_BG = "#FFFFFF"
    BADGE_TEXT = "#B44B70"
    NUMBER_BG = "#FFFDFE"
    NUMBER_TEXT = "#C25279"
    CARD_BG = "#FFFCFD"
    CARD_ACCENT = "#AF5477"
    DIVIDER = "#F2DCE5"

    def __init__(self) -> None:
        self.title_font = self._load_maple_font(38)
        self.summary_font = self._load_lxgw_font(22)
        self.summary_small_font = self._load_lxgw_font(20)
        self.tile_large_title_font = self._load_maple_font(28)
        self.tile_title_font = self._load_maple_font(24)
        self.tile_small_title_font = self._load_maple_font(20)
        self.tile_meta_font = self._load_lxgw_font(18)
        self.tile_badge_font = self._load_maple_font(18)
        self.tile_number_font = self._load_maple_font(18)
        self.card_large_title_font = self._load_lxgw_font(24)
        self.card_title_font = self._load_lxgw_font(20)
        self.card_large_meta_font = self._load_lxgw_font(18)
        self.card_meta_font = self._load_lxgw_font(16)

    def render(self, page: SearchTreemapPage, *, locale: LocaleCode) -> bytes:
        image = Image.new("RGB", (TREEMAP_WIDTH, TREEMAP_HEIGHT), self.BG)
        draw = ImageDraw.Draw(image)

        cursor_y = TREEMAP_MARGIN_Y
        cursor_y = self._draw_header(draw, page.query, locale, cursor_y)
        cursor_y += 18
        cursor_y = self._draw_summary(draw, page.query, locale, cursor_y)
        cursor_y += TREEMAP_SUMMARY_GAP

        content_y = cursor_y
        content_height = (
            TREEMAP_HEIGHT - content_y - TREEMAP_FOOTER_HEIGHT - TREEMAP_MARGIN_Y
        )
        content_width = TREEMAP_WIDTH - TREEMAP_MARGIN_X * 2

        if page.items:
            tiles = build_search_treemap_layout(
                page,
                content_x=TREEMAP_MARGIN_X,
                content_y=content_y,
                content_width=content_width,
                content_height=content_height,
            )
            for index, tile in enumerate(tiles):
                self._draw_tile(image, draw, tile, locale, index=index)
        else:
            self._draw_empty_state(
                draw,
                page.query,
                locale,
                x=TREEMAP_MARGIN_X,
                y=content_y,
                width=content_width,
                height=content_height,
            )

        self._draw_footer(draw, page.query, locale)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchTreemapQuery,
        locale: LocaleCode,
        y: int,
    ) -> int:
        title = tr(locale, "wordbank.search_card.title")
        page_text = tr(
            locale,
            "wordbank.search_card.page",
            page=query.page,
            total_pages=query.total_pages,
        )
        draw.text(
            (TREEMAP_MARGIN_X, y),
            title,
            font=self.title_font,
            fill=self.HEADER,
        )
        page_width = self._text_width(page_text, self.tile_meta_font)
        draw.text(
            (TREEMAP_WIDTH - TREEMAP_MARGIN_X - page_width, y + 10),
            page_text,
            font=self.tile_meta_font,
            fill=self.MUTED,
        )
        return y + self._line_height(self.title_font)

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchTreemapQuery,
        locale: LocaleCode,
        y: int,
    ) -> int:
        left_rows = (
            tr(
                locale,
                "wordbank.search_card.summary.field",
                field=self._field_label(query.field, locale),
            ),
            tr(
                locale,
                "wordbank.search_card.summary.keyword",
                keyword=query.keyword or tr(locale, "wordbank.search_card.none"),
            ),
        )
        right_rows = (
            tr(
                locale,
                "wordbank.search_card.summary.has_image",
                has_image=tr(
                    locale,
                    "wordbank.search_card.boolean.yes"
                    if query.has_image
                    else "wordbank.search_card.boolean.no",
                ),
            ),
            tr(
                locale,
                "wordbank.search_card.summary.creator",
                creator_id=query.creator_id or tr(locale, "wordbank.search_card.none"),
            ),
        )
        row_gap = 6
        row_height = self._line_height(self.summary_small_font)
        box_height = (
            18 + len(left_rows) * row_height + (len(left_rows) - 1) * row_gap + 18
        )
        draw.rectangle(
            (
                TREEMAP_MARGIN_X,
                y,
                TREEMAP_WIDTH - TREEMAP_MARGIN_X,
                y + box_height,
            ),
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        inner_x = TREEMAP_MARGIN_X + 24
        inner_width = TREEMAP_WIDTH - TREEMAP_MARGIN_X * 2 - 48
        column_width = max(1, (inner_width - TREEMAP_SUMMARY_COLUMN_GAP) // 2)
        row_y = y + 18
        for row in left_rows:
            draw.text(
                (inner_x, row_y),
                row,
                font=self.summary_small_font,
                fill=self.BODY,
            )
            row_y += row_height + row_gap
        row_y = y + 18
        for row in right_rows:
            draw.text(
                (inner_x + column_width + TREEMAP_SUMMARY_COLUMN_GAP, row_y),
                self._truncate_line(row, self.summary_small_font, column_width),
                font=self.summary_small_font,
                fill=self.BODY,
            )
            row_y += row_height + row_gap
        return y + box_height

    def _draw_empty_state(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchTreemapQuery,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        draw.rectangle(
            (x, y, x + width, y + height),
            fill="#FFFFFF",
            outline=self.BORDER,
            width=2,
        )
        draw.text(
            (x + 28, y + 36),
            tr(locale, "wordbank.search_card.empty", page=query.page),
            font=self.tile_title_font,
            fill=self.HEADER,
        )
        draw.text(
            (x + 28, y + 80),
            tr(locale, "wordbank.search_card.empty_hint"),
            font=self.summary_font,
            fill=self.MUTED,
        )

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchTreemapQuery,
        locale: LocaleCode,
    ) -> None:
        footer = tr(locale, "wordbank.search_card.total", total=query.total_count)
        if query.page < query.total_pages:
            footer += "  " + tr(
                locale,
                "wordbank.search_card.next_page",
                next_page=query.page + 1,
            )
        draw.text(
            (TREEMAP_MARGIN_X, TREEMAP_HEIGHT - TREEMAP_MARGIN_Y),
            footer,
            font=self.tile_meta_font,
            fill=self.MUTED,
            anchor="ls",
        )

    def _draw_tile(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        tile: SearchTreemapTile,
        locale: LocaleCode,
        *,
        index: int,
    ) -> None:
        rect = tile.rect
        if rect.width <= 0 or rect.height <= 0:
            return

        fill = self.PANEL_ALT[index % len(self.PANEL_ALT)]
        draw.rectangle(
            (
                rect.x,
                rect.y,
                rect.x + rect.width,
                rect.y + rect.height,
            ),
            fill=fill,
            outline=self.BORDER,
            width=2,
        )

        pad = min(
            TREEMAP_TILE_PADDING,
            max(10, min(rect.width // 8, rect.height // 8)),
        )
        inner_x = rect.x + pad
        inner_y = rect.y + pad
        inner_width = max(1, rect.width - pad * 2)
        number_text = self._format_item_number(index + 1)
        number_width = self._text_width(number_text, self.tile_number_font) + 20
        number_height = self._line_height(self.tile_number_font) + 4
        draw.rectangle(
            (
                inner_x,
                inner_y,
                inner_x + number_width,
                inner_y + number_height,
            ),
            fill=self.NUMBER_BG,
            outline=self.BORDER,
            width=1,
        )
        draw.text(
            (inner_x + 10, inner_y + 2),
            number_text,
            font=self.tile_number_font,
            fill=self.NUMBER_TEXT,
        )

        badge_text = f"x{tile.item.response_count}"
        badge_width = self._text_width(badge_text, self.tile_badge_font) + 20
        badge_height = self._line_height(self.tile_badge_font) + 4
        badge_x1 = rect.x + rect.width - pad - badge_width
        badge_y1 = rect.y + pad
        draw.rectangle(
            (
                badge_x1,
                badge_y1,
                badge_x1 + badge_width,
                badge_y1 + badge_height,
            ),
            fill=self.BADGE_BG,
            outline=self.BORDER,
            width=1,
        )
        draw.text(
            (badge_x1 + 10, badge_y1 + 2),
            badge_text,
            font=self.tile_badge_font,
            fill=self.BADGE_TEXT,
        )

        title_x = inner_x + number_width + 10
        title_width = max(1, inner_width - badge_width - number_width - 22)
        short_trigger = len(tile.item.trigger_text.strip()) <= 8
        if title_width >= 300 and rect.height >= 164 and short_trigger:
            title_font = self.tile_large_title_font
        elif title_width >= 170:
            title_font = self.tile_title_font
        else:
            title_font = self.tile_small_title_font
        title_max_lines = (
            2
            if title_width >= TREEMAP_TILE_TITLE_SINGLE_LINE_WIDTH
            and rect.height >= 132
            else 1
        )
        title_lines = (
            [
                self._truncate_line(
                    tile.item.trigger_text or tr(locale, "wordbank.search_card.none"),
                    title_font,
                    title_width,
                )
            ]
            if title_max_lines == 1
            else self._wrap_text(
                tile.item.trigger_text or tr(locale, "wordbank.search_card.none"),
                title_font,
                title_width,
                max_lines=title_max_lines,
            )
        )
        cursor_y = inner_y
        for line in title_lines:
            draw.text(
                (title_x, cursor_y),
                line,
                font=title_font,
                fill=self.HEADER,
            )
            cursor_y += self._line_height(title_font)

        meta_text = (
            tr(
                locale,
                "wordbank.search_card.group_id",
                group_id=tile.item.trigger_group_id,
            )
            + "  "
            + tr(locale, "wordbank.search_card.status", status=tile.item.status)
        )
        if rect.width >= 220 and rect.height >= 145:
            draw.text(
                (inner_x, cursor_y + 2),
                self._truncate_line(meta_text, self.tile_meta_font, inner_width),
                font=self.tile_meta_font,
                fill=self.MUTED,
            )
            cursor_y += self._line_height(self.tile_meta_font) + 6

        if (
            rect.width < TREEMAP_TILE_RESPONSE_MIN_WIDTH
            or rect.height < TREEMAP_TILE_RESPONSE_MIN_HEIGHT
        ):
            return

        draw.line(
            (
                inner_x,
                cursor_y,
                inner_x + inner_width,
                cursor_y,
            ),
            fill=self.DIVIDER,
            width=1,
        )
        cursor_y += 10

        bottom_limit = rect.y + rect.height - pad
        if cursor_y >= bottom_limit:
            return

        self._draw_response_card_grid(
            image,
            draw,
            tile,
            locale,
            x=inner_x,
            y=cursor_y,
            width=inner_width,
            height=max(1, bottom_limit - cursor_y),
        )

    def _field_label(self, field: str, locale: LocaleCode) -> str:
        return {
            "all": tr(locale, "wordbank.search_card.field.all"),
            "trigger": tr(locale, "wordbank.search_card.field.trigger"),
            "response": tr(locale, "wordbank.search_card.field.response"),
        }.get(field, field)

    def _format_item_number(self, number: int) -> str:
        return f"{number:02d}" if number < 100 else str(number)

    def _format_matched_by_label(self, value: str, locale: LocaleCode) -> str:
        if not value:
            return tr(locale, "wordbank.search_card.none")
        return {
            "text:mixed": "触发+响应",
            "text:trigger": tr(locale, "wordbank.search_card.field.trigger"),
            "text:response": tr(locale, "wordbank.search_card.field.response"),
            "text:group": "分组",
            "image:trigger": tr(locale, "wordbank.search_card.preview.trigger"),
            "image:response": tr(locale, "wordbank.search_card.preview.response"),
        }.get(value, value)

    def _normalize_text(self, text: str, locale: LocaleCode) -> str:
        cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return cleaned or tr(locale, "wordbank.search_card.none")

    def _normalize_response_text(
        self,
        text: str,
        locale: LocaleCode,
        *,
        has_image_preview: bool,
    ) -> str:
        candidate = text
        if has_image_preview:
            candidate = _IMAGE_PLACEHOLDER_RE.sub(" ", candidate)
        cleaned = " ".join(
            part.strip() for part in candidate.splitlines() if part.strip()
        )
        if cleaned:
            return cleaned
        if has_image_preview:
            return ""
        return tr(locale, "wordbank.search_card.none")

    def _draw_response_card_grid(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        tile: SearchTreemapTile,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        responses = tile.item.responses
        if width < 150 or height < 54:
            return

        hidden_count = tile.item.hidden_response_count
        overflow_height = 0
        if hidden_count > 0 and height >= 112:
            overflow_height = min(28, max(22, self._line_height(self.tile_meta_font)))

        grid_height = max(1, height - overflow_height)
        cols, _ = self._choose_card_layout(
            width=width,
            height=grid_height,
            responses=responses,
            response_count=len(responses),
            locale=locale,
        )
        placements = self._build_masonry_layout(
            responses=responses,
            locale=locale,
            x=x,
            y=y,
            width=width,
            height=grid_height,
            cols=cols,
        )
        shown_count = len(placements)
        if shown_count <= 0:
            self._draw_overflow_banner(
                draw,
                locale,
                x=x,
                y=y,
                width=width,
                height=height,
                hidden_count=hidden_count,
            )
            return

        for card_index, rect in placements:
            self._draw_response_card(
                image,
                draw,
                responses[card_index],
                locale,
                x=rect.x,
                y=rect.y,
                width=rect.width,
                height=rect.height,
            )

        total_hidden = max(0, tile.item.response_count - shown_count)
        if total_hidden > 0 and overflow_height > 0:
            self._draw_overflow_banner(
                draw,
                locale,
                x=x,
                y=y + height - overflow_height,
                width=width,
                height=overflow_height,
                hidden_count=total_hidden,
            )
        elif (
            total_hidden == 0 and width >= 220 and height >= 84 and tile.item.matched_by
        ):
            meta = self._truncate_line(
                self._format_matched_by_label(tile.item.matched_by, locale),
                self.tile_meta_font,
                width,
            )
            draw.text(
                (x, y + height - self._line_height(self.tile_meta_font)),
                meta,
                font=self.tile_meta_font,
                fill=self.MUTED,
            )

    def _choose_card_layout(
        self,
        *,
        width: int,
        height: int,
        responses: Sequence[SearchTreemapResponseCard],
        response_count: int,
        locale: LocaleCode = "zh-CN",
    ) -> tuple[int, int]:
        if response_count <= 0:
            return (1, 0)
        card_gap = 8
        sample = responses[: min(len(responses), 4)]
        has_images = any(response.has_image for response in sample)
        average_text_len = sum(
            len(response.visible_text) + len(response.rule) for response in sample
        ) / max(len(sample), 1)
        min_card_height = 94 if has_images else (58 if average_text_len <= 18 else 66)
        preferred_height = 120 if has_images else (76 if average_text_len <= 18 else 92)
        candidates: list[tuple[int, int, tuple[int, int, int]]] = []
        for cols in (3, 2, 1):
            if response_count < cols:
                continue
            if cols == 3 and width < (560 if has_images else 480):
                continue
            if cols == 2 and width < 320:
                continue
            card_width = (width - card_gap * (cols - 1)) // cols
            if card_width < (176 if has_images else 136):
                continue
            placements = self._build_masonry_layout(
                responses=responses[:response_count],
                locale=locale,
                x=0,
                y=0,
                width=width,
                height=height,
                cols=cols,
            )
            shown = len(placements)
            if shown <= 0:
                continue
            used_heights = [
                placement[1].y + placement[1].height for placement in placements
            ]
            card_height = sum(rect.height for _, rect in placements) // max(
                len(placements), 1
            )
            if card_height < min_card_height:
                continue
            column_bottom = max(used_heights, default=0)
            score = (shown, -abs(card_height - preferred_height), cols)
            if column_bottom > 0:
                score = (shown, -abs(card_height - preferred_height), -column_bottom)
            candidates.append((cols, shown, score))
        if not candidates:
            return (1, 1 if width >= 180 and height >= min_card_height else 0)
        cols, shown, _ = max(candidates, key=lambda item: item[2])
        return (cols, shown)

    def _build_masonry_layout(
        self,
        *,
        responses: Sequence[SearchTreemapResponseCard],
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
        cols: int,
    ) -> list[tuple[int, TreemapRect]]:
        if cols <= 0 or width <= 0 or height <= 0:
            return []
        card_gap = 8
        card_width = max(1, (width - card_gap * (cols - 1)) // cols)
        column_heights = [0] * cols
        placements: list[tuple[int, TreemapRect]] = []
        for index, response in enumerate(responses):
            estimated_height = self._estimate_response_card_height(
                response,
                locale,
                width=card_width,
            )
            column = min(range(cols), key=lambda item: column_heights[item])
            next_y = y + column_heights[column]
            if column_heights[column] > 0:
                next_y += card_gap
            if next_y + estimated_height > y + height:
                break
            rect = TreemapRect(
                x=x + column * (card_width + card_gap),
                y=next_y,
                width=card_width,
                height=estimated_height,
            )
            placements.append((index, rect))
            column_heights[column] = rect.y + rect.height - y
        return placements

    def _estimate_response_card_height(
        self,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        width: int,
    ) -> int:
        normalized_text = self._normalize_response_text(
            response.visible_text,
            locale,
            has_image_preview=response.has_image,
        )
        compact_card = len(normalized_text) <= 14 and len(response.rule) <= 10
        spacious_card = width >= 240
        pad = 12 if spacious_card and not compact_card else (8 if compact_card else 10)
        meta_font = (
            self.card_large_meta_font
            if spacious_card and len(response.rule.strip()) <= 16
            else self.card_meta_font
        )
        title_font = (
            self.card_large_title_font
            if spacious_card and len(normalized_text.strip()) <= 18
            else self.card_title_font
        )
        meta_lines = self._build_response_meta_lines(
            response,
            locale,
            font=meta_font,
            max_width=max(1, width - pad * 2),
        )
        meta_line_height = self._line_height(meta_font)
        meta_height = (
            len(meta_lines) * meta_line_height + max(0, len(meta_lines) - 1) * 2
        )
        content_height = self._estimate_response_content_height(
            response,
            locale,
            font=title_font,
            width=max(1, width - pad * 2),
        )
        base_height = pad * 2 + content_height + 8 + meta_height
        minimum = 92 if response.has_image else (66 if compact_card else 84)
        maximum = 260 if response.has_image else 220
        return max(minimum, min(maximum, base_height))

    def _estimate_response_content_height(
        self,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        width: int,
    ) -> int:
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if not segments:
            return 0
        line_height = self._line_height(font)
        if all(segment.kind == "image" for segment in segments):
            if len(segments) == 1 or width < 180:
                return max(92, min(176, int(width * 0.75)))
            rows = max(1, math.ceil(min(len(segments), 4) / 2))
            cell_height = max(64, min(120, width // 2))
            return rows * cell_height + (rows - 1) * 6
        content_height = 0
        for index, segment in enumerate(segments):
            if segment.kind == "text":
                text_lines = self._wrap_text(
                    self._normalize_response_text(
                        segment.text,
                        locale,
                        has_image_preview=bool(response.primary_image_path),
                    ),
                    font,
                    width,
                    max_lines=6,
                )
                content_height += len(text_lines) * line_height
            else:
                content_height += self._preferred_sequence_image_height(width)
            if index < len(segments) - 1:
                content_height += 6
        return content_height

    def _preferred_sequence_image_height(self, width: int) -> int:
        return max(84, min(196, int(width * 0.74)))

    def _draw_response_card(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        draw.rectangle(
            (x, y, x + width, y + height),
            fill=self.CARD_BG,
            outline=self.BORDER,
            width=1,
        )
        normalized_text = self._normalize_response_text(
            response.visible_text,
            locale,
            has_image_preview=response.has_image,
        )
        compact_card = len(normalized_text) <= 14 and len(response.rule) <= 10
        spacious_card = width >= 240 and height >= 150
        pad = (
            12
            if spacious_card and not compact_card
            else (8 if compact_card or min(width, height) < 120 else 10)
        )
        meta_font = (
            self.card_large_meta_font
            if spacious_card and len(response.rule.strip()) <= 16
            else self.card_meta_font
        )
        title_font = (
            self.card_large_title_font
            if spacious_card and len(normalized_text.strip()) <= 18
            else self.card_title_font
        )
        meta_lines = self._build_response_meta_lines(
            response,
            locale,
            font=meta_font,
            max_width=max(1, width - pad * 2),
        )
        meta_line_height = self._line_height(meta_font)
        meta_gap = 8
        meta_height = (
            len(meta_lines) * meta_line_height + max(0, len(meta_lines) - 1) * 2
        )
        content_x = x + pad
        content_y = y + pad
        content_width = max(1, width - pad * 2)
        content_height = max(1, height - pad * 2 - meta_gap - meta_height)
        self._draw_response_content(
            image,
            draw,
            response,
            locale,
            font=title_font,
            x=content_x,
            y=content_y,
            width=content_width,
            height=content_height,
        )
        divider_y = y + height - pad - meta_height - 4
        if divider_y > content_y + 8:
            draw.line(
                (content_x, divider_y, content_x + content_width, divider_y),
                fill=self.DIVIDER,
                width=1,
            )
        cursor_y = max(content_y + content_height + 6, y + height - pad - meta_height)
        for line in meta_lines:
            if cursor_y + meta_line_height > y + height - pad + 2:
                break
            draw.text(
                (content_x, cursor_y),
                line,
                font=meta_font,
                fill=self.BODY,
            )
            cursor_y += meta_line_height + 2

    def _build_response_meta_lines(
        self,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        max_width: int,
    ) -> list[str]:
        rule_text = self._normalize_text(response.rule, locale)
        return [
            self._truncate_line(
                f"创建者 {response.created_by}",
                font,
                max_width,
            ),
            self._truncate_line(
                f"权重 {response.weight}",
                font,
                max_width,
            ),
            self._truncate_line(
                f"规则 {rule_text}",
                font,
                max_width,
            ),
        ]

    def _draw_response_content(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if not segments:
            return
        text_segments = [segment for segment in segments if segment.kind == "text"]
        image_segments = [segment for segment in segments if segment.kind == "image"]
        if image_segments and not text_segments:
            self._draw_response_image_grid(
                image,
                draw,
                image_segments=image_segments,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            return
        self._draw_response_sequence_content(
            image,
            draw,
            response,
            locale,
            segments=segments,
            font=font,
            x=x,
            y=y,
            width=width,
            height=height,
        )

    def _draw_response_sequence_content(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        segments: Sequence[SearchTreemapResponseSegment],
        font: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        gap = 6
        cursor_y = y
        for index, segment in enumerate(segments):
            remaining_height = y + height - cursor_y
            if remaining_height <= 16:
                break
            remaining_count = len(segments) - index
            available_height = max(
                1, remaining_height - gap * max(0, remaining_count - 1)
            )
            if segment.kind == "text":
                used = self._draw_text_block(
                    draw,
                    self._normalize_response_text(
                        segment.text,
                        locale,
                        has_image_preview=bool(response.primary_image_path),
                    ),
                    font=font,
                    x=x,
                    y=cursor_y,
                    width=width,
                    height=available_height,
                )
            else:
                used = self._draw_image_block(
                    image,
                    draw,
                    segment.image_path,
                    x=x,
                    y=cursor_y,
                    width=width,
                    height=max(
                        72,
                        min(
                            available_height,
                            self._preferred_sequence_image_height(width),
                        ),
                    ),
                )
            if used <= 0:
                continue
            cursor_y += used + gap

    def _draw_response_image_grid(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        image_segments: Sequence[SearchTreemapResponseSegment],
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        if len(image_segments) == 1 or width < 180:
            self._draw_image_block(
                image,
                draw,
                image_segments[0].image_path,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            return
        gap = 6
        cols = 2
        rows = max(1, math.ceil(min(len(image_segments), 4) / cols))
        cell_width = max(1, (width - gap * (cols - 1)) // cols)
        cell_height = max(1, (height - gap * (rows - 1)) // rows)
        for index, segment in enumerate(image_segments[:4]):
            row = index // cols
            col = index % cols
            self._draw_image_block(
                image,
                draw,
                segment.image_path,
                x=x + col * (cell_width + gap),
                y=y + row * (cell_height + gap),
                width=cell_width,
                height=cell_height,
            )

    def _draw_text_block(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        font: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> int:
        if not text or width <= 0 or height <= 0:
            return 0
        line_height = self._line_height(font)
        max_lines = max(1, height // line_height)
        lines = self._wrap_text(text, font, width, max_lines=max_lines)
        cursor_y = y
        for line in lines:
            if cursor_y + line_height > y + height + 2:
                break
            draw.text((x, cursor_y), line, font=font, fill=self.CARD_ACCENT)
            cursor_y += line_height
        return max(0, cursor_y - y)

    def _draw_image_block(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        image_path: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> int:
        if not image_path or width <= 0 or height <= 0:
            return 0
        preview = self._fit_preview_image(
            image_path,
            max_width=width,
            max_height=height,
        )
        if preview is None:
            return 0
        offset_x = x + max(0, (width - preview.width) // 2)
        offset_y = y + max(0, (height - preview.height) // 2)
        image.paste(preview, (offset_x, offset_y))
        draw.rectangle(
            (
                offset_x,
                offset_y,
                offset_x + preview.width,
                offset_y + preview.height,
            ),
            outline=self.BORDER,
            width=1,
        )
        return height

    def _draw_overflow_banner(
        self,
        draw: ImageDraw.ImageDraw,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        hidden_count: int,
    ) -> None:
        if hidden_count <= 0:
            return
        draw.rectangle(
            (x, y, x + width, y + height),
            fill="#FFF5F8",
            outline=self.BORDER,
            width=1,
        )
        label = tr(
            locale,
            "wordbank.search_card.more_responses",
            count=hidden_count,
        ).strip()
        draw.text(
            (
                x + 10,
                y + max(2, (height - self._line_height(self.tile_meta_font)) // 2),
            ),
            self._truncate_line(label, self.tile_meta_font, max(1, width - 20)),
            font=self.tile_meta_font,
            fill=self.ACCENT,
        )

    def _draw_overflow_card(
        self,
        draw: ImageDraw.ImageDraw,
        tile: SearchTreemapTile,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        hidden_count: int,
    ) -> None:
        draw.rectangle(
            (x, y, x + width, y + height),
            fill="#FFF5F8",
            outline=self.BORDER,
            width=1,
        )
        pad = 10 if min(width, height) >= 120 else 8
        lines = (
            self._normalize_text(
                tr(
                    locale,
                    "wordbank.search_card.more_responses",
                    count=hidden_count,
                ).strip(),
                locale,
            ),
            f"总响应 {tile.item.response_count}",
            f"命中 {self._format_matched_by_label(tile.item.matched_by, locale)}",
        )
        cursor_y = y + pad
        for index, line in enumerate(lines):
            font = self.card_title_font if index == 0 else self.card_meta_font
            color = self.ACCENT if index == 0 else self.BODY
            wrapped = self._wrap_text(
                line,
                font,
                max(1, width - pad * 2),
                max_lines=2 if index == 0 else 1,
            )
            for item in wrapped:
                if cursor_y + self._line_height(font) > y + height - pad:
                    return
                draw.text(
                    (x + pad, cursor_y),
                    item,
                    font=font,
                    fill=color,
                )
                cursor_y += self._line_height(font)
            cursor_y += 2

    def _wrap_text(
        self,
        text: str,
        font: Any,
        max_width: int,
        *,
        max_lines: int,
    ) -> list[str]:
        if not text:
            return [""]
        if max_width <= 0:
            return [""]

        lines: list[str] = []
        for raw_line in text.splitlines() or [text]:
            current = ""
            for char in raw_line:
                candidate = f"{current}{char}"
                if self._text_width(candidate, font) <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = char
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                break
            if current:
                lines.append(current)
            if len(lines) >= max_lines:
                break
        if not lines:
            return [""]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if len(lines) == max_lines:
            lines[-1] = self._truncate_line(lines[-1], font, max_width)
        return lines

    def _truncate_line(self, text: str, font: Any, max_width: int) -> str:
        if self._text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and self._text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..." if candidate else "..."

    def _line_height(self, font: Any) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        return int(bbox[3] - bbox[1] + 4)

    def _text_width(self, text: str, font: Any) -> int:
        return int(
            ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(
                text,
                font=font,
            )
        )

    def _load_maple_font(self, size: int) -> Any:
        try:
            return ImageFont.truetype(MAPLE_FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()

    def _load_lxgw_font(self, size: int) -> Any:
        try:
            return ImageFont.truetype(LXGW_FONG_PATH, size)
        except Exception:
            return ImageFont.load_default()

    def _fit_preview_image(
        self,
        image_path: str,
        *,
        max_width: int,
        max_height: int,
    ) -> Image.Image | None:
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGB")
        except Exception:
            return None
        if max_width <= 0 or max_height <= 0:
            return None
        scale = min(max_width / image.width, max_height / image.height)
        resized = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGB", (resized.width, resized.height), "#FFFFFF")
        canvas.paste(resized, (0, 0))
        return canvas


def render_search_results_treemap_bytes(
    *,
    page: SearchTreemapPage,
    locale: LocaleCode,
) -> bytes:
    return SearchTreemapRenderer().render(page, locale=locale)


def render_search_results_treemap(
    *,
    page: SearchTreemapPage,
    locale: LocaleCode,
) -> Message:
    return Message(
        MessageSegment.image(
            render_search_results_treemap_bytes(page=page, locale=locale)
        )
    )


def _parse_treemap_item(payload: object, index: int) -> SearchTreemapItem:
    if not isinstance(payload, dict):
        raise ValueError(f"Search treemap item at index {index} must be an object")
    response_count = _require_int(payload, "response_count", min_value=1)
    raw_responses = payload.get("responses")
    if not isinstance(raw_responses, list):
        raise ValueError(
            f"Search treemap item at index {index} is missing array field: responses"
        )
    responses = tuple(
        _parse_response_card(item, item_index, parent_index=index)
        for item_index, item in enumerate(raw_responses)
    )
    if len(responses) > response_count:
        raise ValueError(
            "Search treemap item at index "
            f"{index} has more response cards than response_count"
        )
    remaining = payload.get("remaining_response_count")
    remaining_count = (
        _coerce_int(remaining, field_name="remaining_response_count", min_value=0)
        if remaining is not None
        else max(0, response_count - len(responses))
    )
    return SearchTreemapItem(
        trigger_group_id=_require_int(payload, "trigger_group_id", min_value=1),
        trigger_text=_require_str(payload, "trigger_text"),
        status=_require_str(payload, "status"),
        created_by=_require_str(payload, "created_by"),
        response_count=response_count,
        responses=responses,
        remaining_response_count=remaining_count,
        matched_by=str(payload.get("matched_by", "") or ""),
    )


def _parse_response_card(
    payload: object,
    index: int,
    *,
    parent_index: int,
) -> SearchTreemapResponseCard:
    if not isinstance(payload, dict):
        raise ValueError(
            "Search treemap response card at item index "
            f"{parent_index}, response index {index} must be an object"
        )
    raw_segments = payload.get("segments")
    segments = (
        tuple(
            _parse_response_segment(item, item_index, parent_index=parent_index)
            for item_index, item in enumerate(raw_segments)
        )
        if isinstance(raw_segments, list)
        else ()
    )
    return SearchTreemapResponseCard(
        text=_require_str(payload, "text"),
        created_by=_require_str(payload, "created_by"),
        weight=_require_int(payload, "weight", min_value=0),
        rule=_require_str(payload, "rule"),
        image_path=str(payload.get("image_path", "") or ""),
        segments=segments,
    )


def _parse_response_segment(
    payload: object,
    index: int,
    *,
    parent_index: int,
) -> SearchTreemapResponseSegment:
    if not isinstance(payload, dict):
        raise ValueError(
            "Search treemap response segment at item index "
            f"{parent_index}, segment index {index} must be an object"
        )
    kind = _require_str(payload, "kind")
    if kind not in {"text", "image"}:
        raise ValueError(f"Unsupported response segment kind: {kind}")
    return SearchTreemapResponseSegment(
        kind=kind,
        text=str(payload.get("text", "") or ""),
        image_path=str(payload.get("image_path", "") or ""),
    )


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Search treemap fixture field must be string: {key}")
    return value


def _require_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Search treemap fixture field must be bool: {key}")
    return value


def _require_int(
    payload: dict[str, object],
    key: str,
    *,
    min_value: int,
) -> int:
    return _coerce_int(payload.get(key), field_name=key, min_value=min_value)


def _coerce_int(value: object, *, field_name: str, min_value: int) -> int:
    if not isinstance(value, int):
        raise ValueError(f"Search treemap fixture field must be int: {field_name}")
    if value < min_value:
        raise ValueError(
            f"Search treemap fixture field must be >= {min_value}: {field_name}"
        )
    return value


def _layout_weight_from_response_count(response_count: int) -> int:
    clamped = min(max(response_count, 1), TREEMAP_LAYOUT_RESPONSE_CAP)
    ratio = (clamped - 1) / max(TREEMAP_LAYOUT_RESPONSE_CAP - 1, 1)
    return round(
        TREEMAP_LAYOUT_MIN_WEIGHT
        + ratio * (TREEMAP_LAYOUT_MAX_WEIGHT - TREEMAP_LAYOUT_MIN_WEIGHT)
    )


def _build_rects_from_squarify(
    rects: Sequence[dict[str, float]],
) -> list[TreemapRect]:
    built: list[TreemapRect] = []
    for rect in rects:
        width = max(1, round(rect["dx"]))
        height = max(1, round(rect["dy"]))
        built.append(
            TreemapRect(
                x=round(rect["x"]),
                y=round(rect["y"]),
                width=width,
                height=height,
            )
        )
    return built
