# 本文件的 mixin 方法把 self 显式 cast 成组合后的宿主类（如 WaterRepository），
# 再调用同一次实例上的内部辅助方法。这些方法是该仓库层的实现细节，刻意不公开；
# 但它们与调用方是同一个对象，不构成越权访问。pyright 无法表达「同一实例上的
# mixin 内部协作」，故在此显式豁免。
# pyright: reportPrivateUsage=false
"""Rendering utility mixin for wordbank search treemap cards."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from PIL import Image, ImageDraw, ImageFont

from src.lib.i18n.types import LocaleCode

if TYPE_CHECKING:
    from .service import SearchTreemapRenderer

_IMAGE_PLACEHOLDER_RE = re.compile(r"\s*\[图片x\d+\]\s*")

# 渲染字体：truetype 优先，失败时回退 Pillow 默认位图字体
type TreemapFont = ImageFont.FreeTypeFont | ImageFont.ImageFont


class SearchTreemapRenderUtilsMixin:
    def _field_label(self, field: str, locale: LocaleCode) -> str:
        repo_self = cast("SearchTreemapRenderer", self)
        return {
            "all": repo_self._tr(locale, "wordbank.search_card.field.all"),
            "trigger": repo_self._tr(locale, "wordbank.search_card.field.trigger"),
            "response": repo_self._tr(locale, "wordbank.search_card.field.response"),
        }.get(field, field)

    def _format_item_number(self, number: int) -> str:
        return f"{number:02d}" if number < 100 else str(number)

    def _format_matched_by_label(self, value: str, locale: LocaleCode) -> str:
        repo_self = cast("SearchTreemapRenderer", self)
        if not value:
            return repo_self._tr(locale, "wordbank.search_card.none")
        return {
            "text:mixed": repo_self._tr(
                locale, "wordbank.search_card.match.trigger_response"
            ),
            "text:trigger": repo_self._tr(locale, "wordbank.search_card.field.trigger"),
            "text:response": repo_self._tr(
                locale, "wordbank.search_card.field.response"
            ),
            "text:group": repo_self._tr(locale, "wordbank.search_card.match.group"),
            "image:trigger": repo_self._tr(
                locale, "wordbank.search_card.preview.trigger"
            ),
            "image:response": repo_self._tr(
                locale, "wordbank.search_card.preview.response"
            ),
        }.get(value, value)

    def _normalize_text(self, text: str, locale: LocaleCode) -> str:
        repo_self = cast("SearchTreemapRenderer", self)
        cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return cleaned or repo_self._tr(locale, "wordbank.search_card.none")

    def _normalize_response_text(
        self,
        text: str,
        locale: LocaleCode,
        *,
        has_image_preview: bool,
    ) -> str:
        repo_self = cast("SearchTreemapRenderer", self)
        candidate = _IMAGE_PLACEHOLDER_RE.sub(" ", text) if has_image_preview else text
        cleaned = " ".join(
            part.strip() for part in candidate.splitlines() if part.strip()
        )
        if cleaned:
            return cleaned
        if has_image_preview:
            return ""
        return repo_self._tr(locale, "wordbank.search_card.none")

    def _wrap_text(
        self,
        text: str,
        font: TreemapFont,
        max_width: int,
        *,
        max_lines: int,
    ) -> list[str]:
        if not text or max_width <= 0:
            return [""]
        lines: list[str] = []
        for raw_line in text.splitlines() or [text]:
            current = ""
            for char in raw_line:
                candidate = f"{current}{char}"
                if self._text_width(candidate, font) <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = char
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                break
            if current:
                lines.append(current)
            if len(lines) >= max_lines:
                break
        if not lines:
            return [""]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if len(lines) == max_lines:
            lines[-1] = self._truncate_line(lines[-1], font, max_width)
        return lines

    def _truncate_line(self, text: str, font: TreemapFont, max_width: int) -> str:
        if self._text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and self._text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..." if candidate else "..."

    def _line_height(self, font: TreemapFont) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0), "Ag", font=font
        )
        return int(bbox[3] - bbox[1] + 4)

    def _text_width(self, text: str, font: TreemapFont) -> int:
        return int(
            ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(text, font=font)
        )

    def _load_maple_font(self, size: int) -> TreemapFont:
        repo_self = cast("SearchTreemapRenderer", self)
        if size not in repo_self._maple_font_cache:
            try:
                repo_self._maple_font_cache[size] = ImageFont.truetype(
                    repo_self._maple_font_path, size
                )
            except Exception:
                repo_self._maple_font_cache[size] = ImageFont.load_default()
        return repo_self._maple_font_cache[size]

    def _load_lxgw_font(self, size: int) -> TreemapFont:
        repo_self = cast("SearchTreemapRenderer", self)
        if size not in repo_self._lxgw_font_cache:
            try:
                repo_self._lxgw_font_cache[size] = ImageFont.truetype(
                    repo_self._lxgw_font_path, size
                )
            except Exception:
                repo_self._lxgw_font_cache[size] = ImageFont.load_default()
        return repo_self._lxgw_font_cache[size]
