"""Pillow rendering for wordbank group detail pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import arrow
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankResponseItemDetail,
)
from src.plugins.wordbank.message_model import MessageAtom, MessageShape

from .search_cards import _build_copyright_text

CARD_WIDTH = 1320
CARD_PADDING_X = 56
CARD_PADDING_Y = 48
CARD_PANEL_PADDING = 30
CARD_SECTION_GAP = 22
CARD_TEXT_GAP = 10
CARD_FLOW_GAP = 12
CARD_TEXT_WIDTH = CARD_WIDTH - CARD_PADDING_X * 2 - CARD_PANEL_PADDING * 2
CARD_IMAGE_WIDTH = CARD_TEXT_WIDTH
CARD_PREVIEW_MAX_HEIGHT = 400
CARD_IMAGE_RADIUS = 24
CARD_FOOTER_HEIGHT = 110
CARD_FOOTER_LINE_GAP = 6
CARD_RADIUS = 24


@dataclass(slots=True, frozen=True)
class GroupDetailCardPage:
    detail: WordbankGroupDetail
    page: int
    total_pages: int
    page_size: int
    start_index: int
    responses: tuple[WordbankResponseItemDetail, ...]


@dataclass(slots=True, frozen=True)
class _ShapeBlock:
    kind: str
    text: str = ""
    image_id: int | None = None


class GroupDetailCardRenderer:
    BG = "#FDFBF7"
    PANEL = "#FFFFFF"
    PANEL_SOFT = "#FFF5F3"
    HEADER = "#2C2830"
    BODY = "#3A353F"
    MUTED = "#8E8794"
    ACCENT = "#E88B8B"
    ACCENT_SOFT = "#FBEAEA"
    BORDER = "#F0E5E1"

    def __init__(
        self,
        *,
        preview_bytes: Mapping[int, bytes | None] | None = None,
    ) -> None:
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(20)
        self.item_title_font = self._load_font(26)
        self.item_body_font = self._load_font(22)
        self.item_meta_font = self._load_font(17)
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
        cursor_y = self._draw_shape_panel(
            image,
            draw,
            label=tr(locale, "wordbank.group.card.trigger_label"),
            shape=page_data.detail.trigger_shape,
            top=cursor_y,
            panel_fill=self.PANEL_SOFT,
            locale=locale,
        )
        cursor_y += CARD_SECTION_GAP

        for index, response in enumerate(
            page_data.responses,
            start=page_data.start_index + 1,
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
        total += self._shape_panel_height(page_data.detail.trigger_shape, locale=locale)
        total += CARD_SECTION_GAP
        for response in page_data.responses:
            total += self._response_panel_height(response, locale=locale)
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
            radius=CARD_RADIUS,
            fill=self.ACCENT_SOFT,
            outline=self.BORDER,
            width=1,
        )

        chip_x = CARD_PADDING_X + 22
        chip_y = cursor_y + 18
        max_x = CARD_WIDTH - CARD_PADDING_X - 22
        for chip_text in self._summary_chips(page_data, locale):
            chip_width = self._text_width(chip_text, self.summary_font) + 26
            chip_height = self._line_height(self.summary_font) + 6
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
                radius=15,
                fill=self.PANEL,
            )
            draw.text(
                (chip_x + 13, chip_y + 4),
                chip_text,
                font=self.summary_font,
                fill=self.BODY,
            )
            chip_x += chip_width + 10
        return cursor_y + box_height

    def _draw_shape_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        label: str,
        shape: MessageShape,
        top: int,
        panel_fill: str,
        locale: LocaleCode,
    ) -> int:
        height = self._shape_panel_height(shape, locale=locale)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                top,
                CARD_WIDTH - CARD_PADDING_X,
                top + height,
            ),
            radius=CARD_RADIUS,
            fill=panel_fill,
            outline=self.BORDER,
            width=1,
        )

        inner_x = CARD_PADDING_X + CARD_PANEL_PADDING
        inner_y = top + CARD_PANEL_PADDING
        label_width = self._text_width(label, self.item_meta_font) + 28
        draw.rounded_rectangle(
            (
                inner_x,
                inner_y,
                inner_x + label_width,
                inner_y + 28,
            ),
            radius=14,
            fill=self.PANEL,
        )
        draw.text(
            (inner_x + 14, inner_y + 5),
            label,
            font=self.item_meta_font,
            fill=self.ACCENT,
        )
        body_y = inner_y + 40
        self._draw_shape_flow(
            image,
            draw,
            shape,
            x=inner_x,
            y=body_y,
            locale=locale,
        )
        return top + height

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
        height = self._response_panel_height(response, locale=locale)
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

        inner_x = CARD_PADDING_X + CARD_PANEL_PADDING
        inner_y = cursor_y + CARD_PANEL_PADDING

        badge = f"{absolute_index:03d}"
        badge_bbox = draw.textbbox((0, 0), badge, font=self.item_title_font)
        badge_width = int(badge_bbox[2] - badge_bbox[0] + 28)
        badge_height = int(badge_bbox[3] - badge_bbox[1] + 14)
        draw.rounded_rectangle(
            (
                inner_x,
                inner_y,
                inner_x + badge_width,
                inner_y + badge_height,
            ),
            radius=16,
            fill=self.ACCENT_SOFT,
        )
        draw.text(
            (inner_x + 14, inner_y + 7),
            badge,
            font=self.item_title_font,
            fill=self.ACCENT,
        )

        body_y = inner_y + badge_height + 18
        body_y = self._draw_shape_flow(
            image,
            draw,
            response.response_shape,
            x=inner_x,
            y=body_y,
            locale=locale,
        )
        body_y += CARD_FLOW_GAP
        self._draw_response_meta(
            draw,
            response,
            x=inner_x,
            y=body_y,
            locale=locale,
        )
        return cursor_y + height

    def _shape_panel_height(
        self,
        shape: MessageShape,
        *,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PANEL_PADDING * 2 + 40
        total += self._shape_flow_height(shape, locale=locale)
        return total

    def _response_panel_height(
        self,
        response: WordbankResponseItemDetail,
        *,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PANEL_PADDING * 2
        badge_bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "000",
            font=self.item_title_font,
        )
        total += int(badge_bbox[3] - badge_bbox[1] + 34)
        total += self._shape_flow_height(response.response_shape, locale=locale)
        total += CARD_FLOW_GAP
        total += self._response_meta_height(response, locale=locale)
        return total

    def _draw_shape_flow(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        shape: MessageShape,
        *,
        x: int,
        y: int,
        locale: LocaleCode,
    ) -> int:
        blocks = self._shape_blocks(shape, locale)
        cursor_y = y
        for index, block in enumerate(blocks):
            if block.kind == "text" and block.text:
                cursor_y = self._draw_wrapped_text(
                    draw,
                    x=x,
                    y=cursor_y,
                    text=block.text,
                    font=self.item_body_font,
                    fill=self.BODY,
                    max_width=CARD_TEXT_WIDTH,
                )
            elif block.kind == "image" and block.image_id is not None:
                drawn_bottom = self._draw_image_block(
                    image,
                    image_id=block.image_id,
                    x=x,
                    y=cursor_y,
                    width=CARD_IMAGE_WIDTH,
                )
                if drawn_bottom == cursor_y:
                    continue
                cursor_y = drawn_bottom
            if index < len(blocks) - 1:
                cursor_y += CARD_FLOW_GAP
        return cursor_y

    def _shape_flow_height(
        self,
        shape: MessageShape,
        *,
        locale: LocaleCode,
    ) -> int:
        total = 0
        blocks = self._shape_blocks(shape, locale)
        for index, block in enumerate(blocks):
            if block.kind == "text" and block.text:
                total += self._wrapped_text_height(
                    block.text,
                    self.item_body_font,
                    max_width=CARD_TEXT_WIDTH,
                )
            elif block.kind == "image" and block.image_id is not None:
                total += self._preview_height(
                    block.image_id,
                    max_width=CARD_IMAGE_WIDTH,
                    max_height=CARD_PREVIEW_MAX_HEIGHT,
                )
            if index < len(blocks) - 1:
                total += CARD_FLOW_GAP
        return total

    def _draw_response_meta(
        self,
        draw: ImageDraw.ImageDraw,
        response: WordbankResponseItemDetail,
        *,
        x: int,
        y: int,
        locale: LocaleCode,
    ) -> int:
        meta_line = tr(
            locale,
            "wordbank.group.card.response_label",
            response_item_id=response.response_item_id,
            status=response.status,
            enabled=_format_enabled(response.enabled),
            scope=response.scope,
            weight=response.weight,
        )
        cursor_y = self._draw_wrapped_text(
            draw,
            x=x,
            y=y,
            text=meta_line,
            font=self.item_meta_font,
            fill=self.MUTED,
            max_width=CARD_TEXT_WIDTH,
            max_lines=2,
        )
        cursor_y += 4
        rule_line = (
            f"{tr(locale, 'wordbank.group.card.rule_label')}: "
            f"{_format_rule_text(response.rule)}"
        )
        return self._draw_wrapped_text(
            draw,
            x=x,
            y=cursor_y,
            text=rule_line,
            font=self.item_meta_font,
            fill=self.MUTED,
            max_width=CARD_TEXT_WIDTH,
            max_lines=2,
        )

    def _response_meta_height(
        self,
        response: WordbankResponseItemDetail,
        locale: LocaleCode,
    ) -> int:
        meta_line = tr(
            locale,
            "wordbank.group.card.response_label",
            response_item_id=response.response_item_id,
            status=response.status,
            enabled=_format_enabled(response.enabled),
            scope=response.scope,
            weight=response.weight,
        )
        total = self._wrapped_text_height(
            meta_line,
            self.item_meta_font,
            max_width=CARD_TEXT_WIDTH,
            max_lines=2,
        )
        total += 4
        rule_line = (
            f"{tr(locale, 'wordbank.group.card.rule_label')}: "
            f"{_format_rule_text(response.rule)}"
        )
        total += self._wrapped_text_height(
            rule_line,
            self.item_meta_font,
            max_width=CARD_TEXT_WIDTH,
            max_lines=2,
        )
        return total

    def _draw_image_block(
        self,
        image: Image.Image,
        *,
        image_id: int,
        x: int,
        y: int,
        width: int,
    ) -> int:
        image_bytes = self.preview_bytes.get(image_id)
        if not image_bytes:
            return y
        preview = self._prepare_preview_image(
            image_bytes,
            max_width=width,
            max_height=CARD_PREVIEW_MAX_HEIGHT,
        )
        if preview is None:
            return y
        self._paste_rounded_image(image, preview, (x, y), radius=CARD_IMAGE_RADIUS)
        return y + preview.height

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
        example_page = min(page_data.total_pages, max(1, page_data.page + 1))
        command_text = tr(
            locale,
            "wordbank.group.card.footer_command",
            group_id=page_data.detail.trigger_group_id,
            example_page=example_page,
        )
        draw.text(
            self._centered_text_origin(
                draw,
                copyright_text,
                self.footer_minor_font,
                y=cursor_y,
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
                draw,
                command_text,
                self.footer_font,
                y=command_y,
            ),
            command_text,
            font=self.footer_font,
            fill=self.ACCENT,
        )

    def _summary_chips(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> tuple[str, ...]:
        detail = page_data.detail
        active_response_count = sum(
            1
            for item in detail.responses
            if item.status == "approved" and item.enabled == 1 and item.deleted_at == 0
        )
        raw_lines = (
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
        )
        return tuple(
            part.strip()
            for line in raw_lines
            for part in line.split("  ")
            if part.strip()
        )

    def _summary_block_height(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> int:
        chips = self._summary_chips(page_data, locale)
        if not chips:
            return 60
        chip_height = self._line_height(self.summary_font) + 6
        current_x = CARD_PADDING_X + 22
        max_x = CARD_WIDTH - CARD_PADDING_X - 22
        rows = 1
        for chip_text in chips:
            chip_width = self._text_width(chip_text, self.summary_font) + 26
            if current_x + chip_width > max_x:
                rows += 1
                current_x = CARD_PADDING_X + 22 + chip_width + 10
                continue
            current_x += chip_width + 10
        return 18 + rows * chip_height + (rows - 1) * 10 + 18

    def _shape_blocks(
        self,
        shape: MessageShape,
        locale: LocaleCode,
    ) -> tuple[_ShapeBlock, ...]:
        blocks: list[_ShapeBlock] = []
        text_buffer: list[str] = []
        for atom in shape.atoms:
            if atom.kind == "image" and atom.canonical_image_id is not None:
                if not self.preview_bytes.get(atom.canonical_image_id):
                    continue
                if text_buffer:
                    text = "".join(text_buffer).strip()
                    if text:
                        blocks.append(_ShapeBlock(kind="text", text=text))
                    text_buffer = []
                blocks.append(
                    _ShapeBlock(kind="image", image_id=atom.canonical_image_id)
                )
                continue

            atom_text = self._atom_text(atom, locale)
            if not atom_text:
                continue
            if text_buffer and not text_buffer[-1].endswith((" ", "\n")):
                if atom.kind in {"at", "event"}:
                    text_buffer.append(" ")
            text_buffer.append(atom_text)
        if text_buffer:
            text = "".join(text_buffer).strip()
            if text:
                blocks.append(_ShapeBlock(kind="text", text=text))
        if not blocks:
            blocks.append(
                _ShapeBlock(kind="text", text=tr(locale, "wordbank.search_card.none"))
            )
        return tuple(blocks)

    def _atom_text(self, atom: MessageAtom, locale: LocaleCode) -> str:
        if atom.kind == "text":
            return atom.text
        if atom.kind == "at" and atom.target_id:
            return f"[@:{atom.target_id}]"
        if atom.kind == "event" and atom.event_name:
            return tr(
                locale,
                "wordbank.shape.event_ref",
                event_name=atom.event_name,
            )
        return ""

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
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
        cursor_y = y
        for line in lines:
            draw.text((x, cursor_y), line, font=font, fill=fill)
            cursor_y += self._line_height(font)
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
        return len(lines) * self._line_height(font)

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
                if self._text_width(candidate, font) <= max_width:
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
        if self._text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and self._text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..."

    def _preview_height(self, image_id: int, *, max_width: int, max_height: int) -> int:
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
                prepared = preview.convert("RGB")
                width, height = prepared.size
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        scale = min(max_width / width, max_height / height, 1.0)
        return (max(1, int(width * scale)), max(1, int(height * scale)))

    def _prepare_preview_image(
        self,
        image_bytes: bytes,
        *,
        max_width: int,
        max_height: int,
    ) -> Image.Image | None:
        measured = self._measure_preview_size(
            image_bytes,
            max_width=max_width,
            max_height=max_height,
        )
        if measured is None:
            return None
        try:
            with Image.open(BytesIO(image_bytes)) as preview:
                prepared = preview.convert("RGB")
                return ImageOps.contain(prepared, measured).copy()
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
