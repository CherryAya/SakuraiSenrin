"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.consts import Permission
from src.lib.consts import MAPLE_FONT_PATH, TriggerType
from src.lib.demo_theme import BASE_THEME, PALETTE_ACCENTS
from src.lib.plugin_docs import (
    DemoImageRenderer,
    DocNode,
    PluginDocBundle,
    audit_demo_layout,
    build_doc_tree,
    collection_demo_filename,
    create_docs_meta,
    load_doc_node,
    load_plugin_doc_bundle,
    render_demo_png,
    split_inline_text_spans,
)

DOCS_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)


@dataclass(slots=True, frozen=True)
class DemoRenderJob:
    bundle: PluginDocBundle
    feature_index: int
    output: Path


@dataclass(slots=True, frozen=True)
class DemoCollectionTile:
    index: int
    title: str
    slug: str
    summary: str
    trigger: str
    source: Path


@dataclass(slots=True, frozen=True)
class DemoCollectionJob:
    bundle: PluginDocBundle
    output: Path
    tiles: tuple[DemoCollectionTile, ...]
    columns: int
    thumb_width: int


@dataclass(slots=True, frozen=True)
class PreparedCollectionTile:
    index: int
    title: str
    slug: str
    summary: str
    trigger: str
    image: Image.Image


@dataclass(slots=True, frozen=True)
class HeaderLayout:
    panel_left: int
    panel_top: int
    panel_right: int
    panel_height: int
    left_x: int
    right_x: int
    left_width: int
    right_width: int
    title_y: int
    summary_y: int
    right_y: int
    help_y: int
    summary_lines: tuple[tuple, ...]
    help_lines: tuple[tuple, ...]


def iter_readmes() -> list[Path]:
    readmes: list[Path] = []
    for root in DOCS_ROOTS:
        readmes.extend(sorted(root.glob("**/README.MD")))
    return [path for path in readmes if "/docs/" in path.as_posix()]


