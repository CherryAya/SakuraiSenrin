"""Collection demo renderer shared by docs build scripts."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from pathlib import Path
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.demo_theme import DEFAULT_DEMO_THEME, SENRIN_V3_THEME, get_demo_theme
from .command_layout import (
    CommandLayout,
    CommandPalette,
    InlineTextSpan,
    build_command_layout,
    split_inline_text_spans,
)
from .models import PluginDocBundle


def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False, "breaks": False})
    parser.enable("table")
    return parser


def _parse_inline_tokens(text: str) -> tuple[Token, ...]:
    if not text:
        return ()
    inline = _markdown_parser().parseInline(text)[0]
    return tuple(inline.children or ())


@dataclass(slots=True, frozen=True)
class DemoCollectionTile:
    index: int
    title: str
    slug: str
    summary: str
    trigger: str
    demo_help: str


@dataclass(slots=True, frozen=True)
class DemoCollectionJob:
    bundle: PluginDocBundle
    output: Path
    tiles: tuple[DemoCollectionTile, ...]
    columns: int


@dataclass(slots=True, frozen=True)
class PreparedCollectionTile:
    index: int
    title: str
    slug: str
    summary: str
    trigger: str
    demo_help: str
    title_lines: tuple[tuple[tuple[str, bool], ...], ...]
    summary_lines: tuple[tuple[tuple[str, bool], ...], ...]
    trigger_layout: CommandLayout
    demo_layout: CommandLayout
    height: int


@dataclass(slots=True, frozen=True)
class HeaderLayout:
    left_x: int
    title_y: int
    summary_y: int
    summary_lines: tuple[tuple[tuple[str, bool], ...], ...]
    height: int


class DemoCollectionRenderer:
    CANVAS_WIDTH = DEFAULT_DEMO_THEME.canvas_width
    OUTER_MARGIN = 88
    HEADER_TOP = 72
    HEADER_BOTTOM_GAP = 64
    GRID_GAP_X = 32
    GRID_GAP_Y = 32
    CARD_RADIUS = 32
    CARD_PADDING_X = 40
    CARD_PADDING_Y = 40
    CARD_COMMAND_PADDING_X = 24
    CARD_COMMAND_PADDING_Y = 16
    CARD_INNER_GAP = 24
    CARD_BOTTOM_MARGIN = 64
    COMMAND_INDENT_PX = 48

    def __init__(self, *, columns: int) -> None:
        self.theme_name = SENRIN_V3_THEME.name
        self.theme = DEFAULT_DEMO_THEME
        self.columns = max(1, min(columns, 2))
        try:
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 64)
            self.summary_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)
            self.tile_title_font = ImageFont.truetype(MAPLE_FONT_PATH, 36)
            self.tile_summary_font = ImageFont.truetype(MAPLE_FONT_PATH, 28)
            self.tile_command_font = ImageFont.truetype(MAPLE_FONT_PATH, 26)
            self.tile_index_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)
        except OSError:
            self.title_font = ImageFont.load_default()
            self.summary_font = ImageFont.load_default()
            self.tile_title_font = ImageFont.load_default()
            self.tile_summary_font = ImageFont.load_default()
            self.tile_command_font = ImageFont.load_default()
            self.tile_index_font = ImageFont.load_default()

    def render(
        self,
        *,
        title: str,
        summary: str,
        impression_color: str,
        tiles: Sequence[DemoCollectionTile],
    ) -> bytes:
        self.theme = get_demo_theme(
            theme_name=self.theme_name,
            impression_color=impression_color,
        )
        columns = self._effective_columns(len(tiles))
        card_width = self._card_width(columns)
        prepared = tuple(self._prepare_tile(tile, card_width) for tile in tiles)
        header = self._measure_header_layout(title=title, summary=summary)
        placements, content_height = self._grid_layout(
            prepared,
            card_width=card_width,
            content_top=self.OUTER_MARGIN + header.height + self.HEADER_BOTTOM_GAP,
            columns=columns,
        )
        height = (
            self.OUTER_MARGIN
            + header.height
            + self.HEADER_BOTTOM_GAP
            + content_height
            + self.CARD_BOTTOM_MARGIN
        )
        image = Image.new("RGBA", (self.CANVAS_WIDTH, height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_header(draw, title=title, tile_count=len(prepared), layout=header)
        for tile, x, y in placements:
            self._draw_tile(image, draw, tile=tile, x=x, y=y, width=card_width)

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _paint_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, width, height), fill=self.theme.page_bg)
        self._draw_dot_matrix(draw, width=width, height=height)
        self._draw_floating_decor(draw, width=width, height=height)

    def _measure_header_layout(self, *, title: str, summary: str) -> HeaderLayout:
        left_x = self.OUTER_MARGIN
        title_y = self.HEADER_TOP
        max_width = self.CANVAS_WIDTH - self.OUTER_MARGIN * 2
        summary_lines = tuple(
            self._wrap_inline_text(
                summary.strip() or "浏览该模块下的所有说明卡片。",
                self.summary_font,
                max_width=max_width,
                max_lines=3,
            )
        )
        title_height = self._line_height_for_font(self.title_font)
        summary_y = title_y + title_height + 24
        summary_height = len(summary_lines) * self._line_height_for_font(
            self.summary_font
        )
        height = max(200, (summary_y - self.OUTER_MARGIN) + summary_height)
        _ = title
        return HeaderLayout(
            left_x=left_x,
            title_y=title_y,
            summary_y=summary_y,
            summary_lines=summary_lines,
            height=height,
        )

    def _prepare_tile(
        self,
        tile: DemoCollectionTile,
        card_width: int,
    ) -> PreparedCollectionTile:
        content_width = card_width - self.CARD_PADDING_X * 2
        title_lines = tuple(
            self._wrap_inline_text(
                tile.title.strip() or tile.slug,
                self.tile_title_font,
                max_width=content_width - 92,
                max_lines=2,
            )
        )
        summary_lines = tuple(
            self._wrap_inline_text(
                tile.summary.strip() or "查看该子功能的用途、常见用法和关键边界。",
                self.tile_summary_font,
                max_width=content_width,
                max_lines=3,
            )
        )
        trigger_layout = build_command_layout(
            tile.trigger.strip() or f"#help {tile.slug}",
            max_width=content_width - self.CARD_COMMAND_PADDING_X * 2,
            line_height=self._line_height_for_font(self.tile_command_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.tile_command_font),
            palette=self._command_palette(),
            parse_inline_tokens=_parse_inline_tokens,
        )
        demo_layout = build_command_layout(
            tile.demo_help,
            max_width=content_width - self.CARD_COMMAND_PADDING_X * 2,
            line_height=self._line_height_for_font(self.tile_command_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.tile_command_font),
            palette=self._command_palette(),
            parse_inline_tokens=_parse_inline_tokens,
        )
        title_height = len(title_lines) * self._line_height_for_font(
            self.tile_title_font
        )
        summary_height = len(summary_lines) * self._line_height_for_font(
            self.tile_summary_font
        )
        trigger_box_height = (
            trigger_layout.total_height + self.CARD_COMMAND_PADDING_Y * 2
        )
        demo_box_height = demo_layout.total_height + self.CARD_COMMAND_PADDING_Y * 2
        height = (
            self.CARD_PADDING_Y * 2
            + title_height
            + summary_height
            + trigger_box_height
            + demo_box_height
            + self.CARD_INNER_GAP * 4
            + 40
        )
        return PreparedCollectionTile(
            index=tile.index,
            title=tile.title,
            slug=tile.slug,
            summary=tile.summary,
            trigger=tile.trigger,
            demo_help=tile.demo_help,
            title_lines=title_lines,
            summary_lines=summary_lines,
            trigger_layout=trigger_layout,
            demo_layout=demo_layout,
            height=height,
        )

    def _grid_layout(
        self,
        tiles: Sequence[PreparedCollectionTile],
        *,
        card_width: int,
        content_top: int,
        columns: int,
    ) -> tuple[list[tuple[PreparedCollectionTile, int, int]], int]:
        placements: list[tuple[PreparedCollectionTile, int, int]] = []
        content_width = columns * card_width + (columns - 1) * self.GRID_GAP_X
        start_x = (self.CANVAS_WIDTH - content_width) // 2
        y = content_top
        for offset in range(0, len(tiles), columns):
            row = list(tiles[offset : offset + columns])
            row_height = max((tile.height for tile in row), default=0)
            row_width = len(row) * card_width + max(0, len(row) - 1) * self.GRID_GAP_X
            row_start_x = start_x + max(0, (content_width - row_width) // 2)
            for index, tile in enumerate(row):
                x = row_start_x + index * (card_width + self.GRID_GAP_X)
                placements.append((tile, x, y))
            y += row_height + self.GRID_GAP_Y
        content_height = max(0, y - content_top - self.GRID_GAP_Y)
        return placements, content_height

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        title: str,
        tile_count: int,
        layout: HeaderLayout,
    ) -> None:
        draw.text(
            (layout.left_x, layout.title_y),
            title,
            fill=self.theme.deep,
            font=self.title_font,
        )
        for index, line in enumerate(layout.summary_lines):
            self._draw_inline_line(
                draw,
                x=layout.left_x,
                y=layout.summary_y
                + index * self._line_height_for_font(self.summary_font),
                line=line,
                font=self.summary_font,
                fill=self.theme.hint,
            )
        count_text = f"{tile_count:02d} 个功能卡片"
        count_width = self._text_width(count_text, self.tile_command_font) + 40
        chip_height = 48
        chip_right = self.CANVAS_WIDTH - self.OUTER_MARGIN
        chip_left = chip_right - count_width
        chip_top = layout.title_y + 8
        draw.rounded_rectangle(
            (chip_left, chip_top, chip_right, chip_top + chip_height),
            radius=chip_height // 2,
            fill=self.theme.panel_soft_bg,
        )
        draw.text(
            (chip_left + 20, chip_top + 8),
            count_text,
            fill=self.theme.accent,
            font=self.tile_command_font,
        )

    def _draw_tile(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        tile: PreparedCollectionTile,
        x: int,
        y: int,
        width: int,
    ) -> None:
        rect = (x, y, x + width, y + tile.height)
        self._draw_shadowed_rect(image, rect=rect, radius=self.CARD_RADIUS)
        title_x = x + self.CARD_PADDING_X
        title_y = y + self.CARD_PADDING_Y
        draw.text(
            (title_x, title_y + 4),
            f"{tile.index:02d}",
            fill=self.theme.panel_soft_bg,
            font=self.tile_index_font,
        )
        title_text_x = title_x + 88
        for index, line in enumerate(tile.title_lines):
            self._draw_inline_line(
                draw,
                x=title_text_x,
                y=title_y + index * self._line_height_for_font(self.tile_title_font),
                line=line,
                font=self.tile_title_font,
                fill=self.theme.deep,
            )
        title_height = len(tile.title_lines) * self._line_height_for_font(
            self.tile_title_font
        )
        summary_y = title_y + title_height + self.CARD_INNER_GAP
        for index, line in enumerate(tile.summary_lines):
            self._draw_inline_line(
                draw,
                x=title_x,
                y=summary_y
                + index * self._line_height_for_font(self.tile_summary_font),
                line=line,
                font=self.tile_summary_font,
                fill=self.theme.hint,
            )
        summary_height = len(tile.summary_lines) * self._line_height_for_font(
            self.tile_summary_font
        )
        trigger_y = summary_y + summary_height + self.CARD_INNER_GAP
        trigger_height = (
            tile.trigger_layout.total_height + self.CARD_COMMAND_PADDING_Y * 2
        )
        trigger_rect = (
            title_x,
            trigger_y,
            x + width - self.CARD_PADDING_X,
            trigger_y + trigger_height,
        )
        draw.rounded_rectangle(
            trigger_rect,
            radius=20,
            fill=self.theme.panel_soft_bg,
        )
        self._draw_command_layout(
            draw,
            x=trigger_rect[0] + self.CARD_COMMAND_PADDING_X,
            y=trigger_rect[1] + self.CARD_COMMAND_PADDING_Y,
            layout=tile.trigger_layout,
            font=self.tile_command_font,
            default_fill=self.theme.deep,
            guide_fill=self.theme.line,
        )
        demo_y = trigger_rect[3] + self.CARD_INNER_GAP
        demo_label_y = demo_y
        draw.text(
            (title_x, demo_label_y),
            "查看 demo",
            fill=self.theme.hint,
            font=self.tile_summary_font,
        )
        demo_label_height = self._line_height_for_font(self.tile_summary_font)
        demo_height = tile.demo_layout.total_height + self.CARD_COMMAND_PADDING_Y * 2
        demo_rect = (
            title_x,
            demo_label_y + demo_label_height + 8,
            x + width - self.CARD_PADDING_X,
            demo_label_y + demo_label_height + 8 + demo_height,
        )
        draw.rounded_rectangle(
            demo_rect,
            radius=20,
            fill=self.theme.panel_bg,
            outline=self._rgba(self.theme.line, 110),
            width=2,
        )
        self._draw_command_layout(
            draw,
            x=demo_rect[0] + self.CARD_COMMAND_PADDING_X,
            y=demo_rect[1] + self.CARD_COMMAND_PADDING_Y,
            layout=tile.demo_layout,
            font=self.tile_command_font,
            default_fill=self.theme.accent,
            guide_fill=self.theme.line,
        )

    def _draw_shadowed_rect(
        self,
        image: Image.Image,
        *,
        rect: tuple[int, int, int, int],
        radius: int,
    ) -> None:
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_rect = (
            rect[0],
            rect[1] + self.theme.instruction_shadow_offset_y,
            rect[2],
            rect[3] + self.theme.instruction_shadow_offset_y,
        )
        shadow_draw.rounded_rectangle(
            shadow_rect,
            radius=radius,
            fill=self.theme.card_shadow,
        )
        shadow = shadow.filter(
            ImageFilter.GaussianBlur(self.theme.instruction_shadow_blur)
        )
        image.alpha_composite(shadow)
        ImageDraw.Draw(image).rounded_rectangle(
            rect,
            radius=radius,
            fill=self.theme.panel_bg,
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
        for y in range(self.HEADER_TOP // 2, max_height, self.theme.grid_spacing):
            for x in range(
                self.OUTER_MARGIN // 2,
                width - self.OUTER_MARGIN // 2,
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
            (self.OUTER_MARGIN - 8, self.HEADER_TOP + 16),
            (width - self.OUTER_MARGIN - 176, self.HEADER_TOP + 104),
            (width - self.OUTER_MARGIN - 72, height - 160),
        )
        for origin_x, origin_y in plus_origins:
            self._draw_plus_cluster(
                draw,
                origin_x=origin_x,
                origin_y=origin_y,
                fill=decor_fill,
            )
        self._draw_hollow_circle(
            draw,
            center=(width - self.OUTER_MARGIN - 128, self.HEADER_TOP + 172),
            radius=28,
            outline=decor_fill,
        )
        self._draw_zigzag(
            draw,
            start=(width - self.OUTER_MARGIN - 216, height // 2 + 72),
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

    def _card_width(self, columns: int) -> int:
        total_gap = max(0, columns - 1) * self.GRID_GAP_X
        return (self.CANVAS_WIDTH - self.OUTER_MARGIN * 2 - total_gap) // max(
            columns, 1
        )

    def _effective_columns(self, tile_count: int) -> int:
        return max(1, min(self.columns, tile_count or 1))

    def _text_width(
        self,
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
            (0, 0),
            text,
            font=font,
        )
        return int(bbox[2] - bbox[0])

    def _line_height_for_font(
        self,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> int:
        size = int(getattr(font, "size", 16))
        return ((max(ceil(size * 1.4), size + 8) + 7) // 8) * 8

    def _wrap_inline_text(
        self,
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
        *,
        max_lines: int | None,
    ) -> list[tuple]:
        lines: list[tuple] = []
        current: list = []
        for span in split_inline_text_spans(
            text,
            parse_inline_tokens=_parse_inline_tokens,
        ):
            if span.code:
                current, flushed = self._append_code_span_wrapped(
                    current,
                    span.text,
                    font,
                    max_width,
                )
                lines.extend(flushed)
                continue
            current, flushed = self._append_plain_span_wrapped(
                current,
                span.text,
                font,
                max_width,
            )
            lines.extend(flushed)
        if current:
            lines.append(tuple(current))
        lines = self._normalize_wrapped_lines(lines)
        if max_lines is None or len(lines) <= max_lines:
            return lines
        clipped = lines[:max_lines]
        suffix = "..."
        while (
            clipped[-1]
            and self._inline_line_width([*list(clipped[-1]), (suffix, False)], font)
            > max_width
        ):
            clipped[-1] = clipped[-1][:-1]
        clipped[-1] = (*clipped[-1], (suffix, False))
        return clipped

    def _normalize_wrapped_lines(self, lines: list[tuple]) -> list[tuple]:
        separators = (" => ", " --", "|", "/", "_")
        normalized = [list(line) for line in lines]
        for index in range(1, len(normalized)):
            line = normalized[index]
            if not line:
                continue
            text, code = line[0]
            prefix = next((item for item in separators if text.startswith(item)), "")
            if not prefix:
                continue
            normalized[index - 1] = self._append_inline_char(
                normalized[index - 1],
                prefix,
                code=code,
            )
            remaining = text[len(prefix) :]
            if remaining:
                line[0] = (remaining, code)
            else:
                line.pop(0)
        return [tuple(line) for line in normalized if line]

    def _append_plain_span_wrapped(
        self,
        current: list[tuple[str, bool]],
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
    ) -> tuple[list[tuple[str, bool]], list[tuple]]:
        flushed: list[tuple] = []
        for segment in self._split_wrappable_segments(text):
            candidate = [*current, (segment, False)]
            if self._inline_line_width(candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                flushed.append(tuple(current))
            current = []
            if self._inline_line_width([(segment, False)], font) <= max_width:
                current = [(segment, False)]
                continue
            for char in segment:
                candidate = self._append_inline_char(current, char, code=False)
                if not current or self._inline_line_width(candidate, font) <= max_width:
                    current = candidate
                    continue
                flushed.append(tuple(current))
                current = [(char, False)]
        return current, flushed

    def _append_code_span_wrapped(
        self,
        current: list[tuple[str, bool]],
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
    ) -> tuple[list[tuple[str, bool]], list[tuple]]:
        flushed: list[tuple] = []
        candidate = [*current, (text, True)]
        if self._inline_line_width(candidate, font) <= max_width:
            return candidate, flushed
        if self._inline_line_width([(text, True)], font) <= max_width:
            if current:
                flushed.append(tuple(current))
            return [(text, True)], flushed
        for segment in self._split_wrappable_segments(text):
            candidate = [*current, (segment, True)]
            if self._inline_line_width(candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                flushed.append(tuple(current))
            current = []
            if self._inline_line_width([(segment, True)], font) <= max_width:
                current = [(segment, True)]
                continue
            for piece in self._split_oversized_code_segment(segment, font, max_width):
                if (
                    current
                    and self._inline_line_width([*current, (piece, True)], font)
                    > max_width
                ):
                    flushed.append(tuple(current))
                    current = []
                current.append((piece, True))
        return current, flushed

    def _split_wrappable_segments(self, text: str) -> tuple[str, ...]:
        separators = (" => ", " --", "|", "/", "_", " ")
        segments: list[str] = []
        buffer = text
        while buffer:
            matched = False
            for separator in separators:
                if separator in buffer:
                    head, tail = buffer.split(separator, 1)
                    if head:
                        segments.append(head + separator)
                    elif segments:
                        segments[-1] += separator
                    else:
                        segments.append(separator)
                    buffer = tail
                    matched = True
                    break
            if not matched:
                segments.append(buffer)
                break
        return tuple(segment for segment in segments if segment)

    def _split_oversized_code_segment(
        self,
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
    ) -> tuple[str, ...]:
        chunks: list[str] = []
        current = ""
        for segment in self._split_code_units(text):
            candidate = current + segment
            if (
                current
                and self._inline_line_width([(candidate, True)], font) > max_width
            ):
                chunks.append(current)
                current = ""
            if self._inline_line_width([(segment, True)], font) <= max_width:
                current += segment
                continue
            for char in segment:
                candidate = current + char
                if (
                    current
                    and self._inline_line_width([(candidate, True)], font) > max_width
                ):
                    chunks.append(current)
                    current = char
                    continue
                current = candidate
        if current:
            chunks.append(current)
        return tuple(chunks)

    def _split_code_units(self, text: str) -> tuple[str, ...]:
        parts = re.split(r"( => | --|\||/|_| )", text)
        segments: list[str] = []
        for part in parts:
            if not part:
                continue
            if part in {" => ", " --", "|", "/", "_", " "} and segments:
                segments[-1] += part
                continue
            segments.append(part)
        return tuple(segments)

    def _append_inline_char(
        self,
        current: list[tuple[str, bool]],
        char: str,
        *,
        code: bool,
    ) -> list[tuple[str, bool]]:
        if current and current[-1][1] == code:
            current[-1] = (current[-1][0] + char, code)
            return current
        return [*current, (char, code)]

    def _inline_line_width(
        self,
        line: Sequence[tuple[str, bool]],
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> int:
        width = 0
        for text, code in line:
            width += self._text_width(text, font)
            if code:
                width += self.theme.inline_code_pad_x * 2
        return width

    def _command_palette(self) -> CommandPalette:
        return CommandPalette(
            root=self.theme.indigo_text,
            text=self.theme.deep,
            param=self.theme.terminal_param,
            flag=self.theme.terminal_flag,
        )

    def _draw_command_layout(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        layout: CommandLayout,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        default_fill: str,
        guide_fill: str,
    ) -> None:
        if layout.has_guide:
            top = y + layout.line_height
            bottom = y + layout.total_height - max(layout.line_height // 4, 4)
            if bottom > top:
                guide_x = x + layout.indent_px - 18
                draw.line((guide_x, top, guide_x, bottom), fill=guide_fill, width=2)
        for index, line in enumerate(layout.lines):
            self._draw_command_line(
                draw,
                x=x + line.indent_level * layout.indent_px,
                y=y + index * layout.line_height,
                line=line.segments,
                font=font,
                fill=default_fill,
            )

    def _draw_command_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        line: Sequence[InlineTextSpan],
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        fill: str,
    ) -> None:
        cursor_x = x
        for span in line:
            if not span.text:
                continue
            span_fill = span.fill or fill
            draw.text((cursor_x, y), span.text, fill=span_fill, font=font)
            cursor_x += self._text_width(span.text, font)

    def _draw_inline_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        line: tuple,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        fill: str,
        code_background: str | None = None,
        code_fill: str | None = None,
    ) -> None:
        cursor_x = x
        chip_background = code_background or self.theme.inline_code_bg
        chip_fill = code_fill or self.theme.inline_code_text
        chip_height = self._line_height_for_font(font)
        for text, code in line:
            if not text:
                continue
            if not code:
                draw.text((cursor_x, y), text, fill=fill, font=font)
                cursor_x += self._text_width(text, font)
                continue
            text_width = self._text_width(text, font)
            chip_width = text_width + self.theme.inline_code_pad_x * 2
            draw.rounded_rectangle(
                (cursor_x, y - 2, cursor_x + chip_width, y - 2 + chip_height),
                radius=self.theme.inline_code_radius,
                fill=chip_background,
            )
            draw.text(
                (cursor_x + self.theme.inline_code_pad_x, y),
                text,
                fill=chip_fill,
                font=font,
            )
            cursor_x += chip_width

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        value = color.lstrip("#")
        return (
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
            alpha,
        )


def render_collection_png(job: DemoCollectionJob) -> bytes:
    renderer = DemoCollectionRenderer(columns=job.columns)
    return renderer.render(
        title=job.bundle.title,
        summary=job.bundle.summary,
        impression_color=job.bundle.impression_color,
        tiles=job.tiles,
    )


__all__ = [
    "DemoCollectionJob",
    "DemoCollectionRenderer",
    "DemoCollectionTile",
    "HeaderLayout",
    "PreparedCollectionTile",
    "render_collection_png",
]
