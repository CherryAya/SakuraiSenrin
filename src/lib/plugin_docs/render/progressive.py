"""Progressive disclosure renderers for docs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Any, ClassVar

from PIL import Image, ImageDraw

from src.lib.demo_theme import SENRIN_V3_THEME, get_demo_theme
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs.command_layout import (
    CommandLayout,
    CommandPalette,
    InlineTextSpan,
    build_command_layout,
    split_inline_text_spans,
)
from src.lib.plugin_docs.markdown_layout import MarkdownLayout, build_markdown_layout
from src.lib.plugin_docs.models import DocNode, FeatureDoc, HelpDashboardSection
from src.lib.utils.common import get_current_time

from .demo import DemoImageRenderer, _ShowcaseNoteItem, _ShowcaseTurnPlacement
from .encoding import encode_docs_image
from .helpers import (
    build_help_support_bundle,
    build_plugin_summary_copy,
    feature_command_for_display_text,
    feature_demo_help_command,
    node_help_command,
    permission_label,
)


@dataclass(slots=True, frozen=True)
class _DashboardCardLayout:
    node: DocNode
    theme: Any
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    command_layout: CommandLayout
    content_x: int
    content_width: int
    category_rect: tuple[int, int, int, int]
    title_top: int
    title_line_height: int
    title_block_height: int
    summary_top: int
    summary_line_height: int
    summary_block_height: int
    command_rect: tuple[int, int, int, int]
    height: int


@dataclass(slots=True, frozen=True)
class _GuideSectionLayout:
    feature: FeatureDoc
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    trigger_layout: CommandLayout
    demo_layout: CommandLayout
    overview_layout: MarkdownLayout
    note_items: tuple[_ShowcaseNoteItem, ...]
    turn_placements: tuple[_ShowcaseTurnPlacement, ...]
    height: int


@dataclass(slots=True, frozen=True)
class _GuideAdvancedItemLayout:
    feature: FeatureDoc
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    trigger_layout: CommandLayout
    demo_layout: CommandLayout
    height: int


@dataclass(slots=True, frozen=True)
class _SupportStripLayout:
    rect: tuple[int, int, int, int]
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    tip_lines: tuple[tuple[InlineTextSpan, ...], ...]
    group_lines: tuple[tuple[tuple[InlineTextSpan, ...], ...], ...]
    qr_image: Image.Image | None
    qr_rect: tuple[int, int, int, int] | None


class ProgressiveDisclosureRenderer(DemoImageRenderer):
    DASHBOARD_CARD_GAP_X = 32
    DASHBOARD_CARD_GAP_Y = 32
    DASHBOARD_CARD_RADIUS = 32
    DASHBOARD_CARD_PADDING_X = 40
    DASHBOARD_CARD_PADDING_Y = 40
    DASHBOARD_CARD_TEXT_SPACING = 8
    DASHBOARD_CARD_TITLE_GAP = 24
    DASHBOARD_CARD_SUMMARY_GAP = 16
    DASHBOARD_CARD_BOTTOM_SAFE_GAP = 24
    DASHBOARD_CARD_COMMAND_PADDING_X = 24
    DASHBOARD_CARD_COMMAND_PADDING_Y = 16
    DASHBOARD_CARD_SUMMARY_VISIBLE_LINES = 4
    GUIDE_SECTION_GAP = 48
    GUIDE_SECTION_PADDING_X = 48
    GUIDE_SECTION_PADDING_Y = 40
    GUIDE_SECTION_RADIUS = 32
    DASHBOARD_SECTION_TOP_BAR_HEIGHT = 4
    DASHBOARD_SECTION_TITLE_GAP = 32
    DASHBOARD_SECTION_ITEM_GAP = 32
    DASHBOARD_SECTION_SUMMARY_GAP = 18
    DASHBOARD_SECTION_COMMAND_GAP = 20
    SUPPORT_STRIP_GAP = 24
    SUPPORT_STRIP_PADDING_X = 40
    SUPPORT_STRIP_PADDING_Y = 28
    SUPPORT_STRIP_RADIUS = 28
    SUPPORT_STRIP_QR_WIDTH = 300
    SUPPORT_STRIP_QR_MAX_HEIGHT = 180
    SUPPORT_STRIP_QR_FRAME_PADDING_X = 16
    SUPPORT_STRIP_QR_FRAME_PADDING_Y = 16
    SUPPORT_STRIP_QR_FRAME_RADIUS = 18
    SUPPORT_STRIP_QR_VERTICAL_OFFSET_Y = -6
    SINGLE_PAGE_INNER_GAP = 24
    SINGLE_PAGE_SECTION_TOP_PAD = 36
    SINGLE_PAGE_COMMAND_RADIUS = 22
    SINGLE_PAGE_WATERMARK_OFFSET_Y = 18

    _DASHBOARD_MARKER_SIZE: ClassVar[dict[str, int]] = {
        "square": 12,
        "diamond": 14,
        "ring": 14,
    }

    def render_dashboard(
        self,
        *,
        sections: Sequence[HelpDashboardSection],
        locale: LocaleCode,
        generated_at: datetime | None = None,
    ) -> bytes:
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        all_nodes = tuple(node for section in sections for node in section.nodes)
        header_title = tr(locale, "help.dashboard.title")
        header_summary = "\n".join(
            (
                tr(locale, "help.dashboard.lead.line1"),
                tr(locale, "help.dashboard.lead.line2"),
            )
        )
        header_title_lines = tuple(
            self._wrap_inline_text(
                header_title,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.title_font,
            )
        )
        header_summary_lines = tuple(
            self._wrap_inline_text(
                header_summary,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.summary_font,
            )
        )
        header_height = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
            + len(header_summary_lines)
            * self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            )
            + self.theme.hero_bottom_padding
        )

        content_width = self.WIDTH - side * 2
        use_two_columns = len(sections) > 1
        section_width = content_width
        if use_two_columns:
            section_width = (content_width - self.DASHBOARD_CARD_GAP_X) // 2
        section_positions: list[tuple[HelpDashboardSection, int, int, int]] = []
        column_bottoms = [header_height, header_height]
        for section in sections:
            if use_two_columns:
                column = 0 if column_bottoms[0] <= column_bottoms[1] else 1
            else:
                column = 0
            left = side + column * (section_width + self.DASHBOARD_CARD_GAP_X)
            top = column_bottoms[column]
            height = self._measure_dashboard_section_height(section, section_width)
            section_positions.append((section, left, top, height))
            column_bottoms[column] = top + height + self.DASHBOARD_CARD_GAP_Y
        content_bottom = (
            max(column_bottoms) - self.DASHBOARD_CARD_GAP_Y
            if section_positions
            else header_height
        )
        support_layout = self._measure_support_strip(
            locale=locale,
            top=content_bottom + self.SUPPORT_STRIP_GAP,
        )
        footer_rect = (
            side,
            support_layout.rect[3] + self.theme.footer_gap_top,
            self.WIDTH - side,
            support_layout.rect[3]
            + self.theme.footer_gap_top
            + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin

        image = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        self._paint_dashboard_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_multiline_text(
            draw,
            x=side,
            y=self.theme.hero_top,
            lines=header_title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        summary_top = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
        )
        self._draw_multiline_text(
            draw,
            x=side,
            y=summary_top,
            lines=header_summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        standee_rect = (
            self.WIDTH - side - self.theme.hero_standee_size,
            self.theme.hero_top + 8,
            self.WIDTH - side,
            self.theme.hero_top + 8 + self.theme.hero_standee_size,
        )
        self._draw_standee(image, draw, standee_rect)

        for section, left, top, _height in section_positions:
            self._draw_dashboard_section(
                image,
                draw,
                section=section,
                top=top,
                left=left,
                width=section_width,
            )

        self._draw_support_strip(image, draw, layout=support_layout)
        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=(tr(locale, "help.dashboard.footer.left", count=len(all_nodes))),
            right_text=f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin",
        )
        return encode_docs_image(image, webp_quality=88, webp_method=6)

    def _paint_dashboard_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, width, height), fill=self.theme.page_bg)
        self._draw_dashboard_grid(draw, width=width, height=height)
        self._draw_dashboard_corner_accents(draw, width=width, height=height)

    def _draw_dashboard_grid(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        width: int,
        height: int,
    ) -> None:
        line_fill = self._rgba(self.theme.grid_color, 24)
        spacing = self.theme.grid_spacing
        max_height = height
        for y in range(self.theme.hero_top // 2, max_height, spacing):
            draw.line(
                (
                    self.theme.hero_side_padding // 3,
                    y,
                    width - self.theme.hero_side_padding // 3,
                    y,
                ),
                fill=line_fill,
                width=1,
            )
        for x in range(
            self.theme.hero_side_padding // 3,
            width - self.theme.hero_side_padding // 3,
            spacing,
        ):
            draw.line(
                (x, self.theme.hero_top // 2, x, max_height),
                fill=line_fill,
                width=1,
            )

    def _draw_dashboard_corner_accents(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        width: int,
        height: int,
    ) -> None:
        accent = self._rgba(self.theme.accent, 88)
        support = self._rgba(self.theme.grid_color, 96)
        draw.line(
            (
                self.theme.hero_side_padding - 12,
                self.theme.hero_top + 8,
                self.theme.hero_side_padding + 28,
                self.theme.hero_top + 8,
            ),
            fill=accent,
            width=2,
        )
        draw.line(
            (
                self.theme.hero_side_padding + 8,
                self.theme.hero_top - 12,
                self.theme.hero_side_padding + 8,
                self.theme.hero_top + 28,
            ),
            fill=accent,
            width=2,
        )
        draw.line(
            (
                width - self.theme.hero_side_padding - 24,
                height - self.theme.footer_height - 28,
                width - self.theme.hero_side_padding + 8,
                height - self.theme.footer_height - 28,
            ),
            fill=support,
            width=2,
        )
        draw.line(
            (
                width - self.theme.hero_side_padding + 24,
                height - self.theme.footer_height - 44,
                width - self.theme.hero_side_padding + 24,
                height - self.theme.footer_height - 12,
            ),
            fill=accent,
            width=2,
        )

    def render_plugin_guide(
        self,
        *,
        node: DocNode,
        features: Sequence[FeatureDoc],
        locale: LocaleCode,
        generated_at: datetime | None = None,
    ) -> bytes:
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        header_title_lines = tuple(
            self._wrap_inline_text(
                node.title,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.title_font,
            )
        )
        header_summary_lines = tuple(
            self._wrap_inline_text(
                node.summary or node.bundle.summary,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.summary_font,
            )
        )
        hero_bottom = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
            + len(header_summary_lines)
            * self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            )
            + self.theme.hero_bottom_padding
        )

        content_width = self.WIDTH - side * 2
        section_width = content_width
        feature_layouts = tuple(
            self._measure_plugin_guide_section(
                node=node,
                feature=feature,
                section_width=section_width,
            )
            for feature in features
        )

        cursor_y = hero_bottom
        section_positions: list[tuple[_GuideSectionLayout, int]] = []
        for layout in feature_layouts:
            section_positions.append((layout, cursor_y))
            cursor_y += layout.height + self.GUIDE_SECTION_GAP

        support_layout = self._measure_showcase_support_strip(
            locale=locale,
            top=cursor_y + self.SUPPORT_STRIP_GAP,
        )
        footer_rect = (
            side,
            support_layout.rect[3] + self.theme.footer_gap_top,
            self.WIDTH - side,
            support_layout.rect[3]
            + self.theme.footer_gap_top
            + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin
        image = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)

        self._draw_multiline_text(
            draw,
            x=side,
            y=self.theme.hero_top,
            lines=header_title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        summary_top = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
        )
        self._draw_section_watermark(
            draw,
            rect=(
                side,
                summary_top + 4,
                self.WIDTH - side - self.theme.hero_standee_size + 16,
                hero_bottom,
            ),
            text="FEATURE GUIDE",
            font=self.watermark_font,
            fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA_LARGE),
        )
        self._draw_multiline_text(
            draw,
            x=side,
            y=summary_top,
            lines=header_summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        standee_rect = (
            self.WIDTH - side - self.theme.hero_standee_size,
            self.theme.hero_top + 8,
            self.WIDTH - side,
            self.theme.hero_top + 8 + self.theme.hero_standee_size,
        )
        self._draw_standee(image, draw, standee_rect)

        for layout, top in section_positions:
            self._draw_plugin_guide_section(
                image,
                draw,
                node=node,
                layout=layout,
                top=top,
                left=side,
                locale=locale,
            )

        self._draw_showcase_support_strip(image, draw, layout=support_layout)
        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=(
                f"{node.title} · Guide · "
                f"v{node.bundle.version.lstrip('v')} · By {node.bundle.author}"
            ),
            right_text=f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin",
        )
        return encode_docs_image(image, webp_quality=88, webp_method=6)

    def render_static_entry(
        self,
        *,
        node: DocNode,
        locale: LocaleCode,
        generated_at: datetime | None = None,
    ) -> bytes:
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        width = self.WIDTH - side * 2
        header_title_lines = tuple(
            self._wrap_inline_text(
                node.title,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.title_font,
            )
        )
        header_summary_lines = tuple(
            self._wrap_inline_text(
                node.summary or node.bundle.summary,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.summary_font,
            )
        )
        hero_bottom = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
            + len(header_summary_lines)
            * self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            )
            + self.theme.hero_bottom_padding
        )
        primary_feature = self._primary_feature_for_entry(node)
        command_text = (
            primary_feature.trigger.strip()
            if primary_feature is not None and primary_feature.trigger.strip()
            else node_help_command(node)
        )
        command_layout = build_command_layout(
            command_text,
            max_width=width
            - self.GUIDE_SECTION_PADDING_X * 2
            - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.body_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.body_font),
            palette=self._command_palette(),
        )
        command_height = command_layout.total_height + self.theme.trigger_padding_y * 2
        overview_text = (
            primary_feature.overview.strip()
            if primary_feature is not None and primary_feature.overview.strip()
            else (node.description.strip() or node.summary.strip())
        )
        body_lines = tuple(
            self._wrap_inline_text(
                overview_text,
                max_width=width - self.GUIDE_SECTION_PADDING_X * 2,
                font=self.instruction_font,
            )
        )
        body_line_height = self._line_height_for_font(self.instruction_font)
        intro_section_height = (
            self.GUIDE_SECTION_PADDING_Y * 2
            + command_height
            + len(body_lines) * body_line_height
            + self.SINGLE_PAGE_INNER_GAP
            + 24
        )
        demo_turns = primary_feature.demo_turns if primary_feature is not None else ()
        demo_card_height = 0
        demo_card_rect: tuple[int, int, int, int] | None = None
        demo_turn_placements: tuple[_ShowcaseTurnPlacement, ...] = ()
        if demo_turns:
            demo_inner_left = side + self.GUIDE_SECTION_PADDING_X
            demo_inner_right = self.WIDTH - side - self.GUIDE_SECTION_PADDING_X
            turn_top = 0
            placements: list[_ShowcaseTurnPlacement] = []
            for turn in demo_turns:
                spec = self._measure_turn(turn, demo_inner_right - demo_inner_left)
                placement = self._place_turn(
                    spec,
                    top=turn_top,
                    left=demo_inner_left,
                    right=demo_inner_right,
                )
                placements.append(placement)
                turn_top = placement.rect[3] + self.theme.bubble_gap
            demo_turn_placements = tuple(placements)
            demo_content_height = max(0, turn_top - self.theme.bubble_gap)
            demo_card_height = 44 + 56 + 20 + demo_content_height + 36
            demo_card_top = hero_bottom + intro_section_height + self.GUIDE_SECTION_GAP
            demo_card_rect = (
                side,
                demo_card_top,
                side + width,
                demo_card_top + demo_card_height,
            )
        content_bottom = (
            demo_card_rect[3]
            if demo_card_rect is not None
            else hero_bottom + intro_section_height
        )
        support_layout = self._measure_showcase_support_strip(
            locale=locale,
            top=content_bottom + self.SUPPORT_STRIP_GAP,
        )
        footer_rect = (
            side,
            support_layout.rect[3] + self.theme.footer_gap_top,
            self.WIDTH - side,
            support_layout.rect[3]
            + self.theme.footer_gap_top
            + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin
        image = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_multiline_text(
            draw,
            x=side,
            y=self.theme.hero_top,
            lines=header_title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        summary_top = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
        )
        # self._draw_section_watermark(
        #     draw,
        #     rect=(
        #         side,
        #         summary_top + 4,
        #         self.WIDTH - side - self.theme.hero_standee_size + 16,
        #         hero_bottom,
        #     ),
        #     text="GROUP ACCESS",
        #     font=self.watermark_font,
        #     fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA_LARGE),
        # )
        self._draw_multiline_text(
            draw,
            x=side,
            y=summary_top,
            lines=header_summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        standee_rect = (
            self.WIDTH - side - self.theme.hero_standee_size,
            self.theme.hero_top + 8,
            self.WIDTH - side,
            self.theme.hero_top + 8 + self.theme.hero_standee_size,
        )
        self._draw_standee(image, draw, standee_rect)
        rect = (side, hero_bottom, side + width, hero_bottom + intro_section_height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.GUIDE_SECTION_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        draw.rounded_rectangle(
            rect,
            radius=self.GUIDE_SECTION_RADIUS,
            outline=self._rgba(self.theme.accent, 62),
            width=2,
        )
        # self._draw_section_watermark(
        #     draw,
        #     rect=(
        #         rect[0] + self.GUIDE_SECTION_PADDING_X,
        #         rect[1] + 6,
        #         rect[2] - self.GUIDE_SECTION_PADDING_X,
        #         rect[1] + 70,
        #     ),
        #     text="ACCESS NOTE",
        #     font=self.watermark_font_small,
        #     fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA),
        #     align="right",
        # )
        command_rect = (
            rect[0] + self.GUIDE_SECTION_PADDING_X,
            rect[1] + self.GUIDE_SECTION_PADDING_Y,
            rect[2] - self.GUIDE_SECTION_PADDING_X,
            rect[1] + self.GUIDE_SECTION_PADDING_Y + command_height,
        )
        self._draw_soft_subcard(
            image,
            draw,
            command_rect,
            radius=self.SINGLE_PAGE_COMMAND_RADIUS,
            fill=self.theme.terminal_bg,
            outline=None,
        )
        self._draw_command_layout(
            draw,
            x=command_rect[0] + self.theme.trigger_padding_x,
            y=command_rect[1] + self.theme.trigger_padding_y,
            layout=command_layout,
            font=self.body_font,
            default_fill=self.theme.terminal_text,
            guide_fill=self.theme.line,
        )
        body_text_y = command_rect[3] + self.SINGLE_PAGE_INNER_GAP
        self._draw_multiline_text(
            draw,
            x=rect[0] + self.GUIDE_SECTION_PADDING_X,
            y=body_text_y,
            lines=body_lines,
            font=self.instruction_font,
            fill=self.theme.deep,
            line_height=body_line_height,
            render_code_chip=False,
        )
        if demo_card_rect is not None:
            capsule_width = min(
                500,
                max(
                    300, self._text_width("看看它是怎么工作的", self.capsule_font) + 120
                ),
            )
            capsule_top = demo_card_rect[1] - self.CAPSULE_HEIGHT // 2
            capsule_rect = (
                demo_card_rect[0] + (width - capsule_width) // 2,
                capsule_top,
                demo_card_rect[0] + (width - capsule_width) // 2 + capsule_width,
                capsule_top + self.CAPSULE_HEIGHT,
            )
            self._draw_shadowed_rect(
                image,
                rect=demo_card_rect,
                radius=self.GUIDE_SECTION_RADIUS,
                shadow_color=self.theme.card_shadow,
                shadow_offset_y=self.theme.instruction_shadow_offset_y,
                shadow_blur=self.theme.instruction_shadow_blur,
                fill=self.theme.panel_bg,
            )
            draw.rounded_rectangle(
                demo_card_rect,
                radius=self.GUIDE_SECTION_RADIUS,
                outline=self._rgba(self.theme.accent, 62),
                width=2,
            )
            self._draw_capsule_title(
                draw,
                rect=capsule_rect,
                text="看看它是怎么工作的",
                fill=(255, 243, 228, 191),
                outline=None,
            )
            # self._draw_section_watermark(
            #     draw,
            #     rect=(
            #         demo_card_rect[0] + self.GUIDE_SECTION_PADDING_X,
            #         demo_card_rect[1] + 8,
            #         demo_card_rect[2] - self.GUIDE_SECTION_PADDING_X,
            #         demo_card_rect[1] + 78,
            #     ),
            #     text="DEMONSTRATION FLOW",
            #     font=self.watermark_font_small,
            #     fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA),
            #     align="right",
            # )
            badge_rect = (
                demo_card_rect[0] + self.GUIDE_SECTION_PADDING_X,
                demo_card_rect[1] + 28,
                demo_card_rect[0] + self.GUIDE_SECTION_PADDING_X + 66,
                demo_card_rect[1] + 28 + 52,
            )
            draw.rounded_rectangle(badge_rect, radius=18, fill=self.theme.accent)
            self._draw_text_centered(
                draw,
                badge_rect,
                "01",
                font=self.step_badge_font,
                fill="#FFFFFF",
            )
            title_x = badge_rect[2] + 18
            title_y = badge_rect[1] + 4
            self._draw_text(
                draw,
                x=title_x,
                y=title_y,
                text=self.DEFAULT_SECTION_TITLE,
                font=self.instruction_font,
                fill=self.theme.deep,
            )
            self._draw_text(
                draw,
                x=title_x
                + self._text_width(self.DEFAULT_SECTION_TITLE, self.instruction_font)
                + 14,
                y=title_y + 8,
                text=self.STEP_LABELS[0],
                font=self.micro_caps_font,
                fill=self.theme.hint,
            )
            shift_y = demo_card_rect[1] + 44 + 56 + 20
            for placement in demo_turn_placements:
                shifted = _ShowcaseTurnPlacement(
                    spec=placement.spec,
                    rect=(
                        placement.rect[0],
                        placement.rect[1] + shift_y,
                        placement.rect[2],
                        placement.rect[3] + shift_y,
                    ),
                    avatar_rect=(
                        None
                        if placement.avatar_rect is None
                        else (
                            placement.avatar_rect[0],
                            placement.avatar_rect[1] + shift_y,
                            placement.avatar_rect[2],
                            placement.avatar_rect[3] + shift_y,
                        )
                    ),
                    bubble_rect=(
                        None
                        if placement.bubble_rect is None
                        else (
                            placement.bubble_rect[0],
                            placement.bubble_rect[1] + shift_y,
                            placement.bubble_rect[2],
                            placement.bubble_rect[3] + shift_y,
                        )
                    ),
                    text_rect=(
                        placement.text_rect[0],
                        placement.text_rect[1] + shift_y,
                        placement.text_rect[2],
                        placement.text_rect[3] + shift_y,
                    ),
                    label_rect=(
                        None
                        if placement.label_rect is None
                        else (
                            placement.label_rect[0],
                            placement.label_rect[1] + shift_y,
                            placement.label_rect[2],
                            placement.label_rect[3] + shift_y,
                        )
                    ),
                )
                self._draw_turn(image, draw, shifted, locale=locale)
        self._draw_showcase_support_strip(image, draw, layout=support_layout)
        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=f"{node.title} · Static Entry · By {node.bundle.author}",
            right_text=f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin",
        )
        return encode_docs_image(image, webp_quality=88, webp_method=6)

    def render_plugin_summary(
        self,
        *,
        node: DocNode,
        locale: LocaleCode,
        generated_at: datetime | None = None,
    ) -> bytes:
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        width = self.WIDTH - side * 2
        header_title_lines = tuple(
            self._wrap_inline_text(
                node.title,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.title_font,
            )
        )
        header_summary_lines = tuple(
            self._wrap_inline_text(
                node.summary or node.bundle.summary,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.summary_font,
            )
        )
        hero_bottom = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
            + len(header_summary_lines)
            * self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            )
            + self.theme.hero_bottom_padding
        )
        body_text = build_plugin_summary_copy(node).strip()
        primary_feature = self._primary_feature_for_entry(node)
        command_layout = None
        command_height = 0
        if primary_feature is not None:
            command_layout = build_command_layout(
                feature_command_for_display_text(
                    node.bundle, primary_feature, node.title
                ),
                max_width=width
                - self.GUIDE_SECTION_PADDING_X * 2
                - self.theme.trigger_padding_x * 2,
                line_height=self._line_height_for_font(self.body_font),
                indent_px=self.COMMAND_INDENT_PX,
                measure_text=lambda value: self._text_width(value, self.body_font),
                palette=self._command_palette(),
            )
            command_height = (
                command_layout.total_height + self.theme.trigger_padding_y * 2
            )
        body_lines = tuple(
            self._wrap_inline_text(
                body_text,
                max_width=width - self.GUIDE_SECTION_PADDING_X * 2,
                font=self.instruction_font,
            )
        )
        body_line_height = self._line_height_for_font(self.instruction_font)
        body_content_height = len(body_lines) * body_line_height if body_lines else 0
        section_height = (
            self.GUIDE_SECTION_PADDING_Y * 2
            + body_content_height
            + command_height
            + (
                self.SINGLE_PAGE_INNER_GAP
                if command_layout is not None and body_lines
                else 0
            )
            + 24
            if body_lines
            else self.GUIDE_SECTION_PADDING_Y * 2 + command_height + 24
        )
        support_layout = self._measure_showcase_support_strip(
            locale=locale,
            top=hero_bottom + section_height + self.SUPPORT_STRIP_GAP,
        )
        footer_rect = (
            side,
            support_layout.rect[3] + self.theme.footer_gap_top,
            self.WIDTH - side,
            support_layout.rect[3]
            + self.theme.footer_gap_top
            + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin
        image = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_multiline_text(
            draw,
            x=side,
            y=self.theme.hero_top,
            lines=header_title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        summary_top = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
        )
        self._draw_section_watermark(
            draw,
            rect=(
                side,
                summary_top + 4,
                self.WIDTH - side - self.theme.hero_standee_size + 16,
                hero_bottom,
            ),
            text="SUMMARY OVERVIEW",
            font=self.watermark_font,
            fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA_LARGE),
        )
        self._draw_multiline_text(
            draw,
            x=side,
            y=summary_top,
            lines=header_summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        standee_rect = (
            self.WIDTH - side - self.theme.hero_standee_size,
            self.theme.hero_top + 8,
            self.WIDTH - side,
            self.theme.hero_top + 8 + self.theme.hero_standee_size,
        )
        self._draw_standee(image, draw, standee_rect)
        if body_lines:
            rect = (side, hero_bottom, side + width, hero_bottom + section_height)
            self._draw_shadowed_rect(
                image,
                rect=rect,
                radius=self.GUIDE_SECTION_RADIUS,
                shadow_color=self.theme.card_shadow,
                shadow_offset_y=self.theme.instruction_shadow_offset_y,
                shadow_blur=self.theme.instruction_shadow_blur,
                fill=self.theme.panel_bg,
            )
            self._draw_section_watermark(
                draw,
                rect=(
                    rect[0] + self.GUIDE_SECTION_PADDING_X,
                    rect[1] + 8,
                    rect[2] - self.GUIDE_SECTION_PADDING_X,
                    rect[1] + 80,
                ),
                text="MODULE SUMMARY",
                font=self.watermark_font_small,
                fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA),
                align="right",
            )
            cursor_y = rect[1] + self.GUIDE_SECTION_PADDING_Y
            if command_layout is not None:
                command_rect = (
                    rect[0] + self.GUIDE_SECTION_PADDING_X,
                    cursor_y,
                    rect[2] - self.GUIDE_SECTION_PADDING_X,
                    cursor_y + command_height,
                )
                self._draw_soft_subcard(
                    image,
                    draw,
                    command_rect,
                    radius=self.SINGLE_PAGE_COMMAND_RADIUS,
                    fill=self.theme.terminal_bg,
                    outline=self._rgba(self.theme.accent, 52),
                )
                self._draw_command_layout(
                    draw,
                    x=command_rect[0] + self.theme.trigger_padding_x,
                    y=command_rect[1] + self.theme.trigger_padding_y,
                    layout=command_layout,
                    font=self.body_font,
                    default_fill=self.theme.terminal_text,
                    guide_fill=self.theme.line,
                )
                cursor_y = command_rect[3] + self.SINGLE_PAGE_INNER_GAP
            self._draw_multiline_text(
                draw,
                x=rect[0] + self.GUIDE_SECTION_PADDING_X,
                y=cursor_y,
                lines=body_lines,
                font=self.instruction_font,
                fill=self.theme.deep,
                line_height=body_line_height,
                render_code_chip=False,
            )
        self._draw_showcase_support_strip(image, draw, layout=support_layout)
        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=(
                f"{node.title} · Summary · "
                f"v{node.bundle.version.lstrip('v')} · By {node.bundle.author}"
            ),
            right_text=f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin",
        )
        return encode_docs_image(image, webp_quality=88, webp_method=6)

    def _measure_dashboard_card(
        self,
        node: DocNode,
        width: int,
    ) -> _DashboardCardLayout:
        theme = get_demo_theme(
            theme_name=SENRIN_V3_THEME.name,
            impression_color=node.bundle.impression_color,
        )
        content_width = width - self.DASHBOARD_CARD_PADDING_X * 2
        title_lines = tuple(
            self._wrap_inline_text(
                node.title,
                max_width=content_width,
                font=self.summary_font,
            )
        )[:2]
        summary_lines = tuple(
            self._wrap_inline_text(
                node.summary or node.bundle.summary,
                max_width=content_width,
                font=self.note_font,
            )
        )[:3]
        palette = CommandPalette(
            root=theme.indigo_text,
            text=theme.deep,
            param=theme.pill_pink_text,
            flag=theme.note_success,
        )
        content_x = self.DASHBOARD_CARD_PADDING_X + 24
        content_width = width - content_x - self.DASHBOARD_CARD_PADDING_X
        category_top = self.DASHBOARD_CARD_PADDING_Y
        pill_height = 40
        pill_width = max(
            108,
            self._pixel_text_width(node.category.upper(), self.eyebrow_font) + 28,
        )
        category_rect = (
            content_x,
            category_top,
            content_x + pill_width,
            category_top + pill_height,
        )
        title_line_height = self._line_height_for_font(
            self.summary_font,
            minimum=self._font_pixel_height(self.summary_font)
            + self.DASHBOARD_CARD_TEXT_SPACING,
        )
        title_max_height = self._line_block_height_with_spacing(
            (
                "M",
                "M",
            ),
            self.summary_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        title_lines = self._wrap_plain_text_for_height(
            node.title,
            font=self.summary_font,
            max_width=content_width,
            max_height=title_max_height,
            line_spacing=self.DASHBOARD_CARD_TEXT_SPACING,
            ellipsis="...",
        )
        title_top = category_rect[3] + self.DASHBOARD_CARD_TITLE_GAP
        title_block_height = self._line_block_height_with_spacing(
            tuple(self._plain_text_from_line(line) for line in title_lines),
            self.summary_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        summary_top = title_top + title_block_height + self.DASHBOARD_CARD_SUMMARY_GAP
        command_layout = build_command_layout(
            node_help_command(node),
            max_width=content_width - self.DASHBOARD_CARD_COMMAND_PADDING_X * 2,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=palette,
        )
        command_height = (
            command_layout.total_height + self.DASHBOARD_CARD_COMMAND_PADDING_Y * 2
        )
        summary_max_height = self._line_block_height_with_spacing(
            tuple("M" for _ in range(self.DASHBOARD_CARD_SUMMARY_VISIBLE_LINES)),
            self.note_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        height = (
            summary_top
            + summary_max_height
            + self.DASHBOARD_CARD_BOTTOM_SAFE_GAP
            + command_height
            + self.DASHBOARD_CARD_PADDING_Y
        )
        command_rect = (
            content_x,
            height - self.DASHBOARD_CARD_PADDING_Y - command_height,
            width - self.DASHBOARD_CARD_PADDING_X,
            height - self.DASHBOARD_CARD_PADDING_Y,
        )
        summary_available_height = max(
            0,
            command_rect[1] - self.DASHBOARD_CARD_BOTTOM_SAFE_GAP - summary_top,
        )
        summary_lines = self._wrap_plain_text_for_height(
            node.summary or node.bundle.summary,
            font=self.note_font,
            max_width=content_width,
            max_height=summary_available_height,
            line_spacing=self.DASHBOARD_CARD_TEXT_SPACING,
            ellipsis="...",
        )
        summary_line_height = self._line_height_for_font(
            self.note_font,
            minimum=self._font_pixel_height(self.note_font)
            + self.DASHBOARD_CARD_TEXT_SPACING,
        )
        summary_block_height = self._line_block_height_with_spacing(
            tuple(self._plain_text_from_line(line) for line in summary_lines),
            self.note_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        return _DashboardCardLayout(
            node=node,
            theme=theme,
            title_lines=title_lines,
            summary_lines=summary_lines,
            command_layout=command_layout,
            content_x=content_x,
            content_width=content_width,
            category_rect=category_rect,
            title_top=title_top,
            title_line_height=title_line_height,
            title_block_height=title_block_height,
            summary_top=summary_top,
            summary_line_height=summary_line_height,
            summary_block_height=summary_block_height,
            command_rect=command_rect,
            height=height,
        )

    def _draw_dashboard_card(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        card: _DashboardCardLayout,
        x: int,
        y: int,
        width: int,
    ) -> None:
        rect = (x, y, x + width, y + card.height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.DASHBOARD_CARD_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        accent_rect = (x + 24, y + 24, x + 40, y + card.height - 24)
        draw.rounded_rectangle(accent_rect, radius=8, fill=card.theme.accent)
        content_x = x + card.content_x
        category_rect = (
            x + card.category_rect[0],
            y + card.category_rect[1],
            x + card.category_rect[2],
            y + card.category_rect[3],
        )
        draw.rounded_rectangle(
            category_rect,
            radius=(category_rect[3] - category_rect[1]) // 2,
            fill=card.theme.indigo_soft,
        )
        self._draw_text_centered(
            draw,
            category_rect,
            card.node.category.upper(),
            font=self.eyebrow_font,
            fill=card.theme.indigo_text,
        )
        self._draw_multiline_text(
            draw,
            x=content_x,
            y=y + card.title_top,
            lines=card.title_lines,
            font=self.summary_font,
            fill=card.theme.deep,
            line_height=card.title_line_height,
            render_code_chip=False,
        )
        self._draw_multiline_text(
            draw,
            x=content_x,
            y=y + card.summary_top,
            lines=card.summary_lines,
            font=self.note_font,
            fill=card.theme.hint,
            line_height=card.summary_line_height,
            render_code_chip=False,
        )
        command_rect = (
            x + card.command_rect[0],
            y + card.command_rect[1],
            x + card.command_rect[2],
            y + card.command_rect[3],
        )
        draw.rounded_rectangle(command_rect, radius=20, fill=card.theme.panel_soft_bg)
        self._draw_command_layout(
            draw,
            x=command_rect[0] + self.DASHBOARD_CARD_COMMAND_PADDING_X,
            y=command_rect[1] + self.DASHBOARD_CARD_COMMAND_PADDING_Y,
            layout=card.command_layout,
            font=self.note_font,
            default_fill=card.theme.deep,
            guide_fill=card.theme.line,
        )

    def _measure_dashboard_section_height(
        self,
        section: HelpDashboardSection,
        width: int,
    ) -> int:
        content_width = width - self.GUIDE_SECTION_PADDING_X * 2
        title_lines = tuple(
            self._wrap_inline_text(
                section.title,
                max_width=content_width,
                font=self.summary_font,
            )
        )
        line_height = self._line_height_for_font(self.note_font)
        item_height = 0
        for index, node in enumerate(section.nodes):
            summary_lines = tuple(
                self._wrap_inline_text(
                    node.summary or node.bundle.summary,
                    max_width=content_width - 40,
                    font=self.note_font,
                )
            )[:2]
            item_height += self._line_height_for_font(self.instruction_font)
            item_height += self.DASHBOARD_SECTION_SUMMARY_GAP
            item_height += len(summary_lines) * line_height
            item_height += self.DASHBOARD_SECTION_COMMAND_GAP
            command_layout = build_command_layout(
                node_help_command(node),
                max_width=content_width - 32,
                line_height=self._line_height_for_font(self.note_font),
                indent_px=self.COMMAND_INDENT_PX,
                measure_text=lambda value: self._text_width(value, self.note_font),
                palette=self._command_palette(
                    root=section.command_text or section.text or self.theme.deep,
                    text=section.command_text or section.text or self.theme.deep,
                    param=section.command_text or section.text or self.theme.deep,
                    flag=section.command_text or section.text or self.theme.deep,
                ),
            )
            item_height += (
                command_layout.total_height + self.DASHBOARD_CARD_COMMAND_PADDING_Y * 2
            )
            if index < len(section.nodes) - 1:
                item_height += self.DASHBOARD_SECTION_ITEM_GAP
        return (
            self.GUIDE_SECTION_PADDING_Y * 2
            + len(title_lines) * self._line_height_for_font(self.summary_font)
            + self.DASHBOARD_SECTION_TITLE_GAP
            + item_height
        )

    def _draw_dashboard_section(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        section: HelpDashboardSection,
        top: int,
        left: int,
        width: int,
    ) -> None:
        section_height = self._measure_dashboard_section_height(section, width)
        rect = (left, top, left + width, top + section_height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.GUIDE_SECTION_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=section.panel_bg or self.theme.panel_bg,
        )
        draw.rounded_rectangle(
            (
                rect[0],
                rect[1],
                rect[2],
                rect[1] + self.DASHBOARD_SECTION_TOP_BAR_HEIGHT,
            ),
            radius=self.GUIDE_SECTION_RADIUS,
            fill=section.accent or self.theme.accent,
        )
        content_left = rect[0] + self.GUIDE_SECTION_PADDING_X
        content_width = width - self.GUIDE_SECTION_PADDING_X * 2
        cursor_y = rect[1] + self.GUIDE_SECTION_PADDING_Y
        title_lines = tuple(
            self._wrap_inline_text(
                section.title,
                max_width=content_width,
                font=self.summary_font,
            )
        )
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=title_lines,
            font=self.summary_font,
            fill=section.text or self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
            render_code_chip=False,
        )
        cursor_y += (
            len(title_lines) * self._line_height_for_font(self.summary_font)
            + self.DASHBOARD_SECTION_TITLE_GAP
        )
        for node in section.nodes:
            command_layout = build_command_layout(
                node_help_command(node),
                max_width=content_width - 32,
                line_height=self._line_height_for_font(self.note_font),
                indent_px=self.COMMAND_INDENT_PX,
                measure_text=lambda value: self._text_width(value, self.note_font),
                palette=self._command_palette(
                    root=section.command_text or section.text or self.theme.deep,
                    text=section.command_text or section.text or self.theme.deep,
                    param=section.command_text or section.text or self.theme.deep,
                    flag=section.command_text or section.text or self.theme.deep,
                ),
            )
            summary_lines = tuple(
                self._wrap_inline_text(
                    node.summary or node.bundle.summary,
                    max_width=content_width - 40,
                    font=self.note_font,
                )
            )[:2]
            self._draw_dashboard_marker(
                draw,
                marker=section.marker,
                left=content_left,
                center_y=cursor_y
                + self._line_height_for_font(self.instruction_font) // 2,
                color=section.accent or self.theme.accent,
            )
            self._draw_multiline_text(
                draw,
                x=content_left + 24,
                y=cursor_y,
                lines=(split_inline_text_spans(node.title),),
                font=self.instruction_font,
                fill=self.theme.deep,
                line_height=self._line_height_for_font(self.instruction_font),
                render_code_chip=False,
            )
            cursor_y += (
                self._line_height_for_font(self.instruction_font)
                + self.DASHBOARD_SECTION_SUMMARY_GAP
            )
            self._draw_multiline_text(
                draw,
                x=content_left + 24,
                y=cursor_y,
                lines=summary_lines,
                font=self.note_font,
                fill=section.hint or self.theme.hint,
                line_height=self._line_height_for_font(self.note_font),
                render_code_chip=False,
            )
            cursor_y += (
                len(summary_lines) * self._line_height_for_font(self.note_font)
                + self.DASHBOARD_SECTION_COMMAND_GAP
            )
            command_rect = (
                content_left + 24,
                cursor_y,
                rect[2] - self.GUIDE_SECTION_PADDING_X,
                cursor_y
                + command_layout.total_height
                + self.DASHBOARD_CARD_COMMAND_PADDING_Y * 2,
            )
            draw.rounded_rectangle(
                command_rect,
                radius=(command_rect[3] - command_rect[1]) // 2,
                fill=section.command_bg
                or section.panel_soft_bg
                or self.theme.panel_soft_bg,
                outline=self._rgba(section.accent or self.theme.accent, 64),
                width=1,
            )
            self._draw_command_layout(
                draw,
                x=command_rect[0] + self.DASHBOARD_CARD_COMMAND_PADDING_X,
                y=command_rect[1] + self.DASHBOARD_CARD_COMMAND_PADDING_Y,
                layout=command_layout,
                font=self.note_font,
                default_fill=section.command_text or section.text or self.theme.deep,
                guide_fill=section.accent or self.theme.line,
            )
            cursor_y = command_rect[3] + self.DASHBOARD_SECTION_ITEM_GAP

    def _draw_dashboard_marker(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        marker: str,
        left: int,
        center_y: int,
        color: str,
    ) -> None:
        size = self._DASHBOARD_MARKER_SIZE.get(marker, 12)
        half = size // 2
        if marker == "square":
            draw.rectangle(
                (left, center_y - half, left + size, center_y + half),
                fill=color,
            )
            return
        if marker == "diamond":
            center_x = left + half + 1
            draw.polygon(
                (
                    (center_x, center_y - half),
                    (center_x + half, center_y),
                    (center_x, center_y + half),
                    (center_x - half, center_y),
                ),
                fill=color,
            )
            return
        draw.ellipse(
            (left, center_y - half, left + size, center_y + half),
            outline=color,
            width=3,
        )

    def _measure_plugin_guide_section(
        self,
        *,
        node: DocNode,
        feature: FeatureDoc,
        section_width: int,
    ) -> _GuideSectionLayout:
        content_width = section_width - self.GUIDE_SECTION_PADDING_X * 2
        title_lines = tuple(
            self._wrap_inline_text(
                feature.title,
                max_width=content_width,
                font=self.summary_font,
            )
        )
        summary_lines = tuple(
            self._wrap_inline_text(
                feature.summary,
                max_width=content_width,
                font=self.instruction_font,
            )
        )[:2]
        trigger_layout = build_command_layout(
            feature_command_for_display_text(node.bundle, feature, node.title),
            max_width=content_width - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.body_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.body_font),
            palette=self._command_palette(),
        )
        demo_layout = build_command_layout(
            feature_demo_help_command(node, feature),
            max_width=content_width - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=self._command_palette(),
        )
        overview_layout = build_markdown_layout(
            feature.overview or feature.summary,
            max_width=content_width,
            line_height=self._line_height_for_font(self.instruction_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value, code: self._measure_markdown_text_width(
                value,
                self.instruction_font,
                code=code,
            ),
        )
        note_items = tuple(
            self._measure_note_items(
                feature_preconditions=feature.preconditions,
                feature_failures=feature.failures,
                feature_permission=permission_label(feature.permission),
                width=content_width,
                start_y=0,
                x=0,
            )
        )
        demo_left = self.theme.hero_side_padding
        demo_right = self.WIDTH - self.theme.hero_side_padding
        turn_placements: list[_ShowcaseTurnPlacement] = []
        y_cursor = 0
        for turn in feature.demo_turns:
            spec = self._measure_turn(turn, demo_right - demo_left)
            placement = self._place_turn(
                spec,
                top=y_cursor,
                left=demo_left,
                right=demo_right,
            )
            turn_placements.append(placement)
            y_cursor = placement.rect[3] + self.theme.bubble_gap
        demo_height = max(0, y_cursor - self.theme.bubble_gap)
        note_height = 0
        if note_items:
            note_height = note_items[-1].rect[3] - note_items[0].rect[1]
        height = (
            self.GUIDE_SECTION_PADDING_Y * 2
            + len(title_lines) * self._line_height_for_font(self.summary_font)
            + len(summary_lines) * self._line_height_for_font(self.instruction_font)
            + trigger_layout.total_height
            + demo_layout.total_height
            + overview_layout.total_height
            + note_height
            + demo_height
            + 220
        )
        return _GuideSectionLayout(
            feature=feature,
            title_lines=title_lines,
            summary_lines=summary_lines,
            trigger_layout=trigger_layout,
            demo_layout=demo_layout,
            overview_layout=overview_layout,
            note_items=note_items,
            turn_placements=tuple(turn_placements),
            height=height,
        )

    def _draw_plugin_guide_section(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        node: DocNode,
        layout: _GuideSectionLayout,
        top: int,
        left: int,
        locale: LocaleCode,
    ) -> None:
        width = self.WIDTH - left * 2
        rect = (left, top, left + width, top + layout.height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.GUIDE_SECTION_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        content_left = rect[0] + self.GUIDE_SECTION_PADDING_X
        cursor_y = rect[1] + self.GUIDE_SECTION_PADDING_Y
        self._draw_section_watermark(
            draw,
            rect=(
                content_left,
                rect[1] + 8,
                rect[2] - self.GUIDE_SECTION_PADDING_X,
                rect[1] + 74,
            ),
            text="GUIDE SECTION",
            font=self.watermark_font_small,
            fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA),
            align="right",
        )
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=layout.title_lines,
            font=self.summary_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
        )
        cursor_y += (
            len(layout.title_lines) * self._line_height_for_font(self.summary_font) + 12
        )
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=layout.summary_lines,
            font=self.instruction_font,
            fill=self.theme.hint,
            line_height=self._line_height_for_font(self.instruction_font),
            render_code_chip=False,
        )
        cursor_y += (
            len(layout.summary_lines)
            * self._line_height_for_font(self.instruction_font)
            + 24
        )
        trigger_rect = (
            content_left,
            cursor_y,
            rect[2] - self.GUIDE_SECTION_PADDING_X,
            cursor_y
            + layout.trigger_layout.total_height
            + self.theme.trigger_padding_y * 2,
        )
        self._draw_soft_subcard(
            image,
            draw,
            trigger_rect,
            radius=self.SINGLE_PAGE_COMMAND_RADIUS,
            fill=self.theme.terminal_bg,
            outline=self._rgba(self.theme.accent, 48),
        )
        self._draw_command_layout(
            draw,
            x=trigger_rect[0] + self.theme.trigger_padding_x,
            y=trigger_rect[1] + self.theme.trigger_padding_y,
            layout=layout.trigger_layout,
            font=self.body_font,
            default_fill=self.theme.terminal_text,
            guide_fill=self.theme.line,
        )
        cursor_y = trigger_rect[3] + 12
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=(split_inline_text_spans("查看 demo"),),
            font=self.note_font,
            fill=self.theme.hint,
            line_height=self._line_height_for_font(self.note_font),
            render_code_chip=False,
        )
        cursor_y += self._line_height_for_font(self.note_font) + 8
        demo_rect = (
            content_left,
            cursor_y,
            rect[2] - self.GUIDE_SECTION_PADDING_X,
            cursor_y
            + layout.demo_layout.total_height
            + self.theme.trigger_padding_y * 2,
        )
        self._draw_soft_subcard(
            image,
            draw,
            demo_rect,
            radius=self.SINGLE_PAGE_COMMAND_RADIUS,
            fill=self.theme.panel_soft_bg,
            outline=self._rgba(self.theme.accent, 42),
        )
        self._draw_command_layout(
            draw,
            x=demo_rect[0] + self.theme.trigger_padding_x,
            y=demo_rect[1] + self.theme.trigger_padding_y,
            layout=layout.demo_layout,
            font=self.note_font,
            default_fill=self.theme.deep,
            guide_fill=self.theme.line,
        )
        cursor_y = demo_rect[3] + 24
        self._draw_markdown_layout(
            draw,
            x=content_left,
            y=cursor_y,
            layout=layout.overview_layout,
            font=self.instruction_font,
            fill=self.theme.deep,
        )
        cursor_y += layout.overview_layout.total_height + 24

        if layout.note_items:
            for item in layout.note_items:
                actual_rect = (
                    content_left,
                    cursor_y,
                    rect[2] - self.GUIDE_SECTION_PADDING_X,
                    cursor_y + (item.rect[3] - item.rect[1]),
                )
                dot_y = actual_rect[1] + max(
                    0, (item.line_height - self.theme.note_dot_size) // 2
                )
                draw.ellipse(
                    (
                        actual_rect[0],
                        dot_y,
                        actual_rect[0] + self.theme.note_dot_size,
                        dot_y + self.theme.note_dot_size,
                    ),
                    fill=item.dot_color,
                )
                self._draw_markdown_layout(
                    draw,
                    x=actual_rect[0] + 24,
                    y=actual_rect[1],
                    layout=item.layout,
                    font=self.note_font,
                    fill=self.theme.note_text,
                )
                cursor_y = actual_rect[3] + self.theme.note_gap
            cursor_y += 8

        for placement in layout.turn_placements:
            shifted = _ShowcaseTurnPlacement(
                spec=placement.spec,
                rect=(
                    placement.rect[0],
                    placement.rect[1] + cursor_y,
                    placement.rect[2],
                    placement.rect[3] + cursor_y,
                ),
                avatar_rect=(
                    None
                    if placement.avatar_rect is None
                    else (
                        placement.avatar_rect[0],
                        placement.avatar_rect[1] + cursor_y,
                        placement.avatar_rect[2],
                        placement.avatar_rect[3] + cursor_y,
                    )
                ),
                bubble_rect=(
                    None
                    if placement.bubble_rect is None
                    else (
                        placement.bubble_rect[0],
                        placement.bubble_rect[1] + cursor_y,
                        placement.bubble_rect[2],
                        placement.bubble_rect[3] + cursor_y,
                    )
                ),
                text_rect=(
                    placement.text_rect[0],
                    placement.text_rect[1] + cursor_y,
                    placement.text_rect[2],
                    placement.text_rect[3] + cursor_y,
                ),
                label_rect=(
                    None
                    if placement.label_rect is None
                    else (
                        placement.label_rect[0],
                        placement.label_rect[1] + cursor_y,
                        placement.label_rect[2],
                        placement.label_rect[3] + cursor_y,
                    )
                ),
            )
            self._draw_turn(image, draw, shifted, locale=locale)

    def _measure_advanced_item(
        self,
        *,
        node: DocNode,
        feature: FeatureDoc,
        width: int,
    ) -> _GuideAdvancedItemLayout:
        title_lines = tuple(
            self._wrap_inline_text(
                feature.title,
                max_width=width,
                font=self.instruction_font,
            )
        )[:2]
        summary_lines = tuple(
            self._wrap_inline_text(
                feature.summary,
                max_width=width,
                font=self.note_font,
            )
        )[:2]
        trigger_layout = build_command_layout(
            feature_command_for_display_text(node.bundle, feature, node.title),
            max_width=width,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=self._command_palette(),
        )
        demo_layout = build_command_layout(
            feature_demo_help_command(node, feature),
            max_width=width,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=self._command_palette(),
        )
        height = (
            len(title_lines) * self._line_height_for_font(self.instruction_font)
            + len(summary_lines) * self._line_height_for_font(self.note_font)
            + trigger_layout.total_height
            + demo_layout.total_height
            + 48
        )
        return _GuideAdvancedItemLayout(
            feature=feature,
            title_lines=title_lines,
            summary_lines=summary_lines,
            trigger_layout=trigger_layout,
            demo_layout=demo_layout,
            height=height,
        )

    def _draw_advanced_options(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        node: DocNode,
        layouts: Sequence[_GuideAdvancedItemLayout],
        top: int,
        left: int,
        width: int,
    ) -> None:
        total_height = self.GUIDE_SECTION_PADDING_Y * 2 + 72
        total_height += sum(layout.height for layout in layouts)
        total_height += max(0, len(layouts) - 1) * 24
        rect = (left, top, left + width, top + total_height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.GUIDE_SECTION_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        content_left = rect[0] + self.GUIDE_SECTION_PADDING_X
        cursor_y = rect[1] + self.GUIDE_SECTION_PADDING_Y
        title_lines = tuple(
            self._wrap_inline_text(
                "Advanced Options",
                max_width=width - self.GUIDE_SECTION_PADDING_X * 2,
                font=self.summary_font,
            )
        )
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=title_lines,
            font=self.summary_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
        )
        cursor_y += (
            len(title_lines) * self._line_height_for_font(self.summary_font) + 24
        )
        for layout in layouts:
            self._draw_multiline_text(
                draw,
                x=content_left,
                y=cursor_y,
                lines=layout.title_lines,
                font=self.instruction_font,
                fill=self.theme.deep,
                line_height=self._line_height_for_font(self.instruction_font),
            )
            cursor_y += (
                len(layout.title_lines)
                * self._line_height_for_font(self.instruction_font)
                + 8
            )
            self._draw_multiline_text(
                draw,
                x=content_left,
                y=cursor_y,
                lines=layout.summary_lines,
                font=self.note_font,
                fill=self.theme.hint,
                line_height=self._line_height_for_font(self.note_font),
                render_code_chip=False,
            )
            cursor_y += (
                len(layout.summary_lines) * self._line_height_for_font(self.note_font)
                + 12
            )
            trigger_rect = (
                content_left,
                cursor_y,
                rect[2] - self.GUIDE_SECTION_PADDING_X,
                cursor_y
                + layout.trigger_layout.total_height
                + self.theme.trigger_padding_y * 2,
            )
            draw.rounded_rectangle(
                trigger_rect,
                radius=self.theme.trigger_radius,
                fill=self.theme.terminal_bg,
            )
            self._draw_command_layout(
                draw,
                x=trigger_rect[0] + self.theme.trigger_padding_x,
                y=trigger_rect[1] + self.theme.trigger_padding_y,
                layout=layout.trigger_layout,
                font=self.note_font,
                default_fill=self.theme.terminal_text,
                guide_fill=self.theme.line,
            )
            cursor_y = trigger_rect[3] + 10
            demo_rect = (
                content_left,
                cursor_y,
                rect[2] - self.GUIDE_SECTION_PADDING_X,
                cursor_y
                + layout.demo_layout.total_height
                + self.theme.trigger_padding_y * 2,
            )
            draw.rounded_rectangle(
                demo_rect,
                radius=self.theme.trigger_radius,
                fill=self.theme.panel_soft_bg,
            )
            self._draw_command_layout(
                draw,
                x=demo_rect[0] + self.theme.trigger_padding_x,
                y=demo_rect[1] + self.theme.trigger_padding_y,
                layout=layout.demo_layout,
                font=self.note_font,
                default_fill=self.theme.accent,
                guide_fill=self.theme.line,
            )
            cursor_y = demo_rect[3] + 24

    def _draw_trace_footer(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        footer_rect: tuple[int, int, int, int],
        left_text: str,
        right_text: str,
    ) -> None:
        divider_y = footer_rect[1] + 8
        self._draw_dashed_line(
            draw,
            start=(footer_rect[0], divider_y),
            end=(footer_rect[2], divider_y),
            fill=self.theme.footer_divider,
            dash=10,
            gap=10,
        )
        footer_y = footer_rect[1] + 28
        right_bbox = self._text_size(right_text, self.footer_font)
        right_width = int(right_bbox[2] - right_bbox[0])
        right_x = footer_rect[2] - right_width
        left_max_width = max(120, right_x - footer_rect[0] - 32)
        left_fitted = self._truncate_text_to_width_pixels(
            left_text,
            self.footer_font,
            max_width=left_max_width,
            ellipsis="...",
        )
        self._draw_text(
            draw,
            x=footer_rect[0],
            y=footer_y,
            text=left_fitted,
            font=self.footer_font,
            fill=self.theme.system_text,
        )
        self._draw_text(
            draw,
            x=right_x,
            y=footer_y,
            text=right_text,
            font=self.footer_font,
            fill=self.theme.system_text,
        )

    def render_with_support_strip(
        self,
        image_bytes: bytes,
        *,
        locale: LocaleCode,
        footer_left_text: str,
        footer_right_text: str,
    ) -> bytes:
        source = Image.open(BytesIO(image_bytes)).convert("RGBA")
        footer_trim_height = (
            self.theme.footer_gap_top
            + self.theme.footer_height
            + self.theme.outer_margin
        )
        if source.height > footer_trim_height:
            source = source.crop(
                (0, 0, source.width, source.height - footer_trim_height)
            )
        support_layout = self._measure_support_strip(
            locale=locale,
            top=source.height + self.SUPPORT_STRIP_GAP,
        )
        footer_rect = (
            self.theme.hero_side_padding,
            support_layout.rect[3] + self.theme.footer_gap_top,
            self.WIDTH - self.theme.hero_side_padding,
            support_layout.rect[3]
            + self.theme.footer_gap_top
            + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin
        canvas = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        canvas.paste(source, (0, 0))
        draw = ImageDraw.Draw(canvas)
        self._draw_support_strip(canvas, draw, layout=support_layout)
        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=footer_left_text,
            right_text=footer_right_text,
        )
        return encode_docs_image(canvas, webp_quality=88, webp_method=6)

    def compose_with_support_strip(
        self,
        source: Image.Image,
        *,
        locale: LocaleCode,
        footer_left_text: str,
        footer_right_text: str,
        trim_source_footer: bool = True,
    ) -> bytes:
        source_rgba = source.convert("RGBA")
        footer_trim_height = (
            self.theme.footer_gap_top
            + self.theme.footer_height
            + self.theme.outer_margin
        )
        if trim_source_footer and source_rgba.height > footer_trim_height:
            source_rgba = source_rgba.crop(
                (0, 0, source_rgba.width, source_rgba.height - footer_trim_height)
            )
        support_layout = self._measure_support_strip(
            locale=locale,
            top=source_rgba.height + self.SUPPORT_STRIP_GAP,
        )
        footer_rect = (
            self.theme.hero_side_padding,
            support_layout.rect[3] + self.theme.footer_gap_top,
            self.WIDTH - self.theme.hero_side_padding,
            support_layout.rect[3]
            + self.theme.footer_gap_top
            + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin
        canvas = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        canvas.paste(source_rgba, (0, 0))
        draw = ImageDraw.Draw(canvas)
        self._draw_support_strip(canvas, draw, layout=support_layout)
        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=footer_left_text,
            right_text=footer_right_text,
        )
        return encode_docs_image(canvas, webp_quality=88, webp_method=6)

    def _primary_feature_for_entry(self, node: DocNode) -> FeatureDoc | None:
        return node.features[0] if node.features else None

    def _measure_showcase_support_strip(
        self,
        *,
        locale: LocaleCode,
        top: int,
    ) -> _SupportStripLayout:
        bundle = build_help_support_bundle(locale=locale)
        side = self.theme.hero_side_padding
        qr_image = self._load_support_qr_image(bundle.qr_asset_path)
        qr_width = qr_image.width if qr_image is not None else 0
        qr_frame_width = (
            qr_width + self.SUPPORT_STRIP_QR_FRAME_PADDING_X * 2
            if qr_image is not None
            else 0
        )
        qr_frame_height = (
            qr_image.height + self.SUPPORT_STRIP_QR_FRAME_PADDING_Y * 2
            if qr_image is not None
            else 0
        )
        text_width = (
            self.WIDTH
            - side * 2
            - self.SUPPORT_STRIP_PADDING_X * 2
            - (qr_frame_width + 28 if qr_image is not None else 0)
        )
        title_lines = tuple(
            self._wrap_inline_text(
                bundle.title,
                max_width=text_width,
                font=self.summary_font,
            )
        )
        tip_lines = tuple(
            self._wrap_inline_text(
                bundle.tip_text,
                max_width=text_width,
                font=self.note_font,
            )
        )
        group_lines = tuple(
            tuple(
                self._wrap_inline_text(
                    group.title,
                    max_width=max(160, text_width),
                    font=self.note_font,
                )
            )
            for group in bundle.groups
        )
        title_height = len(title_lines) * self._line_height_for_font(self.summary_font)
        tip_height = len(tip_lines) * self._line_height_for_font(self.note_font)
        groups_height = sum(
            len(lines) * self._line_height_for_font(self.note_font) + 10
            for lines in group_lines
        )
        group_panel_height = groups_height if group_lines else 0
        text_height = title_height + 18 + tip_height + 20 + group_panel_height
        strip_height = max(
            text_height + self.SUPPORT_STRIP_PADDING_Y * 2,
            qr_frame_height + self.SUPPORT_STRIP_PADDING_Y * 2,
        )
        rect = (side, top, self.WIDTH - side, top + strip_height)
        qr_rect = None
        if qr_image is not None:
            qr_frame_left = rect[2] - self.SUPPORT_STRIP_PADDING_X - qr_frame_width
            qr_frame_top = (
                rect[1]
                + (strip_height - qr_frame_height) // 2
                + self.SUPPORT_STRIP_QR_VERTICAL_OFFSET_Y
            )
            qr_frame_top = max(rect[1], qr_frame_top)
            qr_left = qr_frame_left + self.SUPPORT_STRIP_QR_FRAME_PADDING_X
            qr_top = qr_frame_top + self.SUPPORT_STRIP_QR_FRAME_PADDING_Y
            qr_rect = (
                qr_left,
                qr_top,
                qr_left + qr_image.width,
                qr_top + qr_image.height,
            )
        return _SupportStripLayout(
            rect=rect,
            title_lines=title_lines,
            tip_lines=tip_lines,
            group_lines=group_lines,
            qr_image=qr_image,
            qr_rect=qr_rect,
        )

    def _draw_showcase_support_strip(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        layout: _SupportStripLayout,
    ) -> None:
        self._draw_shadowed_rect(
            image,
            rect=layout.rect,
            radius=self.SUPPORT_STRIP_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        draw.rounded_rectangle(
            layout.rect,
            radius=self.SUPPORT_STRIP_RADIUS,
            outline=self._rgba(self.theme.accent, 62),
            width=2,
        )
        draw.rounded_rectangle(
            (
                layout.rect[0],
                layout.rect[1],
                layout.rect[0] + 12,
                layout.rect[3],
            ),
            radius=12,
            fill=self.theme.accent,
        )
        # self._draw_section_watermark(
        #     draw,
        #     rect=(
        #         layout.rect[0] + self.SUPPORT_STRIP_PADDING_X,
        #         layout.rect[1] + 10,
        #         layout.rect[2] - self.SUPPORT_STRIP_PADDING_X,
        #         layout.rect[1] + 72,
        #     ),
        #     text="COMMUNITY INTERACTION",
        #     font=self.watermark_font_small,
        #     fill=self._rgba(self.theme.accent, self.WATERMARK_ALPHA),
        #     align="right",
        # )
        text_x = layout.rect[0] + self.SUPPORT_STRIP_PADDING_X
        text_y = layout.rect[1] + self.SUPPORT_STRIP_PADDING_Y
        self._draw_multiline_text(
            draw,
            x=text_x,
            y=text_y,
            lines=layout.title_lines,
            font=self.summary_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
            render_code_chip=False,
        )
        text_y += (
            len(layout.title_lines) * self._line_height_for_font(self.summary_font) + 18
        )
        self._draw_multiline_text(
            draw,
            x=text_x,
            y=text_y,
            lines=layout.tip_lines,
            font=self.note_font,
            fill=self.theme.hint,
            line_height=self._line_height_for_font(self.note_font),
            render_code_chip=False,
        )
        text_y += (
            len(layout.tip_lines) * self._line_height_for_font(self.note_font) + 20
        )
        if layout.group_lines:
            group_text_y = text_y
            for lines in layout.group_lines:
                self._draw_multiline_text(
                    draw,
                    x=text_x,
                    y=group_text_y,
                    lines=lines,
                    font=self.note_font,
                    fill=self.theme.deep,
                    line_height=self._line_height_for_font(self.note_font),
                    render_code_chip=False,
                )
                group_text_y += (
                    len(lines) * self._line_height_for_font(self.note_font) + 10
                )
        if layout.qr_image is not None and layout.qr_rect is not None:
            qr_frame_rect = (
                layout.qr_rect[0] - self.SUPPORT_STRIP_QR_FRAME_PADDING_X,
                layout.qr_rect[1] - self.SUPPORT_STRIP_QR_FRAME_PADDING_Y,
                layout.qr_rect[2] + self.SUPPORT_STRIP_QR_FRAME_PADDING_X,
                layout.qr_rect[3] + self.SUPPORT_STRIP_QR_FRAME_PADDING_Y,
            )
            self._draw_soft_subcard(
                image,
                draw,
                qr_frame_rect,
                radius=self.SUPPORT_STRIP_QR_FRAME_RADIUS,
                fill="#FFFDFC",
                outline=self._rgba(self.theme.accent, 48),
            )
            image.alpha_composite(
                layout.qr_image,
                (layout.qr_rect[0], layout.qr_rect[1]),
            )

    def _load_support_qr_image(self, asset_path: Path) -> Image.Image | None:
        if not asset_path.exists():
            return None
        try:
            image = Image.open(asset_path).convert("RGBA")
        except OSError:
            return None
        bbox = image.getbbox()
        if bbox is not None:
            image = image.crop(bbox)
        scale = min(
            self.SUPPORT_STRIP_QR_WIDTH / max(1, image.width),
            self.SUPPORT_STRIP_QR_MAX_HEIGHT / max(1, image.height),
        )
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        return image.resize((width, height), Image.Resampling.LANCZOS)

    def _measure_support_strip(
        self,
        *,
        locale: LocaleCode,
        top: int,
    ) -> _SupportStripLayout:
        bundle = build_help_support_bundle(locale=locale)
        side = self.theme.hero_side_padding
        qr_image = self._load_support_qr_image(bundle.qr_asset_path)
        qr_width = qr_image.width if qr_image is not None else 0
        qr_frame_width = (
            qr_width + self.SUPPORT_STRIP_QR_FRAME_PADDING_X * 2
            if qr_image is not None
            else 0
        )
        qr_frame_height = (
            qr_image.height + self.SUPPORT_STRIP_QR_FRAME_PADDING_Y * 2
            if qr_image is not None
            else 0
        )
        text_width = (
            self.WIDTH
            - side * 2
            - self.SUPPORT_STRIP_PADDING_X * 2
            - (qr_frame_width + 24 if qr_image is not None else 0)
        )
        title_lines = tuple(
            self._wrap_inline_text(
                bundle.title,
                max_width=text_width,
                font=self.summary_font,
            )
        )
        tip_lines = tuple(
            self._wrap_inline_text(
                bundle.tip_text,
                max_width=text_width,
                font=self.note_font,
            )
        )
        group_lines = tuple(
            tuple(
                self._wrap_inline_text(
                    group.title,
                    max_width=text_width,
                    font=self.note_font,
                )
            )
            for group in bundle.groups
        )
        title_height = len(title_lines) * self._line_height_for_font(self.summary_font)
        tip_height = len(tip_lines) * self._line_height_for_font(self.note_font)
        groups_height = sum(
            len(lines) * self._line_height_for_font(self.note_font) + 8
            for lines in group_lines
        )
        text_height = title_height + 16 + tip_height + 16 + groups_height
        strip_height = max(
            text_height + self.SUPPORT_STRIP_PADDING_Y * 2,
            qr_frame_height + self.SUPPORT_STRIP_PADDING_Y * 2,
        )
        rect = (side, top, self.WIDTH - side, top + strip_height)
        qr_rect = None
        if qr_image is not None:
            qr_frame_left = rect[2] - self.SUPPORT_STRIP_PADDING_X - qr_frame_width
            qr_frame_top = (
                rect[1]
                + (strip_height - qr_frame_height) // 2
                + self.SUPPORT_STRIP_QR_VERTICAL_OFFSET_Y
            )
            qr_frame_top = max(rect[1], qr_frame_top)
            qr_left = qr_frame_left + self.SUPPORT_STRIP_QR_FRAME_PADDING_X
            qr_top = qr_frame_top + self.SUPPORT_STRIP_QR_FRAME_PADDING_Y
            qr_rect = (
                qr_left,
                qr_top,
                qr_left + qr_image.width,
                qr_top + qr_image.height,
            )
        return _SupportStripLayout(
            rect=rect,
            title_lines=title_lines,
            tip_lines=tip_lines,
            group_lines=group_lines,
            qr_image=qr_image,
            qr_rect=qr_rect,
        )

    def _draw_support_strip(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        layout: _SupportStripLayout,
    ) -> None:
        self._draw_shadowed_rect(
            image,
            rect=layout.rect,
            radius=self.SUPPORT_STRIP_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        draw.rounded_rectangle(
            (
                layout.rect[0],
                layout.rect[1],
                layout.rect[0] + 10,
                layout.rect[3],
            ),
            radius=10,
            fill=self.theme.accent,
        )
        text_x = layout.rect[0] + self.SUPPORT_STRIP_PADDING_X
        text_y = layout.rect[1] + self.SUPPORT_STRIP_PADDING_Y
        self._draw_multiline_text(
            draw,
            x=text_x,
            y=text_y,
            lines=layout.title_lines,
            font=self.summary_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
            render_code_chip=False,
        )
        text_y += (
            len(layout.title_lines) * self._line_height_for_font(self.summary_font) + 16
        )
        self._draw_multiline_text(
            draw,
            x=text_x,
            y=text_y,
            lines=layout.tip_lines,
            font=self.note_font,
            fill=self.theme.hint,
            line_height=self._line_height_for_font(self.note_font),
            render_code_chip=False,
        )
        text_y += (
            len(layout.tip_lines) * self._line_height_for_font(self.note_font) + 16
        )
        for lines in layout.group_lines:
            self._draw_multiline_text(
                draw,
                x=text_x,
                y=text_y,
                lines=lines,
                font=self.note_font,
                fill=self.theme.deep,
                line_height=self._line_height_for_font(self.note_font),
                render_code_chip=False,
            )
            text_y += len(lines) * self._line_height_for_font(self.note_font) + 8
        if layout.qr_image is not None and layout.qr_rect is not None:
            draw.rounded_rectangle(
                (
                    layout.qr_rect[0] - self.SUPPORT_STRIP_QR_FRAME_PADDING_X,
                    layout.qr_rect[1] - self.SUPPORT_STRIP_QR_FRAME_PADDING_Y,
                    layout.qr_rect[2] + self.SUPPORT_STRIP_QR_FRAME_PADDING_X,
                    layout.qr_rect[3] + self.SUPPORT_STRIP_QR_FRAME_PADDING_Y,
                ),
                radius=self.SUPPORT_STRIP_QR_FRAME_RADIUS,
                fill=(255, 250, 252, 235),
                outline=(240, 214, 225, 255),
                width=2,
            )
            image.alpha_composite(
                layout.qr_image,
                (layout.qr_rect[0], layout.qr_rect[1]),
            )

    def _wrap_plain_text_for_height(
        self,
        text: str,
        *,
        font: Any,
        max_width: int,
        max_height: int,
        line_spacing: int,
        ellipsis: str = "...",
    ) -> tuple[tuple[InlineTextSpan, ...], ...]:
        normalized = text.strip()
        if not normalized:
            return ((),)

        raw_lines = self._wrap_plain_text_pixels(
            normalized,
            font=font,
            max_width=max_width,
            max_height=max_height,
            line_spacing=line_spacing,
            ellipsis=ellipsis,
        )
        return tuple(
            ((InlineTextSpan(line, code=False),) if line else ()) for line in raw_lines
        )

    def _wrap_plain_text_pixels(
        self,
        text: str,
        *,
        font: Any,
        max_width: int,
        max_height: int,
        line_spacing: int,
        ellipsis: str = "...",
    ) -> tuple[str, ...]:
        if max_width <= 0 or max_height <= 0:
            return (self._truncate_text_to_width_pixels(text, font, max_width=0),)

        lines: list[str] = []
        used_height = 0
        paragraphs = [part.strip() for part in text.splitlines()] or [text]

        for paragraph in paragraphs:
            remaining = paragraph
            while remaining:
                line, rest = self._fit_plain_text_line_pixels(
                    remaining,
                    font=font,
                    max_width=max_width,
                )
                line_height = self._pixel_text_height(line or "Ag", font)
                projected_height = used_height + (
                    line_height if not lines else line_spacing + line_height
                )
                if projected_height > max_height:
                    if lines:
                        lines[-1] = self._truncate_text_to_width_pixels(
                            lines[-1],
                            font,
                            max_width=max_width,
                            ellipsis=ellipsis,
                        )
                        return tuple(lines)
                    return (
                        self._truncate_text_to_width_pixels(
                            remaining,
                            font,
                            max_width=max_width,
                            ellipsis=ellipsis,
                        ),
                    )

                if rest:
                    next_height = (
                        projected_height
                        + line_spacing
                        + self._pixel_text_height("Ag", font)
                    )
                    if next_height > max_height:
                        lines.append(
                            self._truncate_text_to_width_pixels(
                                line,
                                font,
                                max_width=max_width,
                                ellipsis=ellipsis,
                            )
                        )
                        return tuple(lines)

                lines.append(line)
                used_height = projected_height
                remaining = rest

        return tuple(lines or [""])

    def _fit_plain_text_line_pixels(
        self,
        text: str,
        *,
        font: Any,
        max_width: int,
    ) -> tuple[str, str]:
        if not text:
            return "", ""
        if max_width <= 0:
            return "", text

        current_chars: list[str] = []
        current_width = 0
        last_break_after = -1
        allow_mid_word_break = self._looks_like_url(text)

        for index, char in enumerate(text):
            char_width = self._pixel_text_width(char, font)
            if current_chars and current_width + char_width > max_width:
                split_at = len(current_chars)
                if not allow_mid_word_break and last_break_after > 0:
                    split_at = last_break_after
                line = "".join(current_chars[:split_at]).rstrip()
                carry = "".join(current_chars[split_at:]) + text[index:]
                return line or "".join(current_chars), carry.lstrip()

            current_chars.append(char)
            current_width += char_width
            if self._is_wrap_boundary(char):
                last_break_after = len(current_chars)

        return "".join(current_chars).rstrip(), ""

    def _truncate_text_to_width_pixels(
        self,
        text: str,
        font: Any,
        *,
        max_width: int,
        ellipsis: str = "...",
    ) -> str:
        if max_width <= 0:
            return ellipsis
        if self._pixel_text_width(text, font) <= max_width:
            return text

        ellipsis_width = self._pixel_text_width(ellipsis, font)
        if ellipsis_width >= max_width:
            return ellipsis

        current_chars: list[str] = []
        current_width = 0
        for char in text:
            char_width = self._pixel_text_width(char, font)
            if (
                current_chars
                and current_width + char_width + ellipsis_width > max_width
            ):
                break
            if not current_chars and char_width + ellipsis_width > max_width:
                break
            current_chars.append(char)
            current_width += char_width

        candidate = "".join(current_chars).rstrip()
        while (
            candidate and self._pixel_text_width(candidate + ellipsis, font) > max_width
        ):
            candidate = candidate[:-1].rstrip()
        return f"{candidate}{ellipsis}" if candidate else ellipsis

    def _pixel_text_width(self, text: str, font: Any) -> int:
        if not text:
            return 0
        if not self._contains_emoji(text) and hasattr(font, "getlength"):
            return ceil(float(font.getlength(text)))
        return self._text_width(text, font)

    def _pixel_text_height(self, text: str, font: Any) -> int:
        sample = text or "Ag"
        if not self._contains_emoji(sample) and hasattr(font, "getbbox"):
            bbox = font.getbbox(sample)
            return int(bbox[3] - bbox[1])
        bbox = self._text_size(sample, font)
        return int(bbox[3] - bbox[1])

    def _font_pixel_height(self, font: Any) -> int:
        return self._pixel_text_height("Ag", font)

    def _line_block_height_with_spacing(
        self,
        lines: Sequence[str],
        font: Any,
        line_spacing: int,
    ) -> int:
        if not lines:
            return 0
        total = 0
        for index, line in enumerate(lines):
            total += self._pixel_text_height(line or "Ag", font)
            if index < len(lines) - 1:
                total += line_spacing
        return total

    def _plain_text_from_line(self, line: Sequence[InlineTextSpan]) -> str:
        return "".join(span.text for span in line)

    def _is_wrap_boundary(self, char: str) -> bool:
        return char.isspace() or char in "-/_,.;:|)]}>"

    def _looks_like_url(self, text: str) -> bool:
        lowered = text.lower()
        return "://" in lowered or lowered.startswith("www.") or "www." in lowered
