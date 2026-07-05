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
    SearchCardResponsePreview,
    build_search_card_footer_text,
    fallback_match_label,
    fold_hint,
    folded_preview_note,
    has_folded_preview,
    line_height,
    response_preview_items,
    safe_field_label,
    summary_chips,
    text_width,
)

CARD_WIDTH = 1320
CARD_PADDING_X = 32
CARD_PADDING_Y = 28
CARD_COLUMN_GAP = 20
CARD_ROW_GAP = 18
CARD_RADIUS = 22
CARD_HEADER_GAP = 16
CARD_SUMMARY_GAP = 18
CARD_FOOTER_GAP = 18
CARD_FOOTER_HEIGHT = 72
CARD_ITEM_PADDING_X = 20
CARD_ITEM_PADDING_Y = 18
CARD_ITEM_RADIUS = 20
CARD_ITEM_GAP = 12
CARD_SUBSECTION_GAP = 10
CARD_RESPONSE_GAP = 8
CARD_RESPONSE_PADDING_X = 14
CARD_RESPONSE_PADDING_Y = 12
CARD_RESPONSE_RADIUS = 16
CARD_BADGE_SIZE = 42
CARD_TAG_GAP = 8
CARD_TAG_PADDING_X = 12
CARD_TAG_PADDING_Y = 6
CARD_TAG_RADIUS = 14
CARD_IMAGE_RADIUS = 16
CARD_IMAGE_MAX_HEIGHT = 220
CARD_FOLDED_HEIGHT = 52
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
        self.BORDER = self.theme.border
        self.HEADER = self.theme.header
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
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(20)
        self.item_title_font = self._load_font(28)
        self.item_body_font = self._load_font(24)
        self.item_meta_font = self._load_font(18)
        self.item_tag_font = self._load_font(17)
        self.footer_font = self._load_font(17)
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
        total += self._summary_block_height(query, locale)
        total += CARD_SUMMARY_GAP
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
        height = self._summary_block_height(query, locale)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + height,
            ),
            radius=CARD_RADIUS,
            fill=self.PANEL,
            outline=self.BORDER,
            width=1,
        )
        chip_x = CARD_PADDING_X + 16
        chip_y = cursor_y + 14
        chip_height = line_height(self.summary_font) + CARD_TAG_PADDING_Y
        max_x = CARD_WIDTH - CARD_PADDING_X - 16
        for chip_text in summary_chips(
            keyword=query.keyword,
            field=query.field,
            creator_id=query.creator_id,
            has_image=query.has_image,
            locale=locale,
            field_label=self._field_label(query.field, locale),
        ):
            chip_width = (
                text_width(chip_text, self.summary_font) + CARD_TAG_PADDING_X * 2
            )
            if chip_x + chip_width > max_x:
                chip_x = CARD_PADDING_X + 16
                chip_y += chip_height + CARD_TAG_GAP
            draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_width, chip_y + chip_height),
                radius=chip_height // 2,
                fill=self.ACCENT_SOFT,
            )
            draw.text(
                (chip_x + CARD_TAG_PADDING_X, chip_y + 4),
                chip_text,
                font=self.summary_font,
                fill=self.ACCENT_DEEP,
            )
            chip_x += chip_width + CARD_TAG_GAP
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
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=CARD_ITEM_RADIUS,
            fill=self.PANEL,
            outline=self.BORDER,
            width=1,
        )
        inner_x = x + CARD_ITEM_PADDING_X
        cursor_y = y + CARD_ITEM_PADDING_Y
        badge_text = f"{absolute_index:02d}"
        draw.ellipse(
            (inner_x, cursor_y, inner_x + CARD_BADGE_SIZE, cursor_y + CARD_BADGE_SIZE),
            fill=self.ACCENT,
        )
        badge_width = text_width(badge_text, self.item_meta_font)
        draw.text(
            (
                inner_x + (CARD_BADGE_SIZE - badge_width) / 2,
                cursor_y + 9,
            ),
            badge_text,
            font=self.item_meta_font,
            fill=self.BADGE_TEXT,
        )
        draw.text(
            (inner_x + CARD_BADGE_SIZE + 12, cursor_y + 3),
            item.trigger_text or tr(locale, "wordbank.search_card.none"),
            font=self.item_title_font,
            fill=self.HEADER,
        )
        meta_y = cursor_y + 28
        draw.text(
            (inner_x + CARD_BADGE_SIZE + 12, meta_y),
            tr(locale, "wordbank.search_card.group_id", group_id=item.trigger_group_id),
            font=self.item_meta_font,
            fill=self.MUTED,
        )
        cursor_y += CARD_BADGE_SIZE + CARD_ITEM_GAP

        trigger_height = self._trigger_panel_height(
            item=item,
            width=width - CARD_ITEM_PADDING_X * 2,
            locale=locale,
        )
        self._draw_trigger_panel(
            image,
            draw,
            item=item,
            locale=locale,
            x=inner_x,
            y=cursor_y,
            width=width - CARD_ITEM_PADDING_X * 2,
            height=trigger_height,
        )
        cursor_y += trigger_height + CARD_SUBSECTION_GAP

        for response in response_preview_items(item, locale):
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
                x=inner_x,
                y=cursor_y,
                width=width - CARD_ITEM_PADDING_X * 2,
                height=response_height,
            )
            cursor_y += response_height + CARD_RESPONSE_GAP

        if has_folded_preview(item):
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
            cursor_y += CARD_SUBSECTION_GAP - CARD_RESPONSE_GAP

        tag_texts = self._item_tag_texts(item=item, query=query, locale=locale)
        self._draw_tags(
            draw,
            tags=tag_texts,
            x=inner_x,
            y=cursor_y,
            width=width - CARD_ITEM_PADDING_X * 2,
        )

    def _draw_trigger_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        item: WordbankSearchItem,
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        _ = locale
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=CARD_RESPONSE_RADIUS,
            fill=self.TRIGGER_PANEL,
            outline=self.TRIGGER_BORDER,
            width=1,
        )
        cursor_y = y + 12
        cursor_y = self._draw_wrapped_text(
            draw,
            x=x + 14,
            y=cursor_y,
            text=item.trigger_text or "",
            font=self.item_body_font,
            fill=self._contrast_text_color(self.TRIGGER_PANEL),
            max_width=width - 28,
            max_lines=4,
        )
        if item.trigger_preview_image_id is not None:
            preview = self._prepare_preview_image(
                item.trigger_preview_image_id,
                max_width=width - 28,
                max_height=CARD_IMAGE_MAX_HEIGHT,
            )
            if preview is not None:
                cursor_y += 10
                self._paste_rounded_image(
                    image,
                    preview,
                    (x + 14 + int((width - 28 - preview.width) / 2), cursor_y),
                    radius=CARD_IMAGE_RADIUS,
                )

    def _draw_response_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        response: SearchCardResponsePreview,
        item: WordbankSearchItem,
        absolute_index: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        fill = self.RESPONSE_PANEL
        outline = self.RESPONSE_BORDER
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=CARD_RESPONSE_RADIUS,
            fill=fill,
            outline=outline,
            width=1,
        )
        label = f"{absolute_index}-{response.index}"
        label_width = text_width(label, self.item_tag_font) + 20
        draw.rounded_rectangle(
            (
                x + CARD_RESPONSE_PADDING_X,
                y + CARD_RESPONSE_PADDING_Y,
                x + CARD_RESPONSE_PADDING_X + label_width,
                y + CARD_RESPONSE_PADDING_Y + 28,
            ),
            radius=14,
            fill=self.ACCENT_SOFT,
        )
        draw.text(
            (x + CARD_RESPONSE_PADDING_X + 10, y + CARD_RESPONSE_PADDING_Y + 5),
            label,
            font=self.item_tag_font,
            fill=self.ACCENT_DEEP,
        )
        cursor_y = y + CARD_RESPONSE_PADDING_Y + 34
        cursor_y = self._draw_wrapped_text(
            draw,
            x=x + CARD_RESPONSE_PADDING_X,
            y=cursor_y,
            text=response.text,
            font=self.item_body_font,
            fill=self._contrast_text_color(fill),
            max_width=width - CARD_RESPONSE_PADDING_X * 2,
            max_lines=5,
        )
        if response.index == 1 and item.response_preview_image_id is not None:
            preview = self._prepare_preview_image(
                item.response_preview_image_id,
                max_width=width - CARD_RESPONSE_PADDING_X * 2,
                max_height=CARD_IMAGE_MAX_HEIGHT,
            )
            if preview is not None:
                cursor_y += 10
                self._paste_rounded_image(
                    image,
                    preview,
                    (
                        x
                        + CARD_RESPONSE_PADDING_X
                        + int(
                            (width - CARD_RESPONSE_PADDING_X * 2 - preview.width) / 2
                        ),
                        cursor_y,
                    ),
                    radius=CARD_IMAGE_RADIUS,
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
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=16,
            fill=self.ACCENT_SOFT,
            outline=self.theme.folded_outline,
            width=1,
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

    def _draw_tags(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        tags: tuple[str, ...],
        x: int,
        y: int,
        width: int,
    ) -> None:
        cursor_x = x
        cursor_y = y
        line_height_value = line_height(self.item_tag_font) + CARD_TAG_PADDING_Y
        max_x = x + width
        for tag in tags:
            tag_width = text_width(tag, self.item_tag_font) + CARD_TAG_PADDING_X * 2
            if cursor_x + tag_width > max_x:
                cursor_x = x
                cursor_y += line_height_value + CARD_TAG_GAP
            draw.rounded_rectangle(
                (
                    cursor_x,
                    cursor_y,
                    cursor_x + tag_width,
                    cursor_y + line_height_value,
                ),
                radius=CARD_TAG_RADIUS,
                fill="#FFFFFF",
                outline=self.BORDER,
                width=1,
            )
            draw.text(
                (cursor_x + CARD_TAG_PADDING_X, cursor_y + 4),
                tag,
                font=self.item_tag_font,
                fill=self.MUTED,
            )
            cursor_x += tag_width + CARD_TAG_GAP

    def _draw_empty_state(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + 120,
            ),
            radius=CARD_RADIUS,
            fill=self.PANEL,
            outline=self.BORDER,
            width=1,
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
        chip_height = line_height(self.summary_font) + CARD_TAG_PADDING_Y
        rows = 1
        current_x = CARD_PADDING_X + 16
        max_x = CARD_WIDTH - CARD_PADDING_X - 16
        for chip_text in summary_chips(
            keyword=query.keyword,
            field=query.field,
            creator_id=query.creator_id,
            has_image=query.has_image,
            locale=locale,
            field_label=self._field_label(query.field, locale),
        ):
            chip_width = (
                text_width(chip_text, self.summary_font) + CARD_TAG_PADDING_X * 2
            )
            if current_x + chip_width > max_x:
                rows += 1
                current_x = CARD_PADDING_X + 16 + chip_width + CARD_TAG_GAP
            else:
                current_x += chip_width + CARD_TAG_GAP
        return 14 + rows * chip_height + (rows - 1) * CARD_TAG_GAP + 14

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
        total += CARD_BADGE_SIZE + CARD_ITEM_GAP
        total += self._trigger_panel_height(item=item, width=width, locale=locale)
        total += CARD_SUBSECTION_GAP
        previews = response_preview_items(item, locale)
        for response in previews:
            total += self._response_panel_height(
                response=response,
                item=item,
                absolute_index=absolute_index,
                width=width,
            )
            total += CARD_RESPONSE_GAP
        if has_folded_preview(item):
            total += self._folded_block_height(item, locale=locale, width=width)
            total += CARD_SUBSECTION_GAP
        else:
            total += max(0, CARD_SUBSECTION_GAP - CARD_RESPONSE_GAP)
        total += self._tags_height(
            tags=self._item_tag_texts(
                item=item,
                query=None,
                locale=locale,
            ),
            width=width,
        )
        return total

    def _trigger_panel_height(
        self,
        *,
        item: WordbankSearchItem,
        width: int,
        locale: LocaleCode,
    ) -> int:
        total = 12
        total += self._wrapped_text_height(
            item.trigger_text or tr(locale, "wordbank.search_card.none"),
            self.item_body_font,
            max_width=width - 28,
            max_lines=4,
        )
        preview_height = self._preview_height(
            item.trigger_preview_image_id,
            max_width=width - 28,
            max_height=CARD_IMAGE_MAX_HEIGHT,
        )
        if preview_height > 0:
            total += 10 + preview_height
        total += 12
        return total

    def _response_panel_height(
        self,
        *,
        response: SearchCardResponsePreview,
        item: WordbankSearchItem,
        absolute_index: int,
        width: int,
    ) -> int:
        _ = absolute_index
        total = CARD_RESPONSE_PADDING_Y + 34
        total += self._wrapped_text_height(
            response.text,
            self.item_body_font,
            max_width=width - CARD_RESPONSE_PADDING_X * 2,
            max_lines=5,
        )
        if response.index == 1:
            preview_height = self._preview_height(
                item.response_preview_image_id,
                max_width=width - CARD_RESPONSE_PADDING_X * 2,
                max_height=CARD_IMAGE_MAX_HEIGHT,
            )
            if preview_height > 0:
                total += 10 + preview_height
        total += CARD_RESPONSE_PADDING_Y
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

    def _item_tag_texts(
        self,
        *,
        item: WordbankSearchItem,
        query: SearchCardQuery | None,
        locale: LocaleCode,
    ) -> tuple[str, ...]:
        matched_by = item.matched_by or fallback_match_label(
            has_image=query.has_image if query is not None else False,
            keyword=query.keyword if query is not None else "",
            creator_id=query.creator_id if query is not None else "",
            locale=locale,
        )
        return (
            f"#{item.trigger_group_id}",
            tr(locale, "wordbank.search_card.status", status=item.status),
            tr(
                locale,
                "wordbank.search_card.response_count",
                count=item.response_count,
            ),
            tr(locale, "wordbank.search_card.created_by", created_by=item.created_by),
            matched_by,
            f"{safe_field_label(locale, 'scope')} {item.scope}",
            f"{safe_field_label(locale, 'probability')} {item.probability:g}",
            f"{safe_field_label(locale, 'weight')} {item.weight}",
        )

    def _tags_height(self, *, tags: tuple[str, ...], width: int) -> int:
        cursor_x = 0
        rows = 1
        line_height_value = line_height(self.item_tag_font) + CARD_TAG_PADDING_Y
        for tag in tags:
            tag_width = text_width(tag, self.item_tag_font) + CARD_TAG_PADDING_X * 2
            if cursor_x and cursor_x + tag_width > width:
                rows += 1
                cursor_x = tag_width + CARD_TAG_GAP
            else:
                cursor_x += tag_width + CARD_TAG_GAP
        return rows * line_height_value + (rows - 1) * CARD_TAG_GAP

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

    def _contrast_text_color(self, background: str) -> str:
        rgb = ImageColor.getrgb(background)
        red, green, blue = rgb[:3]
        luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
        return "#FFF9FC" if luminance < 0.62 else self.HEADER

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
