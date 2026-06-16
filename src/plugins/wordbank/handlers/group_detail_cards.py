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
from src.lib.demo_theme import SENRIN_V3_WORDBANK_CARD_THEME
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
CARD_SECTION_GAP = 24
CARD_FLOW_GAP = 12
CARD_RADIUS = 24
CARD_TEXT_INSET = 24
CARD_IMAGE_RADIUS = 24
CARD_FOOTER_HEIGHT = 118
CARD_FOOTER_LINE_GAP = 6
CARD_PREVIEW_MAX_HEIGHT = 350
TEXTURE_SPACING = 40
TEXTURE_DOT_RADIUS = 2
TREE_INDENT = 68
TREE_TRUNK_OFFSET = 28
TREE_BRANCH_GAP = 14
BADGE_SIZE = 56
DETAIL_CTA_MIN_HEIGHT = 82

TRIGGER_PANEL_X = CARD_PADDING_X
TRIGGER_PANEL_WIDTH = CARD_WIDTH - CARD_PADDING_X * 2
RESPONSE_PANEL_X = CARD_PADDING_X + TREE_INDENT
RESPONSE_PANEL_WIDTH = CARD_WIDTH - CARD_PADDING_X - RESPONSE_PANEL_X
TRUNK_X = RESPONSE_PANEL_X - TREE_TRUNK_OFFSET
RESPONSE_TEXT_WIDTH = (
    RESPONSE_PANEL_WIDTH - CARD_PANEL_PADDING * 2 - CARD_TEXT_INSET * 2
)
TRIGGER_TEXT_WIDTH = TRIGGER_PANEL_WIDTH - CARD_PANEL_PADDING * 2 - CARD_TEXT_INSET * 2


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


@dataclass(slots=True, frozen=True)
class _ResponseLayout:
    response: WordbankResponseItemDetail
    absolute_index: int
    top: int
    height: int

    @property
    def connector_y(self) -> int:
        return self.top + CARD_PANEL_PADDING + BADGE_SIZE // 2


