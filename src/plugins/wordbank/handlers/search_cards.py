"""Pillow search card rendering for wordbank search results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
import math
from typing import Any

import arrow
from PIL import Image, ImageColor, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.demo_theme import SENRIN_V3_WORDBANK_CARD_THEME
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanEntry, build_image_plan_entry
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.types import WordbankSearchItem

from .search_card_helpers import (
    SearchCardContentBlock,
    SearchCardResponseRenderItem,
    build_search_card_footer_text,
    creator_chip_text,
    fold_hint,
    folded_preview_note,
    has_folded_preview,
    line_height,
    response_preview_items,
    response_rule_chips,
    scope_chip_label,
    status_chip_label,
    summary_chips,
    text_width,
    weight_chip_text,
)

CARD_WIDTH = 1320
CARD_PADDING_X = 30
CARD_PADDING_Y = 28
CARD_COLUMN_GAP = 24
CARD_ROW_GAP = 20
CARD_RADIUS = 24
CARD_HEADER_GAP = 16
CARD_SUMMARY_GAP = 18
CARD_FOOTER_GAP = 18
CARD_FOOTER_HEIGHT = 72
CARD_ITEM_PADDING_X = 22
CARD_ITEM_PADDING_Y = 20
CARD_ITEM_RADIUS = 24
CARD_ITEM_GAP = 14
CARD_SUBSECTION_GAP = 14
CARD_RESPONSE_GAP = 14
CARD_RESPONSE_PADDING_X = 16
CARD_RESPONSE_PADDING_Y = 10
CARD_RESPONSE_RADIUS = 18
CARD_BADGE_HEIGHT = 42
CARD_BADGE_WIDTH = 50
CARD_BADGE_RADIUS = 16
CARD_TAG_COLUMN_GAP = 8
CARD_TAG_ROW_GAP = 8
CARD_TAG_PADDING_X = 12
CARD_TAG_PADDING_Y = 6
CARD_TAG_RADIUS = 14
CARD_IMAGE_RADIUS = 16
CARD_IMAGE_MAX_HEIGHT = 210
CARD_FOLDED_HEIGHT = 52
CARD_CONTENT_GAP = 6
CARD_LABEL_PADDING_X = 12
CARD_LABEL_PADDING_Y = 6
CARD_LABEL_RADIUS = 13
CARD_CHIP_HEIGHT = 28
CARD_CHIP_PADDING_X = 12
CARD_CHIP_GAP = 8
CARD_CHIP_RADIUS = 14
CARD_META_SEPARATOR_GAP = 12
CARD_META_SEPARATOR_MARGIN_TOP = 12
CARD_META_SEPARATOR_MARGIN_BOTTOM = 10
TEXTURE_SPACING = 40
TEXTURE_DOT_RADIUS = 2
CARD_COLUMNS = 2


@dataclass(slots=True, frozen=True)
class SearchCardQuery:
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
class SearchCardChip:
    text: str
    fill: str
    text_fill: str
    outline: str


class SearchResultCardRenderer:
    THEME = SENRIN_V3_WORDBANK_CARD_THEME

    def __init__(
        self,
        *,
        preview_bytes: Mapping[int, bytes | None] | None = None,
    ) -> None:
        self.theme = self.THEME
        self.BG = self.theme.bg
        self.PANEL = self.theme.panel
        self.PANEL_SOFT = self.theme.panel_soft
        self.BORDER = self.theme.border
        self.HEADER = self.theme.header
        self.HEADER_SOFT = self.theme.header_soft
        self.BODY = self.theme.body
        self.MUTED = self.theme.muted
        self.ACCENT = self.theme.accent
        self.ACCENT_DEEP = self.theme.accent_deep
        self.ACCENT_SOFT = self.theme.accent_soft
        self.TRIGGER_PANEL = self.theme.trigger_panel
        self.TRIGGER_BORDER = self.theme.trigger_border
        self.RESPONSE_PANEL = self.theme.response_panel
        self.RESPONSE_BORDER = self.theme.response_border
        self.TEXTURE = self.theme.texture
        self.BADGE_TEXT = self.theme.badge_text
        self.SUCCESS_FILL = self.theme.success_fill
        self.SUCCESS_TEXT = self.theme.success_text
        self.SUCCESS_OUTLINE = self.theme.success_outline
        self.WARNING_FILL = self.theme.warning_fill
        self.WARNING_TEXT = self.theme.warning_text
        self.WARNING_OUTLINE = self.theme.warning_outline
        self.DANGER_FILL = self.theme.danger_fill
        self.DANGER_TEXT = self.theme.danger_text
        self.DANGER_OUTLINE = self.theme.danger_outline
        self.SCOPE_GLOBAL_FILL = self.theme.scope_global_fill
        self.SCOPE_GLOBAL_TEXT = self.theme.scope_global_text
        self.SCOPE_GLOBAL_OUTLINE = self.theme.scope_global_outline
        self.SCOPE_LOCAL_FILL = self.theme.scope_local_fill
        self.SCOPE_LOCAL_TEXT = self.theme.scope_local_text
        self.SCOPE_LOCAL_OUTLINE = self.theme.scope_local_outline
        self.SCOPE_PRIVATE_FILL = self.theme.scope_private_fill
        self.SCOPE_PRIVATE_TEXT = self.theme.scope_private_text
        self.SCOPE_PRIVATE_OUTLINE = self.theme.scope_private_outline
        self.NEUTRAL_CHIP_FILL = self.theme.neutral_chip_fill
        self.NEUTRAL_CHIP_TEXT = self.theme.neutral_chip_text
        self.NEUTRAL_CHIP_OUTLINE = self.theme.neutral_chip_outline
        self.DATA_CHIP_FILL = self.theme.data_chip_fill
        self.DATA_CHIP_TEXT = self.theme.data_chip_text
        self.DATA_CHIP_OUTLINE = self.theme.data_chip_outline
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(20)
        self.item_title_font = self._load_font(30)
        self.response_body_font = self._load_font(24)
        self.item_meta_font = self._load_font(18)
        self.item_tag_font = self._load_font(17)
        self.meta_chip_font = self._load_font(15)
        self.footer_font = self._load_font(17)
        self.response_text_fill = self.BODY
        self.label_fill = self.ACCENT_SOFT
        self.label_text_fill = self.ACCENT_DEEP
        self.preview_bytes = dict(preview_bytes or {})
        self._preview_size_cache: dict[
            tuple[int, int, int], tuple[int, int] | None
        ] = {}

    def render(
        self,
        *,
        items: tuple[WordbankSearchItem, ...],
        query: SearchCardQuery,
        locale: LocaleCode,
    ) -> bytes:
        height = self._measure_height(items, query, locale)
        image = Image.new("RGB", (CARD_WIDTH, height), self.BG)
        draw = ImageDraw.Draw(image)
        self._draw_background_texture(draw, height)

        cursor_y = CARD_PADDING_Y
        cursor_y = self._draw_header(draw, query, locale, cursor_y)
        cursor_y += CARD_HEADER_GAP
        summary_height = self._summary_block_height(query, locale)
        if summary_height > 0:
            cursor_y = self._draw_summary(draw, query, locale, cursor_y)
            cursor_y += CARD_SUMMARY_GAP

        if items:
            cursor_y = self._draw_grid(image, draw, items, query, locale, cursor_y)
        else:
            cursor_y = self._draw_empty_state(draw, query, locale, cursor_y)

        cursor_y += CARD_FOOTER_GAP
        self._draw_footer(draw, query, locale, cursor_y)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _measure_height(
        self,
        items: tuple[WordbankSearchItem, ...],
        query: SearchCardQuery,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PADDING_Y
        total += line_height(self.title_font)
        total += CARD_HEADER_GAP
        summary_height = self._summary_block_height(query, locale)
        if summary_height > 0:
            total += summary_height + CARD_SUMMARY_GAP
        if items:
            column_width = self._column_width()
            column_heights = [0] * CARD_COLUMNS
            for index, item in enumerate(items, start=1):
                card_height = self._item_block_height(
                    item,
                    absolute_index=index + (query.page - 1) * query.limit,
                    locale=locale,
                    column_width=column_width,
                )
                column = min(range(CARD_COLUMNS), key=lambda idx: column_heights[idx])
                if column_heights[column] > 0:
                    column_heights[column] += CARD_ROW_GAP
                column_heights[column] += card_height
            total += max(column_heights)
        else:
            total += 120
        total += CARD_FOOTER_GAP + CARD_FOOTER_HEIGHT
        return total + CARD_PADDING_Y

    def _draw_background_texture(self, draw: ImageDraw.ImageDraw, height: int) -> None:
        for row, y in enumerate(
            range(CARD_PADDING_Y // 2, height + TEXTURE_SPACING, TEXTURE_SPACING)
        ):
            offset = 0 if row % 2 == 0 else TEXTURE_SPACING // 2
            for x in range(
                CARD_PADDING_X // 2 + offset,
                CARD_WIDTH + TEXTURE_SPACING,
                TEXTURE_SPACING,
            ):
                draw.ellipse(
                    (
                        x - TEXTURE_DOT_RADIUS,
                        y - TEXTURE_DOT_RADIUS,
                        x + TEXTURE_DOT_RADIUS,
                        y + TEXTURE_DOT_RADIUS,
                    ),
                    fill=self.TEXTURE,
                )

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        title = f"SakuraiSenrin {tr(locale, 'wordbank.search_card.title')}"
        page_text = tr(
            locale,
            "wordbank.search_card.page",
            page=query.page,
            total_pages=query.total_pages,
        )
        draw.text(
            (CARD_PADDING_X, cursor_y),
            title,
            font=self.title_font,
            fill=self.HEADER,
        )
        draw.text(
            (
                CARD_WIDTH - CARD_PADDING_X - text_width(page_text, self.summary_font),
                cursor_y + 8,
            ),
            page_text,
            font=self.summary_font,
            fill=self.MUTED,
        )
        return cursor_y + line_height(self.title_font)

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        chips = summary_chips(
            keyword=query.keyword,
            field=query.field,
            creator_id=query.creator_id,
            has_image=query.has_image,
            locale=locale,
            field_label=self._field_label(query.field, locale),
        )
        if not chips:
            return cursor_y
        height = self._summary_block_height(query, locale)
        self._draw_surface_panel(
            draw,
            bbox=(
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + height,
            ),
            radius=CARD_RADIUS,
            fill=self._mix_color(self.ACCENT_SOFT, "#FFFFFF", 0.62),
            outline="",
        )
        chip_x = CARD_PADDING_X + 16
        chip_y = cursor_y + 14
        max_x = CARD_WIDTH - CARD_PADDING_X - 16
        for chip_text in chips:
            chip_width = (
                text_width(chip_text, self.summary_font) + CARD_TAG_PADDING_X * 2
            )
            if chip_x + chip_width > max_x:
                chip_x = CARD_PADDING_X + 16
                chip_y += CARD_CHIP_HEIGHT + CARD_TAG_ROW_GAP
            draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_width, chip_y + CARD_CHIP_HEIGHT),
                radius=CARD_CHIP_RADIUS,
                fill=self._mix_color(self.ACCENT_SOFT, self.ACCENT, 0.18),
            )
            draw.text(
                (
                    chip_x + CARD_TAG_PADDING_X,
                    chip_y
                    + max(
                        2,
                        int((CARD_CHIP_HEIGHT - line_height(self.summary_font)) / 2)
                        - 1,
                    ),
                ),
                chip_text,
                font=self.summary_font,
                fill=self.ACCENT_DEEP,
            )
            chip_x += chip_width + CARD_TAG_COLUMN_GAP
        return cursor_y + height

    def _draw_grid(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        items: tuple[WordbankSearchItem, ...],
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        column_width = self._column_width()
        column_heights = [cursor_y] * CARD_COLUMNS
        for index, item in enumerate(items, start=1):
            absolute_index = index + (query.page - 1) * query.limit
            card_height = self._item_block_height(
                item,
                absolute_index=absolute_index,
                locale=locale,
                column_width=column_width,
            )
            column = min(range(CARD_COLUMNS), key=lambda idx: column_heights[idx])
            x = CARD_PADDING_X + column * (column_width + CARD_COLUMN_GAP)
            y = column_heights[column]
            self._draw_item(
                image,
                draw,
                item=item,
                query=query,
                locale=locale,
                absolute_index=absolute_index,
                x=x,
                y=y,
                width=column_width,
                height=card_height,
            )
            column_heights[column] = y + card_height + CARD_ROW_GAP
        return max(column_heights) - CARD_ROW_GAP

    def _draw_item(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        item: WordbankSearchItem,
        query: SearchCardQuery,
        locale: LocaleCode,
        absolute_index: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        self._draw_surface_panel(
            draw,
            bbox=(x, y, x + width, y + height),
            radius=CARD_ITEM_RADIUS,
            fill=self._mix_color(self.ACCENT_SOFT, self.ACCENT, 0.22),
            outline="",
        )
        draw.rounded_rectangle(
            (
                x + 10,
                y + 10,
                x + width - 10,
                y + CARD_ITEM_PADDING_Y + CARD_BADGE_HEIGHT + 14,
            ),
            radius=18,
            fill=self._mix_color(self.ACCENT_SOFT, "#FFFFFF", 0.18),
        )
        inner_x = x + CARD_ITEM_PADDING_X
        cursor_y = y + CARD_ITEM_PADDING_Y
        content_width = width - CARD_ITEM_PADDING_X * 2
        header_height = self._item_header_height(
            item=item,
            width=content_width,
            locale=locale,
        )
        self._draw_item_header(
            draw,
            item=item,
            locale=locale,
            absolute_index=absolute_index,
            x=inner_x,
            y=cursor_y,
            width=content_width,
            height=header_height,
        )
        cursor_y += header_height + CARD_ITEM_GAP

        response_items = response_preview_items(item, locale)
        for response_index, response in enumerate(response_items, start=1):
            response_height = self._response_panel_height(
                response=response,
                item=item,
                absolute_index=absolute_index,
                width=width - CARD_ITEM_PADDING_X * 2,
            )
            self._draw_response_panel(
                image,
                draw,
                response=response,
                item=item,
                absolute_index=absolute_index,
                locale=locale,
                x=inner_x,
                y=cursor_y,
                width=width - CARD_ITEM_PADDING_X * 2,
                height=response_height,
            )
            cursor_y += response_height
            if response_index < len(response_items):
                cursor_y += CARD_RESPONSE_GAP

        if has_folded_preview(item):
            if response_items:
                cursor_y += CARD_RESPONSE_GAP
            folded_height = self._folded_block_height(
                item,
                locale=locale,
                width=width - CARD_ITEM_PADDING_X * 2,
            )
            self._draw_folded_preview_block(
                draw,
                item=item,
                locale=locale,
                x=inner_x,
                y=cursor_y,
                width=width - CARD_ITEM_PADDING_X * 2,
                height=folded_height,
            )
            cursor_y += folded_height + CARD_SUBSECTION_GAP
        else:
            _ = query

    def _draw_item_header(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        item: WordbankSearchItem,
        locale: LocaleCode,
        absolute_index: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        badge_text = f"{absolute_index:02d}"
        badge_y = y + max(0, int((height - CARD_BADGE_HEIGHT) / 2))
        self._draw_surface_panel(
            draw,
            bbox=(x, badge_y, x + CARD_BADGE_WIDTH, badge_y + CARD_BADGE_HEIGHT),
            radius=CARD_BADGE_RADIUS,
            fill=self.ACCENT,
            outline="",
        )
        badge_width = text_width(badge_text, self.item_meta_font)
        draw.text(
            (
                x + (CARD_BADGE_WIDTH - badge_width) / 2,
                badge_y
                + max(
                    7,
                    int(
                        (
                            CARD_BADGE_HEIGHT
                            - line_height(self.item_meta_font)
                        )
                        / 2
                    ),
                ),
            ),
            badge_text,
            font=self.item_meta_font,
            fill=self.BADGE_TEXT,
        )
        title_x = x + CARD_BADGE_WIDTH + 14
        title_width = width - CARD_BADGE_WIDTH - 14
        title_text = item.trigger_text or tr(locale, "wordbank.search_card.none")
        title_lines = self._wrap_text(
            title_text,
            self.item_title_font,
            max_width=title_width,
            max_lines=2,
        )
        title_y = y
        for index, line in enumerate(title_lines):
            draw.text(
                (title_x, title_y + index * line_height(self.item_title_font)),
                line,
                font=self.item_title_font,
                fill=self.HEADER,
            )

    def _item_header_height(
        self,
        *,
        item: WordbankSearchItem,
        width: int,
        locale: LocaleCode,
    ) -> int:
        title_width = width - CARD_BADGE_WIDTH - 14
        title_height = self._wrapped_text_height(
            item.trigger_text or tr(locale, "wordbank.search_card.none"),
            self.item_title_font,
            max_width=title_width,
            max_lines=2,
        )
        return max(CARD_BADGE_HEIGHT, title_height)

    def _draw_response_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        response: SearchCardResponseRenderItem,
        item: WordbankSearchItem,
        absolute_index: int,
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        fill = self.RESPONSE_PANEL
        self._draw_surface_panel(
            draw,
            bbox=(x, y, x + width, y + height),
            radius=CARD_RESPONSE_RADIUS,
            fill=fill,
            outline="",
        )
        content_x = x + CARD_RESPONSE_PADDING_X
        content_y = y + CARD_RESPONSE_PADDING_Y
        content_width = width - CARD_RESPONSE_PADDING_X * 2
        content_bottom = self._draw_content_blocks(
            image,
            draw,
            blocks=response.blocks,
            x=content_x,
            y=content_y,
            max_width=content_width,
            text_font=self.response_body_font,
            text_fill=self.response_text_fill,
        )
        meta_y = content_bottom + CARD_META_SEPARATOR_MARGIN_TOP + 2
        meta_band_top = meta_y - 6
        meta_band_bottom = y + height - 10
        draw.rounded_rectangle(
            (
                content_x - 2,
                meta_band_top,
                x + width - CARD_RESPONSE_PADDING_X + 2,
                meta_band_bottom,
            ),
            radius=14,
            fill=self._mix_color(self.ACCENT_SOFT, "#FFFFFF", 0.38),
        )
        self._draw_response_meta_row(
            draw,
            response=response,
            absolute_index=absolute_index,
            x=content_x,
            y=meta_y,
            width=content_width,
            locale=locale,
        )

    def _draw_folded_preview_block(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        item: WordbankSearchItem,
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        self._draw_surface_panel(
            draw,
            bbox=(x, y, x + width, y + height),
            radius=16,
            fill=self.ACCENT_SOFT,
            outline="",
        )
        hint = folded_preview_note(item, locale)
        self._draw_wrapped_text(
            draw,
            x=x + 14,
            y=y + 10,
            text=hint,
            font=self.item_meta_font,
            fill=self.ACCENT_DEEP,
            max_width=width - 28,
            max_lines=3,
        )

    def _draw_response_meta_row(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        response: SearchCardResponseRenderItem,
        absolute_index: int,
        x: int,
        y: int,
        width: int,
        locale: LocaleCode,
    ) -> None:
        left_chips = self._response_left_chips(
            response,
            locale,
            absolute_index=absolute_index,
        )
        right_chips = self._response_right_chips(response)
        self._draw_chip_row(
            draw,
            chips=left_chips,
            x=x,
            y=y,
            max_width=x + width,
            font=self.meta_chip_font,
        )
        self._draw_chip_row_right(
            draw,
            chips=right_chips,
            right=x + width,
            y=y,
            font=self.meta_chip_font,
        )

    def _draw_chip_row(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        chips: tuple[SearchCardChip, ...],
        x: int,
        y: int,
        max_width: int,
        font: Any | None = None,
    ) -> None:
        cursor_x = x
        chip_font = font or self.item_tag_font
        for chip in chips:
            chip_width = self._chip_width(chip.text, font=chip_font)
            if cursor_x + chip_width > max_width:
                break
            self._draw_chip(draw, chip=chip, x=cursor_x, y=y, font=chip_font)
            cursor_x += chip_width + CARD_CHIP_GAP

    def _draw_chip_row_right(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        chips: tuple[SearchCardChip, ...],
        right: int,
        y: int,
        font: Any | None = None,
    ) -> None:
        chip_font = font or self.item_tag_font
        total_width = self._chip_row_width(chips, font=chip_font)
        cursor_x = right - total_width
        for chip in chips:
            self._draw_chip(draw, chip=chip, x=cursor_x, y=y, font=chip_font)
            cursor_x += self._chip_width(chip.text, font=chip_font) + CARD_CHIP_GAP

    def _draw_chip(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        chip: SearchCardChip,
        x: int,
        y: int,
        font: Any | None = None,
    ) -> None:
        chip_font = font or self.item_tag_font
        chip_width = self._chip_width(chip.text, font=chip_font)
        draw.rounded_rectangle(
            (x, y, x + chip_width, y + CARD_CHIP_HEIGHT),
            radius=CARD_CHIP_RADIUS,
            fill=chip.fill,
        )
        if chip.outline:
            draw.rounded_rectangle(
                (x, y, x + chip_width, y + CARD_CHIP_HEIGHT),
                radius=CARD_CHIP_RADIUS,
                outline=chip.outline,
                width=1,
            )
        draw.text(
            (
                x + CARD_CHIP_PADDING_X,
                y + max(3, int((CARD_CHIP_HEIGHT - line_height(chip_font)) / 2)),
            ),
            chip.text,
            font=chip_font,
            fill=chip.text_fill,
        )

    def _chip_width(self, text: str, *, font: Any | None = None) -> int:
        chip_font = font or self.item_tag_font
        return text_width(text, chip_font) + CARD_CHIP_PADDING_X * 2

    def _chip_row_width(
        self,
        chips: tuple[SearchCardChip, ...],
        *,
        font: Any | None = None,
    ) -> int:
        chip_font = font or self.item_tag_font
        if not chips:
            return 0
        return sum(
            self._chip_width(chip.text, font=chip_font) for chip in chips
        ) + CARD_CHIP_GAP * (len(chips) - 1)

    def _response_left_chips(
        self,
        response: SearchCardResponseRenderItem,
        locale: LocaleCode,
        *,
        absolute_index: int,
    ) -> tuple[SearchCardChip, ...]:
        chips = [
            self._response_index_chip(f"{absolute_index}-{response.index}"),
            self._status_chip(
                status_chip_label(locale, response.status),
                response.status,
            ),
            self._scope_chip(scope_chip_label(response.scope), response.scope),
        ]
        chips.extend(
            self._neutral_chip(text) for text in response_rule_chips(response.rule)
        )
        return tuple(chips)

    def _response_right_chips(
        self,
        response: SearchCardResponseRenderItem,
    ) -> tuple[SearchCardChip, ...]:
        chips = [self._data_chip(creator_chip_text(response.created_by))]
        weight_text = weight_chip_text(response.weight)
        if weight_text:
            chips.append(self._data_chip(weight_text))
        return tuple(chips)

    def _status_chip(self, text: str, status: str) -> SearchCardChip:
        if status == "approved":
            return SearchCardChip(
                text=text,
                fill=self.SUCCESS_FILL,
                text_fill=self.SUCCESS_TEXT,
                outline="",
            )
        if status == "pending":
            return SearchCardChip(
                text=text,
                fill=self.WARNING_FILL,
                text_fill=self.WARNING_TEXT,
                outline="",
            )
        return SearchCardChip(
            text=text,
            fill=self.DANGER_FILL,
            text_fill=self.DANGER_TEXT,
            outline="",
        )

    def _scope_chip(self, text: str, scope: str) -> SearchCardChip:
        if scope == "all_groups":
            return SearchCardChip(
                text=text,
                fill=self.SCOPE_GLOBAL_FILL,
                text_fill=self.SCOPE_GLOBAL_TEXT,
                outline="",
            )
        if scope in {"current_group", "self_in_current_group"}:
            return SearchCardChip(
                text=text,
                fill=self.SCOPE_LOCAL_FILL,
                text_fill=self.SCOPE_LOCAL_TEXT,
                outline="",
            )
        return SearchCardChip(
            text=text,
            fill=self.SCOPE_PRIVATE_FILL,
            text_fill=self.SCOPE_PRIVATE_TEXT,
            outline="",
        )

    def _neutral_chip(self, text: str) -> SearchCardChip:
        return SearchCardChip(
            text=text,
            fill=self._mix_color(self.NEUTRAL_CHIP_FILL, self.ACCENT_SOFT, 0.22),
            text_fill=self._shade_color(self.NEUTRAL_CHIP_TEXT, 0.95),
            outline="",
        )

    def _data_chip(self, text: str) -> SearchCardChip:
        return SearchCardChip(
            text=text,
            fill=self._mix_color(self.DATA_CHIP_FILL, self.ACCENT_SOFT, 0.12),
            text_fill=self._shade_color(self.DATA_CHIP_TEXT, 0.94),
            outline="",
        )

    def _response_index_chip(self, text: str) -> SearchCardChip:
        return SearchCardChip(
            text=text,
            fill=self._mix_color(self.ACCENT_SOFT, "#FFFFFF", 0.18),
            text_fill=self._shade_color(self.ACCENT_DEEP, 0.92),
            outline="",
        )

    def _draw_empty_state(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        self._draw_surface_panel(
            draw,
            bbox=(
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + 120,
            ),
            radius=CARD_RADIUS,
            fill=self._mix_color(self.ACCENT_SOFT, "#FFFFFF", 0.5),
            outline="",
        )
        draw.text(
            (CARD_PADDING_X + 24, cursor_y + 28),
            tr(locale, "wordbank.search_card.empty", page=query.page),
            font=self.item_title_font,
            fill=self.HEADER,
        )
        draw.text(
            (CARD_PADDING_X + 24, cursor_y + 70),
            tr(locale, "wordbank.search_card.empty_hint"),
            font=self.item_meta_font,
            fill=self.MUTED,
        )
        return cursor_y + 120

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> None:
        footer_time = arrow.get(get_current_time()).to("Asia/Shanghai")
        generated_at_text = tr(
            locale,
            "water.image.generated_at",
            time=footer_time.format("YYYY-MM-DD HH:mm:ss"),
        )
        footer = tr(locale, "wordbank.search_card.total", total=query.total_count)
        if query.page < query.total_pages:
            footer += "  " + tr(
                locale, "wordbank.search_card.next_page", next_page=query.page + 1
            )
        draw.text(
            (CARD_PADDING_X, cursor_y),
            generated_at_text,
            font=self.footer_font,
            fill=self.MUTED,
        )
        copyright_text = build_search_card_footer_text(footer_time.year)
        draw.text(
            (
                CARD_WIDTH
                - CARD_PADDING_X
                - text_width(copyright_text, self.footer_font),
                cursor_y,
            ),
            copyright_text,
            font=self.footer_font,
            fill=self.MUTED,
        )
        draw.text(
            (
                int((CARD_WIDTH - text_width(footer, self.footer_font)) / 2),
                cursor_y + 28,
            ),
            footer,
            font=self.footer_font,
            fill=self.MUTED,
        )

    def _summary_block_height(self, query: SearchCardQuery, locale: LocaleCode) -> int:
        chips = summary_chips(
            keyword=query.keyword,
            field=query.field,
            creator_id=query.creator_id,
            has_image=query.has_image,
            locale=locale,
            field_label=self._field_label(query.field, locale),
        )
        if not chips:
            return 0
        rows = 1
        current_x = CARD_PADDING_X + 16
        max_x = CARD_WIDTH - CARD_PADDING_X - 16
        for chip_text in chips:
            chip_width = (
                text_width(chip_text, self.summary_font) + CARD_TAG_PADDING_X * 2
            )
            if current_x + chip_width > max_x:
                rows += 1
                current_x = CARD_PADDING_X + 16 + chip_width + CARD_TAG_COLUMN_GAP
            else:
                current_x += chip_width + CARD_TAG_COLUMN_GAP
        return 14 + rows * CARD_CHIP_HEIGHT + (rows - 1) * CARD_TAG_ROW_GAP + 14

    def _item_block_height(
        self,
        item: WordbankSearchItem,
        *,
        absolute_index: int,
        locale: LocaleCode,
        column_width: int,
    ) -> int:
        width = column_width - CARD_ITEM_PADDING_X * 2
        total = CARD_ITEM_PADDING_Y * 2
        total += self._item_header_height(item=item, width=width, locale=locale)
        total += CARD_ITEM_GAP
        previews = response_preview_items(item, locale)
        for response_index, response in enumerate(previews, start=1):
            total += self._response_panel_height(
                response=response,
                item=item,
                absolute_index=absolute_index,
                width=width,
            )
        if has_folded_preview(item):
            if len(previews) > 1:
                total += CARD_RESPONSE_GAP * (len(previews) - 1)
            if previews:
                total += CARD_RESPONSE_GAP
            total += self._folded_block_height(item, locale=locale, width=width)
            total += CARD_SUBSECTION_GAP
        elif len(previews) > 1:
            total += CARD_RESPONSE_GAP * (len(previews) - 1)
        return total

    def _response_panel_height(
        self,
        *,
        response: SearchCardResponseRenderItem,
        item: WordbankSearchItem,
        absolute_index: int,
        width: int,
    ) -> int:
        _ = (absolute_index, item)
        total = CARD_RESPONSE_PADDING_Y * 2
        total += self._content_blocks_height(
            response.blocks,
            max_width=width - CARD_RESPONSE_PADDING_X * 2,
            text_font=self.response_body_font,
        )
        total += CARD_META_SEPARATOR_MARGIN_TOP
        total += CARD_META_SEPARATOR_GAP
        total += CARD_META_SEPARATOR_MARGIN_BOTTOM
        total += CARD_CHIP_HEIGHT
        return total

    def _folded_block_height(
        self,
        item: WordbankSearchItem,
        *,
        locale: LocaleCode,
        width: int,
    ) -> int:
        text_height = self._wrapped_text_height(
            folded_preview_note(item, locale),
            self.item_meta_font,
            max_width=width - 28,
            max_lines=3,
        )
        return max(CARD_FOLDED_HEIGHT, text_height + 20)

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        text: str,
        font: Any,
        fill: str,
        max_width: int,
        max_lines: int | None = None,
    ) -> int:
        cursor_y = int(y)
        for line in self._wrap_text(
            text,
            font,
            max_width=max_width,
            max_lines=max_lines,
        ):
            draw.text((x, cursor_y), line, font=font, fill=fill)
            cursor_y += line_height(font)
        return cursor_y

    def _draw_content_blocks(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        blocks: tuple[SearchCardContentBlock, ...],
        x: int,
        y: int,
        max_width: int,
        text_font: Any,
        text_fill: str,
    ) -> int:
        cursor_y = y
        drawn = 0
        for block in blocks:
            if drawn > 0:
                cursor_y += CARD_CONTENT_GAP
            if block.kind == "text" and block.text:
                cursor_y = self._draw_wrapped_text(
                    draw,
                    x=x,
                    y=cursor_y,
                    text=block.text,
                    font=text_font,
                    fill=text_fill,
                    max_width=max_width,
                )
                drawn += 1
            elif block.kind == "image" and block.image_id is not None:
                preview = self._prepare_preview_image(
                    block.image_id,
                    max_width=max_width,
                    max_height=CARD_IMAGE_MAX_HEIGHT,
                )
                if preview is None:
                    if drawn > 0:
                        cursor_y -= CARD_CONTENT_GAP
                    continue
                self._paste_rounded_image(
                    image,
                    preview,
                    (x, cursor_y),
                    radius=CARD_IMAGE_RADIUS,
                )
                cursor_y += preview.height
                drawn += 1
            elif block.kind == "label" and block.label:
                cursor_y = self._draw_label_block(
                    draw,
                    x=x,
                    y=cursor_y,
                    text=block.label,
                )
                drawn += 1
            elif drawn > 0:
                cursor_y -= CARD_CONTENT_GAP
        return cursor_y

    def _content_blocks_height(
        self,
        blocks: tuple[SearchCardContentBlock, ...],
        *,
        max_width: int,
        text_font: Any,
    ) -> int:
        total = 0
        visible = 0
        for block in blocks:
            block_height = 0
            if block.kind == "text" and block.text:
                block_height = self._wrapped_text_height(
                    block.text,
                    text_font,
                    max_width=max_width,
                )
            elif block.kind == "image" and block.image_id is not None:
                block_height = self._preview_height(
                    block.image_id,
                    max_width=max_width,
                    max_height=CARD_IMAGE_MAX_HEIGHT,
                )
            elif block.kind == "label" and block.label:
                block_height = self._label_block_height(block.label)
            if block_height <= 0:
                continue
            if visible > 0:
                total += CARD_CONTENT_GAP
            total += block_height
            visible += 1
        return total

    def _draw_label_block(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
    ) -> int:
        height = self._label_block_height(text)
        width = text_width(text, self.item_meta_font) + CARD_LABEL_PADDING_X * 2
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=CARD_LABEL_RADIUS,
            fill=self.label_fill,
        )
        draw.text(
            (x + CARD_LABEL_PADDING_X, y + 4),
            text,
            font=self.item_meta_font,
            fill=self.label_text_fill,
        )
        return y + height

    def _label_block_height(self, text: str) -> int:
        _ = text
        return line_height(self.item_meta_font) + CARD_LABEL_PADDING_Y

    def _wrapped_text_height(
        self,
        text: str,
        font: Any,
        *,
        max_width: int,
        max_lines: int | None = None,
    ) -> int:
        return len(
            self._wrap_text(text, font, max_width=max_width, max_lines=max_lines)
        ) * line_height(font)

    def _wrap_text(
        self,
        text: str,
        font: Any,
        *,
        max_width: int,
        max_lines: int | None = None,
    ) -> list[str]:
        if not text:
            return [""]
        lines: list[str] = []
        for raw_line in text.splitlines() or [text]:
            current = ""
            if not raw_line:
                lines.append("")
                continue
            for char in raw_line:
                candidate = f"{current}{char}"
                if text_width(candidate, font) <= max_width:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = char
            if current:
                lines.append(current)
        if not lines:
            lines = [""]
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._truncate_line(f"{lines[-1]}...", font, max_width)
        return [self._truncate_line(line, font, max_width) for line in lines]

    def _truncate_line(self, text: str, font: Any, max_width: int) -> str:
        if text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..."

    def _preview_height(
        self,
        image_id: int | None,
        *,
        max_width: int,
        max_height: int,
    ) -> int:
        if image_id is None:
            return 0
        cache_key = (image_id, max_width, max_height)
        if cache_key not in self._preview_size_cache:
            image_bytes = self.preview_bytes.get(image_id)
            if not image_bytes:
                self._preview_size_cache[cache_key] = None
            else:
                self._preview_size_cache[cache_key] = self._measure_preview_size(
                    image_bytes,
                    max_width=max_width,
                    max_height=max_height,
                )
        measured = self._preview_size_cache[cache_key]
        return measured[1] if measured is not None else 0

    def _measure_preview_size(
        self,
        image_bytes: bytes,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[int, int] | None:
        try:
            with Image.open(BytesIO(image_bytes)) as preview:
                width, height = preview.convert("RGB").size
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        scale = min(max_width / width, max_height / height, 1.0)
        return (max(1, int(width * scale)), max(1, int(height * scale)))

    def _prepare_preview_image(
        self,
        image_id: int,
        *,
        max_width: int,
        max_height: int,
    ) -> Image.Image | None:
        image_bytes = self.preview_bytes.get(image_id)
        if not image_bytes:
            return None
        measured = self._measure_preview_size(
            image_bytes,
            max_width=max_width,
            max_height=max_height,
        )
        if measured is None:
            return None
        target_width, target_height = measured
        try:
            with Image.open(BytesIO(image_bytes)) as preview:
                prepared = preview.convert("RGB")
                if prepared.size != (target_width, target_height):
                    prepared.thumbnail(
                        (target_width, target_height),
                        Image.Resampling.LANCZOS,
                    )
                return prepared.copy()
        except Exception:
            return None

    def _paste_rounded_image(
        self,
        image: Image.Image,
        preview: Image.Image,
        origin: tuple[int, int],
        *,
        radius: int,
    ) -> None:
        mask = Image.new("L", preview.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, preview.width, preview.height),
            radius=radius,
            fill=255,
        )
        image.paste(preview, origin, mask)

    def _field_label(self, field: str, locale: LocaleCode) -> str:
        return {
            "all": tr(locale, "wordbank.search_card.field.all"),
            "trigger": tr(locale, "wordbank.search_card.field.trigger"),
            "response": tr(locale, "wordbank.search_card.field.response"),
        }.get(field, field)

    def _column_width(self) -> int:
        return int((CARD_WIDTH - CARD_PADDING_X * 2 - CARD_COLUMN_GAP) / CARD_COLUMNS)

    def _draw_surface_panel(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        bbox: tuple[int, int, int, int],
        radius: int,
        fill: str,
        outline: str,
    ) -> None:
        draw.rounded_rectangle(
            bbox,
            radius=radius,
            fill=fill,
        )
        if outline:
            draw.rounded_rectangle(
                bbox,
                radius=radius,
                outline=outline,
                width=1,
            )

    def _shade_color(self, color: str, factor: float) -> str:
        red, green, blue = ImageColor.getrgb(color)[:3]
        red_value = max(0, min(255, int(red * factor)))
        green_value = max(0, min(255, int(green * factor)))
        blue_value = max(0, min(255, int(blue * factor)))
        return f"#{red_value:02X}{green_value:02X}{blue_value:02X}"

    def _mix_color(self, color_a: str, color_b: str, ratio_b: float) -> str:
        red_a, green_a, blue_a = ImageColor.getrgb(color_a)[:3]
        red_b, green_b, blue_b = ImageColor.getrgb(color_b)[:3]
        ratio = max(0.0, min(1.0, ratio_b))
        red = int(red_a * (1 - ratio) + red_b * ratio)
        green = int(green_a * (1 - ratio) + green_b * ratio)
        blue = int(blue_a * (1 - ratio) + blue_b * ratio)
        return f"#{red:02X}{green:02X}{blue:02X}"

    # Compatibility helpers kept for tests and local renderer inspection.
    def _fold_hint(self, item: WordbankSearchItem, locale: LocaleCode) -> str:
        return fold_hint(item, locale)

    def _has_folded_preview(self, item: WordbankSearchItem) -> bool:
        return has_folded_preview(item)

    def _folded_preview_block_height(self) -> int:
        return CARD_FOLDED_HEIGHT

    def _load_font(self, size: int) -> Any:
        try:
            return ImageFont.truetype(MAPLE_FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()


def render_search_results_card_bytes(
    *,
    items: tuple[WordbankSearchItem, ...],
    query: SearchCardQuery,
    locale: LocaleCode,
    preview_bytes: Mapping[int, bytes | None] | None = None,
) -> bytes:
    renderer = SearchResultCardRenderer(preview_bytes=preview_bytes)
    return renderer.render(items=items, query=query, locale=locale)


def build_search_results_card_plan_entry(
    *,
    items: tuple[WordbankSearchItem, ...],
    query: SearchCardQuery,
    locale: LocaleCode,
    preview_bytes: Mapping[int, bytes | None] | None = None,
) -> MessagePlanEntry:
    image_bytes = render_search_results_card_bytes(
        items=items,
        query=query,
        locale=locale,
        preview_bytes=preview_bytes,
    )
    return build_image_plan_entry(image_bytes)
