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


@dataclass(slots=True, frozen=True)
class ResponseCardVerticalLayout:
    content_y: int
    content_height: int
    divider_y: int
    meta_y: int


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
        self._maple_font_cache: dict[int, Any] = {}
        self._lxgw_font_cache: dict[int, Any] = {}
        self._image_size_cache: dict[str, tuple[int, int] | None] = {}
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

        poster_tile = self._use_poster_tile_header(tile, locale, rect=rect)
        pad = min(
            TREEMAP_TILE_PADDING,
            max(10, min(rect.width // 8, rect.height // 8)),
        )
        if poster_tile:
            pad = min(pad, 8)
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

        if poster_tile:
            title_x = inner_x
            title_width = inner_width
            title_y = inner_y + number_height + 6
        else:
            title_x = inner_x + number_width + 10
            title_width = max(1, inner_width - badge_width - number_width - 22)
            title_y = inner_y
        title_budget = max(
            self._line_height(self.tile_small_title_font),
            rect.height - pad * 2 - (132 if poster_tile else 112),
        )
        if poster_tile:
            title_font, title_lines = self._fit_poster_tile_title_layout(
                tile.item.trigger_text or tr(locale, "wordbank.search_card.none"),
                max_width=title_width,
                max_height=title_budget,
            )
        else:
            title_font, title_lines = self._fit_tile_title_layout(
                tile.item.trigger_text or tr(locale, "wordbank.search_card.none"),
                max_width=title_width,
                max_height=title_budget,
            )
        cursor_y = title_y
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
        if tile.item.matched_by and rect.width >= 460:
            meta_text += "  命中 " + self._format_matched_by_label(
                tile.item.matched_by,
                locale,
            )
        if not poster_tile and rect.width >= 260 and rect.height >= 152:
            draw.text(
                (inner_x, cursor_y + 2),
                self._truncate_line(meta_text, self.tile_meta_font, inner_width),
                font=self.tile_meta_font,
                fill=self.MUTED,
            )
            cursor_y += self._line_height(self.tile_meta_font) + 4
        elif poster_tile:
            cursor_y += 4

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
        cursor_y += 8

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

    def _use_poster_tile_header(
        self,
        tile: SearchTreemapTile,
        locale: LocaleCode,
        *,
        rect: TreemapRect,
    ) -> bool:
        if len(tile.item.responses) != 1:
            return False
        if rect.width >= 220:
            return False
        if rect.height < max(260, rect.width + 90):
            return False
        return (
            self._single_text_response_text(tile.item.responses[0], locale) is not None
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
        if len(responses) == 1:
            self._draw_response_card(
                image,
                draw,
                responses[0],
                locale,
                x=x,
                y=y,
                width=width,
                height=grid_height,
            )
            total_hidden = max(0, tile.item.response_count - 1)
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
            return
        dual_rects = (
            self._dual_response_rects(width=width, height=grid_height)
            if len(responses) == 2 and tile.item.hidden_response_count <= 0
            else None
        )
        if dual_rects and self._can_use_dual_response_layout(
            responses=responses,
            locale=locale,
            rects=dual_rects,
        ):
            self._draw_dual_response_cards(
                image,
                draw,
                responses=responses,
                locale=locale,
                x=x,
                y=y,
                width=width,
                height=grid_height,
            )
            return
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
        placements = self._expand_masonry_layout(
            placements,
            responses=responses,
            x=x,
            y=y,
            height=grid_height,
            cols=cols,
        )
        shown_count = len(placements)
        if shown_count <= 0:
            if responses:
                self._draw_response_card(
                    image,
                    draw,
                    responses[0],
                    locale,
                    x=x,
                    y=y,
                    width=width,
                    height=grid_height,
                )
                shown_count = 1
            else:
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

    def _draw_dual_response_cards(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        responses: Sequence[SearchTreemapResponseCard],
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        rects = self._dual_response_rects(width=width, height=height)
        if rects is None:
            return
        for response, rect in zip(responses[:2], rects):
            self._draw_response_card(
                image,
                draw,
                response,
                locale,
                x=rect.x,
                y=rect.y,
                width=rect.width,
                height=rect.height,
            )

    def _dual_response_rects(
        self,
        *,
        width: int,
        height: int,
    ) -> tuple[TreemapRect, TreemapRect] | None:
        if width <= 0 or height <= 0:
            return None
        gap = 8
        if width >= max(320, height + 80):
            card_width = max(1, (width - gap) // 2)
            return (
                TreemapRect(x=0, y=0, width=card_width, height=height),
                TreemapRect(
                    x=card_width + gap,
                    y=0,
                    width=max(1, width - card_width - gap),
                    height=height,
                ),
            )
        card_height = max(1, (height - gap) // 2)
        return (
            TreemapRect(x=0, y=0, width=width, height=card_height),
            TreemapRect(
                x=0,
                y=card_height + gap,
                width=width,
                height=max(1, height - card_height - gap),
            ),
        )

    def _can_use_dual_response_layout(
        self,
        *,
        responses: Sequence[SearchTreemapResponseCard],
        locale: LocaleCode,
        rects: Sequence[TreemapRect],
    ) -> bool:
        if len(responses) < 2 or len(rects) < 2:
            return False
        for response, rect in zip(responses[:2], rects):
            estimated_height = self._estimate_response_card_height(
                response,
                locale,
                width=rect.width,
            )
            if estimated_height > rect.height:
                return False
        return True

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

    def _expand_masonry_layout(
        self,
        placements: Sequence[tuple[int, TreemapRect]],
        *,
        responses: Sequence[SearchTreemapResponseCard],
        x: int,
        y: int,
        height: int,
        cols: int,
    ) -> list[tuple[int, TreemapRect]]:
        if not placements or cols <= 0 or height <= 0:
            return list(placements)
        column_map: dict[int, list[tuple[int, TreemapRect]]] = {
            index: [] for index in range(cols)
        }
        for item_index, rect in placements:
            column = max(0, round((rect.x - x) / max(rect.width + 8, 1)))
            column_map[min(cols - 1, column)].append((item_index, rect))

        expanded: list[tuple[int, TreemapRect]] = []
        column_bottom = y + height
        for column in range(cols):
            entries = column_map.get(column, [])
            if not entries:
                continue
            used_bottom = max(rect.y + rect.height for _, rect in entries)
            leftover = column_bottom - used_bottom
            if leftover <= 2:
                expanded.extend(entries)
                continue
            weights = [
                max(
                    1,
                    self._estimate_card_flex_weight(responses[item_index], rect.height),
                )
                for item_index, rect in entries
            ]
            total_weight = sum(weights)
            if total_weight <= 0:
                expanded.extend(entries)
                continue
            cursor_y = entries[0][1].y
            consumed = 0
            column_expanded: list[tuple[int, TreemapRect]] = []
            weighted_entries = zip(entries, weights)
            for entry_index, ((item_index, rect), weight) in enumerate(
                weighted_entries
            ):
                extra = (
                    leftover - consumed
                    if entry_index == len(entries) - 1
                    else round(leftover * weight / total_weight)
                )
                consumed += extra
                new_rect = TreemapRect(
                    x=rect.x,
                    y=cursor_y,
                    width=rect.width,
                    height=rect.height + max(0, extra),
                )
                column_expanded.append((item_index, new_rect))
                cursor_y = new_rect.y + new_rect.height + 8
            if column_expanded:
                last_index, last_rect = column_expanded[-1]
                delta = column_bottom - (last_rect.y + last_rect.height)
                if delta != 0:
                    column_expanded[-1] = (
                        last_index,
                        TreemapRect(
                            x=last_rect.x,
                            y=last_rect.y,
                            width=last_rect.width,
                            height=max(1, last_rect.height + delta),
                        ),
                    )
            expanded.extend(column_expanded)
        expanded.sort(key=lambda item: (item[1].y, item[1].x))
        return expanded

    def _estimate_card_flex_weight(
        self,
        response: SearchTreemapResponseCard,
        estimated_height: int,
    ) -> int:
        segment_count = len(response.ordered_segments) or 1
        text_weight = max(1, len(response.visible_text.strip()) // 10)
        image_weight = 2 if response.has_image else 0
        base_weight = max(1, estimated_height // 48)
        return base_weight + segment_count + text_weight + image_weight

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
        single_text = self._single_text_response_text(response, locale)
        compact_card = len(normalized_text) <= 14 and len(response.rule) <= 10
        spacious_card = width >= 240
        narrow_text_card = single_text is not None and width < 176
        if narrow_text_card:
            pad = 4
        elif spacious_card and not compact_card:
            pad = 12
        else:
            pad = 8 if compact_card else 10
        meta_font = self._choose_response_meta_font(
            width=width,
            spacious=spacious_card,
            rule=response.rule,
        )
        title_font = self._choose_response_title_font(
            normalized_text,
            width=width - pad * 2,
            spacious=spacious_card,
            has_image=response.has_image,
        )
        title_line_height = self._line_height(title_font)
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
        content_height = self._measure_response_content_height_for_layout(
            response,
            locale,
            font=title_font,
            width=max(1, width - pad * 2),
        )
        meta_gap = 2 if narrow_text_card else (6 if compact_card else 8)
        base_height = pad * 2 + content_height + meta_gap + meta_height
        if response.has_image:
            minimum_content = max(
                58 if len(response.ordered_segments) <= 1 else 72,
                content_height,
            )
        elif single_text is not None:
            minimum_content = max(title_line_height + 6, content_height)
        else:
            minimum_content = max(
                title_line_height + (6 if compact_card else 10), content_height
            )
        minimum = pad * 2 + meta_gap + meta_height + minimum_content
        maximum = 248 if response.has_image else 212
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
                return self._preferred_sequence_image_height(
                    width,
                    image_path=segments[0].image_path,
                )
            rows = max(1, math.ceil(min(len(segments), 4) / 2))
            cell_width = max(1, (width - 6) // 2)
            cell_height = max(
                self._preferred_sequence_image_height(
                    cell_width,
                    image_path=segment.image_path,
                )
                for segment in segments[:4]
            )
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
                    max_lines=8 if width >= 220 else 6,
                )
                content_height += len(text_lines) * line_height
            else:
                content_height += self._preferred_sequence_image_height(
                    width,
                    image_path=segment.image_path,
                )
            if index < len(segments) - 1:
                content_height += 6
        return content_height

    def _measure_response_content_height_for_layout(
        self,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        width: int,
    ) -> int:
        single_text = self._single_text_response_text(response, locale)
        if single_text is not None:
            layout_font, lines = self._fit_single_text_response_layout(
                single_text,
                max_width=width,
                max_height=self._single_text_layout_height_cap(width, text=single_text),
            )
            return len(lines) * self._line_height(layout_font)

        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if not segments:
            return 0

        mixed_content = any(segment.kind == "text" for segment in segments) and any(
            segment.kind == "image" for segment in segments
        )
        content_height = 0
        for index, segment in enumerate(segments):
            if segment.kind == "text":
                content_height += self._measure_response_text_height_for_layout(
                    self._normalize_response_text(
                        segment.text,
                        locale,
                        has_image_preview=bool(response.primary_image_path),
                    ),
                    width=width,
                    preferred_size=getattr(font, "size", None),
                    has_image=response.has_image,
                )
            else:
                content_height += self._estimate_layout_image_height(
                    width,
                    image_path=segment.image_path,
                    mixed_content=mixed_content,
                )
            if index < len(segments) - 1:
                content_height += 6
        return content_height

    def _measure_response_text_height_for_layout(
        self,
        text: str,
        *,
        width: int,
        preferred_size: int | None,
        has_image: bool,
    ) -> int:
        fitted_font, lines = self._fit_lxgw_text_block_layout(
            text,
            max_width=width,
            max_height=self._layout_text_height_cap(
                width, has_image=has_image, text=text
            ),
            preferred_size=preferred_size,
        )
        return len(lines) * self._line_height(fitted_font)

    def _layout_text_height_cap(
        self,
        width: int,
        *,
        has_image: bool,
        text: str,
    ) -> int:
        if width < 120:
            base = 88
        elif width < 168:
            base = 108
        elif width < 224:
            base = 132
        else:
            base = 156
        if not has_image:
            base += 20
        if len(text.strip()) >= 28:
            base += 16
        return base

    def _single_text_layout_height_cap(self, width: int, *, text: str) -> int:
        if width < 120:
            base = 96
        elif width < 168:
            base = 118
        elif width < 224:
            base = 146
        else:
            base = 236
        if len(text.strip()) >= 18:
            base += 18
        elif len(text.strip()) <= 8:
            base += 42
        return base

    def _estimate_layout_image_height(
        self,
        width: int,
        *,
        image_path: str,
        mixed_content: bool,
    ) -> int:
        natural_height = self._preferred_sequence_image_height(
            width,
            image_path=image_path,
        )
        if not mixed_content:
            return natural_height
        mixed_floor = max(40, int(width * 0.24))
        mixed_ceiling = max(72, int(width * 0.72))
        return max(mixed_floor, min(mixed_ceiling, natural_height))

    def _preferred_sequence_image_height(self, width: int, *, image_path: str) -> int:
        if width <= 0:
            return 0
        image_size = self._load_image_size(image_path)
        if image_size is None:
            natural_height = int(width * 0.62)
        else:
            image_width, image_height = image_size
            natural_height = max(
                36,
                round(width * (image_height / max(image_width, 1))),
            )
        soft_floor = max(52, int(width * 0.26))
        if natural_height < soft_floor:
            natural_height = (natural_height + soft_floor) // 2
        soft_ceiling = max(120, int(width * 1.05))
        return max(44, min(soft_ceiling, natural_height))

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
        single_text = self._single_text_response_text(response, locale)
        compact_card = len(normalized_text) <= 14 and len(response.rule) <= 10
        spacious_card = width >= 240 and height >= 150
        narrow_text_card = single_text is not None and width < 176 and height >= 220
        if narrow_text_card:
            pad = 4
        elif spacious_card and not compact_card:
            pad = 12
        else:
            pad = 8 if compact_card or min(width, height) < 120 else 10
        meta_font = self._choose_response_meta_font(
            width=width,
            spacious=spacious_card,
            rule=response.rule,
        )
        title_font = self._choose_response_title_font(
            normalized_text,
            width=width - pad * 2,
            spacious=spacious_card,
            has_image=response.has_image,
        )
        meta_lines = self._build_response_meta_lines(
            response,
            locale,
            font=meta_font,
            max_width=max(1, width - pad * 2),
        )
        meta_line_height = self._line_height(meta_font)
        meta_gap = 2 if narrow_text_card else (6 if compact_card else 8)
        meta_height = (
            len(meta_lines) * meta_line_height + max(0, len(meta_lines) - 1) * 2
        )
        content_x = x + pad
        content_width = max(1, width - pad * 2)
        measured_content_height = max(
            1,
            self._measure_response_content_height_for_layout(
                response,
                locale,
                font=title_font,
                width=content_width,
            ),
        )
        content_mode = self._response_content_mode(
            response,
            locale,
            single_text=single_text,
        )
        card_layout = self._compute_response_card_vertical_layout(
            y=y,
            height=height,
            width=width,
            pad=pad,
            content_height=measured_content_height,
            meta_height=meta_height,
            meta_gap=meta_gap,
            content_mode=content_mode,
            narrow_text_card=narrow_text_card,
        )
        if single_text is not None:
            self._draw_fitted_single_text_response(
                draw,
                single_text,
                x=content_x,
                y=card_layout.content_y,
                width=content_width,
                height=card_layout.content_height,
            )
        else:
            self._draw_response_content(
                image,
                draw,
                response,
                locale,
                font=title_font,
                x=content_x,
                y=card_layout.content_y,
                width=content_width,
                height=card_layout.content_height,
            )
        if card_layout.divider_y > card_layout.content_y + 8:
            draw.line(
                (
                    content_x,
                    card_layout.divider_y,
                    content_x + content_width,
                    card_layout.divider_y,
                ),
                fill=self.DIVIDER,
                width=1,
            )
        cursor_y = card_layout.meta_y
        for line in meta_lines:
            if cursor_y + meta_line_height > y + height - pad + 2:
                break
            draw.text(
                (content_x, cursor_y),
                line,
                font=meta_font,
                fill=self.BODY,
            )
            cursor_y += meta_line_height + (1 if narrow_text_card else 2)

    def _response_content_mode(
        self,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        single_text: str | None,
    ) -> str:
        if single_text is not None:
            return "single_text"
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        has_text = any(segment.kind == "text" for segment in segments)
        has_image = any(segment.kind == "image" for segment in segments)
        if has_image and has_text:
            return "mixed"
        if has_image:
            return "image"
        normalized = self._normalize_response_text(
            response.visible_text,
            locale,
            has_image_preview=False,
        )
        return "text_short" if len(normalized) <= 24 else "text"

    def _compute_response_card_vertical_layout(
        self,
        *,
        y: int,
        height: int,
        width: int,
        pad: int,
        content_height: int,
        meta_height: int,
        meta_gap: int,
        content_mode: str,
        narrow_text_card: bool = False,
    ) -> ResponseCardVerticalLayout:
        inner_height = max(1, height - pad * 2)
        if narrow_text_card:
            divider_gap = 4
        else:
            divider_gap = 8 if inner_height >= 118 else 6
        max_content_height = max(1, inner_height - meta_height - meta_gap - divider_gap)
        clipped_content_height = min(max_content_height, max(1, content_height))
        block_height = clipped_content_height + divider_gap + meta_gap + meta_height
        spare_height = max(0, inner_height - block_height)
        bias = {
            "single_text": 0.52,
            "text_short": 0.44,
            "text": 0.27,
            "mixed": 0.20,
            "image": 0.16,
        }.get(content_mode, 0.24)
        if content_mode == "single_text" and height >= max(240, width + 56):
            bias = 0.66 if narrow_text_card else 0.60
        top_offset = round(spare_height * bias)
        content_y = y + pad + top_offset
        divider_y = content_y + clipped_content_height + divider_gap - 2
        meta_y = divider_y + meta_gap
        return ResponseCardVerticalLayout(
            content_y=content_y,
            content_height=clipped_content_height,
            divider_y=divider_y,
            meta_y=meta_y,
        )

    def _choose_response_meta_font(
        self,
        *,
        width: int,
        spacious: bool,
        rule: str,
    ) -> Any:
        if width < 176:
            return self._load_lxgw_font(15)
        if spacious and len(rule.strip()) <= 16:
            return self.card_large_meta_font
        return self.card_meta_font

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

    def _single_text_response_text(
        self,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
    ) -> str | None:
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if len(segments) != 1 or segments[0].kind != "text":
            return None
        text = self._normalize_response_text(
            segments[0].text,
            locale,
            has_image_preview=False,
        )
        return text or None

    def _draw_fitted_single_text_response(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        font, lines = self._fit_single_text_response_layout(
            text,
            max_width=width,
            max_height=height,
        )
        if not lines:
            return
        line_height = self._line_height(font)
        total_height = len(lines) * line_height
        if len(lines) <= 2:
            cursor_y = y + max(0, (height - total_height) // 2)
        elif total_height < int(height * 0.55):
            cursor_y = y + max(0, (height - total_height) // 3)
        else:
            cursor_y = y
        centered_lines = len(lines) <= 4 and len(text.strip()) <= 24
        for line in lines:
            line_x = x
            if centered_lines:
                line_x += max(0, (width - self._text_width(line, font)) // 2)
            draw.text((line_x, cursor_y), line, font=font, fill=self.CARD_ACCENT)
            cursor_y += line_height

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
        estimated_height = self._estimate_response_content_height(
            response,
            locale,
            font=font,
            width=width,
        )
        if estimated_height > 0 and estimated_height < height:
            spare_height = height - estimated_height
            if not response.has_image and len(response.visible_text.strip()) <= 20:
                offset = min(64, max(0, spare_height // 2))
            else:
                offset = min(
                    36 if response.has_image else 48,
                    max(0, spare_height // (3 if response.has_image else 2)),
                )
            y += offset
            height = max(1, height - offset)
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
                        44,
                        min(
                            available_height,
                            self._preferred_sequence_image_height(
                                width,
                                image_path=segment.image_path,
                            ),
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
        fitted_font, lines = self._fit_lxgw_text_block_layout(
            text,
            max_width=width,
            max_height=height,
            preferred_size=getattr(font, "size", None),
        )
        line_height = self._line_height(fitted_font)
        cursor_y = y
        for line in lines:
            if cursor_y + line_height > y + height + 2:
                break
            draw.text((x, cursor_y), line, font=fitted_font, fill=self.CARD_ACCENT)
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
        return preview.height

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
        if size not in self._maple_font_cache:
            try:
                self._maple_font_cache[size] = ImageFont.truetype(MAPLE_FONT_PATH, size)
            except Exception:
                self._maple_font_cache[size] = ImageFont.load_default()
        return self._maple_font_cache[size]

    def _load_lxgw_font(self, size: int) -> Any:
        if size not in self._lxgw_font_cache:
            try:
                self._lxgw_font_cache[size] = ImageFont.truetype(LXGW_FONG_PATH, size)
            except Exception:
                self._lxgw_font_cache[size] = ImageFont.load_default()
        return self._lxgw_font_cache[size]

    def _fit_tile_title_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[Any, list[str]]:
        safe_text = text.strip() or "?"
        if len(safe_text) <= 4:
            preferred_lines = 1
        elif len(safe_text) <= 8:
            preferred_lines = 2
        elif len(safe_text) <= 14:
            preferred_lines = 3
        else:
            preferred_lines = 4
        fallback_fit: tuple[Any, list[str]] | None = None
        for size in (28, 24, 20, 18, 16, 14, 12, 10):
            font = self._load_maple_font(size)
            line_height = self._line_height(font)
            max_lines = max(1, min(6, max_height // max(line_height, 1)))
            if max_lines <= 0:
                continue
            lines = self._wrap_text(
                safe_text,
                font,
                max_width,
                max_lines=max(1, len(safe_text)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                if len(lines) <= preferred_lines:
                    return font, lines
                if fallback_fit is None:
                    fallback_fit = (font, lines)
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = self._load_maple_font(10)
        fallback_line_height = self._line_height(fallback_font)
        fallback_max_lines = max(1, min(7, max_height // max(fallback_line_height, 1)))
        full_lines = self._wrap_text(
            safe_text,
            fallback_font,
            max_width,
            max_lines=max(1, len(safe_text)),
        )
        if len(full_lines) <= fallback_max_lines:
            return fallback_font, full_lines
        return fallback_font, full_lines[:fallback_max_lines]

    def _fit_poster_tile_title_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[Any, list[str]]:
        safe_text = text.strip() or "?"
        fallback_fit: tuple[Any, list[str]] | None = None
        for size in (20, 18, 16, 14, 12, 10):
            font = self._load_maple_font(size)
            line_height = self._line_height(font)
            max_lines = max(1, min(2, max_height // max(line_height, 1)))
            if max_lines <= 0:
                continue
            lines = self._wrap_text(
                safe_text,
                font,
                max_width,
                max_lines=max(1, len(safe_text)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                return font, lines
            if fallback_fit is None:
                fallback_fit = (font, lines[:max_lines])
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = self._load_maple_font(10)
        fallback_lines = self._wrap_text(
            safe_text,
            fallback_font,
            max_width,
            max_lines=2,
        )
        return fallback_font, fallback_lines[:2]

    def _choose_response_title_font(
        self,
        text: str,
        *,
        width: int,
        spacious: bool,
        has_image: bool,
    ) -> Any:
        normalized = text.strip()
        if not normalized:
            return self.card_large_title_font if spacious else self.card_title_font
        if has_image:
            if width < 112:
                candidate_sizes = (18, 16, 14)
            elif width < 148:
                candidate_sizes = (20, 18, 16, 14)
            elif width < 196:
                candidate_sizes = (22, 20, 18, 16)
            elif spacious:
                candidate_sizes = (28, 24, 22, 20, 18)
            else:
                candidate_sizes = (24, 22, 20, 18, 16)
        elif width < 112:
            candidate_sizes = (18, 16, 14)
        elif width < 148:
            candidate_sizes = (22, 20, 18, 16, 14)
        elif width < 196:
            candidate_sizes = (24, 22, 20, 18, 16)
        elif spacious and len(normalized) <= 12:
            candidate_sizes = (34, 30, 26, 24, 22, 20)
        elif spacious:
            candidate_sizes = (30, 26, 24, 22, 20, 18)
        else:
            candidate_sizes = (28, 24, 22, 20, 18, 16)
        max_lines = 3 if has_image else 5
        for size in candidate_sizes:
            font = self._load_lxgw_font(size)
            lines = self._wrap_text(
                normalized,
                font,
                max(1, width),
                max_lines=max(1, len(normalized)),
            )
            if len(lines) <= max_lines:
                return font
        return self._load_lxgw_font(candidate_sizes[-1])

    def _fit_single_text_response_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[Any, list[str]]:
        normalized = text.strip()
        if not normalized:
            return self.card_title_font, []
        largest_size = self._single_text_initial_font_size(
            text=normalized,
            max_width=max_width,
            max_height=max_height,
        )
        step = 2 if largest_size <= 44 else 4
        candidate_sizes = list(range(largest_size, 11, -step))
        if candidate_sizes[-1] != 12:
            candidate_sizes.append(12)
        if len(normalized) <= 4:
            preferred_lines = 1
        elif len(normalized) <= 8:
            preferred_lines = 2
        elif len(normalized) <= 16:
            preferred_lines = 3
        else:
            preferred_lines = 5
        fallback_fit: tuple[Any, list[str]] | None = None
        for size in candidate_sizes:
            font = self._load_lxgw_font(size)
            line_height = self._line_height(font)
            max_lines = max(1, min(12, max_height // max(line_height, 1)))
            lines = self._wrap_text(
                normalized,
                font,
                max_width,
                max_lines=max(1, len(normalized)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                if len(lines) <= preferred_lines:
                    return font, lines
                if fallback_fit is None:
                    fallback_fit = (font, lines)
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = self._load_lxgw_font(10)
        fallback_lines = self._wrap_text(
            normalized,
            fallback_font,
            max_width,
            max_lines=max(1, len(normalized)),
        )
        return fallback_font, fallback_lines

    def _single_text_initial_font_size(
        self,
        *,
        text: str,
        max_width: int,
        max_height: int,
    ) -> int:
        if len(text) <= 4:
            text_cap = 108
        elif len(text) <= 8:
            text_cap = 112
        elif len(text) <= 16:
            text_cap = 70
        else:
            text_cap = 34
        width_cap = max(
            24, int(max_width * 0.64 if len(text) <= 8 else max_width * 0.54)
        )
        height_cap = max(24, int(max_height * 0.80))
        return max(12, min(text_cap, width_cap, height_cap))

    def _fit_lxgw_text_block_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
        preferred_size: int | None,
    ) -> tuple[Any, list[str]]:
        normalized = text.strip()
        if not normalized:
            return self.card_title_font, []
        if preferred_size is None or preferred_size <= 0:
            preferred_size = 20
        candidate_sizes = list(range(preferred_size, 7, -2))
        if candidate_sizes[-1] != 8:
            candidate_sizes.append(8)
        fallback_fit: tuple[Any, list[str]] | None = None
        for size in candidate_sizes:
            font = self._load_lxgw_font(size)
            line_height = self._line_height(font)
            max_lines = max(1, max_height // max(line_height, 1))
            lines = self._wrap_text(
                normalized,
                font,
                max_width,
                max_lines=max(1, len(normalized)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                return font, lines
            if fallback_fit is None:
                fallback_fit = (font, lines)
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = self._load_lxgw_font(8)
        return (
            fallback_font,
            self._wrap_text(
                normalized,
                fallback_font,
                max_width,
                max_lines=max(1, len(normalized)),
            ),
        )

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

    def _load_image_size(self, image_path: str) -> tuple[int, int] | None:
        if not image_path:
            return None
        if image_path in self._image_size_cache:
            return self._image_size_cache[image_path]
        try:
            with Image.open(image_path) as source:
                size = (source.width, source.height)
        except Exception:
            size = None
        self._image_size_cache[image_path] = size
        return size


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
