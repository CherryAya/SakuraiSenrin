"""Pillow search card rendering for wordbank search results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
import math
from typing import Any

import arrow
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.demo_theme import SENRIN_V3_WORDBANK_CARD_THEME
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.types import WordbankSearchItem
from .search_card_helpers import (
    build_search_card_footer_text,
    fallback_match_label,
    folded_preview_note,
    has_folded_preview,
    response_preview,
    line_height,
    safe_field_label,
    safe_meta_label,
    summary_chips,
    text_width,
)

CARD_WIDTH = 1320
CARD_PADDING_X = 56
CARD_PADDING_Y = 48
CARD_ITEM_GAP = 24
CARD_ITEM_PADDING = 30
CARD_SUMMARY_GAP = 14
CARD_SECTION_GAP = 18
CARD_FLOW_GAP = 12
CARD_MAX_TEXT_WIDTH = CARD_WIDTH - CARD_PADDING_X * 2 - CARD_ITEM_PADDING * 2
CARD_IMAGE_WIDTH = CARD_MAX_TEXT_WIDTH - 8
CARD_PREVIEW_MAX_HEIGHT = 350
CARD_IMAGE_RADIUS = 24
CARD_FOLDED_BLOCK_MIN_HEIGHT = 64
CARD_FOOTER_HEIGHT = 82
CARD_FOOTER_LINE_GAP = 6
CARD_RADIUS = 24
TEXTURE_SPACING = 40
TEXTURE_DOT_RADIUS = 2


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
        self.TRIGGER_PANEL = self.theme.trigger_panel
        self.TRIGGER_BORDER = self.theme.trigger_border
        self.RESPONSE_PANEL = self.theme.response_panel
        self.RESPONSE_BORDER = self.theme.response_border
        self.PANEL = self.theme.panel
        self.HEADER = self.theme.header
        self.BODY = self.theme.body
        self.MUTED = self.theme.muted
        self.ACCENT = self.theme.accent
        self.ACCENT_DEEP = self.theme.accent_deep
        self.ACCENT_SOFT = self.theme.accent_soft
        self.BORDER = self.theme.border
        self.TEXTURE = self.theme.texture
        self.BADGE_TEXT = self.theme.badge_text
        self.title_font = self._load_font(42)
        self.summary_font = self._load_font(24)
        self.item_title_font = self._load_font(28)
        self.item_body_font = self._load_font(30)
        self.item_meta_font = self._load_font(20)
        self.footer_font = self._load_font(18)
        self.footer_minor_font = self._load_font(16)
        self.preview_bytes = dict(preview_bytes or {})
        self._preview_size_cache: dict[
            tuple[int, int, int],
            tuple[int, int] | None,
        ] = {}

    def render(
        self,
        *,
        items: tuple[WordbankSearchItem, ...],
        query: SearchCardQuery,
        locale: LocaleCode,
    ) -> bytes:
        height = self._measure_height(items, query, locale)
        image = Image.new("RGB", (CARD_WIDTH, height), self.theme.bg)
        draw = ImageDraw.Draw(image)
        self._draw_background_texture(draw, height)

        cursor_y = CARD_PADDING_Y
        cursor_y = self._draw_header(draw, query, locale, cursor_y)
        cursor_y += 22
        cursor_y = self._draw_summary(draw, query, locale, cursor_y)
        cursor_y += 30

        if items:
            for index, item in enumerate(items, start=1):
                cursor_y = self._draw_item(
                    image,
                    draw,
                    item,
                    query,
                    locale,
                    index,
                    cursor_y,
                )
                cursor_y += CARD_ITEM_GAP
        else:
            cursor_y = self._draw_empty_state(draw, query, locale, cursor_y)

        cursor_y += 14
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
        height = CARD_PADDING_Y
        height += line_height(self.title_font) + 12
        height += self._summary_block_height(query, locale) + 22
        if items:
            for index, item in enumerate(items, start=1):
                height += self._item_block_height(item, index, locale)
                if index < len(items):
                    height += CARD_ITEM_GAP
        else:
            height += 132
        height += CARD_FOOTER_HEIGHT + 20
        return height

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
        title = tr(locale, "wordbank.search_card.title")
        page_text = tr(
            locale,
            "wordbank.search_card.page",
            page=query.page,
            total_pages=query.total_pages,
        )
        draw.text(
            (CARD_PADDING_X, cursor_y),
            f"SakuraiSenrin {title}",
            font=self.title_font,
            fill=self.HEADER,
        )
        page_width = int(draw.textlength(page_text, font=self.summary_font))
        draw.text(
            (CARD_WIDTH - CARD_PADDING_X - page_width, cursor_y + 10),
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
        box_height = self._summary_block_height(query, locale)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + box_height,
            ),
            radius=CARD_RADIUS,
            fill=self.PANEL,
            outline=self.BORDER,
            width=1,
        )

        chip_x = CARD_PADDING_X + 22
        chip_y = cursor_y + 18
        max_x = CARD_WIDTH - CARD_PADDING_X - 22
        for chip_text in summary_chips(
            keyword=query.keyword,
            field=query.field,
            creator_id=query.creator_id,
            has_image=query.has_image,
            locale=locale,
            field_label=self._field_label(query.field, locale),
        ):
            chip_width = text_width(chip_text, self.summary_font) + 28
            chip_height = line_height(self.summary_font) + 6
            if chip_x + chip_width > max_x:
                chip_x = CARD_PADDING_X + 22
                chip_y += chip_height + 10
            draw.rounded_rectangle(
                (
                    chip_x,
                    chip_y,
                    chip_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=chip_height // 2,
                fill=self.ACCENT_SOFT,
            )
            draw.text(
                (chip_x + 14, chip_y + 4),
                chip_text,
                font=self.summary_font,
                fill=self.HEADER,
            )
            chip_x += chip_width + 10
        return cursor_y + box_height

    def _draw_item(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: WordbankSearchItem,
        query: SearchCardQuery,
        locale: LocaleCode,
        index: int,
        cursor_y: int,
    ) -> int:
        height = self._item_block_height(item, index, locale)
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

        inner_x = CARD_PADDING_X + CARD_ITEM_PADDING
        inner_y = cursor_y + CARD_ITEM_PADDING

        badge = f"{index + (query.page - 1) * query.limit:02d}"
        badge_size = 48
        badge_center_y = inner_y + badge_size // 2
        draw.ellipse(
            (
                inner_x,
                inner_y,
                inner_x + badge_size,
                inner_y + badge_size,
            ),
            fill=self.ACCENT,
        )
        badge_width = text_width(badge, self.item_title_font)
        badge_height = line_height(self.item_title_font) - 8
        draw.text(
            (
                inner_x + (badge_size - badge_width) / 2,
                badge_center_y - badge_height / 2 - 2,
            ),
            badge,
            font=self.item_title_font,
            fill=self.BADGE_TEXT,
        )

        meta_text = (
            tr(locale, "wordbank.search_card.group_id", group_id=item.trigger_group_id)
            + "  "
            + tr(locale, "wordbank.search_card.status", status=item.status)
            + "  "
            + tr(
                locale,
                "wordbank.search_card.response_count",
                count=item.response_count,
            )
            + "  "
            + tr(locale, "wordbank.search_card.created_by", created_by=item.created_by)
        )
        draw.text(
            (inner_x + badge_size + 18, inner_y + 11),
            meta_text,
            font=self.item_meta_font,
            fill=self.MUTED,
        )

        cursor_y_body = inner_y + badge_size + 18
        cursor_y_body = self._draw_flow_panel(
            image,
            draw,
            title=tr(locale, "wordbank.search_card.label.trigger"),
            text=item.trigger_text or tr(locale, "wordbank.search_card.none"),
            image_id=item.trigger_preview_image_id,
            x=inner_x,
            y=cursor_y_body,
            width=CARD_MAX_TEXT_WIDTH,
            fill=self.TRIGGER_PANEL,
            outline=self.TRIGGER_BORDER,
        )
        cursor_y_body += CARD_SECTION_GAP
        cursor_y_body = self._draw_flow_panel(
            image,
            draw,
            title=tr(locale, "wordbank.search_card.label.response_summary"),
            text=response_preview(item, locale),
            image_id=item.response_preview_image_id,
            x=inner_x,
            y=cursor_y_body,
            width=CARD_MAX_TEXT_WIDTH,
            fill=self.RESPONSE_PANEL,
            outline=self.RESPONSE_BORDER,
        )
        if has_folded_preview(item):
            cursor_y_body += CARD_FLOW_GAP
            cursor_y_body = self._draw_folded_preview_block(
                image,
                draw,
                item,
                x=inner_x,
                y=cursor_y_body,
                width=CARD_MAX_TEXT_WIDTH,
                locale=locale,
            )
        cursor_y_body += CARD_SECTION_GAP
        cursor_y_body = self._draw_meta_line(
            draw,
            x=inner_x,
            y=cursor_y_body,
            text=(
                f"{tr(locale, 'wordbank.search_card.label.matched_by')}: "
                f"{item.matched_by or fallback_match_label(
                    has_image=query.has_image,
                    keyword=query.keyword,
                    creator_id=query.creator_id,
                    locale=locale,
                )}"
            ),
            align="left",
        )
        cursor_y_body += 6
        config_text = (
            f"{safe_meta_label(locale)}  "
            f"{safe_field_label(locale, 'scope')}: {item.scope}  "
            f"{safe_field_label(locale, 'probability')}: {item.probability:g}  "
            f"{safe_field_label(locale, 'weight')}: {item.weight}"
        )
        self._draw_meta_line(
            draw,
            x=inner_x,
            y=cursor_y_body,
            text=config_text,
            align="left",
        )
        return cursor_y + height

    def _draw_flow_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        title: str,
        text: str,
        image_id: int | None,
        x: int,
        y: int,
        width: int,
        fill: str,
        outline: str,
    ) -> int:
        panel_height = self._flow_panel_height(
            text=text,
            image_id=image_id,
            width=width,
        )
        draw.rounded_rectangle(
            (
                x,
                y,
                x + width,
                y + panel_height,
            ),
            radius=CARD_RADIUS,
            fill=fill,
            outline=outline,
            width=1,
        )

        inner_x = x + 24
        inner_y = y + 18
        label_width = text_width(title, self.item_meta_font) + 28
        draw.rounded_rectangle(
            (
                inner_x,
                inner_y,
                inner_x + label_width,
                inner_y + 32,
            ),
            radius=16,
            fill=self.PANEL,
        )
        draw.text(
            (inner_x + 14, inner_y + 6),
            title,
            font=self.item_meta_font,
            fill=self.ACCENT_DEEP,
        )
        cursor_y = inner_y + 44
        cursor_y = self._draw_wrapped_text(
            draw,
            x=inner_x,
            y=cursor_y,
            text=text,
            font=self.item_body_font,
            fill=self.BODY,
            max_width=width - 48,
            max_lines=5,
        )
        if image_id is not None:
            preview = self._prepare_preview_image(
                image_id,
                max_width=min(CARD_IMAGE_WIDTH, width - 48),
                max_height=CARD_PREVIEW_MAX_HEIGHT,
            )
            if preview is not None:
                cursor_y += CARD_FLOW_GAP
                preview_x = inner_x + int((width - 48 - preview.width) / 2)
                self._paste_rounded_image(
                    image,
                    preview,
                    (preview_x, cursor_y),
                    radius=CARD_IMAGE_RADIUS,
                )
                cursor_y += preview.height
        return y + panel_height

    def _flow_panel_height(
        self,
        *,
        text: str,
        image_id: int | None,
        width: int,
    ) -> int:
        total = 18 + 44
        total += self._wrapped_text_height(
            text,
            self.item_body_font,
            max_width=width - 48,
            max_lines=5,
        )
        if image_id is not None:
            preview_height = self._preview_height(
                image_id,
                max_width=min(CARD_IMAGE_WIDTH, width - 48),
                max_height=CARD_PREVIEW_MAX_HEIGHT,
            )
            if preview_height > 0:
                total += CARD_FLOW_GAP + preview_height
        total += 18
        return total

    def _draw_folded_preview_block(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: WordbankSearchItem,
        *,
        x: int,
        y: int,
        width: int,
        locale: LocaleCode,
    ) -> int:
        _ = image
        hint = folded_preview_note(item, locale)
        text_height = self._wrapped_text_height(
            hint,
            self.item_meta_font,
            max_width=width - 48,
            max_lines=3,
        )
        block_height = max(CARD_FOLDED_BLOCK_MIN_HEIGHT, text_height + 28)
        draw.rounded_rectangle(
            (
                x + 12,
                y + 3,
                x + width + 12,
                y + block_height + 3,
            ),
            radius=20,
            fill=self.theme.folded_shadow_fill,
        )
        draw.rounded_rectangle(
            (
                x,
                y,
                x + width,
                y + block_height,
            ),
            radius=20,
            fill=self.theme.accent_soft,
            outline=self.theme.folded_outline,
            width=1,
        )
        lines = self._wrap_text(
            hint,
            self.item_meta_font,
            max_width=width - 48,
            max_lines=3,
        )
        cursor_y = y + int(
            (block_height - len(lines) * line_height(self.item_meta_font)) / 2
        )
        for line in lines:
            line_width = text_width(line, self.item_meta_font)
            draw.text(
                (x + (width - line_width) / 2, cursor_y),
                line,
                font=self.item_meta_font,
                fill=self.ACCENT_DEEP,
            )
            cursor_y += line_height(self.item_meta_font)
        return y + block_height

    def _draw_meta_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        align: str,
    ) -> int:
        lines = self._wrap_text(
            text,
            self.item_meta_font,
            max_width=CARD_MAX_TEXT_WIDTH,
            max_lines=2,
        )
        cursor_y = y
        for line in lines:
            line_width = text_width(line, self.item_meta_font)
            line_x = x
            if align == "right":
                line_x = x + CARD_MAX_TEXT_WIDTH - line_width
            draw.text(
                (line_x, cursor_y),
                line,
                font=self.item_meta_font,
                fill=self.MUTED,
            )
            cursor_y += line_height(self.item_meta_font)
        return cursor_y

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
                cursor_y + 132,
            ),
            radius=CARD_RADIUS,
            fill=self.PANEL,
            outline=self.BORDER,
            width=1,
        )
        draw.text(
            (CARD_PADDING_X + 28, cursor_y + 32),
            tr(locale, "wordbank.search_card.empty", page=query.page),
            font=self.item_title_font,
            fill=self.HEADER,
        )
        draw.text(
            (CARD_PADDING_X + 28, cursor_y + 78),
            tr(locale, "wordbank.search_card.empty_hint"),
            font=self.item_meta_font,
            fill=self.MUTED,
        )
        return cursor_y + 132

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> None:
        footer_time = arrow.get(get_current_time()).to("Asia/Shanghai")
        copyright_text = build_search_card_footer_text(footer_time.year)
        generated_at_text = tr(
            locale,
            "water.image.generated_at",
            time=footer_time.format("YYYY-MM-DD HH:mm:ss"),
        )
        footer = tr(locale, "wordbank.search_card.total", total=query.total_count)
        if query.page < query.total_pages:
            footer += "  " + tr(locale, "wordbank.search_card.next_page", next_page=query.page + 1)
        draw.text(
            self._centered_text_origin(draw, copyright_text, self.footer_minor_font, y=cursor_y),
            copyright_text,
            font=self.footer_minor_font,
            fill=self.MUTED,
        )
        generated_y = cursor_y + line_height(self.footer_minor_font)
        draw.text(
            (CARD_PADDING_X, generated_y),
            generated_at_text,
            font=self.footer_minor_font,
            fill=self.MUTED,
        )
        footer_y = generated_y + line_height(self.footer_minor_font) + CARD_FOOTER_LINE_GAP
        draw.text(
            self._centered_text_origin(draw, footer, self.footer_font, y=footer_y),
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
        chip_height = line_height(self.summary_font) + 6
        current_x = CARD_PADDING_X + 22
        max_x = CARD_WIDTH - CARD_PADDING_X - 22
        rows = 1
        for chip_text in chips:
            chip_width = text_width(chip_text, self.summary_font) + 28
            if current_x + chip_width > max_x:
                rows += 1
                current_x = CARD_PADDING_X + 22 + chip_width + 10
                continue
            current_x += chip_width + 10
        return 18 + rows * chip_height + (rows - 1) * 10 + 18

    def _item_block_height(
        self,
        item: WordbankSearchItem,
        index: int,
        locale: LocaleCode,
    ) -> int:
        _ = index
        total = CARD_ITEM_PADDING * 2
        total += 48 + 18
        total += self._flow_panel_height(
            text=item.trigger_text or tr(locale, "wordbank.search_card.none"),
            image_id=item.trigger_preview_image_id,
            width=CARD_MAX_TEXT_WIDTH,
        )
        total += CARD_SECTION_GAP
        total += self._flow_panel_height(
            text=response_preview(item, locale),
            image_id=item.response_preview_image_id,
            width=CARD_MAX_TEXT_WIDTH,
        )
        if has_folded_preview(item):
            total += CARD_FLOW_GAP + self._folded_block_height(item, locale)
        total += CARD_SECTION_GAP
        total += self._wrapped_text_height(
            (
                f"{tr(locale, 'wordbank.search_card.label.matched_by')}: "
                f"{item.matched_by or tr(locale, 'wordbank.search_card.match.default')}"
            ),
            self.item_meta_font,
            max_width=CARD_MAX_TEXT_WIDTH,
            max_lines=2,
        )
        total += 6
        total += self._wrapped_text_height(
            (
                f"{safe_meta_label(locale)}  "
                f"{safe_field_label(locale, 'scope')}: {item.scope}  "
                f"{safe_field_label(locale, 'probability')}: {item.probability:g}  "
                f"{safe_field_label(locale, 'weight')}: {item.weight}"
            ),
            self.item_meta_font,
            max_width=CARD_MAX_TEXT_WIDTH,
            max_lines=2,
        )
        return total

    def _folded_block_height(self, item: WordbankSearchItem, locale: LocaleCode) -> int:
        text_height = self._wrapped_text_height(
            folded_preview_note(item, locale),
            self.item_meta_font,
            max_width=CARD_MAX_TEXT_WIDTH - 48,
            max_lines=3,
        )
        return max(CARD_FOLDED_BLOCK_MIN_HEIGHT, text_height + 28)

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
        lines = self._wrap_text(
            text,
            font,
            max_width=max_width,
            max_lines=max_lines,
        )
        cursor_y = int(y)
        for line in lines:
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
        lines = self._wrap_text(
            text,
            font,
            max_width=max_width,
            max_lines=max_lines,
        )
        return len(lines) * line_height(font)

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
            if not raw_line:
                lines.append("")
                continue
            current = ""
            for char in raw_line:
                candidate = f"{current}{char}"
                if text_width(candidate, font) <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = char
            if current:
                lines.append(current)
        if not lines:
            return [""]
        if max_lines is None or len(lines) <= max_lines:
            return [self._truncate_line(line, font, max_width) for line in lines]
        truncated = lines[:max_lines]
        truncated[-1] = self._truncate_line(
            f"{truncated[-1]}...",
            font,
            max_width,
        )
        return truncated

    def _truncate_line(self, text: str, font: Any, max_width: int) -> str:
        if text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..."

    def _preview_height(
        self,
        image_id: int,
        *,
        max_width: int,
        max_height: int,
    ) -> int:
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

        scale = 1.0
        if width > max_width:
            scale = max_width / width
            width = max(1, int(width * scale))
            height = max(1, int(height * scale))
        if height > max_height:
            height = max_height
        return (width, height)

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
                if prepared.width > target_width:
                    scale = target_width / prepared.width
                    prepared = prepared.resize(
                        (
                            target_width,
                            max(1, int(prepared.height * scale)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                if prepared.height > target_height:
                    top = max(0, int((prepared.height - target_height) / 2))
                    prepared = prepared.crop(
                        (0, top, prepared.width, top + target_height)
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

    def _centered_text_origin(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        *,
        y: int,
    ) -> tuple[int, int]:
        rendered_width = int(draw.textlength(text, font=font))
        return (int((CARD_WIDTH - rendered_width) / 2), y)

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


def render_search_results_card(
    *,
    items: tuple[WordbankSearchItem, ...],
    query: SearchCardQuery,
    locale: LocaleCode,
    preview_bytes: Mapping[int, bytes | None] | None = None,
) -> Message:
    image_bytes = render_search_results_card_bytes(
        items=items,
        query=query,
        locale=locale,
        preview_bytes=preview_bytes,
    )
    return Message(MessageSegment.image(image_bytes))
