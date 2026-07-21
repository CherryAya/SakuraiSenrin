"""Pillow rendering for wordbank group detail pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import arrow
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.demo_theme import SENRIN_V3_WORDBANK_CARD_THEME
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import (
    MessagePlanEntry,
    build_image_plan_entry,
)
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankResponseItemDetail,
)
from src.plugins.wordbank.message_model import MessageShape

from .group_detail_card_helpers import (
    build_copyright_text,
    centered_text_origin,
    display_group_detail,
    format_batch_delete_hint,
    format_response_delete_hint,
    line_height,
    measure_preview_size,
    paste_rounded_image,
    prepare_preview_image,
    response_meta_text,
    shape_blocks,
    summary_chips,
    text_width,
    wrap_text,
)

CARD_WIDTH = 1320
CARD_PADDING_X = 56
CARD_PADDING_Y = 48
CARD_PANEL_PADDING = 30
CARD_SECTION_GAP = 24
CARD_FLOW_GAP = 12
CARD_RADIUS = 24
CARD_TEXT_INSET = 24
CARD_IMAGE_RADIUS = 24
CARD_FOOTER_HEIGHT = 150
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
        page_data = GroupDetailCardPage(
            detail=display_group_detail(page_data.detail),
            page=page_data.page,
            total_pages=page_data.total_pages,
            page_size=page_data.page_size,
            start_index=page_data.start_index,
            responses=page_data.responses,
        )
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
        total += line_height(self.title_font) + 22
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
        return cursor_y + line_height(self.title_font)

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
        for chip_text in summary_chips(page_data.detail, locale=locale):
            chip_width = text_width(chip_text, self.summary_font) + 26
            chip_height = line_height(self.summary_font) + 6
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
        label_width = text_width(label, self.item_meta_font) + 28
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
        badge_width = text_width(badge_text, self.item_title_font)
        badge_height = line_height(self.item_title_font) - 8
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
        body_y = self._draw_response_meta(
            draw,
            response,
            x=inner_x + CARD_TEXT_INSET,
            y=body_y,
            locale=locale,
        )
        body_y += 14
        self._draw_response_delete_hint(
            draw,
            response,
            x=inner_x + CARD_TEXT_INSET,
            y=body_y,
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
        lines = wrap_text(
            hint,
            self.item_meta_font,
            max_width=width - 60,
            max_lines=3,
        )
        item_meta_line_height = line_height(self.item_meta_font)
        text_y = cursor_y + int((block_height - len(lines) * item_meta_line_height) / 2)
        for line in lines:
            line_width = text_width(line, self.item_meta_font)
            draw.text(
                (x + (width - line_width) / 2, text_y),
                line,
                font=self.item_meta_font,
                fill=self.ACCENT_DEEP,
            )
            text_y += item_meta_line_height
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
        lines = wrap_text(
            response_meta_text(response, locale=locale),
            self.item_meta_font,
            max_width=RESPONSE_TEXT_WIDTH,
            max_lines=2,
        )
        cursor_y = y
        for line in lines:
            line_width = text_width(line, self.item_meta_font)
            draw.text(
                (x + RESPONSE_TEXT_WIDTH - line_width, cursor_y),
                line,
                font=self.item_meta_font,
                fill=self.MUTED,
            )
            cursor_y += line_height(self.item_meta_font)
        return cursor_y

    def _response_meta_height(
        self,
        response: WordbankResponseItemDetail,
        locale: LocaleCode,
    ) -> int:
        return self._wrapped_text_height(
            response_meta_text(response, locale=locale),
            self.item_meta_font,
            max_width=RESPONSE_TEXT_WIDTH,
            max_lines=2,
        )

    def _draw_response_delete_hint(
        self,
        draw: ImageDraw.ImageDraw,
        response: WordbankResponseItemDetail,
        *,
        x: int,
        y: int,
    ) -> int:
        hint = format_response_delete_hint(response.response_item_id)
        box_padding_x = 16
        box_padding_y = 12
        lines = wrap_text(
            hint,
            self.item_meta_font,
            max_width=RESPONSE_TEXT_WIDTH - box_padding_x * 2,
            max_lines=3,
        )
        text_height = len(lines) * line_height(self.item_meta_font)
        box_height = text_height + box_padding_y * 2
        draw.rounded_rectangle(
            (
                x,
                y,
                x + RESPONSE_TEXT_WIDTH,
                y + box_height,
            ),
            radius=18,
            fill=self.ACCENT_SOFT,
            outline=self.theme.page_more_outline,
            width=1,
        )
        cursor_y = y + box_padding_y
        for line in lines:
            draw.text(
                (x + box_padding_x, cursor_y),
                line,
                font=self.item_meta_font,
                fill=self.ACCENT_DEEP,
            )
            cursor_y += line_height(self.item_meta_font)
        return y + box_height

    def _response_delete_hint_height(
        self,
        response: WordbankResponseItemDetail,
    ) -> int:
        _ = response
        text_height = self._wrapped_text_height(
            format_response_delete_hint(response.response_item_id),
            self.item_meta_font,
            max_width=RESPONSE_TEXT_WIDTH - 32,
            max_lines=3,
        )
        return text_height + 24

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
        total += 14
        total += self._response_delete_hint_height(response)
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
        blocks = shape_blocks(shape, locale=locale, preview_bytes=self.preview_bytes)
        cursor_y = y
        for index, block in enumerate(blocks):
            kind, text, image_id = block
            if kind == "text" and text:
                cursor_y = self._draw_wrapped_text(
                    draw,
                    x=x,
                    y=cursor_y,
                    text=text,
                    font=self.item_body_font,
                    fill=self.BODY,
                    max_width=max_width,
                )
            elif kind == "image" and image_id is not None:
                drawn_bottom = self._draw_image_block(
                    image,
                    image_id=image_id,
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
        blocks = shape_blocks(shape, locale=locale, preview_bytes=self.preview_bytes)
        for index, block in enumerate(blocks):
            kind, text, image_id = block
            if kind == "text" and text:
                total += self._wrapped_text_height(
                    text,
                    self.item_body_font,
                    max_width=max_width,
                )
            elif kind == "image" and image_id is not None:
                total += self._preview_height(
                    image_id,
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
        paste_rounded_image(image, preview, (x, y), radius=CARD_IMAGE_RADIUS)
        return y + preview.height

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
        cursor_y: int,
    ) -> None:
        footer_time = arrow.get(get_current_time()).to("Asia/Shanghai")
        copyright_text = build_copyright_text(footer_time.year)
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
        batch_delete_text = format_batch_delete_hint(
            tuple(
                response.response_item_id
                for response in page_data.responses
                if response.response_item_id > 0
            )
        )
        draw.text(
            centered_text_origin(
                draw,
                copyright_text,
                self.footer_minor_font,
                canvas_width=CARD_WIDTH,
                y=cursor_y,
            ),
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
        stats_y = (
            generated_y + line_height(self.footer_minor_font) + CARD_FOOTER_LINE_GAP
        )
        draw.text(
            centered_text_origin(
                draw,
                stats_text,
                self.footer_font,
                canvas_width=CARD_WIDTH,
                y=stats_y,
            ),
            stats_text,
            font=self.footer_font,
            fill=self.MUTED,
        )
        command_y = stats_y + line_height(self.footer_font) + CARD_FOOTER_LINE_GAP
        draw.text(
            centered_text_origin(
                draw,
                command_text,
                self.footer_font,
                canvas_width=CARD_WIDTH,
                y=command_y,
            ),
            command_text,
            font=self.footer_font,
            fill=self.ACCENT_DEEP,
        )
        if batch_delete_text:
            batch_y = command_y + line_height(self.footer_font) + CARD_FOOTER_LINE_GAP
            draw.text(
                centered_text_origin(
                    draw,
                    batch_delete_text,
                    self.footer_font,
                    canvas_width=CARD_WIDTH,
                    y=batch_y,
                ),
                batch_delete_text,
                font=self.footer_font,
                fill=self.ACCENT_DEEP,
            )

    def _summary_block_height(
        self,
        page_data: GroupDetailCardPage,
        locale: LocaleCode,
    ) -> int:
        chips = summary_chips(page_data.detail, locale=locale)
        if not chips:
            return 62
        chip_height = line_height(self.summary_font) + 6
        current_x = CARD_PADDING_X + 20
        max_x = CARD_WIDTH - CARD_PADDING_X - 20
        rows = 1
        for chip_text in chips:
            chip_width = text_width(chip_text, self.summary_font) + 26
            if current_x + chip_width > max_x:
                rows += 1
                current_x = CARD_PADDING_X + 20 + chip_width + 10
                continue
            current_x += chip_width + 10
        return 18 + rows * chip_height + (rows - 1) * 10 + 18

    def _preview_height(self, image_id: int, *, max_width: int, max_height: int) -> int:
        cache_key = (image_id, max_width, max_height)
        if cache_key not in self._preview_size_cache:
            image_bytes = self.preview_bytes.get(image_id)
            if not image_bytes:
                self._preview_size_cache[cache_key] = None
            else:
                self._preview_size_cache[cache_key] = measure_preview_size(
                    image_bytes,
                    max_width=max_width,
                    max_height=max_height,
                )
        measured = self._preview_size_cache[cache_key]
        return measured[1] if measured is not None else 0

    def _prepare_preview_image(
        self,
        image_bytes: bytes,
        *,
        max_width: int,
        max_height: int,
    ) -> Image.Image | None:
        return prepare_preview_image(
            image_bytes,
            max_width=max_width,
            max_height=max_height,
        )

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
        lines = wrap_text(
            text,
            font,
            max_width=max_width,
            max_lines=max_lines,
        )
        cursor_y = y
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
        lines = wrap_text(
            text,
            font,
            max_width=max_width,
            max_lines=max_lines,
        )
        return len(lines) * line_height(font)

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


def build_group_detail_card_plan_entry(
    *,
    page_data: GroupDetailCardPage,
    locale: LocaleCode,
    preview_bytes: Mapping[int, bytes | None] | None = None,
) -> MessagePlanEntry:
    image_bytes = render_group_detail_card_bytes(
        page_data=page_data,
        locale=locale,
        preview_bytes=preview_bytes,
    )
    return build_image_plan_entry(image_bytes)
