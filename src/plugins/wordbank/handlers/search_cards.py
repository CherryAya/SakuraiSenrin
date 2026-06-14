"""Pillow search card rendering for wordbank search results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
import math
from typing import Any

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import WordbankSearchItem

CARD_WIDTH = 1320
CARD_PADDING_X = 56
CARD_PADDING_Y = 48
CARD_ITEM_GAP = 22
CARD_ITEM_PADDING = 26
CARD_SUMMARY_GAP = 14
CARD_LINE_GAP = 10
CARD_MAX_TEXT_WIDTH = CARD_WIDTH - CARD_PADDING_X * 2 - CARD_ITEM_PADDING * 2
CARD_PREVIEW_HEIGHT = 180
CARD_PREVIEW_GAP = 14
CARD_PREVIEW_RADIUS = 20


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
    BG = "#FFF9FB"
    PANEL = "#FFFFFF"
    HEADER = "#2E2533"
    BODY = "#5E5263"
    MUTED = "#8C7A88"
    ACCENT = "#E2799D"
    ACCENT_SOFT = "#FFE8F0"
    BORDER = "#F2D7E2"

    def __init__(
        self,
        *,
        preview_bytes: Mapping[int, bytes | None] | None = None,
    ) -> None:
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(22)
        self.item_title_font = self._load_font(24)
        self.item_body_font = self._load_font(22)
        self.item_meta_font = self._load_font(20)
        self.preview_bytes = dict(preview_bytes or {})

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

        cursor_y = CARD_PADDING_Y
        cursor_y = self._draw_header(draw, query, locale, cursor_y)
        cursor_y += 22
        cursor_y = self._draw_summary(draw, query, locale, cursor_y)
        cursor_y += 28

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

        cursor_y += 8
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
        height += self._line_height(self.title_font) + 12
        height += self._summary_block_height(query, locale) + 22
        if items:
            for index, item in enumerate(items, start=1):
                height += self._item_block_height(item, index, locale)
                if index < len(items):
                    height += CARD_ITEM_GAP
        else:
            height += 120
        height += 64
        return height

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
            title,
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
        return cursor_y + self._line_height(self.title_font)

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        lines = self._summary_lines(query, locale)
        box_height = self._summary_block_height(query, locale)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + box_height,
            ),
            radius=28,
            fill=self.ACCENT_SOFT,
            outline=self.BORDER,
            width=2,
        )
        text_y = cursor_y + 20
        for line in lines:
            draw.text(
                (CARD_PADDING_X + 24, text_y),
                line,
                font=self.summary_font,
                fill=self.BODY,
            )
            text_y += self._line_height(self.summary_font) + CARD_SUMMARY_GAP
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
            radius=28,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )

        inner_x = CARD_PADDING_X + CARD_ITEM_PADDING
        inner_y = cursor_y + CARD_ITEM_PADDING

        badge = f"{index + (query.page - 1) * query.limit:02d}"
        badge_bbox = draw.textbbox((0, 0), badge, font=self.item_title_font)
        badge_width = badge_bbox[2] - badge_bbox[0] + 26
        badge_height = badge_bbox[3] - badge_bbox[1] + 12
        draw.rounded_rectangle(
            (
                inner_x,
                inner_y,
                inner_x + badge_width,
                inner_y + badge_height,
            ),
            radius=18,
            fill=self.ACCENT_SOFT,
        )
        draw.text(
            (inner_x + 13, inner_y + 6),
            badge,
            font=self.item_title_font,
            fill=self.ACCENT,
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
            (inner_x + badge_width + 18, inner_y + 8),
            meta_text,
            font=self.item_meta_font,
            fill=self.MUTED,
        )

        body_y = int(inner_y + badge_height + 18)
        body_y = self._draw_previews(image, draw, item, inner_x, body_y, locale)
        for label, value in (
            (tr(locale, "wordbank.search_card.label.trigger"), item.trigger_text),
            (
                tr(locale, "wordbank.search_card.label.response_summary"),
                self._response_preview(item, locale),
            ),
            (
                tr(locale, "wordbank.search_card.label.matched_by"),
                item.matched_by or self._fallback_match_label(query, locale),
            ),
        ):
            body_y = self._draw_labeled_lines(
                draw,
                x=inner_x,
                y=body_y,
                label=label,
                value=value,
            )
            body_y += CARD_LINE_GAP
        return cursor_y + height

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
            radius=28,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        draw.text(
            (CARD_PADDING_X + 26, cursor_y + 32),
            tr(locale, "wordbank.search_card.empty", page=query.page),
            font=self.item_title_font,
            fill=self.HEADER,
        )
        draw.text(
            (CARD_PADDING_X + 26, cursor_y + 72),
            tr(locale, "wordbank.search_card.empty_hint"),
            font=self.item_body_font,
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
        footer = tr(locale, "wordbank.search_card.total", total=query.total_count)
        if query.page < query.total_pages:
            footer += "  " + tr(
                locale,
                "wordbank.search_card.next_page",
                next_page=query.page + 1,
            )
        draw.text(
            (CARD_PADDING_X, cursor_y),
            footer,
            font=self.item_meta_font,
            fill=self.MUTED,
        )

    def _summary_lines(self, query: SearchCardQuery, locale: LocaleCode) -> list[str]:
        lines = [
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
                    (
                        "wordbank.search_card.boolean.yes"
                        if query.has_image
                        else "wordbank.search_card.boolean.no"
                    ),
                ),
            ),
            tr(
                locale,
                "wordbank.search_card.summary.creator",
                creator_id=query.creator_id or tr(locale, "wordbank.search_card.none"),
            ),
        ]
        return lines

    def _summary_block_height(self, query: SearchCardQuery, locale: LocaleCode) -> int:
        line_height = self._line_height(self.summary_font)
        return (
            40
            + len(self._summary_lines(query, locale)) * line_height
            + (len(self._summary_lines(query, locale)) - 1) * CARD_SUMMARY_GAP
        )

    def _item_block_height(
        self,
        item: WordbankSearchItem,
        index: int,
        locale: LocaleCode,
    ) -> int:
        _ = index
        total = CARD_ITEM_PADDING * 2
        badge_bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "00",
            font=self.item_title_font,
        )
        total += int(badge_bbox[3] - badge_bbox[1] + 30)
        if self._preview_specs(item, locale):
            total += CARD_PREVIEW_HEIGHT + CARD_PREVIEW_GAP
        for label, value in (
            (tr(locale, "wordbank.search_card.label.trigger"), item.trigger_text),
            (
                tr(locale, "wordbank.search_card.label.response_summary"),
                self._response_preview(item, locale),
            ),
            (
                tr(locale, "wordbank.search_card.label.matched_by"),
                item.matched_by or tr(locale, "wordbank.search_card.match.default"),
            ),
        ):
            wrapped = self._wrap_text(f"{label}: {value}", self.item_body_font)
            total += len(wrapped) * self._line_height(self.item_body_font)
            total += CARD_LINE_GAP
        return int(total)

    def _draw_previews(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: WordbankSearchItem,
        x: int,
        y: int,
        locale: LocaleCode,
    ) -> int:
        specs = self._preview_specs(item, locale)
        if not specs:
            return y
        count = len(specs)
        box_gap = 18
        total_width = CARD_MAX_TEXT_WIDTH
        box_width = total_width if count == 1 else int((total_width - box_gap) / 2)
        current_x = x
        for label, image_id in specs:
            box = (
                current_x,
                y,
                current_x + box_width,
                y + CARD_PREVIEW_HEIGHT,
            )
            draw.rounded_rectangle(
                box,
                radius=CARD_PREVIEW_RADIUS,
                fill=self.ACCENT_SOFT,
                outline=self.BORDER,
                width=2,
            )
            image_bytes = self.preview_bytes.get(image_id)
            if image_bytes:
                preview = self._fit_preview_image(
                    image_bytes,
                    box_width,
                    CARD_PREVIEW_HEIGHT,
                )
                if preview is not None:
                    preview_x = current_x + int((box_width - preview.width) / 2)
                    preview_y = y + int((CARD_PREVIEW_HEIGHT - preview.height) / 2)
                    image.paste(preview, (preview_x, preview_y))
            draw.text(
                (current_x + 12, y + 10),
                label,
                font=self.item_meta_font,
                fill=self.ACCENT,
            )
            current_x += box_width + box_gap
        return y + CARD_PREVIEW_HEIGHT + CARD_PREVIEW_GAP

    def _preview_specs(
        self,
        item: WordbankSearchItem,
        locale: LocaleCode,
    ) -> list[tuple[str, int]]:
        specs: list[tuple[str, int]] = []
        if item.trigger_preview_image_id is not None:
            specs.append(
                (
                    tr(locale, "wordbank.search_card.preview.trigger"),
                    item.trigger_preview_image_id,
                )
            )
        if item.response_preview_image_id is not None:
            specs.append(
                (
                    tr(locale, "wordbank.search_card.preview.response"),
                    item.response_preview_image_id,
                )
            )
        return specs[:2]

    def _fit_preview_image(
        self,
        image_bytes: bytes,
        max_width: int,
        max_height: int,
    ) -> Image.Image | None:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                prepared = image.convert("RGB")
                prepared.thumbnail((max_width - 16, max_height - 16))
                return prepared.copy()
        except Exception:
            return None

    def _draw_labeled_lines(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        label: str,
        value: str,
    ) -> int:
        wrapped = self._wrap_text(f"{label}: {value}", self.item_body_font)
        cursor_y = y
        for line in wrapped:
            draw.text(
                (x, cursor_y),
                line,
                font=self.item_body_font,
                fill=self.BODY,
            )
            cursor_y += self._line_height(self.item_body_font)
        return cursor_y

    def _fallback_match_label(self, query: SearchCardQuery, locale: LocaleCode) -> str:
        if query.has_image and query.keyword:
            return tr(locale, "wordbank.search_card.match.text_image")
        if query.has_image:
            return tr(locale, "wordbank.search_card.match.image")
        if query.keyword:
            return tr(locale, "wordbank.search_card.match.text")
        if query.creator_id:
            return tr(locale, "wordbank.search_card.match.creator")
        return tr(locale, "wordbank.search_card.match.recent")

    def _response_preview(self, item: WordbankSearchItem, locale: LocaleCode) -> str:
        summaries = list(item.response_summaries[:3]) or (
            [item.response_text] if item.response_text else []
        )
        preview = " / ".join(summary for summary in summaries if summary)
        if item.remaining_response_count > 0:
            suffix = tr(
                locale,
                "wordbank.search_card.more_responses",
                count=item.remaining_response_count,
            )
            preview = f"{preview}{suffix}" if preview else suffix.strip()
        return preview or tr(locale, "wordbank.search_card.none")

    def _wrap_text(self, text: str, font: Any) -> list[str]:
        if not text:
            return [""]
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = f"{current}{char}"
            if self._text_width(candidate, font) <= CARD_MAX_TEXT_WIDTH:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = char
        if current:
            lines.append(current)
        return [self._truncate_line(line, font) for line in lines[:3]]

    def _truncate_line(self, text: str, font: Any) -> str:
        if self._text_width(text, font) <= CARD_MAX_TEXT_WIDTH:
            return text
        candidate = text
        while (
            candidate
            and self._text_width(f"{candidate}...", font) > CARD_MAX_TEXT_WIDTH
        ):
            candidate = candidate[:-1]
        return f"{candidate}..."

    def _field_label(self, field: str, locale: LocaleCode) -> str:
        return {
            "all": tr(locale, "wordbank.search_card.field.all"),
            "trigger": tr(locale, "wordbank.search_card.field.trigger"),
            "response": tr(locale, "wordbank.search_card.field.response"),
        }.get(field, field)

    def _line_height(self, font: Any) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        return int(bbox[3] - bbox[1] + 6)

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
