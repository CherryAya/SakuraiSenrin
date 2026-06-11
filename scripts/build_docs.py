"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import sys
from typing import ClassVar

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.consts import Permission
from src.lib.consts import MAPLE_FONT_PATH, TriggerType
from src.lib.plugin_docs import (
    PluginDocBundle,
    audit_demo_layout,
    collection_demo_filename,
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
    title: str
    slug: str
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
    title: str
    slug: str
    image: Image.Image


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
                title=feature.title,
                slug=feature.slug,
                source=demos_dir / feature.demo_filename,
            )
            for feature in bundle.index
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
    thumb_width: int = 620,
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
    thumb_width: int = 620,
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
    MIN_WIDTH = 1440
    MAX_WIDTH = 2380
    OUTER_MARGIN = 36
    HEADER_HEIGHT = 192
    HEADER_PANEL_HEIGHT = 112
    HEADER_LEFT = 84
    HEADER_TITLE_Y = 74
    HEADER_SUMMARY_Y = 122
    HEADER_RIGHT_Y = 74
    CONTENT_TOP = 252
    CARD_PADDING = 20
    CARD_RADIUS = 26
    CARD_TITLE_HEIGHT = 52
    CARD_TITLE_GAP = 16
    CARD_GAP_X = 28
    CARD_GAP_Y = 30
    BOTTOM_MARGIN = 42
    PAGE_BG = "#FAFAF8"
    SHELL_BG = "#FFFFFF"
    SHELL_BORDER = "#EFE9ED"
    CARD_BG = "#FFFFFF"
    CARD_BORDER = "#EDE5EA"
    TITLE = "#2E2630"
    META = "#8F8190"
    TITLE_BAND_TEXT = "#3B3538"
    CHIP_BG = "#FFF0F5"
    CHIP_TEXT = "#9A3F62"
    HELP_TEXT = "#5E565B"
    CARD_SHADOW = "#F4EDF1"
    INLINE_CODE_BG = "#FFF7FA"
    INLINE_CODE_TEXT = "#7D3653"
    INLINE_CODE_RADIUS = 9
    INLINE_CODE_PAD_X = 7
    INLINE_CODE_PAD_Y = 4
    PALETTES: ClassVar[dict[str, tuple[str, str]]] = {
        "study": ("#FFE4B5", "#FFF0CF"),
        "wordbank_approval": ("#F8D0D2", "#FBE0E3"),
        "wordbank": ("#C9DEF3", "#D9EAFB"),
        "default": ("#E8DEF8", "#F0E8FB"),
    }

    def __init__(self, *, columns: int, thumb_width: int) -> None:
        self.columns = max(1, columns)
        self.thumb_width = max(240, thumb_width)
        try:
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 44)
            self.summary_font = ImageFont.truetype(MAPLE_FONT_PATH, 19)
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.tile_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.slug_font = ImageFont.truetype(MAPLE_FONT_PATH, 17)
        except OSError:
            self.title_font = ImageFont.load_default()
            self.summary_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.tile_font = ImageFont.load_default()
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
                title=tile.title,
                slug=tile.slug,
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
            )
        else:
            placements, content_height = self._masonry_layout(
                prepared,
                content_x=content_x,
                columns=columns,
            )
        height = (
            self.CONTENT_TOP + content_height + self.BOTTOM_MARGIN + self.OUTER_MARGIN
        )
        image = Image.new("RGB", (width, height), self.PAGE_BG)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN,
                self.OUTER_MARGIN,
                width - self.OUTER_MARGIN,
                height - self.OUTER_MARGIN,
            ),
            radius=32,
            fill=self.SHELL_BG,
            outline=self.SHELL_BORDER,
            width=2,
        )
        self._draw_header(
            draw,
            title=title,
            summary=summary,
            source_path=source_path,
            tile_count=len(prepared),
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
        height = max(1, round(image.height * self.thumb_width / image.width))
        return image.resize((self.thumb_width, height), Image.Resampling.LANCZOS)

    def _card_height(self, tile: PreparedCollectionTile) -> int:
        return (
            self.CARD_PADDING
            + self.CARD_TITLE_HEIGHT
            + self.CARD_TITLE_GAP
            + tile.image.height
            + self.CARD_PADDING
        )

    def _masonry_layout(
        self,
        tiles: Sequence[PreparedCollectionTile],
        *,
        content_x: int,
        columns: int,
    ) -> tuple[list[tuple[PreparedCollectionTile, int, int, int]], int]:
        column_bottoms = [self.CONTENT_TOP for _ in range(columns)]
        placements: list[tuple[PreparedCollectionTile, int, int, int]] = []
        for index, tile in enumerate(tiles):
            _ = index
            height = self._card_height(tile)
            column = min(range(columns), key=column_bottoms.__getitem__)
            x = content_x + column * (self.card_width + self.CARD_GAP_X)
            y = column_bottoms[column]
            placements.append((tile, x, y, height))
            column_bottoms[column] = y + height + self.CARD_GAP_Y
        content_height = (
            max(column_bottoms, default=self.CONTENT_TOP) - self.CONTENT_TOP
        )
        content_height = max(0, content_height - self.CARD_GAP_Y)
        return placements, content_height

    def _grid_layout(
        self,
        tiles: Sequence[PreparedCollectionTile],
        *,
        content_x: int,
        columns: int,
    ) -> tuple[list[tuple[PreparedCollectionTile, int, int, int]], int]:
        placements: list[tuple[PreparedCollectionTile, int, int, int]] = []
        y = self.CONTENT_TOP
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
        content_height = max(0, y - self.CONTENT_TOP - self.CARD_GAP_Y)
        return placements, content_height

    def _effective_columns(self, tile_count: int) -> int:
        if tile_count <= 0:
            return 1
        columns = min(self.columns, tile_count)
        if columns == 2 and tile_count >= 8:
            return min(3, tile_count)
        return columns

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
    ) -> None:
        panel_left = self.OUTER_MARGIN + 18
        panel_top = self.OUTER_MARGIN + 14
        panel_right = draw.im.size[0] - self.OUTER_MARGIN - 18
        panel_bottom = panel_top + self.HEADER_PANEL_HEIGHT
        panel_fill, _ = self._palette_for_source(source_path)
        draw.rounded_rectangle(
            (panel_left, panel_top, panel_right, panel_bottom),
            radius=28,
            fill=panel_fill,
        )

        right_block_width = 360
        right_x = panel_right - right_block_width - 20
        left_width = right_x - self.HEADER_LEFT - 36
        summary_lines = self._wrap_inline_text(
            summary.strip() or "已按子功能拆分生成合集预览。",
            self.summary_font,
            left_width,
            max_lines=3,
        )
        help_lines = self._wrap_inline_text(
            f"使用 #help {title} <子功能> 查看对应说明",
            self.summary_font,
            right_block_width,
            max_lines=2,
        )

        draw.text(
            (self.HEADER_LEFT, self.HEADER_TITLE_Y),
            title,
            fill=self.TITLE,
            font=self.title_font,
        )
        for index, line in enumerate(summary_lines):
            self._draw_inline_line(
                draw,
                x=self.HEADER_LEFT,
                y=self.HEADER_SUMMARY_Y + index * 22,
                line=line,
                font=self.summary_font,
                fill=self.META,
            )
        draw.text(
            (right_x, self.HEADER_RIGHT_Y),
            f"共 {tile_count} 个功能卡片",
            fill=self.TITLE,
            font=self.meta_font,
        )
        for index, line in enumerate(help_lines):
            self._draw_inline_line(
                draw,
                x=right_x,
                y=self.HEADER_RIGHT_Y + 34 + index * 20,
                line=line,
                font=self.summary_font,
                fill=self.HELP_TEXT,
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
            fill=self.CARD_SHADOW,
        )
        draw.rounded_rectangle(
            (x, y, x + self.card_width, y + height),
            radius=self.CARD_RADIUS,
            fill=self.CARD_BG,
            outline=self.CARD_BORDER,
            width=2,
        )
        _, title_band = self._palette_for_slug(tile.slug)
        draw.rounded_rectangle(
            (
                x + 12,
                y + 12,
                x + self.card_width - 12,
                y + 56,
            ),
            radius=18,
            fill=title_band,
        )
        title_x = x + self.CARD_PADDING
        title_y = y + self.CARD_PADDING
        max_title_width = self.card_width - self.CARD_PADDING * 2 - 116
        draw.text(
            (title_x, title_y + 1),
            self._ellipsize(tile.title, self.tile_font, max_title_width),
            fill=self.TITLE_BAND_TEXT,
            font=self.tile_font,
        )
        chip_text = tile.slug
        chip_width = min(
            104,
            self._text_width(chip_text, self.slug_font) + 26,
        )
        chip_x = x + self.card_width - self.CARD_PADDING - chip_width
        chip_y = title_y
        draw.rounded_rectangle(
            (chip_x, chip_y, chip_x + chip_width, chip_y + 30),
            radius=14,
            fill=self.CHIP_BG,
        )
        draw.text(
            (chip_x + 13, chip_y + 5),
            self._ellipsize(chip_text, self.slug_font, chip_width - 26),
            fill=self.CHIP_TEXT,
            font=self.slug_font,
        )
        image.paste(
            tile.image,
            (
                x + self.CARD_PADDING,
                y + self.CARD_PADDING + self.CARD_TITLE_HEIGHT + self.CARD_TITLE_GAP,
            ),
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
        max_lines: int,
    ) -> list[tuple]:
        lines: list[tuple] = []
        current: list = []
        for span in split_inline_text_spans(text):
            for char in span.text:
                candidate = self._append_inline_char(current, char, code=span.code)
                if not current or self._inline_line_width(candidate, font) <= max_width:
                    current = candidate
                    continue
                lines.append(tuple(current))
                current = [(char, span.code)]
        if current:
            lines.append(tuple(current))
        if len(lines) <= max_lines:
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

    def _draw_inline_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        line: tuple,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        fill: str,
    ) -> None:
        cursor_x = x
        line_height = self._text_height(font)
        for text, code in line:
            if not text:
                continue
            if not code:
                draw.text((cursor_x, y), text, fill=fill, font=font)
                cursor_x += self._text_width(text, font)
                continue
            text_width = self._text_width(text, font)
            chip_height = line_height + self.INLINE_CODE_PAD_Y * 2 - 2
            chip_y = y - 1
            chip_width = text_width + self.INLINE_CODE_PAD_X * 2
            draw.rounded_rectangle(
                (
                    cursor_x,
                    chip_y,
                    cursor_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=self.INLINE_CODE_RADIUS,
                fill=self.INLINE_CODE_BG,
            )
            draw.text(
                (cursor_x + self.INLINE_CODE_PAD_X, y),
                text,
                fill=self.INLINE_CODE_TEXT,
                font=font,
            )
            cursor_x += chip_width

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
                width += self.INLINE_CODE_PAD_X * 2
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

    def _palette_for_source(self, source_path: Path) -> tuple[str, str]:
        path_text = source_path.as_posix()
        for key in ("wordbank_approval", "wordbank", "study"):
            if key in path_text:
                return self.PALETTES[key]
        return self.PALETTES["default"]

    def _palette_for_slug(self, slug: str) -> tuple[str, str]:
        lowered = slug.lower()
        if lowered.startswith("add") or lowered in {"shortcut", "guided-flow"}:
            return self.PALETTES["study"]
        if "approve" in lowered or "reject" in lowered or "pending" in lowered:
            return self.PALETTES["wordbank_approval"]
        return self.PALETTES["wordbank"]


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
    for path in iter_readmes():
        try:
            bundle = load_bundle(path)
        except Exception as exc:
            errors.append(
                f"{path.relative_to(ROOT)}: parse failed: {type(exc).__name__}: {exc}"
            )
            continue

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
        default=620,
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
        default=620,
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
