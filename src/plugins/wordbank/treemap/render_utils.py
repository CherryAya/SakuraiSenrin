"""Rendering utility mixin for wordbank search treemap cards."""

from __future__ import annotations

import re
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_IMAGE_PLACEHOLDER_RE = re.compile(r"\s*\[图片x\d+\]\s*")


class SearchTreemapRenderUtilsMixin:
    def _field_label(self: Any, field: str, locale: str) -> str:
        return {
            "all": self._tr(locale, "wordbank.search_card.field.all"),
            "trigger": self._tr(locale, "wordbank.search_card.field.trigger"),
            "response": self._tr(locale, "wordbank.search_card.field.response"),
        }.get(field, field)

    def _format_item_number(self, number: int) -> str:
        return f"{number:02d}" if number < 100 else str(number)

    def _format_matched_by_label(self: Any, value: str, locale: str) -> str:
        if not value:
            return self._tr(locale, "wordbank.search_card.none")
        return {
            "text:mixed": "触发+响应",
            "text:trigger": self._tr(locale, "wordbank.search_card.field.trigger"),
            "text:response": self._tr(locale, "wordbank.search_card.field.response"),
            "text:group": "分组",
            "image:trigger": self._tr(locale, "wordbank.search_card.preview.trigger"),
            "image:response": self._tr(locale, "wordbank.search_card.preview.response"),
        }.get(value, value)

    def _normalize_text(self: Any, text: str, locale: str) -> str:
        cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return cleaned or self._tr(locale, "wordbank.search_card.none")

    def _normalize_response_text(
        self: Any,
        text: str,
        locale: str,
        *,
        has_image_preview: bool,
    ) -> str:
        candidate = _IMAGE_PLACEHOLDER_RE.sub(" ", text) if has_image_preview else text
        cleaned = " ".join(
            part.strip() for part in candidate.splitlines() if part.strip()
        )
        if cleaned:
            return cleaned
        if has_image_preview:
            return ""
        return self._tr(locale, "wordbank.search_card.none")

    def _wrap_text(
        self: Any,
        text: str,
        font: Any,
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

    def _truncate_line(self: Any, text: str, font: Any, max_width: int) -> str:
        if self._text_width(text, font) <= max_width:
            return text
        candidate = text
        while candidate and self._text_width(f"{candidate}...", font) > max_width:
            candidate = candidate[:-1]
        return f"{candidate}..." if candidate else "..."

    def _line_height(self, font: Any) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0), "Ag", font=font
        )
        return int(bbox[3] - bbox[1] + 4)

    def _text_width(self, text: str, font: Any) -> int:
        return int(
            ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(text, font=font)
        )

    def _load_maple_font(self: Any, size: int) -> Any:
        if size not in self._maple_font_cache:
            try:
                self._maple_font_cache[size] = ImageFont.truetype(
                    self._maple_font_path, size
                )
            except Exception:
                self._maple_font_cache[size] = ImageFont.load_default()
        return self._maple_font_cache[size]

    def _load_lxgw_font(self: Any, size: int) -> Any:
        if size not in self._lxgw_font_cache:
            try:
                self._lxgw_font_cache[size] = ImageFont.truetype(
                    self._lxgw_font_path, size
                )
            except Exception:
                self._lxgw_font_cache[size] = ImageFont.load_default()
        return self._lxgw_font_cache[size]
