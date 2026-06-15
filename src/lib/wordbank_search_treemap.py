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
class SearchTreemapResponseCard:
    text: str
    created_by: str
    weight: int
    rule: str


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
    CARD_BG = "#FFFCFD"
    CARD_ACCENT = "#AF5477"
    DIVIDER = "#F2DCE5"

    def __init__(self) -> None:
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(22)
        self.tile_title_font = self._load_font(24)
        self.tile_meta_font = self._load_font(18)
        self.tile_body_font = self._load_font(18)
        self.tile_badge_font = self._load_font(18)
        self.card_title_font = self._load_font(19)
        self.card_meta_font = self._load_font(15)

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

        bottom_limit = rect.y + rect.height - pad
        if cursor_y >= bottom_limit:
            return

        self._draw_response_card_grid(
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

    def _normalize_text(self, text: str, locale: LocaleCode) -> str:
        cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return cleaned or tr(locale, "wordbank.search_card.none")

    def _draw_response_card_grid(
        self,
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
            overflow_height = min(
                30, max(24, self._line_height(self.tile_meta_font) + 4)
            )

        grid_height = max(1, height - overflow_height)
        cols, max_cards = self._choose_card_layout(
            width=width,
            height=grid_height,
            response_count=len(responses) + (1 if hidden_count > 0 else 0),
        )
        overflow_as_card = hidden_count > 0 and max_cards > len(responses)
        shown_count = min(len(responses), max_cards - (1 if overflow_as_card else 0))
        display_count = shown_count + (1 if overflow_as_card else 0)
        if display_count <= 0:
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

        rows = max(1, math.ceil(display_count / cols))
        card_gap = 8
        card_width = max(1, (width - card_gap * (cols - 1)) // cols)
        card_height = max(1, (grid_height - card_gap * (rows - 1)) // rows)
        for card_index, response in enumerate(responses[:shown_count]):
            row = card_index // cols
            col = card_index % cols
            card_x = x + col * (card_width + card_gap)
            card_y = y + row * (card_height + card_gap)
            self._draw_response_card(
                draw,
                response,
                locale,
                x=card_x,
                y=card_y,
                width=card_width,
                height=card_height,
            )

        if overflow_as_card:
            overflow_index = shown_count
            row = overflow_index // cols
            col = overflow_index % cols
            card_x = x + col * (card_width + card_gap)
            card_y = y + row * (card_height + card_gap)
            self._draw_overflow_card(
                draw,
                tile,
                locale,
                x=card_x,
                y=card_y,
                width=card_width,
                height=card_height,
                hidden_count=max(0, tile.item.response_count - shown_count),
            )

        total_hidden = max(0, tile.item.response_count - shown_count)
        if total_hidden > 0 and overflow_height > 0 and not overflow_as_card:
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
                tile.item.matched_by,
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
        response_count: int,
    ) -> tuple[int, int]:
        if response_count <= 0:
            return (1, 0)
        card_gap = 8
        candidates: list[tuple[int, int, tuple[int, int, int]]] = []
        for cols in (2, 1):
            if cols == 2 and width < 340:
                continue
            card_width = (width - card_gap * (cols - 1)) // cols
            if card_width < 150:
                continue
            max_rows = min(5, max(1, (height + card_gap) // (72 + card_gap)))
            shown = min(response_count, cols * max_rows)
            rows = max(1, math.ceil(shown / cols))
            card_height = (height - card_gap * (rows - 1)) // rows
            if card_height < 72:
                continue
            score = (shown, -abs(card_height - 112), cols)
            candidates.append((cols, shown, score))
        if not candidates:
            return (1, 1 if width >= 180 and height >= 90 else 0)
        cols, shown, _ = max(candidates, key=lambda item: item[2])
        return (cols, shown)

    def _draw_response_card(
        self,
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
        pad = 10 if min(width, height) >= 120 else 8
        text_max_lines = 3 if height >= 138 else 2
        title_lines = self._wrap_text(
            self._normalize_text(response.text, locale),
            self.card_title_font,
            max(1, width - pad * 2),
            max_lines=text_max_lines,
        )
        cursor_y = y + pad
        for line in title_lines:
            draw.text(
                (x + pad, cursor_y),
                line,
                font=self.card_title_font,
                fill=self.CARD_ACCENT,
            )
            cursor_y += self._line_height(self.card_title_font)

        cursor_y += 2
        meta_width = max(1, width - pad * 2)
        for label, value in (
            ("创建者", response.created_by),
            ("权重", str(response.weight)),
            ("规则", response.rule),
        ):
            if cursor_y + self._line_height(self.card_meta_font) > y + height - pad:
                break
            line = self._truncate_line(
                f"{label} {self._normalize_text(value, locale)}",
                self.card_meta_font,
                meta_width,
            )
            draw.text(
                (x + pad, cursor_y),
                line,
                font=self.card_meta_font,
                fill=self.BODY,
            )
            cursor_y += self._line_height(self.card_meta_font)

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
            f"命中 {tile.item.matched_by or '-'}",
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
    return SearchTreemapResponseCard(
        text=_require_str(payload, "text"),
        created_by=_require_str(payload, "created_by"),
        weight=_require_int(payload, "weight", min_value=0),
        rule=_require_str(payload, "rule"),
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