class GroupDetailCardRenderer:
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
        self.TEXTURE = self.theme.texture
        self.BADGE_TEXT = self.theme.badge_text
        self.title_font = self._load_font(42)
        self.summary_font = self._load_font(24)
        self.item_title_font = self._load_font(30)
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
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> bytes:
        height = self._measure_height(page_data, locale)
        image = Image.new("RGB", (CARD_WIDTH, height), self.theme.bg)
        draw = ImageDraw.Draw(image)
        self._draw_background_texture(draw, height)

        cursor_y = CARD_PADDING_Y
        cursor_y = self._draw_header(draw, page_data, locale, cursor_y)
        cursor_y += 22
        cursor_y = self._draw_summary(draw, page_data, locale, cursor_y)
        cursor_y += 26
        trigger_top = cursor_y
        cursor_y = self._draw_trigger_panel(image, draw, page_data, locale, trigger_top)
        cursor_y += CARD_SECTION_GAP

        response_layouts: list[_ResponseLayout] = []
        layout_cursor = cursor_y
        for offset, response in enumerate(page_data.responses):
            absolute_index = page_data.start_index + offset + 1
            height = self._response_panel_height(response, locale=locale)
            response_layouts.append(
                _ResponseLayout(
                    response=response,
                    absolute_index=absolute_index,
                    top=layout_cursor,
                    height=height,
                )
            )
            layout_cursor += height + CARD_SECTION_GAP

        if response_layouts:
            self._draw_tree_trunk(
                draw,
                start_y=trigger_top + self._trigger_trunk_anchor_offset(),
                end_y=response_layouts[-1].connector_y,
            )
            for layout in response_layouts:
                self._draw_response_panel(
                    image,
                    draw,
                    response=layout.response,
                    absolute_index=layout.absolute_index,
                    locale=locale,
                    cursor_y=layout.top,
                    connector_y=layout.connector_y,
                )
            cursor_y = (
                response_layouts[-1].top
                + response_layouts[-1].height
                + CARD_SECTION_GAP
            )
        else:
            cursor_y = self._draw_empty_state(draw, locale, cursor_y)
            cursor_y += CARD_SECTION_GAP

        if page_data.page < page_data.total_pages:
            cursor_y = self._draw_page_more_cta(draw, page_data, locale, cursor_y)
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
        total += self._line_height(self.title_font) + 22
        total += self._summary_block_height(page_data, locale) + 26
        total += self._trigger_panel_height(
            page_data.detail.trigger_shape,
            locale=locale,
        )
        total += CARD_SECTION_GAP
        if page_data.responses:
            for index, response in enumerate(page_data.responses):
                total += self._response_panel_height(response, locale=locale)
                if index < len(page_data.responses) - 1:
                    total += CARD_SECTION_GAP
            total += CARD_SECTION_GAP
        else:
            total += self._empty_state_height() + CARD_SECTION_GAP
        if page_data.page < page_data.total_pages:
            total += self._page_more_cta_height(page_data, locale) + CARD_SECTION_GAP
        total += CARD_FOOTER_HEIGHT
        return total

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
            fill=self.PANEL,
            outline=self.theme.panel_outline,
            width=1,
        )

        chip_x = CARD_PADDING_X + 20
        chip_y = cursor_y + 18
        max_x = CARD_WIDTH - CARD_PADDING_X - 20
        for chip_text in self._summary_chips(page_data, locale):
            chip_width = self._text_width(chip_text, self.summary_font) + 26
            chip_height = self._line_height(self.summary_font) + 6
            if chip_x + chip_width > max_x:
                chip_x = CARD_PADDING_X + 20
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
                (chip_x + 13, chip_y + 4),
                chip_text,
                font=self.summary_font,
                fill=self.HEADER,
            )
            chip_x += chip_width + 10
        return cursor_y + box_height

    def _draw_trigger_panel(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
        top: int,
    ) -> int:
        height = self._trigger_panel_height(
            page_data.detail.trigger_shape,
            locale=locale,
        )
        self._draw_card_panel(
            draw,
            x=TRIGGER_PANEL_X,
            y=top,
            width=TRIGGER_PANEL_WIDTH,
            height=height,
            fill=self.TRIGGER_PANEL,
            outline=self.TRIGGER_BORDER,
        )

        inner_x = TRIGGER_PANEL_X + CARD_PANEL_PADDING
        inner_y = top + CARD_PANEL_PADDING
        label = tr(locale, "wordbank.group.card.trigger_label")
        label_width = self._text_width(label, self.item_meta_font) + 28
        draw.rounded_rectangle(
            (
                inner_x,
                inner_y,
                inner_x + label_width,
                inner_y + 34,
            ),
            radius=17,
            fill=self.PANEL,
        )
        draw.text(
            (inner_x + 14, inner_y + 6),
            label,
            font=self.item_meta_font,
            fill=self.ACCENT_DEEP,
        )
        self._draw_shape_flow(
            image,
            draw,
            page_data.detail.trigger_shape,
            x=inner_x + CARD_TEXT_INSET,
            y=inner_y + 46,
            max_width=TRIGGER_TEXT_WIDTH,
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
        connector_y: int,
    ) -> int:
        height = self._response_panel_height(response, locale=locale)
        self._draw_horizontal_branch(draw, y=connector_y)
        self._draw_card_panel(
            draw,
            x=RESPONSE_PANEL_X,
            y=cursor_y,
            width=RESPONSE_PANEL_WIDTH,
            height=height,
            fill=self.RESPONSE_PANEL,
            outline=self.RESPONSE_BORDER,
        )

        inner_x = RESPONSE_PANEL_X + CARD_PANEL_PADDING
        inner_y = cursor_y + CARD_PANEL_PADDING
        badge_text = f"{absolute_index:02d}"
        draw.ellipse(
            (
                inner_x,
                inner_y,
                inner_x + BADGE_SIZE,
                inner_y + BADGE_SIZE,
            ),
            fill=self.ACCENT,
        )
        badge_bbox = draw.textbbox((0, 0), badge_text, font=self.item_title_font)
        badge_width = int(badge_bbox[2] - badge_bbox[0])
        badge_height = int(badge_bbox[3] - badge_bbox[1])
        draw.text(
            (
                inner_x + (BADGE_SIZE - badge_width) / 2,
                inner_y + (BADGE_SIZE - badge_height) / 2 - 2,
            ),
            badge_text,
            font=self.item_title_font,
            fill=self.BADGE_TEXT,
        )

        body_y = self._draw_shape_flow(
            image,
            draw,
            response.response_shape,
            x=inner_x + CARD_TEXT_INSET,
            y=inner_y + BADGE_SIZE + 16,
            max_width=RESPONSE_TEXT_WIDTH,
            locale=locale,
        )
        body_y += CARD_FLOW_GAP
        self._draw_response_meta(
            draw,
            response,
            x=inner_x + CARD_TEXT_INSET,
            y=body_y,
            locale=locale,
        )
        return cursor_y + height

    def _draw_tree_trunk(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        start_y: int,
        end_y: int,
    ) -> None:
        self._draw_vertical_dashed_line(
            draw,
            x=TRUNK_X,
            start_y=start_y,
            end_y=end_y,
            color=self.theme.tree_line,
        )

    def _draw_horizontal_branch(self, draw: ImageDraw.ImageDraw, *, y: int) -> None:
        draw.line(
            ((TRUNK_X, y), (RESPONSE_PANEL_X - TREE_BRANCH_GAP, y)),
            fill=self.theme.tree_line,
            width=4,
        )
        draw.ellipse(
            (
                TRUNK_X - 8,
                y - 8,
                TRUNK_X + 8,
                y + 8,
            ),
            fill=self.ACCENT,
        )

    def _draw_card_panel(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        fill: str,
        outline: str,
    ) -> None:
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=CARD_RADIUS,
            fill=fill,
            outline=outline,
            width=1,
        )

    def _draw_page_more_cta(
        self,
        draw: ImageDraw.ImageDraw,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        hint = "💬 " + tr(
            locale,
            "wordbank.group.page_more",
            next_page=min(page_data.total_pages, page_data.page + 1),
            group_id=page_data.detail.trigger_group_id,
        )
        block_height = self._page_more_cta_height(page_data, locale)
        x = RESPONSE_PANEL_X
        width = RESPONSE_PANEL_WIDTH
        draw.rounded_rectangle(
            (x + 12, cursor_y + 5, x + width + 12, cursor_y + block_height + 5),
            radius=22,
            fill=self.theme.page_more_shadow_fill,
        )
        draw.rounded_rectangle(
            (x, cursor_y, x + width, cursor_y + block_height),
            radius=22,
            fill=self.ACCENT_SOFT,
            outline=self.theme.page_more_outline,
            width=1,
        )
        lines = self._wrap_text(
            hint,
            self.item_meta_font,
            max_width=width - 60,
            max_lines=3,
        )
        line_height = self._line_height(self.item_meta_font)
        text_y = cursor_y + int((block_height - len(lines) * line_height) / 2)
        for line in lines:
            line_width = self._text_width(line, self.item_meta_font)
            draw.text(
                (x + (width - line_width) / 2, text_y),
                line,
                font=self.item_meta_font,
                fill=self.ACCENT_DEEP,
            )
            text_y += line_height
        return cursor_y + block_height

    def _page_more_cta_height(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> int:
        hint = "💬 " + tr(
            locale,
            "wordbank.group.page_more",
            next_page=min(page_data.total_pages, page_data.page + 1),
            group_id=page_data.detail.trigger_group_id,
        )
        text_height = self._wrapped_text_height(
            hint,
            self.item_meta_font,
            max_width=RESPONSE_PANEL_WIDTH - 60,
            max_lines=3,
        )
        return max(DETAIL_CTA_MIN_HEIGHT, text_height + 28)

    def _draw_empty_state(
        self,
        draw: ImageDraw.ImageDraw,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        height = self._empty_state_height()
        self._draw_card_panel(
            draw,
            x=RESPONSE_PANEL_X,
            y=cursor_y,
            width=RESPONSE_PANEL_WIDTH,
            height=height,
            fill=self.RESPONSE_PANEL,
            outline=self.RESPONSE_BORDER,
        )
        draw.text(
            (RESPONSE_PANEL_X + 32, cursor_y + 32),
            tr(locale, "wordbank.search_card.none"),
            font=self.item_title_font,
            fill=self.HEADER,
        )
        return cursor_y + height

    def _empty_state_height(self) -> int:
        return 108

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
        rule_line = (
            f"{tr(locale, 'wordbank.group.card.rule_label')}: "
            f"{_format_rule_text(response.rule)}"
        )
        lines = self._wrap_text(
            f"{meta_line}  ·  {rule_line}",
            self.item_meta_font,
            max_width=RESPONSE_TEXT_WIDTH,
            max_lines=2,
        )
        cursor_y = y
        for line in lines:
            line_width = self._text_width(line, self.item_meta_font)
            draw.text(
                (x + RESPONSE_TEXT_WIDTH - line_width, cursor_y),
                line,
                font=self.item_meta_font,
                fill=self.MUTED,
            )
            cursor_y += self._line_height(self.item_meta_font)
        return cursor_y

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
        rule_line = (
            f"{tr(locale, 'wordbank.group.card.rule_label')}: "
            f"{_format_rule_text(response.rule)}"
        )
        return self._wrapped_text_height(
            f"{meta_line}  ·  {rule_line}",
            self.item_meta_font,
            max_width=RESPONSE_TEXT_WIDTH,
            max_lines=2,
        )

    def _trigger_panel_height(
        self,
        shape: MessageShape,
        *,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PANEL_PADDING * 2 + 46
        total += self._shape_flow_height(
            shape,
            max_width=TRIGGER_TEXT_WIDTH,
            locale=locale,
        )
        return total

    def _response_panel_height(
        self,
        response: WordbankResponseItemDetail,
        *,
        locale: LocaleCode,
    ) -> int:
        total = CARD_PANEL_PADDING * 2 + BADGE_SIZE + 16
        total += self._shape_flow_height(
            response.response_shape,
            max_width=RESPONSE_TEXT_WIDTH,
            locale=locale,
        )
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
        max_width: int,
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
                    max_width=max_width,
                )
            elif block.kind == "image" and block.image_id is not None:
                drawn_bottom = self._draw_image_block(
                    image,
                    image_id=block.image_id,
                    x=x,
                    y=cursor_y,
                    max_width=max_width,
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
        max_width: int,
        locale: LocaleCode,
    ) -> int:
        total = 0
        blocks = self._shape_blocks(shape, locale)
        for index, block in enumerate(blocks):
            if block.kind == "text" and block.text:
                total += self._wrapped_text_height(
                    block.text,
                    self.item_body_font,
                    max_width=max_width,
                )
            elif block.kind == "image" and block.image_id is not None:
                total += self._preview_height(
                    block.image_id,
                    max_width=max_width,
                    max_height=CARD_PREVIEW_MAX_HEIGHT,
                )
            if index < len(blocks) - 1:
                total += CARD_FLOW_GAP
        return total

    def _draw_image_block(
        self,
        image: Image.Image,
        *,
        image_id: int,
        x: int,
        y: int,
        max_width: int,
    ) -> int:
        image_bytes = self.preview_bytes.get(image_id)
        if not image_bytes:
            return y
        preview = self._prepare_preview_image(
            image_bytes,
            max_width=max_width,
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
            fill=self.ACCENT_DEEP,
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
            return 62
        chip_height = self._line_height(self.summary_font) + 6
        current_x = CARD_PADDING_X + 20
        max_x = CARD_WIDTH - CARD_PADDING_X - 20
        rows = 1
        for chip_text in chips:
            chip_width = self._text_width(chip_text, self.summary_font) + 26
            if current_x + chip_width > max_x:
                rows += 1
                current_x = CARD_PADDING_X + 20 + chip_width + 10
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
                _ShapeBlock(
                    kind="text",
                    text=tr(locale, "wordbank.search_card.none"),
                )
            )
        return tuple(blocks)

    def _atom_text(self, atom: MessageAtom, locale: LocaleCode) -> str:
        if atom.kind == "text":
            return atom.text
        if atom.kind == "at" and atom.target_id:
            return f"[@:{atom.target_id}]"
        if atom.kind == "event" and atom.event_name:
            return tr(locale, "wordbank.shape.event_ref", event_name=atom.event_name)
        return ""

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
        truncated[-1] = self._truncate_line(f"{truncated[-1]}...", font, max_width)
        return truncated

    def _truncate_line(self, text: str, font: Any, max_width: int) -> str:
        if self._text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and self._text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..."

    def _draw_vertical_dashed_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        start_y: int,
        end_y: int,
        color: str,
    ) -> None:
        dash = 10
        gap = 8
        current_y = start_y
        while current_y < end_y:
            draw.line(
                ((x, current_y), (x, min(current_y + dash, end_y))),
                fill=color,
                width=4,
            )
            current_y += dash + gap

    def _trigger_trunk_anchor_offset(self) -> int:
        return CARD_PANEL_PADDING + 56

    def _line_height(self, font: Any) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        return int(bbox[3] - bbox[1] + 8)

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
