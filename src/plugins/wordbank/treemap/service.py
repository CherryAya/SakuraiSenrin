"""Standalone treemap rendering for wordbank search results."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw

from src.lib.consts import LXGW_FONG_PATH, MAPLE_FONT_PATH
from src.lib.demo_theme import SENRIN_V3_WORDBANK_TREEMAP_THEME
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .fitters import SearchTreemapFittersMixin
from .models import (
    SearchTreemapPage,
    SearchTreemapQuery,
    SearchTreemapTile,
    TreemapRect,
    build_search_treemap_layout,
)
from .render_utils import SearchTreemapRenderUtilsMixin
from .response_draw import SearchTreemapResponseDrawMixin
from .response_layout import SearchTreemapResponseLayoutMixin

TREEMAP_WIDTH = 1760
TREEMAP_HEIGHT = 1280
TREEMAP_MARGIN_X = 52
TREEMAP_MARGIN_Y = 44
TREEMAP_SUMMARY_GAP = 22
TREEMAP_SUMMARY_COLUMN_GAP = 44
TREEMAP_FOOTER_HEIGHT = 42
TREEMAP_TILE_PADDING = 18
TREEMAP_TILE_RESPONSE_MIN_WIDTH = 180
TREEMAP_TILE_RESPONSE_MIN_HEIGHT = 120


class SearchTreemapRenderer(
    SearchTreemapRenderUtilsMixin,
    SearchTreemapFittersMixin,
    SearchTreemapResponseLayoutMixin,
    SearchTreemapResponseDrawMixin,
):
    THEME = SENRIN_V3_WORDBANK_TREEMAP_THEME

    def __init__(self) -> None:
        self.theme = self.THEME
        self.BG = self.theme.bg
        self.PANEL = self.theme.panel
        self.PANEL_ALT = self.theme.panel_alt
        self.BORDER = self.theme.border
        self.HEADER = self.theme.header
        self.BODY = self.theme.body
        self.MUTED = self.theme.muted
        self.ACCENT = self.theme.accent
        self.BADGE_BG = self.theme.badge_bg
        self.BADGE_TEXT = self.theme.badge_text
        self.NUMBER_BG = self.theme.number_bg
        self.NUMBER_TEXT = self.theme.number_text
        self.CARD_BG = self.theme.card_bg
        self.CARD_ACCENT = self.theme.card_accent
        self.DIVIDER = self.theme.divider
        self._maple_font_cache: dict[int, Any] = {}
        self._lxgw_font_cache: dict[int, Any] = {}
        self._maple_font_path = MAPLE_FONT_PATH
        self._lxgw_font_path = LXGW_FONG_PATH
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

    @staticmethod
    def _tr(locale: LocaleCode, key: Any, /, **kwargs: object) -> str:
        return tr(locale, key, **kwargs)

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
            fill=self.theme.white,
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
            (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height),
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
            (inner_x, inner_y, inner_x + number_width, inner_y + number_height),
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
            (badge_x1, badge_y1, badge_x1 + badge_width, badge_y1 + badge_height),
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
            draw.text((title_x, cursor_y), line, font=title_font, fill=self.HEADER)
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
            (inner_x, cursor_y, inner_x + inner_width, cursor_y),
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
