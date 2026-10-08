# 本文件的 mixin 方法把 self 显式 cast 成组合后的宿主类（如 WaterRepository），
# 再调用同一次实例上的内部辅助方法。这些方法是该仓库层的实现细节，刻意不公开；
# 但它们与调用方是同一个对象，不构成越权访问。pyright 无法表达「同一实例上的
# mixin 内部协作」，故在此显式豁免。
# pyright: reportPrivateUsage=false
"""Title/text fitting and preview helpers for wordbank treemap rendering."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from PIL import Image

from .render_utils import TreemapFont

if TYPE_CHECKING:
    from .service import SearchTreemapRenderer


class SearchTreemapFittersMixin:
    def _fit_tile_title_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[TreemapFont, list[str]]:
        repo_self = cast("SearchTreemapRenderer", self)
        safe_text = text.strip() or "?"
        if len(safe_text) <= 4:
            preferred_lines = 1
        elif len(safe_text) <= 8:
            preferred_lines = 2
        elif len(safe_text) <= 14:
            preferred_lines = 3
        else:
            preferred_lines = 4
        fallback_fit: tuple[TreemapFont, list[str]] | None = None
        for size in (28, 24, 20, 18, 16, 14, 12, 10):
            font = repo_self._load_maple_font(size)
            line_height = repo_self._line_height(font)
            max_lines = max(1, min(6, max_height // max(line_height, 1)))
            if max_lines <= 0:
                continue
            lines = repo_self._wrap_text(
                safe_text,
                font,
                max_width,
                max_lines=max(1, len(safe_text)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                if len(lines) <= preferred_lines:
                    return font, lines
                if fallback_fit is None:
                    fallback_fit = (font, lines)
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = repo_self._load_maple_font(10)
        fallback_line_height = repo_self._line_height(fallback_font)
        fallback_max_lines = max(1, min(7, max_height // max(fallback_line_height, 1)))
        full_lines = repo_self._wrap_text(
            safe_text,
            fallback_font,
            max_width,
            max_lines=max(1, len(safe_text)),
        )
        if len(full_lines) <= fallback_max_lines:
            return fallback_font, full_lines
        return fallback_font, full_lines[:fallback_max_lines]

    def _fit_poster_tile_title_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[TreemapFont, list[str]]:
        repo_self = cast("SearchTreemapRenderer", self)
        safe_text = text.strip() or "?"
        fallback_fit: tuple[TreemapFont, list[str]] | None = None
        for size in (20, 18, 16, 14, 12, 10):
            font = repo_self._load_maple_font(size)
            line_height = repo_self._line_height(font)
            max_lines = max(1, min(2, max_height // max(line_height, 1)))
            if max_lines <= 0:
                continue
            lines = repo_self._wrap_text(
                safe_text,
                font,
                max_width,
                max_lines=max(1, len(safe_text)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                return font, lines
            if fallback_fit is None:
                fallback_fit = (font, lines[:max_lines])
        # max_lines 恒 >= 1（max(1, min(2, ...))），循环体至少执行一次且必然
        # 命中上面的赋值分支，故此处 fallback_fit 必非 None；原先「最小字号兜底」
        # 分支不可达。
        return fallback_fit

    def _choose_response_title_font(
        self,
        text: str,
        *,
        width: int,
        spacious: bool,
        has_image: bool,
    ) -> TreemapFont:
        repo_self = cast("SearchTreemapRenderer", self)
        normalized = text.strip()
        if not normalized:
            return (
                repo_self.card_large_title_font
                if spacious
                else repo_self.card_title_font
            )
        if has_image:
            if width < 112:
                candidate_sizes = (18, 16, 14)
            elif width < 148:
                candidate_sizes = (20, 18, 16, 14)
            elif width < 196:
                candidate_sizes = (22, 20, 18, 16)
            elif spacious:
                candidate_sizes = (28, 24, 22, 20, 18)
            else:
                candidate_sizes = (24, 22, 20, 18, 16)
        elif width < 112:
            candidate_sizes = (18, 16, 14)
        elif width < 148:
            candidate_sizes = (22, 20, 18, 16, 14)
        elif width < 196:
            candidate_sizes = (24, 22, 20, 18, 16)
        elif spacious and len(normalized) <= 12:
            candidate_sizes = (34, 30, 26, 24, 22, 20)
        elif spacious:
            candidate_sizes = (30, 26, 24, 22, 20, 18)
        else:
            candidate_sizes = (28, 24, 22, 20, 18, 16)
        max_lines = 3 if has_image else 5
        for size in candidate_sizes:
            font = repo_self._load_lxgw_font(size)
            lines = repo_self._wrap_text(
                normalized,
                font,
                max(1, width),
                max_lines=max(1, len(normalized)),
            )
            if len(lines) <= max_lines:
                return font
        return repo_self._load_lxgw_font(candidate_sizes[-1])

    def _fit_single_text_response_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
    ) -> tuple[TreemapFont, list[str]]:
        repo_self = cast("SearchTreemapRenderer", self)
        normalized = text.strip()
        if not normalized:
            return repo_self.card_title_font, []
        largest_size = self._single_text_initial_font_size(
            text=normalized,
            max_width=max_width,
            max_height=max_height,
        )
        step = 2 if largest_size <= 44 else 4
        candidate_sizes = list(range(largest_size, 11, -step))
        if candidate_sizes[-1] != 12:
            candidate_sizes.append(12)
        manual_lines = self._preferred_single_text_manual_lines(normalized)
        if len(normalized) <= 4:
            preferred_lines = 1
        elif len(normalized) <= 8:
            preferred_lines = 2
        elif len(normalized) <= 16:
            preferred_lines = 3
        else:
            preferred_lines = 5
        fallback_fit: tuple[TreemapFont, list[str]] | None = None
        if manual_lines and max_width < 220 and max_height >= 220:
            manual_gap = repo_self._single_text_line_gap(
                width=max_width,
                height_cap=max_height,
                text=normalized,
                line_count=len(manual_lines),
            )
            for size in candidate_sizes:
                font = repo_self._load_lxgw_font(size)
                line_height = repo_self._line_height(font)
                if any(
                    repo_self._text_width(line, font) > max_width
                    for line in manual_lines
                ):
                    continue
                total_height = (
                    len(manual_lines) * line_height
                    + max(0, len(manual_lines) - 1) * manual_gap
                )
                if total_height <= max_height:
                    return font, list(manual_lines)
        for size in candidate_sizes:
            font = repo_self._load_lxgw_font(size)
            line_height = repo_self._line_height(font)
            max_lines = max(1, min(12, max_height // max(line_height, 1)))
            lines = repo_self._wrap_text(
                normalized,
                font,
                max_width,
                max_lines=max(1, len(normalized)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                if len(lines) <= preferred_lines:
                    return font, lines
                if fallback_fit is None:
                    fallback_fit = (font, lines)
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = repo_self._load_lxgw_font(10)
        fallback_lines = repo_self._wrap_text(
            normalized,
            fallback_font,
            max_width,
            max_lines=max(1, len(normalized)),
        )
        return fallback_font, fallback_lines

    def _preferred_single_text_manual_lines(self, text: str) -> tuple[str, ...]:
        if len(text.strip()) > 12:
            return ()
        match = re.search(r"[，,、：:；;！？!?~～]", text)
        if match is None:
            return ()
        index = match.start()
        left = text[: index + 1].strip()
        right = text[index + 1 :].strip()
        if not left or not right:
            return ()
        return (left, right)

    def _single_text_initial_font_size(
        self,
        *,
        text: str,
        max_width: int,
        max_height: int,
    ) -> int:
        if len(text) <= 4:
            text_cap = 124
        elif len(text) <= 8:
            text_cap = 128
        elif len(text) <= 16:
            text_cap = 78
        else:
            text_cap = 34
        width_ratio = 0.58 if len(text) > 8 else 0.74
        if len(text) <= 8 and max_width < 220 and max_height >= 260:
            width_ratio = 0.82
        width_cap = max(24, int(max_width * width_ratio))
        height_cap = max(24, int(max_height * 0.92))
        return max(12, min(text_cap, width_cap, height_cap))

    def _fit_lxgw_text_block_layout(
        self,
        text: str,
        *,
        max_width: int,
        max_height: int,
        preferred_size: int | None,
    ) -> tuple[TreemapFont, list[str]]:
        repo_self = cast("SearchTreemapRenderer", self)
        normalized = text.strip()
        if not normalized:
            return repo_self.card_title_font, []
        if preferred_size is None or preferred_size <= 0:
            preferred_size = 20
        candidate_sizes = list(range(preferred_size, 7, -2))
        if candidate_sizes[-1] != 8:
            candidate_sizes.append(8)
        fallback_fit: tuple[TreemapFont, list[str]] | None = None
        for size in candidate_sizes:
            font = repo_self._load_lxgw_font(size)
            line_height = repo_self._line_height(font)
            max_lines = max(1, max_height // max(line_height, 1))
            lines = repo_self._wrap_text(
                normalized,
                font,
                max_width,
                max_lines=max(1, len(normalized)),
            )
            if len(lines) <= max_lines and len(lines) * line_height <= max_height:
                return font, lines
            if fallback_fit is None:
                fallback_fit = (font, lines)
        if fallback_fit is not None:
            return fallback_fit
        fallback_font = repo_self._load_lxgw_font(8)
        return (
            fallback_font,
            repo_self._wrap_text(
                normalized,
                fallback_font,
                max_width,
                max_lines=max(1, len(normalized)),
            ),
        )

    def _fit_preview_image(
        self,
        image_path: str,
        *,
        max_width: int,
        max_height: int,
    ) -> Image.Image | None:
        repo_self = cast("SearchTreemapRenderer", self)
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGB")
        except Exception:
            return None
        if max_width <= 0 or max_height <= 0:
            return None
        scale = min(max_width / image.width, max_height / image.height)
        resized = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new(
            "RGB", (resized.width, resized.height), repo_self.theme.white
        )
        canvas.paste(resized, (0, 0))
        return canvas

    def _load_image_size(self, image_path: str) -> tuple[int, int] | None:
        repo_self = cast("SearchTreemapRenderer", self)
        if not image_path:
            return None
        if image_path in repo_self._image_size_cache:
            return repo_self._image_size_cache[image_path]
        try:
            with Image.open(image_path) as source:
                size = (source.width, source.height)
        except Exception:
            size = None
        repo_self._image_size_cache[image_path] = size
        return size