def _write_line(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def load_bundle(path: Path) -> PluginDocBundle:
    return load_plugin_doc_bundle(
        source=path,
        default_name=path.parent.name,
        default_description="",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )


def default_worker_count() -> int:
    return max(1, os.cpu_count() or 1)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def collect_demo_jobs() -> tuple[int, tuple[DemoRenderJob, ...]]:
    total_files = 0
    jobs: list[DemoRenderJob] = []
    for path in iter_readmes():
        bundle = load_bundle(path)
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        total_files += 1
        for feature_index, feature in enumerate(bundle.index):
            if not feature.demo_turns or not feature.demo_filename:
                continue
            jobs.append(
                DemoRenderJob(
                    bundle=bundle,
                    feature_index=feature_index,
                    output=demos_dir / feature.demo_filename,
                )
            )
    return total_files, tuple(jobs)


def render_demo_job(job: DemoRenderJob) -> tuple[Path, bytes]:
    feature = job.bundle.index[job.feature_index]
    return job.output, render_demo_png(job.bundle, feature)


def write_demo_result(result: tuple[Path, bytes]) -> Path:
    output, demo_bytes = result
    output.write_bytes(demo_bytes)
    return output


def collect_collection_jobs(
    *,
    columns: int,
    thumb_width: int,
) -> tuple[int, tuple[DemoCollectionJob, ...]]:
    total_files = 0
    jobs: list[DemoCollectionJob] = []
    for path in iter_readmes():
        bundle = load_bundle(path)
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        total_files += 1
        tiles = tuple(
            DemoCollectionTile(
                index=feature_index + 1,
                title=feature.title,
                slug=feature.slug,
                summary=feature.summary,
                trigger=feature.trigger,
                source=demos_dir / feature.demo_filename,
            )
            for feature_index, feature in enumerate(bundle.index)
            if feature.demo_turns and feature.demo_filename
        )
        if not tiles:
            continue
        jobs.append(
            DemoCollectionJob(
                bundle=bundle,
                output=demos_dir / collection_demo_filename(path),
                tiles=tiles,
                columns=columns,
                thumb_width=thumb_width,
            )
        )
    return total_files, tuple(jobs)


def render_collection_job(job: DemoCollectionJob) -> tuple[Path, bytes]:
    return job.output, render_collection_png(job)


def render_collection_png(job: DemoCollectionJob) -> bytes:
    renderer = DemoCollectionRenderer(columns=job.columns, thumb_width=job.thumb_width)
    return renderer.render(
        title=job.bundle.title,
        summary=job.bundle.summary,
        source_path=job.bundle.source_path,
        tiles=job.tiles,
    )


def compose(
    *,
    workers: int | None = None,
    columns: int = 2,
    thumb_width: int = 860,
) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_collection_jobs(
        columns=columns,
        thumb_width=thumb_width,
    )
    if worker_count == 1 or len(jobs) <= 1:
        for job in jobs:
            output = write_demo_result(render_collection_job(job))
            _write_line(f"composed {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(render_collection_job, jobs):
                output = write_demo_result(result)
                _write_line(f"composed {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, composed {len(jobs)} collection images"
    )
    return 0


def build(
    *,
    workers: int | None = None,
    columns: int = 2,
    thumb_width: int = 860,
) -> int:
    generated = generate(workers=workers)
    if generated != 0:
        return generated
    composed = compose(
        workers=workers,
        columns=columns,
        thumb_width=thumb_width,
    )
    if composed != 0:
        return composed
    return validate()


class DemoCollectionRenderer:
    MIN_WIDTH = 1920
    MAX_WIDTH = 2400
    OUTER_MARGIN = 40  # 统一为 40px
    HEADER_PANEL_MIN_HEIGHT = 112
    HEADER_TOP = 50
    HEADER_LEFT = 84
    HEADER_RIGHT_TOP = 74
    HEADER_BOTTOM_PAD = 26
    HEADER_SIDE_PAD = 18
    HEADER_EXTRA_COMPENSATION = 10
    HEADER_RIGHT_BLOCK_WIDTH = 440
    HEADER_RIGHT_GAP = 20
    HEADER_LEFT_GAP = 36
    CONTENT_TOP_GAP = 28
    CARD_PADDING = 24
    CARD_RADIUS = 26
    CARD_BAND_HEIGHT = 64
    CARD_DEMO_TOP_GAP = 18
    CARD_DEMO_BOTTOM_GAP = 20
    CARD_GAP_X = 28
    CARD_GAP_Y = 30
    BOTTOM_MARGIN = 42
    CARD_SLUG_MIN_WIDTH = 68
    CARD_SLUG_MAX_WIDTH = 240
    CARD_COMMAND_BG = BASE_THEME.muted_light
    CARD_COMMAND_CODE_BG = BASE_THEME.inline_code_bg
    CARD_COMMAND_CODE_BORDER = BASE_THEME.line

    def __init__(self, *, columns: int, thumb_width: int) -> None:
        self.theme = BASE_THEME
        self.columns = max(1, columns)
        self.thumb_width = max(240, thumb_width)
        try:
            # 移动端优化：增大字体
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 58)  # 44 → 58
            self.summary_font = ImageFont.truetype(MAPLE_FONT_PATH, 25)  # 19 → 25
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 26)  # 20 → 26
            self.tile_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)  # 24 → 32
            self.tile_meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)  # 18 → 24
            self.tile_body_font = ImageFont.truetype(MAPLE_FONT_PATH, 26)  # 20 → 26
            self.tile_index_font = ImageFont.truetype(MAPLE_FONT_PATH, 28)  # 21 → 28
            self.slug_font = ImageFont.truetype(MAPLE_FONT_PATH, 23)  # 17 → 23
        except OSError:
            self.title_font = ImageFont.load_default()
            self.summary_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.tile_font = ImageFont.load_default()
            self.tile_meta_font = ImageFont.load_default()
            self.tile_body_font = ImageFont.load_default()
            self.tile_index_font = ImageFont.load_default()
            self.slug_font = ImageFont.load_default()

    @property
    def card_width(self) -> int:
        return self.thumb_width + self.CARD_PADDING * 2

    def render(
        self,
        *,
        title: str,
        summary: str,
        source_path: Path,
        tiles: Sequence[DemoCollectionTile],
    ) -> bytes:
        columns = self._effective_columns(len(tiles))
        prepared = tuple(
            PreparedCollectionTile(
                index=tile.index,
                title=tile.title,
                slug=tile.slug,
                summary=tile.summary,
                trigger=tile.trigger,
                image=self._load_thumbnail(tile.source),
            )
            for tile in tiles
        )
        content_width = columns * self.card_width + (columns - 1) * self.CARD_GAP_X
        width = min(
            self.MAX_WIDTH,
            max(self.MIN_WIDTH, content_width + self.OUTER_MARGIN * 2 + 24),
        )
        content_x = (width - content_width) // 2
        if self._should_use_grid_layout(len(prepared), columns):
            placements, content_height = self._grid_layout(
                prepared,
                content_x=content_x,
                columns=columns,
                content_top=0,
            )
        else:
            placements, content_height = self._masonry_layout(
                prepared,
                content_x=content_x,
                columns=columns,
                content_top=0,
            )
        header_height = self._header_height(summary=summary, title=title, width=width)
        content_top = self.OUTER_MARGIN + header_height + self.CONTENT_TOP_GAP
        placements = [
            (tile, x, y + content_top, height) for tile, x, y, height in placements
        ]
        height = content_top + content_height + self.BOTTOM_MARGIN + self.OUTER_MARGIN
        image = Image.new("RGB", (width, height), self.theme.page_bg)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN,
                self.OUTER_MARGIN,
                width - self.OUTER_MARGIN,
                height - self.OUTER_MARGIN,
            ),
            radius=self.theme.shell_radius,
            fill=self.theme.shell_bg,
            outline=self.theme.shell_border,
            width=2,
        )
        self._draw_header(
            draw,
            title=title,
            summary=summary,
            source_path=source_path,
            tile_count=len(prepared),
            width=width,
        )
        self._draw_tiles(
            image,
            draw,
            placements=placements,
        )

        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _load_thumbnail(self, path: Path) -> Image.Image:
        with Image.open(path) as source:
            image = source.convert("RGB")
        image = self._crop_demo_conversation(image)
        height = max(1, round(image.height * self.thumb_width / image.width))
        return image.resize((self.thumb_width, height), Image.Resampling.LANCZOS)

    def _crop_demo_conversation(self, image: Image.Image) -> Image.Image:
        renderer = DemoImageRenderer()
        panel_top = (
            renderer.OUTER_MARGIN + renderer.HEADER_HEIGHT + renderer.BODY_TOP_GAP
        )
        footer_cut = (
            renderer.FOOTER_TOP_GAP + renderer.FOOTER_HEIGHT + renderer.OUTER_MARGIN
        )
        left = renderer.OUTER_MARGIN + 28
        top = panel_top
        right = renderer.WIDTH - renderer.OUTER_MARGIN - 28
        bottom = max(top + 1, image.height - footer_cut)
        cropped = image.crop((left, top, right, bottom))
        return cropped if cropped.width > 0 and cropped.height > 0 else image

    def _card_height(self, tile: PreparedCollectionTile) -> int:
        content_width = self.card_width - self.CARD_PADDING * 2
        command_lines = self._tile_command_lines(tile, content_width)
        summary_lines = self._tile_summary_lines(tile, content_width)
        command_height = len(command_lines) * self._line_height(self.tile_body_font, 8)
        summary_height = len(summary_lines) * self._line_height(self.tile_meta_font, 6)
        return (
            self.CARD_PADDING
            + self.CARD_BAND_HEIGHT
            + self.CARD_DEMO_TOP_GAP
            + tile.image.height
            + self.CARD_DEMO_BOTTOM_GAP
            + command_height
            + summary_height
            + 94
        )

    def _masonry_layout(
        self,
        tiles: Sequence[PreparedCollectionTile],
        *,
        content_x: int,
        columns: int,
        content_top: int,
    ) -> tuple[list[tuple[PreparedCollectionTile, int, int, int]], int]:
        column_bottoms = [content_top for _ in range(columns)]
        placements: list[tuple[PreparedCollectionTile, int, int, int]] = []
        for index, tile in enumerate(tiles):
            _ = index
            height = self._card_height(tile)
            column = min(range(columns), key=column_bottoms.__getitem__)
            x = content_x + column * (self.card_width + self.CARD_GAP_X)
            y = column_bottoms[column]
            placements.append((tile, x, y, height))
            column_bottoms[column] = y + height + self.CARD_GAP_Y
        content_height = max(column_bottoms, default=content_top) - content_top
        content_height = max(0, content_height - self.CARD_GAP_Y)
        return placements, content_height

    def _grid_layout(
        self,
        tiles: Sequence[PreparedCollectionTile],
        *,
        content_x: int,
        columns: int,
        content_top: int,
    ) -> tuple[list[tuple[PreparedCollectionTile, int, int, int]], int]:
        placements: list[tuple[PreparedCollectionTile, int, int, int]] = []
        y = content_top
        for start in range(0, len(tiles), columns):
            row = list(tiles[start : start + columns])
            row_heights = [self._card_height(tile) for tile in row]
            row_height = max(row_heights, default=0)
            row_width = (
                len(row) * self.card_width + max(0, len(row) - 1) * self.CARD_GAP_X
            )
            row_x = content_x
            if len(row) < columns:
                full_width = columns * self.card_width + (columns - 1) * self.CARD_GAP_X
                row_x = content_x + (full_width - row_width) // 2
            for index, tile in enumerate(row):
                x = row_x + index * (self.card_width + self.CARD_GAP_X)
                placements.append((tile, x, y, self._card_height(tile)))
            y += row_height + self.CARD_GAP_Y
        content_height = max(0, y - content_top - self.CARD_GAP_Y)
        return placements, content_height

    def _effective_columns(self, tile_count: int) -> int:
        if tile_count <= 0:
            return 1
        return min(self.columns, tile_count)

    def _should_use_grid_layout(self, tile_count: int, columns: int) -> bool:
        if columns <= 1:
            return True
        return columns == 2 and tile_count <= 6

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        title: str,
        summary: str,
        source_path: Path,
        tile_count: int,
        width: int,
    ) -> None:
        layout = self._measure_header_layout(summary=summary, title=title, width=width)
        panel_left = layout.panel_left
        panel_top = layout.panel_top
        panel_right = layout.panel_right
        panel_bottom = panel_top + layout.panel_height
        panel_fill, _ = self._palette_for_source(source_path)
        draw.rounded_rectangle(
            (panel_left, panel_top, panel_right, panel_bottom),
            radius=28,
            fill=panel_fill,
        )

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
                y=layout.summary_y + index * self._line_height(self.summary_font, 4),
                line=line,
                font=self.summary_font,
                fill=self.theme.hint,
            )
        draw.text(
            (layout.right_x, layout.right_y),
            f"共 {tile_count} 个功能卡片",
            fill=self.theme.deep,
            font=self.meta_font,
        )
        for index, line in enumerate(layout.help_lines):
            self._draw_inline_line(
                draw,
                x=layout.right_x,
                y=layout.help_y + index * self._line_height(self.summary_font, 4),
                line=line,
                font=self.summary_font,
                fill=self.theme.hint,
            )

    def _draw_tiles(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        placements: Sequence[tuple[PreparedCollectionTile, int, int, int]],
    ) -> None:
        for tile, x, y, height in placements:
            self._draw_tile(image, draw, tile=tile, x=x, y=y, height=height)

    def _draw_tile(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        tile: PreparedCollectionTile,
        x: int,
        y: int,
        height: int,
    ) -> None:
        draw.rounded_rectangle(
            (x + 4, y + 6, x + self.card_width + 4, y + height + 6),
            radius=self.CARD_RADIUS,
            fill=self.theme.panel_soft_bg,
        )
        draw.rounded_rectangle(
            (x, y, x + self.card_width, y + height),
            radius=self.CARD_RADIUS,
            fill=self.theme.panel_bg,
            outline=self.theme.line,
            width=2,
        )
        _, title_band = self._palette_for_slug(tile.slug)
        draw.rounded_rectangle(
            (
                x + 12,
                y + 12,
                x + self.card_width - 12,
                y + 12 + self.CARD_BAND_HEIGHT,
            ),
            radius=18,
            fill=title_band,
        )
        title_x = x + self.CARD_PADDING
        title_y = y + self.CARD_PADDING
        content_width = self.card_width - self.CARD_PADDING * 2

        index_text = f"{tile.index:02d}"
        title_text = f"{index_text}  {tile.title}"
        slug_text = tile.slug
        slug_width = min(
            self.CARD_SLUG_MAX_WIDTH,
            max(
                self.CARD_SLUG_MIN_WIDTH,
                self._text_width(slug_text, self.slug_font) + 26,
            ),
        )
        slug_x = x + self.card_width - self.CARD_PADDING - slug_width
        title_width_limit = max(180, slug_x - title_x - 18)
        draw.text(
            (title_x, title_y + 7),
            self._ellipsize(title_text, self.tile_index_font, title_width_limit),
            fill=self.theme.deep,
            font=self.tile_index_font,
        )
        draw.rounded_rectangle(
            (slug_x, title_y, slug_x + slug_width, title_y + 30),
            radius=14,
            fill=self.theme.muted_light,
        )
        draw.text(
            (slug_x + 13, title_y + 5),
            self._ellipsize(slug_text, self.slug_font, slug_width - 26),
            fill=self.theme.strong,
            font=self.slug_font,
        )

        demo_x = x + self.CARD_PADDING
        demo_y = y + self.CARD_PADDING + self.CARD_BAND_HEIGHT + self.CARD_DEMO_TOP_GAP
        image.paste(tile.image, (demo_x, demo_y))

        command_lines = self._tile_command_lines(tile, content_width)
        command_top = demo_y + tile.image.height + self.CARD_DEMO_BOTTOM_GAP
        self._draw_info_block(
            draw,
            x=title_x,
            y=command_top,
            width=content_width,
            lines=command_lines,
            font=self.tile_body_font,
            fill=self.theme.inline_code_text,
            background=self.theme.inline_code_bg,
        )

        command_height = (
            len(command_lines) * self._line_height(self.tile_body_font, 8) + 22
        )
        summary_lines = self._tile_summary_lines(tile, content_width)
        summary_top = command_top + command_height + 18
        for index, line in enumerate(summary_lines):
            self._draw_inline_line(
                draw,
                x=title_x,
                y=summary_top + index * self._line_height(self.tile_meta_font, 6),
                line=line,
                font=self.tile_meta_font,
                fill=self.theme.hint,
            )

    def _ellipsize(
        self,
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
    ) -> str:
        if self._text_width(text, font) <= max_width:
            return text
        suffix = "..."
        suffix_width = self._text_width(suffix, font)
        clipped = text
        while clipped and self._text_width(clipped, font) + suffix_width > max_width:
            clipped = clipped[:-1]
        return f"{clipped}{suffix}" if clipped else suffix

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
        for span in split_inline_text_spans(text):
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
            and self._inline_line_width(
                [*list(clipped[-1]), (suffix, False)],
                font,
            )
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

    def _tile_command_lines(
        self,
        tile: PreparedCollectionTile,
        content_width: int,
    ) -> list[tuple]:
        return self._wrap_inline_text(
            tile.trigger.strip() or f"#help {tile.slug}",
            self.tile_body_font,
            content_width,
            max_lines=None,
        )

    def _tile_summary_lines(
        self,
        tile: PreparedCollectionTile,
        content_width: int,
    ) -> list[tuple]:
        return self._wrap_inline_text(
            tile.summary.strip() or "查看该子功能的用途、常见用法和关键边界。",
            self.tile_meta_font,
            content_width,
            max_lines=None,
        )

    def _append_plain_span_wrapped(
        self,
        current: list[tuple[str, bool]],
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
    ) -> tuple[list[tuple[str, bool]], list[tuple]]:
        flushed: list[tuple] = []
        segments = self._split_wrappable_segments(text)
        for segment in segments:
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
        segments = self._split_wrappable_segments(text)
        for segment in segments:
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
                    and self._inline_line_width(
                        [*current, (piece, True)],
                        font,
                    )
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
        code_outline: str | None = None,
    ) -> None:
        cursor_x = x
        line_height = self._text_height(font)
        chip_background = code_background or self.theme.inline_code_bg
        chip_fill = code_fill or self.theme.inline_code_text
        chip_outline = code_outline if code_outline is not None else None
        for text, code in line:
            if not text:
                continue
            if not code:
                draw.text((cursor_x, y), text, fill=fill, font=font)
                cursor_x += self._text_width(text, font)
                continue
            text_width = self._text_width(text, font)
            chip_height = line_height + self.theme.inline_code_pad_y * 2 - 2
            chip_y = y - 1
            chip_width = text_width + self.theme.inline_code_pad_x * 2
            draw.rounded_rectangle(
                (
                    cursor_x,
                    chip_y,
                    cursor_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=self.theme.inline_code_radius,
                fill=chip_background,
                outline=chip_outline,
            )
            draw.text(
                (cursor_x + self.theme.inline_code_pad_x, y),
                text,
                fill=chip_fill,
                font=font,
            )
            cursor_x += chip_width

    def _draw_info_block(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        width: int,
        lines: Sequence[tuple[str, bool] | tuple],
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        fill: str,
        background: str,
    ) -> None:
        line_height = self._line_height(font, 8)
        height = len(lines) * line_height + 22
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=18,
            fill=background,
        )
        for index, line in enumerate(lines):
            self._draw_inline_line(
                draw,
                x=x + 16,
                y=y + 11 + index * line_height,
                line=line,
                font=font,
                fill=fill,
                code_background="#FFFFFF",
                code_fill=self.theme.strong,
                code_outline=self.theme.accent,
            )

    def _header_height(self, *, summary: str, title: str, width: int) -> int:
        return self._measure_header_layout(
            summary=summary,
            title=title,
            width=width,
        ).panel_height

    def _measure_header_layout(
        self,
        *,
        summary: str,
        title: str,
        width: int,
    ) -> HeaderLayout:
        panel_left = self.OUTER_MARGIN + self.HEADER_SIDE_PAD
        panel_top = self.HEADER_TOP
        panel_right = width - self.OUTER_MARGIN - self.HEADER_SIDE_PAD
        panel_width = panel_right - panel_left
        right_block_width = min(
            self.HEADER_RIGHT_BLOCK_WIDTH,
            max(340, panel_width // 4),
        )
        left_x = panel_left + 44
        right_x = panel_right - right_block_width - self.HEADER_RIGHT_GAP
        left_width = max(260, right_x - left_x - self.HEADER_LEFT_GAP)
        summary_lines = self._wrap_inline_text(
            summary.strip() or "已按子功能拆分生成合集预览。",
            self.summary_font,
            left_width,
            max_lines=6,
        )
        help_lines = self._wrap_inline_text(
            f"使用 #help {title} <子功能> 查看对应说明",
            self.summary_font,
            right_block_width,
            max_lines=4,
        )
        title_y = panel_top + 24
        summary_y = title_y + 48
        right_y = panel_top + 28
        help_y = right_y + 34
        summary_step = self._line_height(self.summary_font, 4)
        help_step = self._line_height(self.summary_font, 4)
        summary_bottom = summary_y + len(summary_lines) * summary_step
        help_bottom = help_y + len(help_lines) * help_step
        title_bottom = title_y + self._text_height(self.title_font)
        meta_bottom = right_y + self._text_height(self.meta_font)
        panel_height = max(
            self.HEADER_PANEL_MIN_HEIGHT,
            max(
                title_bottom,
                meta_bottom,
                summary_bottom,
                help_bottom,
            )
            - panel_top
            + self.HEADER_BOTTOM_PAD
            + self.HEADER_EXTRA_COMPENSATION,
        )
        return HeaderLayout(
            panel_left=panel_left,
            panel_top=panel_top,
            panel_right=panel_right,
            panel_height=panel_height,
            left_x=left_x,
            right_x=right_x,
            left_width=left_width,
            right_width=right_block_width,
            title_y=title_y,
            summary_y=summary_y,
            right_y=right_y,
            help_y=help_y,
            summary_lines=tuple(summary_lines),
            help_lines=tuple(help_lines),
        )

    def _inline_line_width(
        self,
        line: Sequence[tuple[str, bool]],
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> int:
        width = 0
        for text, code in line:
            if not text:
                continue
            width += self._text_width(text, font)
            if code:
                width += self.theme.inline_code_pad_x * 2
        return width

    def _append_inline_char(
        self,
        line: Sequence[tuple[str, bool]],
        char: str,
        *,
        code: bool,
    ) -> list[tuple[str, bool]]:
        updated = list(line)
        if updated and updated[-1][1] is code:
            prev_text, _ = updated[-1]
            updated[-1] = (prev_text + char, code)
        else:
            updated.append((char, code))
        return updated

    def _text_height(
        self,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        return int(bbox[3] - bbox[1])

    def _line_height(
        self,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        extra: int,
    ) -> int:
        return self._text_height(font) + extra

    def _palette_for_source(self, source_path: Path) -> tuple[str, str]:
        path_text = source_path.as_posix()
        if "/wordbank/docs/approval/" in path_text:
            return PALETTE_ACCENTS["wordbank-approval"]
        for key in ("wordbank", "study"):
            if key in path_text:
                return PALETTE_ACCENTS[key]
        return PALETTE_ACCENTS["default"]

    def _palette_for_slug(self, slug: str) -> tuple[str, str]:
        lowered = slug.lower()
        if lowered.startswith("add") or lowered in {"shortcut", "guided-flow"}:
            return PALETTE_ACCENTS["study"]
        if "approve" in lowered or "reject" in lowered or "pending" in lowered:
            return PALETTE_ACCENTS["wordbank-approval"]
        return PALETTE_ACCENTS["wordbank"]


def generate(*, workers: int | None = None) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_demo_jobs()
    if worker_count == 1 or len(jobs) <= 1:
        for job in jobs:
            output = write_demo_result(render_demo_job(job))
            _write_line(f"generated {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(render_demo_job, jobs):
                output = write_demo_result(result)
                _write_line(f"generated {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, generated {len(jobs)} demo images"
    )
    return 0


def validate() -> int:
    errors: list[str] = []
    nodes: list[DocNode] = []
    slugs_seen: dict[str, Path] = {}
    for path in iter_readmes():
        try:
            bundle = load_bundle(path)
        except Exception as exc:
            errors.append(
                f"{path.relative_to(ROOT)}: parse failed: {type(exc).__name__}: {exc}"
            )
            continue

        docs_meta = create_docs_meta(
            visible=True,
            category="general",
            order=100,
            source=path,
        )
        node = load_doc_node(
            source=path,
            default_name=bundle.title,
            default_description=bundle.description,
            trigger=TriggerType.COMMAND,
            permission=Permission.NORMAL,
            docs_meta=docs_meta,
        )
        nodes.append(node)
        prior = slugs_seen.get(node.slug)
        if prior is not None:
            errors.append(
                f"{path.relative_to(ROOT)}: duplicate doc slug {node.slug} "
                f"(first seen in {prior.relative_to(ROOT)})"
            )
        else:
            slugs_seen[node.slug] = path

        if not bundle.summary.strip():
            errors.append(f"{path.relative_to(ROOT)}: missing 概览 section content")
        if not bundle.index:
            errors.append(f"{path.relative_to(ROOT)}: missing feature entries")

        for feature in bundle.index:
            if not feature.overview.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 说明 section"
                )
            if not feature.preconditions.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 前置条件 section"
                )
            if not feature.failures.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 失败情况 section"
                )
            if not feature.demo_turns:
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing demo turns"
                )
            demo_path = path.parent / "demos" / feature.demo_filename
            if not demo_path.exists():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    f"missing demo file {feature.demo_filename}"
                )
            layout_errors = audit_demo_layout(bundle, feature)
            if layout_errors:
                errors.extend(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} {message}"
                    for message in layout_errors
                )
        if any(feature.demo_turns for feature in bundle.index):
            collection_path = path.parent / "demos" / collection_demo_filename(path)
            if not collection_path.exists():
                errors.append(
                    f"{path.relative_to(ROOT)}: missing collection demo file "
                    f"{collection_path.name}"
                )

    if not errors:
        tree = build_doc_tree(nodes)
        known_slugs = {node.slug for node in tree.nodes}
        for node in tree.nodes:
            if node.parent_slug is not None and node.parent_slug not in known_slugs:
                errors.append(
                    f"{node.source_path.relative_to(ROOT)}: missing parent node "
                    f"{node.parent_slug} for slug {node.slug}"
                )

    if errors:
        _write_line("\n".join(errors))
        return 1

    _write_line(f"validated {len(iter_readmes())} README files")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plugin docs helper")
    subparsers = parser.add_subparsers(dest="action", required=True)
    build_parser = subparsers.add_parser(
        "build",
        help=(
            "generate feature demos, compose collection images, "
            "then validate all docs assets"
        ),
    )
    build_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel workers shared by generate/compose; use 1 for serial execution "
            "(default: %(default)s)"
        ),
    )
    build_parser.add_argument(
        "--columns",
        type=positive_int,
        default=2,
        help="number of columns in each collection image (default: %(default)s)",
    )
    build_parser.add_argument(
        "--thumb-width",
        type=positive_int,
        default=700,
        help="thumbnail width for each feature demo (default: %(default)s)",
    )
    compose_parser = subparsers.add_parser(
        "compose",
        help="compose per-README collection PNG assets from generated demo files",
    )
    compose_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel compose workers; use 1 for serial rendering "
            "(default: %(default)s)"
        ),
    )
    compose_parser.add_argument(
        "--columns",
        type=positive_int,
        default=2,
        help="number of columns in each collection image (default: %(default)s)",
    )
    compose_parser.add_argument(
        "--thumb-width",
        type=positive_int,
        default=700,
        help="thumbnail width for each feature demo (default: %(default)s)",
    )
    generate_parser = subparsers.add_parser(
        "generate",
        help="generate demo PNG assets from README specs",
    )
    generate_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel render workers; use 1 for serial rendering (default: %(default)s)"
        ),
    )
    subparsers.add_parser("validate", help="validate README structure and demo assets")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    match args.action:
        case "build":
            return build(
                workers=args.workers,
                columns=args.columns,
                thumb_width=args.thumb_width,
            )
        case "compose":
            return compose(
                workers=args.workers,
                columns=args.columns,
                thumb_width=args.thumb_width,
            )
        case "generate":
            return generate(workers=args.workers)
        case "validate":
            return validate()
        case _:
            parser.error("unknown action")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
