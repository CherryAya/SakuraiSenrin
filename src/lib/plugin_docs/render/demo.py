"""Feature showcase demo renderer."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from math import ceil
from pathlib import Path
import re
from typing import Any, ClassVar, Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pil_utils import BuildImage
from pil_utils.text2image import Text2Image

from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.demo_theme import (
    DEFAULT_DEMO_THEME,
    SENRIN_V3_THEME,
    get_demo_theme,
    normalize_hex_color,
)
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs.command_layout import (
    CommandLayout,
    CommandPalette,
    InlineTextSpan,
    build_command_layout,
    split_inline_text_spans,
)
from src.lib.plugin_docs.markdown_layout import (
    MarkdownLayout,
    MarkdownLayoutLine,
    build_markdown_layout,
)
from src.lib.plugin_docs.models import DocsDemoTurn
from src.lib.utils.common import get_current_time

DEMO_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
DEMO_AVATAR_PATH = DEMO_ASSETS_DIR / "senrin-demo-avatar.png"
DEMO_STANDEE_PATH = DEMO_ASSETS_DIR / "senrin-demo-standee.png"


@dataclass(slots=True, frozen=True)
class _TurnSpec:
    turn: DocsDemoTurn
    lines: list[tuple[InlineTextSpan, ...]]
    width: int
    height: int


@dataclass(slots=True, frozen=True)
class _ShowcaseNoteItem:
    rect: tuple[int, int, int, int]
    layout: MarkdownLayout
    line_height: int
    dot_color: str


@dataclass(slots=True, frozen=True)
class _ShowcaseTurnSpec:
    turn: DocsDemoTurn
    lines: tuple[tuple[InlineTextSpan, ...], ...]
    width: int
    height: int
    line_height: int
    detail_lines: tuple[tuple[InlineTextSpan, ...], ...] = ()


@dataclass(slots=True, frozen=True)
class _ShowcaseTurnPlacement:
    spec: _ShowcaseTurnSpec
    rect: tuple[int, int, int, int]
    avatar_rect: tuple[int, int, int, int] | None
    bubble_rect: tuple[int, int, int, int] | None
    text_rect: tuple[int, int, int, int]
    label_rect: tuple[int, int, int, int] | None = None


@dataclass(slots=True, frozen=True)
class _DemoSectionBand:
    index: int
    title: str
    rect: tuple[int, int, int, int]
    content_rect: tuple[int, int, int, int]
    tag_rect: tuple[int, int, int, int]
    header_rect: tuple[int, int, int, int]
    divider_rect: tuple[int, int, int, int]
    fill: str
    accent: str


@dataclass(slots=True, frozen=True)
class _ShowcaseLayout:
    plugin_title: str
    feature_title: str
    feature_summary: str
    plugin_version: str
    plugin_author: str
    pills: tuple[tuple[str, str, str], ...]
    pill_rects: tuple[tuple[int, int, int, int], ...]
    plugin_rect: tuple[int, int, int, int]
    plugin_lines: tuple[tuple[InlineTextSpan, ...], ...]
    title_compact: bool
    title_rect: tuple[int, int, int, int]
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_compact: bool
    summary_rect: tuple[int, int, int, int]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    hero_rect: tuple[int, int, int, int]
    standee_rect: tuple[int, int, int, int]
    instruction_rect: tuple[int, int, int, int]
    trigger_rect: tuple[int, int, int, int]
    trigger_layout: CommandLayout
    overview_rect: tuple[int, int, int, int]
    overview_layout: MarkdownLayout
    note_items: tuple[_ShowcaseNoteItem, ...]
    instruction_content_rects: tuple[tuple[int, int, int, int], ...]
    demo_heading_rect: tuple[int, int, int, int] | None
    demo_rect: tuple[int, int, int, int]
    demo_section_bands: tuple[_DemoSectionBand, ...]
    turn_placements: tuple[_ShowcaseTurnPlacement, ...]
    footer_rect: tuple[int, int, int, int]
    footer_left_text: str
    footer_right_text: str
    total_height: int


class DemoImageRenderer:
    """Render plugin docs feature demos as a single-canvas showcase infographic."""

    WIDTH = DEFAULT_DEMO_THEME.canvas_width
    OUTER_MARGIN = DEFAULT_DEMO_THEME.outer_margin
    FONT_FAMILIES: ClassVar[list[str]] = [MAPLE_FONT_NAME]
    COMMAND_INDENT_PX = 48
    DEFAULT_SECTION_TITLE = "流程演示"

    def __init__(self, *, impression_color: str | None = None) -> None:
        self.theme_name = SENRIN_V3_THEME.name
        self.impression_color = normalize_hex_color(impression_color)
        self.theme = get_demo_theme(
            theme_name=self.theme_name,
            impression_color=self.impression_color,
        )
        try:
            self.kicker_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.eyebrow_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 64)
            self.title_font_compact = ImageFont.truetype(MAPLE_FONT_PATH, 56)
            self.summary_font = ImageFont.truetype(MAPLE_FONT_PATH, 36)
            self.summary_font_compact = ImageFont.truetype(MAPLE_FONT_PATH, 32)
            self.instruction_font = ImageFont.truetype(MAPLE_FONT_PATH, 28)
            self.body_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)
            self.note_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.footer_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.system_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
        except OSError:
            self.kicker_font = ImageFont.load_default()
            self.eyebrow_font = ImageFont.load_default()
            self.title_font = ImageFont.load_default()
            self.title_font_compact = ImageFont.load_default()
            self.summary_font = ImageFont.load_default()
            self.summary_font_compact = ImageFont.load_default()
            self.instruction_font = ImageFont.load_default()
            self.body_font = ImageFont.load_default()
            self.note_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.footer_font = ImageFont.load_default()
            self.system_font = ImageFont.load_default()
        self.senrin_avatar = self._load_asset(DEMO_AVATAR_PATH, self.theme.avatar_size)
        self.senrin_standee = self._load_asset(
            DEMO_STANDEE_PATH,
            self.theme.hero_standee_size,
            alpha=255,
        )

    def render(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_summary: str,
        feature_trigger: str,
        feature_overview: str,
        feature_preconditions: str,
        feature_failures: str,
        feature_flow_notes: str,
        plugin_trigger: str,
        feature_permission: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
        generated_at: datetime | None = None,
    ) -> bytes:
        layout = self._measure_layout(
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_summary=feature_summary,
            feature_trigger=feature_trigger,
            feature_overview=feature_overview,
            feature_preconditions=feature_preconditions,
            feature_failures=feature_failures,
            feature_flow_notes=feature_flow_notes,
            plugin_trigger=plugin_trigger,
            feature_permission=feature_permission,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
            turns=turns,
            locale=locale,
            generated_at=generated_at,
        )
        image = Image.new("RGBA", (self.WIDTH, layout.total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_hero(image, draw, layout)
        self._draw_instruction_card(image, draw, layout)
        self._draw_demo(image, draw, layout, locale=locale)
        self._draw_footer(draw, layout)

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def audit(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_summary: str,
        feature_trigger: str,
        feature_overview: str,
        feature_preconditions: str,
        feature_failures: str,
        feature_flow_notes: str,
        plugin_trigger: str,
        feature_permission: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
        generated_at: datetime | None = None,
    ) -> tuple[str, ...]:
        layout = self._measure_layout(
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_summary=feature_summary,
            feature_trigger=feature_trigger,
            feature_overview=feature_overview,
            feature_preconditions=feature_preconditions,
            feature_failures=feature_failures,
            feature_flow_notes=feature_flow_notes,
            plugin_trigger=plugin_trigger,
            feature_permission=feature_permission,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
            turns=turns,
            locale=locale,
            generated_at=generated_at,
        )
        errors: list[str] = []
        canvas = (0, 0, self.WIDTH, layout.total_height)
        hero_safe = (
            self.theme.hero_side_padding,
            self.theme.hero_top,
            self.WIDTH - self.theme.hero_side_padding,
            layout.hero_rect[3],
        )
        instruction_inner = (
            layout.instruction_rect[0] + self.theme.instruction_padding_x,
            layout.instruction_rect[1] + self.theme.instruction_padding_y,
            layout.instruction_rect[2] - self.theme.instruction_padding_x,
            layout.instruction_rect[3] - self.theme.instruction_padding_y,
        )
        self._ensure_inside(canvas, layout.hero_rect, "hero section", errors)
        self._ensure_inside(
            canvas, layout.instruction_rect, "instruction section", errors
        )
        self._ensure_inside(canvas, layout.footer_rect, "footer section", errors)
        for index, rect in enumerate(layout.pill_rects, start=1):
            self._ensure_inside(hero_safe, rect, f"hero pill {index}", errors)
        self._ensure_inside(hero_safe, layout.plugin_rect, "plugin kicker", errors)
        self._ensure_inside(hero_safe, layout.title_rect, "hero title", errors)
        self._ensure_inside(hero_safe, layout.summary_rect, "hero summary", errors)
        self._ensure_inside(canvas, layout.standee_rect, "hero standee", errors)
        self._ensure_inside(
            instruction_inner, layout.trigger_rect, "trigger block", errors
        )
        if self._boxes_overlap(layout.title_rect, layout.standee_rect, padding=12):
            errors.append("hero title overlaps standee")
        if self._boxes_overlap(layout.summary_rect, layout.standee_rect, padding=12):
            errors.append("hero summary overlaps standee")
        for index, rect in enumerate(layout.instruction_content_rects, start=1):
            self._ensure_inside(
                instruction_inner, rect, f"instruction block {index}", errors
            )
        prior_rects: list[tuple[str, tuple[int, int, int, int]]] = []
        for index, placement in enumerate(layout.turn_placements, start=1):
            for name, rect in self._turn_rects(placement):
                self._ensure_inside(
                    layout.demo_rect, rect, f"turn {index} {name}", errors
                )
                for prior_name, prior_rect in prior_rects:
                    self._ensure_no_overlap(
                        prior_rect,
                        rect,
                        prior_name,
                        f"turn {index} {name}",
                        errors,
                        padding=4,
                    )
                prior_rects.append((f"turn {index} {name}", rect))
        return tuple(errors)

    def preview_crop_box(
        self, image_size: tuple[int, int]
    ) -> tuple[int, int, int, int]:
        width, height = image_size
        side = max(0, self.theme.hero_side_padding - 16)
        if height >= 1600:
            top = int(height * 0.34)
            bottom = int(height * 0.84)
        elif height >= 1200:
            top = int(height * 0.24)
            bottom = int(height * 0.82)
        else:
            top = int(height * 0.14)
            bottom = int(height * 0.84)
        bottom = max(top + 1, bottom)
        return (side, top, max(side + 1, width - side), min(height, bottom))

    def _measure_layout(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_summary: str,
        feature_trigger: str,
        feature_overview: str,
        feature_preconditions: str,
        feature_failures: str,
        feature_flow_notes: str,
        plugin_trigger: str,
        feature_permission: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode,
        generated_at: datetime | None,
    ) -> _ShowcaseLayout:
        _ = locale
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        standee_size = self.theme.hero_standee_size
        text_max_width = min(
            self.WIDTH - side * 2 - standee_size - self.theme.hero_content_gap - 36,
            int((self.WIDTH - side * 2) * 0.62),
        )
        pills_list = [
            ("PLUGIN DOCS", self.theme.pill_blue_bg, self.theme.pill_blue_text),
            (
                plugin_trigger or "文档指引",
                self.theme.pill_blue_bg,
                self.theme.pill_blue_text,
            ),
            (plugin_author, self.theme.pill_pink_bg, self.theme.pill_pink_text),
        ]
        if feature_permission.strip() and feature_permission != "普通用户":
            pills_list.insert(
                1,
                (
                    feature_permission,
                    self.theme.pill_pink_bg,
                    self.theme.pill_pink_text,
                ),
            )
        pills = tuple(pills_list)
        pill_rects: list[tuple[int, int, int, int]] = []
        x = side
        y = self.theme.hero_top
        row_bottom = y + self.theme.pill_height
        for text, _, _ in pills:
            pill_width = self._pill_width(text, self.eyebrow_font)
            if x > side and x + pill_width > side + text_max_width:
                x = side
                y += self.theme.pill_height + self.theme.pill_gap
            rect = (x, y, x + pill_width, y + self.theme.pill_height)
            pill_rects.append(rect)
            x += pill_width + self.theme.pill_gap
            row_bottom = max(row_bottom, rect[3])

        normalized_plugin_title = plugin_title.strip()
        normalized_feature_title = feature_title.strip()
        show_plugin_kicker = bool(normalized_plugin_title) and (
            normalized_plugin_title != normalized_feature_title
        )
        plugin_lines = (
            tuple(
                self._wrap_inline_text(
                    normalized_plugin_title or "插件文档",
                    max_width=text_max_width,
                    font=self.kicker_font,
                )[:1]
            )
            if show_plugin_kicker
            else ()
        )
        plugin_y = row_bottom + 24
        plugin_rect = (
            side,
            plugin_y,
            side + self._max_inline_line_width(plugin_lines, self.kicker_font),
            plugin_y
            + self._line_block_height(
                plugin_lines,
                self._line_height_for_font(self.kicker_font),
            ),
        )

        title_text = feature_title.strip() or plugin_title.strip() or "功能说明"
        title_lines = tuple(
            self._wrap_inline_text(
                title_text,
                max_width=text_max_width,
                font=self.title_font,
            )[:2]
        )
        title_compact = False
        if len(title_lines) > 1:
            title_lines = tuple(
                self._wrap_inline_text(
                    title_text,
                    max_width=text_max_width,
                    font=self.title_font_compact,
                )[:2]
            )
            title_compact = True
        active_title_font = (
            self.title_font_compact if title_compact else self.title_font
        )
        title_y = plugin_rect[3] + (24 if plugin_lines else 0)
        title_rect = (
            side,
            title_y,
            side + self._max_inline_line_width(title_lines, active_title_font),
            title_y
            + self._line_block_height(
                title_lines,
                self._line_height_for_font(active_title_font),
            ),
        )

        summary_source = (
            feature_summary.strip()
            or feature_overview.strip()
            or "查看触发方式、前置条件与实机演示。"
        )
        summary_lines = tuple(
            self._wrap_inline_text(
                summary_source,
                max_width=text_max_width,
                font=self.summary_font,
            )[:4]
        )
        summary_compact = False
        if len(summary_lines) > 2:
            summary_lines = tuple(
                self._wrap_inline_text(
                    summary_source,
                    max_width=text_max_width,
                    font=self.summary_font_compact,
                )[:3]
            )
            summary_compact = True
        active_summary_font = (
            self.summary_font_compact if summary_compact else self.summary_font
        )
        summary_y = title_rect[3] + self.theme.hero_text_gap
        summary_rect = (
            side,
            summary_y,
            side + self._max_inline_line_width(summary_lines, active_summary_font),
            summary_y
            + self._line_block_height(
                summary_lines,
                self._line_height_for_font(
                    active_summary_font,
                    minimum=self.theme.hero_summary_line_height,
                ),
            ),
        )

        instruction_top = summary_rect[3] + self.theme.section_gap
        standee_y = instruction_top - standee_size + self.theme.hero_standee_overlap
        min_standee_top = self.theme.hero_top + 16
        if standee_y < min_standee_top:
            instruction_top += min_standee_top - standee_y
            standee_y = min_standee_top
        standee_rect = (
            self.WIDTH - side - standee_size,
            standee_y,
            self.WIDTH - side,
            standee_y + standee_size,
        )
        hero_rect = (
            0,
            0,
            self.WIDTH,
            max(
                instruction_top,
                summary_rect[3] + self.theme.hero_bottom_padding,
            ),
        )

        instruction_left = side
        instruction_right = self.WIDTH - side
        content_left = instruction_left + self.theme.instruction_padding_x
        content_right = instruction_right - self.theme.instruction_padding_x
        content_width = content_right - content_left
        instruction_y = instruction_top + self.theme.instruction_padding_y

        trigger_layout = build_command_layout(
            feature_trigger.strip() or f"#help {feature_title}",
            max_width=content_width - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.body_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.body_font),
            palette=self._command_palette(),
        )
        trigger_height = self.theme.trigger_padding_y * 2 + trigger_layout.total_height
        trigger_rect = (
            content_left,
            instruction_y,
            content_right,
            instruction_y + trigger_height,
        )
        instruction_y = trigger_rect[3] + self.theme.trigger_gap

        instruction_content_rects: list[tuple[int, int, int, int]] = [trigger_rect]
        overview_layout = build_markdown_layout(
            feature_overview.strip() or summary_source,
            max_width=content_width,
            line_height=self._line_height_for_font(self.instruction_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value, code: self._measure_markdown_text_width(
                value,
                self.instruction_font,
                code=code,
            ),
        )
        overview_rect = (
            content_left,
            instruction_y,
            content_right,
            instruction_y + overview_layout.total_height,
        )
        instruction_content_rects.append(overview_rect)
        instruction_y = overview_rect[3]

        if feature_flow_notes.strip():
            flow_lines = tuple(
                self._wrap_inline_text(
                    feature_flow_notes.strip(),
                    max_width=content_width,
                    font=self.note_font,
                )
            )
            flow_rect = (
                content_left,
                instruction_y + self.theme.note_gap,
                content_left + self._max_inline_line_width(flow_lines, self.note_font),
                instruction_y
                + self.theme.note_gap
                + self._line_block_height(
                    flow_lines,
                    self._line_height_for_font(self.note_font),
                ),
            )
            instruction_content_rects.append(flow_rect)
            instruction_y = flow_rect[3]

        note_items = self._measure_note_items(
            feature_preconditions=feature_preconditions,
            feature_failures=feature_failures,
            feature_permission=feature_permission,
            width=content_width,
            start_y=instruction_y + self.theme.note_gap,
            x=content_left,
        )
        instruction_content_rects.extend(item.rect for item in note_items)
        instruction_bottom = (
            max((rect[3] for rect in instruction_content_rects), default=instruction_y)
            + self.theme.instruction_padding_y
        )
        instruction_rect = (
            instruction_left,
            instruction_top,
            instruction_right,
            instruction_bottom,
        )

        demo_heading_rect: tuple[int, int, int, int] | None = None
        demo_rect: tuple[int, int, int, int] | None = None
        demo_section_bands: list[_DemoSectionBand] = []
        turn_placements: list[_ShowcaseTurnPlacement] = []
        current_bottom = instruction_bottom
        if turns:
            heading_text = "看看它是怎么工作的"
            heading_top = instruction_bottom + max(
                20, self.theme.demo_heading_gap_top - 20
            )
            heading_box = self._text_size(heading_text, self.note_font)
            demo_heading_rect = (
                side,
                heading_top,
                side + heading_box[2] - heading_box[0],
                heading_top + (heading_box[3] - heading_box[1]),
            )
            demo_panel_top = demo_heading_rect[3] + max(
                16, self.theme.demo_heading_gap_bottom - 8
            )
            demo_left = side + 8
            demo_right = self.WIDTH - side - 8
            demo_top = demo_panel_top + 16
            grouped_turns: list[tuple[str, list[DocsDemoTurn]]] = []
            for turn in turns:
                section_title = turn.section.strip()
                if not grouped_turns or grouped_turns[-1][0] != section_title:
                    grouped_turns.append((section_title, [turn]))
                else:
                    grouped_turns[-1][1].append(turn)

            y_cursor = demo_top
            section_gap = 28
            outer_pad_x = 0
            inner_pad_left = 30
            inner_pad_right = 30
            inner_pad_top = 26
            inner_pad_bottom = 22
            title_gap = 14
            title_height = 70
            section_tag_width = min(
                340,
                max(
                    184,
                    max(
                        self._text_width(
                            title or self.DEFAULT_SECTION_TITLE, self.meta_font
                        )
                        + 110
                        for title, _ in grouped_turns
                    ),
                ),
            )
            for section_index, (section_title, section_turns) in enumerate(
                grouped_turns
            ):
                section_left = demo_left + outer_pad_x
                section_right = demo_right - outer_pad_x
                content_left = section_left + inner_pad_left
                content_right = section_right - inner_pad_right
                section_top = y_cursor
                turn_top = section_top + inner_pad_top
                normalized_title = section_title or self.DEFAULT_SECTION_TITLE
                turn_top += title_height + title_gap
                section_turn_placements: list[_ShowcaseTurnPlacement] = []
                for turn in section_turns:
                    spec = self._measure_turn(turn, content_right - content_left)
                    placement = self._place_turn(
                        spec,
                        top=turn_top,
                        left=content_left,
                        right=content_right,
                    )
                    section_turn_placements.append(placement)
                    turn_placements.append(placement)
                    turn_top = placement.rect[3] + self.theme.bubble_gap
                section_last_bottom = (
                    section_turn_placements[-1].rect[3]
                    if section_turn_placements
                    else turn_top
                )
                section_bottom = section_last_bottom + inner_pad_bottom
                fill = (
                    self.theme.showcase_accent_rail_bg
                    if section_index % 2 == 0
                    else self.theme.showcase_support_rail_bg
                )
                accent = (
                    self.theme.accent if section_index % 2 == 0 else self.theme.indigo
                )
                header_rect = (
                    section_left + 28,
                    section_top + 18,
                    section_right - 28,
                    section_top + 18 + title_height,
                )
                tag_rect = (
                    header_rect[0] + 14,
                    header_rect[1] + 12,
                    header_rect[0] + 14 + section_tag_width,
                    header_rect[1] + 12 + 48,
                )
                divider_rect = (
                    tag_rect[2] + 20,
                    tag_rect[1] + 23,
                    header_rect[2] - 18,
                    tag_rect[1] + 25,
                )
                demo_section_bands.append(
                    _DemoSectionBand(
                        index=section_index + 1,
                        title=normalized_title,
                        rect=(section_left, section_top, section_right, section_bottom),
                        content_rect=(
                            content_left,
                            turn_top - title_gap,
                            content_right,
                            section_bottom - inner_pad_bottom,
                        ),
                        tag_rect=tag_rect,
                        header_rect=header_rect,
                        divider_rect=divider_rect,
                        fill=fill,
                        accent=accent,
                    )
                )
                y_cursor = section_bottom + section_gap
            demo_content_bottom = (
                y_cursor - section_gap if demo_section_bands else demo_top
            )
            demo_rect = (
                demo_left,
                demo_panel_top,
                demo_right,
                demo_content_bottom + 16,
            )
            current_bottom = demo_rect[3]

        footer_top = current_bottom + self.theme.footer_gap_top
        footer_rect = (
            side,
            footer_top,
            self.WIDTH - side,
            footer_top + self.theme.footer_height,
        )
        footer_parts = [normalized_plugin_title or "插件文档"]
        if (
            normalized_feature_title
            and normalized_feature_title != normalized_plugin_title
        ):
            footer_parts.append(normalized_feature_title)
        footer_parts.extend([f"v{plugin_version.lstrip('v')}", f"By {plugin_author}"])
        footer_left_text = " · ".join(footer_parts)
        footer_right_text = (
            f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin"
        )
        return _ShowcaseLayout(
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_summary=summary_source,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
            pills=pills,
            pill_rects=tuple(pill_rects),
            plugin_rect=plugin_rect,
            plugin_lines=plugin_lines,
            title_compact=title_compact,
            title_rect=title_rect,
            title_lines=title_lines,
            summary_compact=summary_compact,
            summary_rect=summary_rect,
            summary_lines=summary_lines,
            hero_rect=hero_rect,
            standee_rect=standee_rect,
            instruction_rect=instruction_rect,
            trigger_rect=trigger_rect,
            trigger_layout=trigger_layout,
            overview_rect=overview_rect,
            overview_layout=overview_layout,
            note_items=tuple(note_items),
            instruction_content_rects=tuple(instruction_content_rects),
            demo_heading_rect=demo_heading_rect,
            demo_rect=demo_rect
            or (
                side,
                instruction_bottom,
                self.WIDTH - side,
                instruction_bottom,
            ),
            demo_section_bands=tuple(demo_section_bands),
            turn_placements=tuple(turn_placements),
            footer_rect=footer_rect,
            footer_left_text=footer_left_text,
            footer_right_text=footer_right_text,
            total_height=footer_rect[3] + self.theme.outer_margin,
        )

    def _paint_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, width, height), fill=self.theme.page_bg)
        self._draw_dot_matrix(draw, width=width, height=height)
        self._draw_floating_decor(draw, width=width, height=height)

    def _draw_hero(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
    ) -> None:
        for rect, pill in zip(layout.pill_rects, layout.pills, strict=True):
            self._draw_pill(
                draw,
                rect=rect,
                text=pill[0],
                fill=pill[1],
                text_fill=pill[2],
            )
        self._draw_multiline_text(
            draw,
            x=layout.plugin_rect[0],
            y=layout.plugin_rect[1],
            lines=layout.plugin_lines,
            font=self.kicker_font,
            fill=self.theme.strong,
            line_height=self._line_height_for_font(self.kicker_font),
        )
        self._draw_multiline_text(
            draw,
            x=layout.title_rect[0],
            y=layout.title_rect[1] + self.theme.hero_title_shadow_offset_y,
            lines=layout.title_lines,
            font=self.title_font_compact if layout.title_compact else self.title_font,
            fill=self.theme.hero_title_shadow,
            line_height=self._line_height_for_font(
                self.title_font_compact if layout.title_compact else self.title_font
            ),
        )
        self._draw_multiline_text(
            draw,
            x=layout.title_rect[0],
            y=layout.title_rect[1],
            lines=layout.title_lines,
            font=self.title_font_compact if layout.title_compact else self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(
                self.title_font_compact if layout.title_compact else self.title_font
            ),
        )
        self._draw_multiline_text(
            draw,
            x=layout.summary_rect[0],
            y=layout.summary_rect[1],
            lines=layout.summary_lines,
            font=(
                self.summary_font_compact
                if layout.summary_compact
                else self.summary_font
            ),
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font_compact
                if layout.summary_compact
                else self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        self._draw_standee(image, draw, layout.standee_rect)

    def _draw_instruction_card(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
    ) -> None:
        self._draw_shadowed_rect(
            image,
            rect=layout.instruction_rect,
            radius=self.theme.instruction_radius,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        draw.rounded_rectangle(
            layout.trigger_rect,
            radius=self.theme.trigger_radius,
            fill=self.theme.terminal_bg,
        )
        self._draw_command_layout(
            draw,
            x=layout.trigger_rect[0] + self.theme.trigger_padding_x,
            y=layout.trigger_rect[1] + self.theme.trigger_padding_y,
            layout=layout.trigger_layout,
            font=self.body_font,
            default_fill=self.theme.terminal_text,
            guide_fill=self.theme.line,
        )
        self._draw_markdown_layout(
            draw,
            x=layout.overview_rect[0],
            y=layout.overview_rect[1],
            layout=layout.overview_layout,
            font=self.instruction_font,
            fill=self.theme.deep,
            max_width=layout.overview_rect[2] - layout.overview_rect[0],
        )
        for item in layout.note_items:
            dot_y = item.rect[1] + max(
                0,
                ((item.line_height - self.theme.note_dot_size) // 2) + 2,
            )
            draw.ellipse(
                (
                    item.rect[0],
                    dot_y,
                    item.rect[0] + self.theme.note_dot_size,
                    dot_y + self.theme.note_dot_size,
                ),
                fill=item.dot_color,
            )
            self._draw_markdown_layout(
                draw,
                x=item.rect[0] + 24,
                y=item.rect[1],
                layout=item.layout,
                font=self.note_font,
                fill=self.theme.note_text,
            )

    def _draw_demo(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
        *,
        locale: LocaleCode,
    ) -> None:
        if layout.demo_heading_rect is None:
            return
        self._draw_text(
            draw,
            x=layout.demo_heading_rect[0],
            y=layout.demo_heading_rect[1],
            text="看看它是怎么工作的",
            font=self.note_font,
            fill=self.theme.demo_heading,
        )
        self._draw_shadowed_rect(
            image,
            rect=layout.demo_rect,
            radius=34,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=24,
            shadow_blur=44,
            fill="#FFFFFF",
        )
        for band in layout.demo_section_bands:
            self._draw_shadowed_rect(
                image,
                rect=band.rect,
                radius=24,
                shadow_color=self.theme.bubble_shadow,
                shadow_offset_y=8,
                shadow_blur=24,
                fill=band.fill,
            )
            draw.rounded_rectangle(
                band.header_rect,
                radius=20,
                fill=self._rgba(band.accent, 18),
            )
            top_accent_rect = (
                band.header_rect[0] + 4,
                band.header_rect[1] + 4,
                band.header_rect[2] - 4,
                band.header_rect[1] + 14,
            )
            draw.rounded_rectangle(
                top_accent_rect,
                radius=999,
                fill=self._rgba(band.accent, 118),
            )
            self._draw_shadowed_rect(
                image,
                rect=band.tag_rect,
                radius=18,
                shadow_color=self.theme.standee_anchor_shadow,
                shadow_offset_y=4,
                shadow_blur=8,
                fill="#FFFFFF",
            )
            index_text = f"{band.index:02d}"
            index_rect = (
                band.tag_rect[0] + 12,
                band.tag_rect[1] + 8,
                band.tag_rect[0] + 50,
                band.tag_rect[3] - 8,
            )
            draw.rounded_rectangle(
                index_rect,
                radius=999,
                fill=band.accent,
            )
            self._draw_text(
                draw,
                x=index_rect[0] + 10,
                y=index_rect[1] + 1,
                text=index_text,
                font=self.meta_font,
                fill="#FFFFFF",
            )
            self._draw_text(
                draw,
                x=index_rect[2] + 12,
                y=band.tag_rect[1] + 6,
                text=band.title,
                font=self.meta_font,
                fill=band.accent,
            )
            draw.rounded_rectangle(
                band.divider_rect,
                radius=999,
                fill=self._rgba(band.accent, 52),
            )
        for placement in layout.turn_placements:
            self._draw_turn(image, draw, placement, locale=locale)

    def _draw_turn(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        placement: _ShowcaseTurnPlacement,
        *,
        locale: LocaleCode,
    ) -> None:
        spec = placement.spec
        if spec.turn.speaker == "SYSTEM":
            if placement.bubble_rect is None or placement.label_rect is None:
                return
            draw.rounded_rectangle(
                placement.bubble_rect,
                radius=self.theme.bubble_radius,
                fill=self.theme.system_bubble,
                outline=self.theme.system_border,
                width=2,
            )
            draw.rounded_rectangle(
                placement.label_rect,
                radius=(placement.label_rect[3] - placement.label_rect[1]) // 2,
                fill=self.theme.system_label_bg,
            )
            self._draw_text_centered(
                draw,
                placement.label_rect,
                "SYSTEM",
                font=self.meta_font,
                fill=self.theme.system_label_text,
            )
            self._draw_multiline_text(
                draw,
                x=placement.text_rect[0],
                y=placement.text_rect[1],
                lines=spec.lines,
                font=self.system_font,
                fill=self.theme.system_text,
                line_height=self._line_height_for_font(self.system_font),
                align="center",
                area_width=placement.text_rect[2] - placement.text_rect[0],
                render_code_chip=False,
            )
            return

        if placement.avatar_rect is None or placement.bubble_rect is None:
            return
        if spec.turn.speaker == "USER":
            self._draw_avatar(
                draw,
                rect=placement.avatar_rect,
                label=tr(locale, "docs.demo.avatar.user"),
                fill=self.theme.accent,
            )
            self._draw_shadowed_rect(
                image,
                rect=placement.bubble_rect,
                radius=self.theme.bubble_radius,
                shadow_color=self.theme.bubble_shadow,
                shadow_offset_y=self.theme.instruction_shadow_offset_y,
                shadow_blur=18,
                fill=self.theme.user_bubble,
            )
            self._draw_message_bubble_shape(
                draw,
                rect=placement.bubble_rect,
                fill=self.theme.user_bubble,
                top_left_radius=12,
                other_radius=self.theme.bubble_radius,
            )
            bubble_fill = self.theme.deep
        else:
            self._draw_bot_avatar(
                image,
                draw,
                rect=placement.avatar_rect,
                locale=locale,
            )
            self._draw_message_bubble_shape(
                draw,
                rect=placement.bubble_rect,
                fill=self.theme.bot_bubble,
                top_left_radius=12,
                other_radius=self.theme.bubble_radius,
            )
            bubble_fill = self.theme.bot_text

        self._draw_multiline_text(
            draw,
            x=placement.text_rect[0],
            y=placement.text_rect[1],
            lines=spec.lines,
            font=self.body_font,
            fill=bubble_fill,
            line_height=self._line_height_for_font(
                self.body_font,
                minimum=self.theme.bubble_line_height,
            ),
        )
        if spec.detail_lines:
            detail_top = (
                placement.text_rect[1]
                + self._line_block_height(
                    spec.lines,
                    spec.line_height,
                )
                + 18
            )
            detail_rect = (
                placement.text_rect[0],
                detail_top,
                placement.text_rect[2],
                placement.bubble_rect[3] - self.theme.bubble_padding_y,
            )
            draw.rounded_rectangle(
                detail_rect,
                radius=16,
                fill="#1F2937",
            )
            self._draw_multiline_text(
                draw,
                x=detail_rect[0] + 18,
                y=detail_rect[1] + 16,
                lines=spec.detail_lines,
                font=self.meta_font,
                fill="#E5E7EB",
                line_height=self._line_height_for_font(self.meta_font, minimum=28),
            )

    def _draw_standee(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
    ) -> None:
        self._draw_standee_anchor(image, rect)
        if self.senrin_standee is None:
            self._draw_avatar(
                draw,
                rect=rect,
                label=tr("zh-CN", "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        image.alpha_composite(self.senrin_standee, (rect[0], rect[1]))

    def _draw_avatar(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        label: str,
        fill: str,
    ) -> None:
        draw.ellipse(rect, fill=fill)
        bbox = self._text_size(label, self.meta_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        self._draw_text(
            draw,
            x=rect[0] + ((rect[2] - rect[0]) - text_width) / 2,
            y=rect[1] + ((rect[3] - rect[1]) - text_height) / 2 - 2,
            text=label,
            font=self.meta_font,
            fill=self.theme.avatar_text,
        )

    def _draw_bot_avatar(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        locale: LocaleCode,
    ) -> None:
        if self.senrin_avatar is None:
            self._draw_avatar(
                draw,
                rect=rect,
                label=tr(locale, "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        draw.ellipse(rect, fill=self.theme.panel_bg, outline=self.theme.line, width=2)
        mask = Image.new("L", self.senrin_avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
        image.paste(self.senrin_avatar, (rect[0], rect[1]), mask)

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
    ) -> None:
        divider_y = layout.footer_rect[1] + 8
        self._draw_dashed_line(
            draw,
            start=(layout.footer_rect[0], divider_y),
            end=(layout.footer_rect[2], divider_y),
            fill=self.theme.footer_divider,
            dash=10,
            gap=10,
        )
        right_bbox = self._text_size(layout.footer_right_text, self.footer_font)
        right_width = int(right_bbox[2] - right_bbox[0])
        footer_y = layout.footer_rect[1] + 28
        right_x = layout.footer_rect[2] - right_width
        left_max_width = max(120, right_x - layout.footer_rect[0] - 32)
        left_text = self._fit_text(
            ImageDraw.Draw(Image.new("RGB", (1, 1), self.theme.panel_bg)),
            layout.footer_left_text,
            self.footer_font,
            max_width=left_max_width,
        )
        self._draw_text(
            draw,
            x=layout.footer_rect[0],
            y=footer_y,
            text=left_text,
            font=self.footer_font,
            fill=self.theme.note_text,
        )
        self._draw_text(
            draw,
            x=right_x,
            y=footer_y,
            text=layout.footer_right_text,
            font=self.footer_font,
            fill=self.theme.note_text,
        )

    def _draw_shadowed_rect(
        self,
        image: Image.Image,
        *,
        rect: tuple[int, int, int, int],
        radius: int,
        shadow_color: tuple[int, int, int, int],
        shadow_offset_y: int,
        shadow_blur: int,
        fill: str,
    ) -> None:
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_rect = (
            rect[0],
            rect[1] + shadow_offset_y,
            rect[2],
            rect[3] + shadow_offset_y,
        )
        shadow_draw.rounded_rectangle(shadow_rect, radius=radius, fill=shadow_color)
        shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        image.alpha_composite(shadow)
        ImageDraw.Draw(image).rounded_rectangle(rect, radius=radius, fill=fill)

    def _draw_standee_anchor(
        self,
        image: Image.Image,
        rect: tuple[int, int, int, int],
    ) -> None:
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        anchor_size = int(min(width, height) * 0.82)
        anchor_left = rect[0] + (width - anchor_size) // 2
        anchor_top = rect[1] + int(height * 0.1)
        anchor_rect = (
            anchor_left,
            anchor_top,
            anchor_left + anchor_size,
            anchor_top + anchor_size,
        )
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_rect = (
            anchor_rect[0],
            anchor_rect[1] + self.theme.instruction_shadow_offset_y,
            anchor_rect[2],
            anchor_rect[3] + self.theme.instruction_shadow_offset_y,
        )
        shadow_draw.ellipse(shadow_rect, fill=self.theme.standee_anchor_shadow)
        shadow = shadow.filter(ImageFilter.GaussianBlur(20))
        image.alpha_composite(shadow)
        ImageDraw.Draw(image).ellipse(
            anchor_rect,
            fill=self.theme.standee_anchor_fill,
            outline=self.theme.line,
            width=1,
        )

    def _draw_dot_matrix(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        width: int,
        height: int,
    ) -> None:
        dot_fill = self._rgba(self.theme.grid_color, 38)
        radius = 1
        max_height = min(height, max(height // 2 + 160, 480))
        for y in range(self.theme.hero_top // 2, max_height, self.theme.grid_spacing):
            for x in range(
                self.theme.hero_side_padding // 2,
                width - self.theme.hero_side_padding // 2,
                self.theme.grid_spacing,
            ):
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=dot_fill,
                )

    def _draw_floating_decor(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        width: int,
        height: int,
    ) -> None:
        decor_fill = self._rgba(self.theme.decor_color, 72)
        plus_origins = (
            (self.theme.hero_side_padding - 8, self.theme.hero_top + 24),
            (width - self.theme.hero_side_padding - 184, self.theme.hero_top + 120),
            (width - self.theme.hero_side_padding - 56, height - 168),
        )
        for origin_x, origin_y in plus_origins:
            self._draw_plus_cluster(
                draw, origin_x=origin_x, origin_y=origin_y, fill=decor_fill
            )
        self._draw_hollow_circle(
            draw,
            center=(
                width - self.theme.hero_side_padding - 132,
                self.theme.hero_top + 184,
            ),
            radius=28,
            outline=decor_fill,
        )
        self._draw_zigzag(
            draw,
            start=(width - self.theme.hero_side_padding - 224, height // 2 + 88),
            fill=decor_fill,
        )

    def _draw_plus_cluster(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        origin_x: int,
        origin_y: int,
        fill: tuple[int, int, int, int],
    ) -> None:
        offsets = ((0, 0), (28, 12), (12, 32))
        for offset_x, offset_y in offsets:
            cx = origin_x + offset_x
            cy = origin_y + offset_y
            draw.line((cx - 6, cy, cx + 6, cy), fill=fill, width=2)
            draw.line((cx, cy - 6, cx, cy + 6), fill=fill, width=2)

    def _draw_hollow_circle(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        center: tuple[int, int],
        radius: int,
        outline: tuple[int, int, int, int],
    ) -> None:
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            outline=outline,
            width=3,
        )

    def _draw_zigzag(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        start: tuple[int, int],
        fill: tuple[int, int, int, int],
    ) -> None:
        x, y = start
        points = [
            (x, y),
            (x + 16, y - 8),
            (x + 32, y),
            (x + 48, y - 8),
            (x + 64, y),
        ]
        draw.line(points, fill=fill, width=3)

    def _draw_dashed_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        start: tuple[int, int],
        end: tuple[int, int],
        fill: str,
        dash: int,
        gap: int,
    ) -> None:
        x1, y = start
        x2, _ = end
        cursor = x1
        while cursor < x2:
            segment_end = min(cursor + dash, x2)
            draw.line((cursor, y, segment_end, y), fill=fill, width=1)
            cursor = segment_end + gap

    def _draw_pill(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        text: str,
        fill: str,
        text_fill: str,
    ) -> None:
        draw.rounded_rectangle(rect, radius=(rect[3] - rect[1]) // 2, fill=fill)
        self._draw_text_centered(
            draw,
            rect,
            text,
            font=self.eyebrow_font,
            fill=text_fill,
        )

    def _command_palette(
        self,
        *,
        root: str | None = None,
        text: str | None = None,
        param: str | None = None,
        flag: str | None = None,
    ) -> CommandPalette:
        return CommandPalette(
            root=root or self.theme.indigo_text,
            text=text or self.theme.terminal_text,
            param=param or self.theme.terminal_param,
            flag=flag or self.theme.terminal_flag,
        )

    def _draw_command_layout(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        layout: CommandLayout,
        font: Any,
        default_fill: str,
        guide_fill: str,
    ) -> None:
        sample_bbox = self._text_size("Ag", font)
        sample_height = max(sample_bbox[3] - sample_bbox[1], 0)
        line_offset_y = max(
            (layout.line_height - sample_height) / 2 - sample_bbox[1], 0
        )
        base_y = y + line_offset_y
        if layout.has_guide:
            top = base_y + layout.line_height
            bottom = base_y + layout.total_height - max(layout.line_height // 4, 4)
            if bottom > top:
                guide_x = x + layout.indent_px - 18
                draw.line((guide_x, top, guide_x, bottom), fill=guide_fill, width=2)
        for index, line in enumerate(layout.lines):
            self._draw_inline_text_line(
                draw,
                x=x + line.indent_level * layout.indent_px,
                y=base_y + index * layout.line_height,
                line=line.segments,
                font=font,
                fill=default_fill,
                render_code_chip=False,
            )

    def _measure_note_items(
        self,
        *,
        feature_preconditions: str,
        feature_failures: str,
        feature_permission: str,
        width: int,
        start_y: int,
        x: int,
    ) -> list[_ShowcaseNoteItem]:
        items: list[_ShowcaseNoteItem] = []
        cursor_y = start_y
        source_items: list[tuple[str, str]] = []
        if feature_permission.strip() and feature_permission != "普通用户":
            source_items.append((feature_permission.strip(), self.theme.note_danger))
        source_items.extend(
            (text, self.theme.note_success)
            for text in self._split_note_lines(feature_preconditions)
        )
        source_items.extend(
            (text, self.theme.note_danger)
            for text in self._split_note_lines(feature_failures)
        )
        for text, color in source_items:
            layout = build_markdown_layout(
                text,
                max_width=width - 24,
                line_height=self._line_height_for_font(self.note_font),
                indent_px=self.COMMAND_INDENT_PX,
                measure_text=lambda value, code: self._measure_markdown_text_width(
                    value,
                    self.note_font,
                    code=code,
                ),
            )
            line_height = self._line_height_for_font(self.note_font)
            height = layout.total_height
            rect = (x, cursor_y, x + width, cursor_y + height)
            items.append(
                _ShowcaseNoteItem(
                    rect=rect,
                    layout=layout,
                    line_height=line_height,
                    dot_color=color,
                )
            )
            cursor_y = rect[3] + self.theme.note_gap
        return items

    def _split_note_lines(self, text: str) -> tuple[str, ...]:
        raw_lines = [line.strip(" -") for line in text.splitlines()]
        return tuple(line for line in raw_lines if line)

    def _split_bot_detail_text(self, text: str) -> tuple[str, str]:
        lines = [line.rstrip() for line in text.splitlines()]
        if len(lines) <= 1:
            return text, ""
        detail_start = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.startswith(("Type:", "Value:", "Traceback:", "File:"))
            ),
            -1,
        )
        if detail_start <= 0:
            return text, ""
        summary = "\n".join(lines[:detail_start]).strip()
        detail = "\n".join(lines[detail_start:]).strip()
        return summary or text, detail

    def _measure_markdown_text_width(self, text: str, font: Any, *, code: bool) -> int:
        width = self._text_width(text, font)
        if code and text:
            width += self.theme.inline_code_pad_x * 2
        return width

    def _draw_markdown_layout(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        layout: MarkdownLayout,
        font: Any,
        fill: str,
        max_width: int | None = None,
    ) -> None:
        cursor_y = y
        tip_group: list[tuple[int, MarkdownLayoutLine]] = []
        tip_width = max_width or 980

        def flush_tip_group() -> None:
            nonlocal cursor_y, tip_group
            if not tip_group:
                return
            tip_top = tip_group[0][0] - 10
            tip_bottom = tip_group[-1][0] + layout.line_height + 10
            draw.rounded_rectangle(
                (
                    x,
                    tip_top,
                    x + tip_width,
                    tip_bottom,
                ),
                radius=18,
                fill=self.theme.panel_soft_bg,
                outline=self.theme.line,
                width=1,
            )
            for line_y, tip_line in tip_group:
                line_x = x + 24 + max(tip_line.indent_level, 0) * layout.indent_px
                self._draw_markdown_inline_code_backgrounds(
                    draw,
                    x=line_x,
                    y=line_y,
                    line=tip_line.segments,
                    font=font,
                )
                self._draw_inline_text_line(
                    draw,
                    x=line_x,
                    y=line_y,
                    line=tip_line.segments,
                    font=font,
                    fill=fill,
                    render_code_chip=False,
                    render_inline_code_text=False,
                )
            tip_group = []

        for line in layout.lines:
            if line.kind == "spacer":
                flush_tip_group()
                cursor_y += line.indent_level
                continue
            if line.kind == "tip":
                tip_group.append((cursor_y, line))
                cursor_y += layout.line_height
                continue
            flush_tip_group()
            line_x = x + max(line.indent_level, 0) * layout.indent_px
            if line.bullet:
                bullet_text = f"{line.bullet} "
                self._draw_text(
                    draw,
                    x=line_x,
                    y=cursor_y,
                    text=bullet_text,
                    font=font,
                    fill=fill,
                )
                line_x += self._text_width(bullet_text, font)
            self._draw_markdown_inline_code_backgrounds(
                draw,
                x=line_x,
                y=cursor_y,
                line=line.segments,
                font=font,
            )
            self._draw_inline_text_line(
                draw,
                x=line_x,
                y=cursor_y,
                line=line.segments,
                font=font,
                fill=fill,
                render_code_chip=False,
                render_inline_code_text=False,
            )
            cursor_y += layout.line_height
        flush_tip_group()

    def _draw_markdown_inline_code_backgrounds(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        line: Sequence[InlineTextSpan],
        font: Any,
    ) -> None:
        cursor_x = x
        line_height = self._font_line_height(font)
        for span in line:
            if not span.text:
                continue
            text_width = self._text_width(span.text, font)
            if not span.code:
                cursor_x += text_width
                continue
            text_bbox = self._text_size(span.text, font)
            text_height = int(text_bbox[3] - text_bbox[1])
            pad_x = max(4, self.theme.inline_code_pad_x - 3)
            pad_y = max(2, self.theme.inline_code_pad_y - 2)
            chip_height = max(text_height + pad_y * 2, 18)
            chip_y = y + max((line_height - chip_height) / 2, 0)
            chip_width = text_width + pad_x * 2
            draw.rounded_rectangle(
                (
                    cursor_x - pad_x,
                    chip_y,
                    cursor_x - pad_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=max(6, self.theme.inline_code_radius - 2),
                fill=self.theme.inline_code_bg,
            )
            self._draw_text(
                draw,
                x=cursor_x,
                y=chip_y + (chip_height - text_height) / 2 - text_bbox[1],
                text=span.text,
                font=font,
                fill=span.fill or self.theme.inline_code_text,
            )
            cursor_x += text_width

    def _draw_message_bubble_shape(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        fill: str,
        top_left_radius: int,
        other_radius: int,
    ) -> None:
        left, top, right, bottom = rect
        draw.rounded_rectangle(rect, radius=other_radius, fill=fill)
        cap = min(other_radius + 10, (right - left) // 3, (bottom - top) // 3)
        draw.rounded_rectangle(
            (left, top, left + cap, top + cap),
            radius=top_left_radius,
            fill=fill,
        )

    def _place_turn(
        self,
        spec: _ShowcaseTurnSpec,
        *,
        top: int,
        left: int,
        right: int,
    ) -> _ShowcaseTurnPlacement:
        if spec.turn.speaker == "SYSTEM":
            text_width = self._max_inline_line_width(
                spec.lines,
                self.system_font,
                code_padding=False,
            )
            content_width = min(max(text_width + 96, 420), right - left - 120)
            text_left = left + (right - left - content_width) // 2
            text_block_height = self._line_block_height(
                spec.lines,
                spec.line_height,
            )
            label_height = 36
            label_width = 116
            bubble_height = max(text_block_height + label_height + 58, 112)
            bubble_rect = (
                text_left - 28,
                top,
                text_left + content_width + 28,
                top + bubble_height,
            )
            label_rect = (
                bubble_rect[0] + 20,
                bubble_rect[1] + 18,
                bubble_rect[0] + 20 + label_width,
                bubble_rect[1] + 18 + label_height,
            )
            content_top = label_rect[3] + 20
            content_bottom = bubble_rect[3] - 20
            available_height = max(content_bottom - content_top, text_block_height)
            text_top = content_top + max(0, (available_height - text_block_height) // 2)
            text_bottom = text_top + text_block_height
            return _ShowcaseTurnPlacement(
                spec=spec,
                rect=bubble_rect,
                avatar_rect=None,
                bubble_rect=bubble_rect,
                text_rect=(text_left, text_top, text_left + content_width, text_bottom),
                label_rect=label_rect,
            )

        avatar_size = self.theme.avatar_size
        bubble_gap = self.theme.avatar_gap
        if spec.turn.speaker == "USER":
            avatar_rect = (right - avatar_size, top, right, top + avatar_size)
            bubble_right = avatar_rect[0] - bubble_gap
            bubble_left = bubble_right - spec.width
        else:
            avatar_rect = (left, top, left + avatar_size, top + avatar_size)
            bubble_left = avatar_rect[2] + bubble_gap
            bubble_right = bubble_left + spec.width
        bubble_top = top + max(0, (avatar_size - spec.height) // 2)
        bubble_rect = (bubble_left, bubble_top, bubble_right, bubble_top + spec.height)
        text_rect = (
            bubble_left + self.theme.bubble_padding_x,
            bubble_top + self.theme.bubble_padding_y,
            bubble_right - self.theme.bubble_padding_x,
            bubble_top
            + self.theme.bubble_padding_y
            + self._line_block_height(spec.lines, spec.line_height),
        )
        rect = (
            min(avatar_rect[0], bubble_left),
            top,
            max(avatar_rect[2], bubble_right),
            max(avatar_rect[3], bubble_rect[3]),
        )
        return _ShowcaseTurnPlacement(
            spec=spec,
            rect=rect,
            avatar_rect=avatar_rect,
            bubble_rect=bubble_rect,
            text_rect=text_rect,
        )

    def _measure_turn(
        self,
        turn: DocsDemoTurn,
        content_width: int,
    ) -> _ShowcaseTurnSpec:
        if turn.speaker == "SYSTEM":
            lines = tuple(
                self._wrap_inline_text(
                    self._normalize_demo_text(turn.text),
                    max_width=min(content_width - 120, 760),
                    font=self.system_font,
                )
            )
            line_height = self._line_height_for_font(self.system_font)
            return _ShowcaseTurnSpec(
                turn=turn,
                lines=lines,
                detail_lines=(),
                width=0,
                height=self._line_block_height(lines, line_height),
                line_height=line_height,
            )

        bubble_max = min(
            760,
            content_width - self.theme.avatar_size - self.theme.avatar_gap - 80,
        )
        normalized_text = self._normalize_demo_text(turn.text)
        summary_text = normalized_text
        detail_lines: tuple[tuple[InlineTextSpan, ...], ...] = ()
        if turn.speaker == "BOT":
            summary_text, detail_payload = self._split_bot_detail_text(normalized_text)
            if detail_payload:
                detail_lines = tuple(
                    self._wrap_inline_text(
                        detail_payload,
                        max_width=bubble_max - self.theme.bubble_padding_x * 2 - 36,
                        font=self.meta_font,
                    )
                )
        lines = tuple(
            self._wrap_inline_text(
                summary_text,
                max_width=bubble_max - self.theme.bubble_padding_x * 2,
                font=self.body_font,
            )
        )
        line_height = self._line_height_for_font(
            self.body_font,
            minimum=self.theme.bubble_line_height,
        )
        text_height = self._line_block_height(lines, line_height)
        detail_height = 0
        if detail_lines:
            detail_height = (
                self._line_block_height(
                    detail_lines,
                    self._line_height_for_font(self.meta_font, minimum=28),
                )
                + 32
                + 18
            )
        bubble_height = text_height + self.theme.bubble_padding_y * 2 + detail_height
        bubble_width = (
            self._max_inline_line_width(lines, self.body_font)
            + self.theme.bubble_padding_x * 2
        )
        return _ShowcaseTurnSpec(
            turn=turn,
            lines=lines,
            detail_lines=detail_lines,
            width=max(280, min(bubble_width, bubble_max)),
            height=max(bubble_height, self.theme.avatar_size),
            line_height=line_height,
        )

    def _turn_rects(
        self,
        placement: _ShowcaseTurnPlacement,
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        if placement.avatar_rect is None:
            if placement.bubble_rect is not None:
                return [("system bubble", placement.bubble_rect)]
            return [("system text", placement.text_rect)]
        rects: list[tuple[str, tuple[int, int, int, int]]] = []
        if placement.avatar_rect is not None:
            rects.append(("avatar", placement.avatar_rect))
        if placement.bubble_rect is not None:
            rects.append(("bubble", placement.bubble_rect))
        return rects

    def _load_asset(
        self,
        path: Path,
        size: int,
        *,
        alpha: int = 255,
    ) -> Image.Image | None:
        if not path.exists():
            return None
        try:
            image = Image.open(path).convert("RGBA")
        except OSError:
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        if alpha < 255:
            image = image.copy()
            alpha_channel = image.getchannel("A")
            alpha_channel = alpha_channel.point(
                [value * alpha // 255 for value in range(256)]
            )
            image.putalpha(alpha_channel)
        return image

    def _draw_text_centered(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
        text: str,
        *,
        font: Any,
        fill: str,
        align: Literal["center", "left", "right"] = "center",
        padding_x: int = 0,
    ) -> None:
        bbox = self._text_size(text, font)
        text_width = int(bbox[2] - bbox[0])
        text_height = int(bbox[3] - bbox[1])
        if align == "left":
            x = rect[0] + padding_x
        elif align == "right":
            x = rect[2] - text_width
        else:
            x = rect[0] + (rect[2] - rect[0] - text_width) / 2
        y = rect[1] + (rect[3] - rect[1] - text_height) / 2 - bbox[1]
        self._draw_text(draw, x=x, y=y, text=text, font=font, fill=fill)

    def _ensure_inside(
        self,
        outer: tuple[int, int, int, int],
        inner: tuple[int, int, int, int],
        label: str,
        errors: list[str],
    ) -> None:
        if (
            inner[0] < outer[0]
            or inner[1] < outer[1]
            or inner[2] > outer[2]
            or inner[3] > outer[3]
        ):
            errors.append(f"{label} exceeds its container bounds")

    def _ensure_no_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        a_label: str,
        b_label: str,
        errors: list[str],
        *,
        padding: int = 0,
    ) -> None:
        if self._boxes_overlap(a, b, padding=padding):
            errors.append(f"{a_label} overlaps {b_label}")

    def _boxes_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        *,
        padding: int = 0,
    ) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return not (
            ax2 + padding <= bx1
            or bx2 + padding <= ax1
            or ay2 + padding <= by1
            or by2 + padding <= ay1
        )

    def _fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        *,
        max_width: int,
    ) -> str:
        _ = draw
        if self._text_width(text, font) <= max_width:
            return text
        ellipsis = "..."
        current = text
        while current:
            current = current[:-1]
            candidate = current.rstrip() + ellipsis
            if self._text_width(candidate, font) <= max_width:
                return candidate
        return ellipsis

    def _normalize_demo_text(self, text: str) -> str:
        return text

    def _wrap_inline_text(
        self,
        text: str,
        *,
        max_width: int,
        font: Any,
    ) -> list[tuple[InlineTextSpan, ...]]:
        if not text:
            return [()]
        lines: list[tuple[InlineTextSpan, ...]] = []
        for paragraph in text.splitlines():
            lines.extend(
                self._wrap_inline_spans(
                    split_inline_text_spans(paragraph),
                    max_width=max_width,
                    font=font,
                )
            )
        return lines or [()]

    def _wrap_inline_spans(
        self,
        spans: Sequence[InlineTextSpan],
        *,
        max_width: int,
        font: Any,
        code_padding: bool = True,
    ) -> list[tuple[InlineTextSpan, ...]]:
        lines: list[tuple[InlineTextSpan, ...]] = []
        current: list[InlineTextSpan] = []
        for span in spans:
            for char in span.text:
                candidate = self._append_inline_char(
                    current,
                    char,
                    code=span.code,
                    fill=span.fill,
                )
                if (
                    not current
                    or self._inline_line_width(
                        candidate,
                        font,
                        code_padding=code_padding,
                    )
                    <= max_width
                ):
                    current = candidate
                    continue
                lines.append(tuple(current))
                current = [InlineTextSpan(char, code=span.code, fill=span.fill)]
        if current or not lines:
            lines.append(tuple(current))
        return lines

    def _line_block_height(
        self,
        lines: Iterable[tuple[InlineTextSpan, ...]],
        line_height: int,
    ) -> int:
        count = sum(1 for _ in lines)
        return 0 if count == 0 else count * line_height

    def _max_inline_line_width(
        self,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
        *,
        code_padding: bool = True,
    ) -> int:
        return int(
            max(
                (
                    self._inline_line_width(
                        line,
                        font,
                        code_padding=code_padding,
                    )
                    for line in lines
                ),
                default=0,
            )
        )

    def _draw_multiline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
        fill: str,
        line_height: int,
        align: Literal["left", "center"] = "left",
        area_width: int | None = None,
        render_code_chip: bool = True,
    ) -> None:
        for index, line in enumerate(lines):
            line_x = x
            if align == "center" and area_width is not None:
                line_width = self._inline_line_width(
                    line,
                    font,
                    code_padding=render_code_chip,
                )
                line_x = x + max(0, (area_width - line_width) // 2)
            self._draw_inline_text_line(
                draw,
                x=line_x,
                y=y + index * line_height,
                line=line,
                font=font,
                fill=fill,
                render_code_chip=render_code_chip,
            )

    def _draw_inline_text_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        line: Sequence[InlineTextSpan],
        font: Any,
        fill: str,
        render_code_chip: bool = True,
        render_inline_code_text: bool = True,
    ) -> None:
        cursor_x = x
        line_height = self._font_line_height(font)
        for span in line:
            if not span.text:
                continue
            span_fill = span.fill or fill
            if span.code and not render_code_chip and not render_inline_code_text:
                cursor_x += self._text_width(span.text, font)
                continue
            if not span.code or not render_code_chip:
                self._draw_text(
                    draw,
                    x=cursor_x,
                    y=y,
                    text=span.text,
                    font=font,
                    fill=span_fill,
                )
                cursor_x += self._text_width(span.text, font)
                continue
            text_bbox = self._text_size(span.text, font)
            text_width = int(text_bbox[2] - text_bbox[0])
            text_height = int(text_bbox[3] - text_bbox[1])
            chip_height = max(text_height + self.theme.inline_code_pad_y * 2, 22)
            chip_y = y + max((line_height - chip_height) / 2, 0)
            chip_width = text_width + self.theme.inline_code_pad_x * 2
            draw.rounded_rectangle(
                (
                    cursor_x,
                    chip_y,
                    cursor_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=self.theme.inline_code_radius,
                fill=self.theme.inline_code_bg,
            )
            text_y = chip_y + (chip_height - text_height) / 2 - text_bbox[1]
            self._draw_text(
                draw,
                x=cursor_x + self.theme.inline_code_pad_x,
                y=text_y,
                text=span.text,
                font=font,
                fill=span.fill or self.theme.inline_code_text,
            )
            cursor_x += chip_width

    def _font_line_height(self, font: Any) -> int:
        bbox = self._text_size("Ag", font)
        return int(bbox[3] - bbox[1] + 10)

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        text: str,
        font: Any,
        fill: str,
    ) -> None:
        if not text:
            return
        if not self._contains_emoji(text):
            draw.text((x, y), text, font=font, fill=fill)
            return
        text_box = self._text_size(text, font)
        text_width = max(text_box[2] - text_box[0] + 8, 1)
        text_height = max(text_box[3] - text_box[1] + 8, 1)
        text_layer = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
        BuildImage(text_layer).draw_text(
            (0, 0),
            text,
            font_size=self._font_size(font),
            fill=fill,
            font_families=self.FONT_FAMILIES,
            stroke_ratio=0,
        )
        draw._image.paste(text_layer, (int(x), int(y)), text_layer)

    def _text_size(self, text: str, font: Any) -> tuple[int, int, int, int]:
        if not text:
            return (0, 0, 0, self._font_line_height(font))
        if not self._contains_emoji(text):
            draw = ImageDraw.Draw(Image.new("RGB", (10, 10), self.theme.panel_bg))
            bbox = draw.textbbox((0, 0), text, font=font)
            return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        text_image = Text2Image.from_text(
            text,
            self._font_size(font),
            fill=self.theme.deep,
            stroke_width=0,
            font_families=self.FONT_FAMILIES,
        )
        return (0, 0, ceil(text_image.longest_line), ceil(text_image.height))

    def _text_width(self, text: str, font: Any) -> int:
        return self._text_size(text, font)[2]

    def _inline_line_width(
        self,
        line: Sequence[InlineTextSpan],
        font: Any,
        *,
        code_padding: bool = True,
    ) -> int:
        width = 0
        for span in line:
            if not span.text:
                continue
            width += self._text_width(span.text, font)
            if span.code and code_padding:
                width += self.theme.inline_code_pad_x * 2
        return width

    def _append_inline_char(
        self,
        spans: Sequence[InlineTextSpan],
        char: str,
        *,
        code: bool,
        fill: str | None = None,
    ) -> list[InlineTextSpan]:
        updated = list(spans)
        if updated and updated[-1].code is code and updated[-1].fill == fill:
            updated[-1] = InlineTextSpan(
                updated[-1].text + char,
                code=code,
                fill=fill,
            )
        else:
            updated.append(InlineTextSpan(char, code=code, fill=fill))
        return updated

    def _fit_inline_spans(
        self,
        spans: Sequence[InlineTextSpan],
        font: Any,
        max_width: int,
    ) -> tuple[InlineTextSpan, ...]:
        if self._inline_line_width(spans, font) <= max_width:
            return tuple(spans)
        ellipsis = InlineTextSpan("...", code=False)
        current: list[InlineTextSpan] = list(spans)
        while current:
            last = current[-1]
            if len(last.text) > 1:
                current[-1] = InlineTextSpan(
                    last.text[:-1],
                    code=last.code,
                    fill=last.fill,
                )
                if not current[-1].text:
                    current.pop()
            else:
                current.pop()
            candidate = [*current, ellipsis]
            if self._inline_line_width(candidate, font) <= max_width:
                return tuple(candidate)
        return (ellipsis,)

    def _trigger_spans(self, text: str) -> tuple[InlineTextSpan, ...]:
        spans: list[InlineTextSpan] = []
        for span in split_inline_text_spans(text):
            if not span.code:
                spans.append(
                    InlineTextSpan(
                        span.text,
                        code=False,
                        fill=self.theme.terminal_text,
                    )
                )
                continue
            for piece in re.split(r"(\[[^\]]+\]|<[^>]+>)", span.text):
                if not piece:
                    continue
                fill = (
                    self.theme.terminal_param
                    if re.fullmatch(r"(\[[^\]]+\]|<[^>]+>)", piece)
                    else self.theme.terminal_text
                )
                spans.append(InlineTextSpan(piece, code=True, fill=fill))
        return tuple(spans)

    def _pill_width(self, text: str, font: Any) -> int:
        return max(88, self._text_width(text, font) + 32)

    def _font_size(self, font: Any) -> int:
        return int(getattr(font, "size", 16))

    def _line_height_for_font(self, font: Any, *, minimum: int = 0) -> int:
        natural = ceil(self._font_size(font) * 1.4)
        return max(minimum, ceil(natural / 8) * 8)

    def _contains_emoji(self, text: str) -> bool:
        return any(
            "\U0001f000" <= char <= "\U0001faff" or char == "\ufe0f" for char in text
        )

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        color = color.lstrip("#")
        return (
            int(color[0:2], 16),
            int(color[2:4], 16),
            int(color[4:6], 16),
            alpha,
        )
