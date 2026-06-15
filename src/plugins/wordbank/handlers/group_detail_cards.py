"""Pillow rendering for wordbank group detail pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import arrow
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankResponseItemDetail,
)
from src.plugins.wordbank.message_model import MessageShape, shape_to_summary_text

from .search_cards import _build_copyright_text

CARD_WIDTH = 1320
CARD_PADDING_X = 56
CARD_PADDING_Y = 48
CARD_PANEL_PADDING = 26
CARD_SECTION_GAP = 22
CARD_LINE_GAP = 10
CARD_TEXT_WIDTH = CARD_WIDTH - CARD_PADDING_X * 2 - CARD_PANEL_PADDING * 2
CARD_PREVIEW_HEIGHT = 156
CARD_PREVIEW_RADIUS = 20
CARD_PREVIEW_GAP = 14
CARD_FOOTER_HEIGHT = 110
CARD_FOOTER_LINE_GAP = 6


@dataclass(slots=True, frozen=True)
class GroupDetailCardPage:
    detail: WordbankGroupDetail
    page: int
    total_pages: int
    page_size: int
    start_index: int
    responses: tuple[WordbankResponseItemDetail, ...]


class GroupDetailCardRenderer:
    BG = "#FFF8F3"
    PANEL = "#FFFFFF"
    PANEL_SOFT = "#FFF4EF"
    HEADER = "#2F2A34"
    BODY = "#564D5C"
    MUTED = "#8A7B86"
    ACCENT = "#D9826B"
    ACCENT_SOFT = "#FBE9E2"
    BORDER = "#E8D5CF"
    TRIGGER_PREVIEW_BG = "#FCEFE8"

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
        self.footer_font = self._load_font(18)
        self.footer_minor_font = self._load_font(16)
        self.preview_bytes = dict(preview_bytes or {})

    def render(
        self,
        *,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> bytes:
        height = self._measure_height(page_data, locale)
        image = Image.new("RGB", (CARD_WIDTH, height), self.BG)
        draw = ImageDraw.Draw(image)

        cursor_y = CARD_PADDING_Y
        cursor_y = self._draw_header(draw, page_data, locale, cursor_y)
        cursor_y += 22
        cursor_y = self._draw_summary(draw, page_data, locale, cursor_y)
        cursor_y += 24
        cursor_y = self._draw_trigger_panel(
            image,
            draw,
            page_data.detail,
            locale,
            cursor_y,
        )
        cursor_y += CARD_SECTION_GAP

        for index, response in enumerate(
            page_data.responses, start=page_data.start_index + 1
        ):
            cursor_y = self._draw_response_panel(
                image,
                draw,
                response,
                absolute_index=index,
                locale=locale,
                cursor_y=cursor_y,
            )
            cursor_y += CARD_SECTION_GAP

        self._draw_footer(draw, page_data, locale, cursor_y)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _measure_height(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PADDING_Y
        total += self._line_height(self.title_font) + 12
        total += self._summary_block_height(page_data, locale) + 22
        total += self._shape_panel_height(
            label=tr(locale, "wordbank.group.card.trigger_label"),
            shape=page_data.detail.trigger_shape,
            locale=locale,
        )
        total += CARD_SECTION_GAP
        for response in page_data.responses:
            total += self._response_panel_height(response, locale)
            total += CARD_SECTION_GAP
        total += CARD_FOOTER_HEIGHT + 16
        return total

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        title = tr(locale, "wordbank.group.card.title")
        page_text = tr(
            locale,
            "wordbank.group.card.page",
            page=page_data.page,
            total_pages=page_data.total_pages,
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
        return cursor_y + self._line_height(self.title_font)

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        box_height = self._summary_block_height(page_data, locale)
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
        for line in self._summary_lines(page_data, locale):
            draw.text(
                (CARD_PADDING_X + 24, text_y),
                line,
                font=self.summary_font,
                fill=self.BODY,
            )
            text_y += self._line_height(self.summary_font) + 12
        return cursor_y + box_height

    def _draw_trigger_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        detail: WordbankGroupDetail,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        return self._draw_shape_panel(
            image,
            draw,
            label=tr(locale, "wordbank.group.card.trigger_label"),
            shape=detail.trigger_shape,
            top=cursor_y,
            accent_fill=self.TRIGGER_PREVIEW_BG,
            locale=locale,
        )

    def _draw_response_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: WordbankResponseItemDetail,
        *,
        absolute_index: int,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        height = self._response_panel_height(response, locale)
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
        inner_x = CARD_PADDING_X + CARD_PANEL_PADDING
        inner_y = cursor_y + CARD_PANEL_PADDING

        badge = f"{absolute_index:03d}"
        badge_bbox = draw.textbbox((0, 0), badge, font=self.item_title_font)
        badge_width = badge_bbox[2] - badge_bbox[0] + 26
        badge_height = badge_bbox[3] - badge_bbox[1] + 12
        draw.rounded_rectangle(
            (inner_x, inner_y, inner_x + badge_width, inner_y + badge_height),
            radius=18,
            fill=self.ACCENT_SOFT,
        )
        draw.text(
            (inner_x + 13, inner_y + 6),
            badge,
            font=self.item_title_font,
            fill=self.ACCENT,
        )

        meta_text = tr(
            locale,
            "wordbank.group.card.response_label",
            response_item_id=response.response_item_id,
            status=response.status,
            enabled=_format_enabled(response.enabled),
            scope=response.scope,
            weight=response.weight,
        )
        draw.text(
            (inner_x + badge_width + 18, inner_y + 8),
            meta_text,
            font=self.item_meta_font,
            fill=self.MUTED,
        )

        body_y = int(inner_y + badge_height + 18)
        summary = self._shape_summary(response.response_shape, locale)
        body_y = self._draw_labeled_lines(
            draw,
            x=inner_x,
            y=body_y,
            label=tr(locale, "wordbank.search_card.label.response_summary"),
            value=summary,
            max_lines=4,
        )
        body_y += CARD_LINE_GAP

        rule_text = _format_rule_text(response.rule)
        body_y = self._draw_labeled_lines(
            draw,
            x=inner_x,
            y=body_y,
            label=tr(locale, "wordbank.group.card.rule_label"),
            value=rule_text,
            max_lines=3,
        )
        body_y += CARD_LINE_GAP

        image_id = _first_image_id(response.response_shape)
        if image_id is not None:
            body_y = self._draw_preview(
                image,
                draw,
                image_id=image_id,
                x=inner_x,
                y=body_y,
                width=CARD_TEXT_WIDTH,
            )
            body_y += CARD_PREVIEW_GAP
        return cursor_y + height

    def _draw_shape_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        label: str,
        shape: MessageShape,
        top: int,
        accent_fill: str,
        locale: LocaleCode,
    ) -> int:
        height = self._shape_panel_height(label=label, shape=shape, locale=locale)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                top,
                CARD_WIDTH - CARD_PADDING_X,
                top + height,
            ),
            radius=28,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        inner_x = CARD_PADDING_X + CARD_PANEL_PADDING
        inner_y = top + CARD_PANEL_PADDING

        chip_width = self._text_width(label, self.item_meta_font) + 30
        draw.rounded_rectangle(
            (inner_x, inner_y, inner_x + chip_width, inner_y + 30),
            radius=15,
            fill=accent_fill,
        )
        draw.text(
            (inner_x + 15, inner_y + 7),
            label,
            font=self.item_meta_font,
            fill=self.ACCENT,
        )

        body_y = inner_y + 46
        body_y = self._draw_labeled_lines(
            draw,
            x=inner_x,
            y=body_y,
            label=tr(locale, "wordbank.group.card.content_label"),
            value=self._shape_summary(shape, locale),
            max_lines=4,
        )
        body_y += CARD_LINE_GAP

        image_id = _first_image_id(shape)
        if image_id is not None:
            body_y = self._draw_preview(
                image,
                draw,
                image_id=image_id,
                x=inner_x,
                y=body_y,
                width=CARD_TEXT_WIDTH,
            )
            body_y += CARD_PREVIEW_GAP
        return top + height

    def _shape_panel_height(
        self,
        *,
        label: str,
        shape: MessageShape,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PANEL_PADDING * 2 + 46
        content_label = tr(locale, "wordbank.group.card.content_label")
        summary = self._shape_summary(shape, locale)
        wrapped = self._wrap_text(
            f"{content_label}: {summary}",
            self.item_body_font,
            max_lines=4,
        )
        total += len(wrapped) * self._line_height(self.item_body_font)
        total += CARD_LINE_GAP
        if _first_image_id(shape) is not None:
            total += CARD_PREVIEW_HEIGHT + CARD_PREVIEW_GAP
        _ = label
        return total

    def _response_panel_height(
        self,
        response: WordbankResponseItemDetail,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PANEL_PADDING * 2
        badge_bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "000",
            font=self.item_title_font,
        )
        total += int(badge_bbox[3] - badge_bbox[1] + 30)
        summary = self._shape_summary(response.response_shape, locale)
        wrapped_summary = self._wrap_text(
            f"{tr(locale, 'wordbank.search_card.label.response_summary')}: {summary}",
            self.item_body_font,
            max_lines=4,
        )
        total += len(wrapped_summary) * self._line_height(self.item_body_font)
        total += CARD_LINE_GAP
        rule_label = tr(locale, "wordbank.group.card.rule_label")
        rule_value = _format_rule_text(response.rule)
        wrapped_rule = self._wrap_text(
            f"{rule_label}: {rule_value}",
            self.item_body_font,
            max_lines=3,
        )
        total += len(wrapped_rule) * self._line_height(self.item_body_font)
        total += CARD_LINE_GAP
        if _first_image_id(response.response_shape) is not None:
            total += CARD_PREVIEW_HEIGHT + CARD_PREVIEW_GAP
        return int(total)

    def _draw_preview(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        image_id: int,
        x: int,
        y: int,
        width: int,
    ) -> int:
        box = (x, y, x + width, y + CARD_PREVIEW_HEIGHT)
        draw.rounded_rectangle(
            box,
            radius=CARD_PREVIEW_RADIUS,
            fill=self.ACCENT_SOFT,
            outline=self.BORDER,
            width=2,
        )
        image_bytes = self.preview_bytes.get(image_id)
        if image_bytes is not None:
            preview = self._fit_preview_image(
                image_bytes,
                width,
                CARD_PREVIEW_HEIGHT,
            )
            if preview is not None:
                preview_x = x + int((width - preview.width) / 2)
                preview_y = y + int((CARD_PREVIEW_HEIGHT - preview.height) / 2)
                image.paste(preview, (preview_x, preview_y))
        return y + CARD_PREVIEW_HEIGHT

    def _draw_labeled_lines(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        label: str,
        value: str,
        max_lines: int = 3,
    ) -> int:
        wrapped = self._wrap_text(
            f"{label}: {value}",
            self.item_body_font,
            max_lines=max_lines,
        )
        cursor_y = y
        for line in wrapped:
            draw.text((x, cursor_y), line, font=self.item_body_font, fill=self.BODY)
            cursor_y += self._line_height(self.item_body_font)
        return cursor_y

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
        cursor_y: int,
    ) -> None:
        footer_time = arrow.get(get_current_time()).to("Asia/Shanghai")
        copyright_text = _build_copyright_text(footer_time.year)
        generated_at_text = tr(
            locale,
            "water.image.generated_at",
            time=footer_time.format("YYYY-MM-DD HH:mm:ss"),
        )
        stats_text = tr(
            locale,
            "wordbank.group.card.footer_stats",
            total=len(page_data.detail.responses),
            page_size=page_data.page_size,
            page=page_data.page,
            total_pages=page_data.total_pages,
        )
        example_page = min(
            page_data.total_pages,
            max(1, page_data.page + 1),
        )
        command_text = tr(
            locale,
            "wordbank.group.card.footer_command",
            group_id=page_data.detail.trigger_group_id,
            example_page=example_page,
        )
        draw.text(
            self._centered_text_origin(
                draw, copyright_text, self.footer_minor_font, y=cursor_y
            ),
            copyright_text,
            font=self.footer_minor_font,
            fill=self.MUTED,
        )
        generated_y = cursor_y + self._line_height(self.footer_minor_font)
        draw.text(
            (CARD_PADDING_X, generated_y),
            generated_at_text,
            font=self.footer_minor_font,
            fill=self.MUTED,
        )
        stats_y = (
            generated_y
            + self._line_height(self.footer_minor_font)
            + CARD_FOOTER_LINE_GAP
        )
        draw.text(
            self._centered_text_origin(draw, stats_text, self.footer_font, y=stats_y),
            stats_text,
            font=self.footer_font,
            fill=self.MUTED,
        )
        command_y = stats_y + self._line_height(self.footer_font) + CARD_FOOTER_LINE_GAP
        draw.text(
            self._centered_text_origin(
                draw, command_text, self.footer_font, y=command_y
            ),
            command_text,
            font=self.footer_font,
            fill=self.ACCENT,
        )

    def _summary_lines(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> list[str]:
        detail = page_data.detail
        active_response_count = sum(
            1
            for item in detail.responses
            if item.status == "approved" and item.enabled == 1 and item.deleted_at == 0
        )
        return [
            tr(
                locale,
                "wordbank.group.card.summary",
                group_id=detail.trigger_group_id,
                status=detail.status,
                created_by=detail.created_by,
            ),
            tr(
                locale,
                "wordbank.group.card.summary_extra",
                probability=f"{detail.probability:g}",
                response_count=len(detail.responses),
                active_response_count=active_response_count,
            ),
        ]

    def _summary_block_height(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> int:
        line_height = self._line_height(self.summary_font)
        return (
            40
            + len(self._summary_lines(page_data, locale)) * line_height
            + (len(self._summary_lines(page_data, locale)) - 1) * 12
        )

    def _shape_summary(self, shape: MessageShape, locale: LocaleCode) -> str:
        summary = shape_to_summary_text(shape).strip()
        return summary or tr(locale, "wordbank.search_card.none")

    def _wrap_text(self, text: str, font: Any, *, max_lines: int = 3) -> list[str]:
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
                if self._text_width(candidate, font) <= CARD_TEXT_WIDTH:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = char
            if current:
                lines.append(current)
        if len(lines) <= max_lines:
            return [self._truncate_line(line, font) for line in lines or [""]]
        truncated = lines[:max_lines]
        truncated[-1] = self._truncate_line(f"{truncated[-1]}...", font)
        return truncated

    def _truncate_line(self, text: str, font: Any) -> str:
        if self._text_width(text, font) <= CARD_TEXT_WIDTH:
            return text
        candidate = text
        while candidate and self._text_width(f"{candidate}...", font) > CARD_TEXT_WIDTH:
            candidate = candidate[:-1]
        return f"{candidate}..."

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

    def _line_height(self, font: Any) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        return int(bbox[3] - bbox[1] + 6)

    def _text_width(self, text: str, font: Any) -> int:
        return int(
            ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(text, font=font)
        )

    def _centered_text_origin(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        *,
        y: int,
    ) -> tuple[int, int]:
        text_width = int(draw.textlength(text, font=font))
        return (int((CARD_WIDTH - text_width) / 2), y)

    def _load_font(self, size: int) -> Any:
        try:
            return ImageFont.truetype(MAPLE_FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()


def render_group_detail_card_bytes(
    *,
    page_data: GroupDetailCardPage,
    locale: LocaleCode,
    preview_bytes: Mapping[int, bytes | None] | None = None,
) -> bytes:
    renderer = GroupDetailCardRenderer(preview_bytes=preview_bytes)
    return renderer.render(page_data=page_data, locale=locale)


def render_group_detail_card(
    *,
    page_data: GroupDetailCardPage,
    locale: LocaleCode,
    preview_bytes: Mapping[int, bytes | None] | None = None,
) -> Message:
    image_bytes = render_group_detail_card_bytes(
        page_data=page_data,
        locale=locale,
        preview_bytes=preview_bytes,
    )
    return Message(MessageSegment.image(image_bytes))


def _first_image_id(shape: MessageShape) -> int | None:
    for atom in shape.atoms:
        if atom.kind == "image" and atom.canonical_image_id is not None:
            return atom.canonical_image_id
    return None


def _format_enabled(enabled: int) -> str:
    return "enabled" if enabled else "disabled"


def _format_rule_text(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    role = str(rule.get("roles", "") or "").strip()
    if role:
        parts.append(f"roles={role}")
    call_count = rule.get("call_count")
    if isinstance(call_count, dict):
        window_seconds = int(call_count.get("window_seconds", 0))
        min_count = int(call_count.get("min", 0))
        max_count = int(call_count.get("max", 0))
        parts.append(f"call={window_seconds}:{min_count}:{max_count}")
    return ", ".join(parts) if parts else "-"
