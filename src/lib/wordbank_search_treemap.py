"""Standalone treemap rendering for wordbank search results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import reduce
from io import BytesIO
import json
import math
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont
import squarify

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

TREEMAP_WIDTH = 1560
TREEMAP_HEIGHT = 1080
TREEMAP_MARGIN_X = 52
TREEMAP_MARGIN_Y = 44
TREEMAP_SUMMARY_GAP = 22
TREEMAP_FOOTER_HEIGHT = 42
TREEMAP_TILE_GAP = 10
TREEMAP_TILE_PADDING = 18
TREEMAP_MIN_TILE_WIDTH = 80
TREEMAP_MIN_TILE_HEIGHT = 72


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
class SearchTreemapItem:
    trigger_group_id: int
    trigger_text: str
    status: str
    created_by: str
    response_count: int
    response_summaries: tuple[str, ...]
    remaining_response_count: int = 0
    matched_by: str = ""

    @property
    def display_summaries(self) -> tuple[str, ...]:
        return self.response_summaries


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
    divisor = _gcd_many(weights)
    normalized = [max(1, weight // divisor) for weight in weights]
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
    BULLET = "#B85B7F"
    DIVIDER = "#F2DCE5"

    def __init__(self) -> None:
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(22)
        self.tile_title_font = self._load_font(24)
        self.tile_meta_font = self._load_font(18)
        self.tile_body_font = self._load_font(18)
        self.tile_badge_font = self._load_font(18)

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
        page_width = self._text_width(page_text, self.summary_font)
        draw.text(
            (TREEMAP_WIDTH - TREEMAP_MARGIN_X - page_width, y + 10),
            page_text,
            font=self.summary_font,
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
        rows = (
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
        row_height = self._line_height(self.summary_font)
        box_height = 22 + len(rows) * row_height + (len(rows) - 1) * 8 + 24
        draw.rounded_rectangle(
            (
                TREEMAP_MARGIN_X,
                y,
                TREEMAP_WIDTH - TREEMAP_MARGIN_X,
                y + box_height,
            ),
            radius=28,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        row_y = y + 22
        for row_index, row in enumerate(rows):
            draw.text(
                (TREEMAP_MARGIN_X + 24, row_y),
                row,
                font=self.summary_font,
                fill=self.BODY,
            )
            row_y += row_height + 8
            if row_index == 1:
                row_y += 2
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
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=32,
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
        radius = max(12, min(26, min(rect.width, rect.height) // 7))
        draw.rounded_rectangle(
            (
                rect.x,
                rect.y,
                rect.x + rect.width,
                rect.y + rect.height,
            ),
            radius=radius,
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
        badge_text = f"x{tile.item.response_count}"
        badge_width = self._text_width(badge_text, self.tile_badge_font) + 20
        badge_height = self._line_height(self.tile_badge_font) + 4
        badge_x1 = rect.x + rect.width - pad - badge_width
        badge_y1 = rect.y + pad
        draw.rounded_rectangle(
            (
                badge_x1,
                badge_y1,
                badge_x1 + badge_width,
                badge_y1 + badge_height,
            ),
            radius=16,
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

        title_width = max(1, inner_width - badge_width - 12)
        title_lines = self._wrap_text(
            tile.item.trigger_text or tr(locale, "wordbank.search_card.none"),
            self.tile_title_font,
            title_width,
            max_lines=2,
        )
        cursor_y = inner_y
        for line in title_lines:
            draw.text(
                (inner_x, cursor_y),
                line,
                font=self.tile_title_font,
                fill=self.HEADER,
            )
            cursor_y += self._line_height(self.tile_title_font)

        meta_text = (
            tr(
                locale,
                "wordbank.search_card.group_id",
                group_id=tile.item.trigger_group_id,
            )
            + "  "
            + tr(locale, "wordbank.search_card.status", status=tile.item.status)
        )
        if rect.width >= 180 and rect.height >= 120:
            draw.text(
                (inner_x, cursor_y + 2),
                self._truncate_line(meta_text, self.tile_meta_font, inner_width),
                font=self.tile_meta_font,
                fill=self.MUTED,
            )
            cursor_y += self._line_height(self.tile_meta_font) + 8

        if rect.width < 170 or rect.height < 112:
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

        shown = 0
        bottom_limit = rect.y + rect.height - pad
        summary_candidates = tile.item.display_summaries
        response_max_lines = 2 if rect.width >= 260 and rect.height >= 180 else 1
        for summary in summary_candidates:
            response_lines = self._wrap_text(
                f"• {self._normalize_summary(summary, locale)}",
                self.tile_body_font,
                inner_width,
                max_lines=response_max_lines,
            )
            needed_height = (
                len(response_lines) * self._line_height(self.tile_body_font) + 8
            )
            if cursor_y + needed_height > bottom_limit - self._line_height(
                self.tile_meta_font
            ):
                break
            for line in response_lines:
                draw.text(
                    (inner_x, cursor_y),
                    line,
                    font=self.tile_body_font,
                    fill=self.BODY if shown else self.BULLET,
                )
                cursor_y += self._line_height(self.tile_body_font)
            cursor_y += 8
            shown += 1

        hidden_count = (
            max(0, len(summary_candidates) - shown) + tile.item.remaining_response_count
        )
        capsule_drawn = False
        if hidden_count > 0:
            capsule_drawn = self._draw_hidden_response_capsules(
                draw,
                x=inner_x,
                y=cursor_y + 4,
                width=inner_width,
                bottom_limit=bottom_limit - 2,
                count=hidden_count,
            )
        if (
            hidden_count > 0
            and not capsule_drawn
            and cursor_y + self._line_height(self.tile_meta_font) <= bottom_limit
        ):
            suffix = tr(
                locale,
                "wordbank.search_card.more_responses",
                count=hidden_count,
            ).strip()
            draw.text(
                (inner_x, bottom_limit - self._line_height(self.tile_meta_font)),
                suffix,
                font=self.tile_meta_font,
                fill=self.ACCENT,
            )
        if hidden_count == 0 and rect.width >= 240 and rect.height >= 150:
            footer_meta = self._truncate_line(
                f"@{tile.item.created_by}  {tile.item.matched_by or '-'}",
                self.tile_meta_font,
                inner_width,
            )
            footer_y = bottom_limit - self._line_height(self.tile_meta_font)
            if footer_y > cursor_y + 6:
                draw.text(
                    (inner_x, footer_y),
                    footer_meta,
                    font=self.tile_meta_font,
                    fill=self.MUTED,
                )

    def _field_label(self, field: str, locale: LocaleCode) -> str:
        return {
            "all": tr(locale, "wordbank.search_card.field.all"),
            "trigger": tr(locale, "wordbank.search_card.field.trigger"),
            "response": tr(locale, "wordbank.search_card.field.response"),
        }.get(field, field)

    def _normalize_summary(self, summary: str, locale: LocaleCode) -> str:
        cleaned = " ".join(
            part.strip() for part in summary.splitlines() if part.strip()
        )
        return cleaned or tr(locale, "wordbank.search_card.none")

    def _draw_hidden_response_capsules(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        width: int,
        bottom_limit: int,
        count: int,
    ) -> bool:
        chip_height = self._line_height(self.tile_meta_font) + 4
        chip_gap_x = 8
        chip_gap_y = 8
        chip_width = 62
        available_height = bottom_limit - y
        if width < 180 or available_height < chip_height * 2 + chip_gap_y:
            return False

        cols = max(1, (width + chip_gap_x) // (chip_width + chip_gap_x))
        rows = max(1, (available_height + chip_gap_y) // (chip_height + chip_gap_y))
        capacity = cols * rows
        if capacity < 2:
            return False

        visible = min(count, capacity)
        chip_index = 0
        for row in range(rows):
            for col in range(cols):
                if chip_index >= visible:
                    return True
                chip_x = x + col * (chip_width + chip_gap_x)
                chip_y = y + row * (chip_height + chip_gap_y)
                if chip_y + chip_height > bottom_limit:
                    return True
                is_overflow_chip = chip_index == visible - 1 and count > capacity
                label = (
                    f"+{count - capacity + 1}"
                    if is_overflow_chip
                    else f"R{chip_index + 1}"
                )
                draw.rounded_rectangle(
                    (
                        chip_x,
                        chip_y,
                        chip_x + chip_width,
                        chip_y + chip_height,
                    ),
                    radius=12,
                    fill="#FFFDFE",
                    outline=self.BORDER,
                    width=1,
                )
                draw.text(
                    (chip_x + 10, chip_y + 2),
                    label,
                    font=self.tile_meta_font,
                    fill=self.ACCENT if is_overflow_chip else self.MUTED,
                )
                chip_index += 1
        return True

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

    def _load_font(self, size: int) -> Any:
        try:
            return ImageFont.truetype(MAPLE_FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()


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
    raw_summaries = payload.get("response_summaries")
    if not isinstance(raw_summaries, list):
        raise ValueError(
            "Search treemap item at index "
            f"{index} is missing array field: response_summaries"
        )
    summaries = tuple(str(item) for item in raw_summaries if str(item))
    remaining = payload.get("remaining_response_count")
    remaining_count = (
        _coerce_int(remaining, field_name="remaining_response_count", min_value=0)
        if remaining is not None
        else max(0, response_count - len(summaries))
    )
    return SearchTreemapItem(
        trigger_group_id=_require_int(payload, "trigger_group_id", min_value=1),
        trigger_text=_require_str(payload, "trigger_text"),
        status=_require_str(payload, "status"),
        created_by=_require_str(payload, "created_by"),
        response_count=response_count,
        response_summaries=summaries,
        remaining_response_count=remaining_count,
        matched_by=str(payload.get("matched_by", "") or ""),
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


def _gcd_many(values: Sequence[int]) -> int:
    positive = [value for value in values if value > 0]
    return reduce(math.gcd, positive, positive[0]) if positive else 1


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
