"""wordbank 卡片渲染共用的文本度量与字体加载。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH

type TextFont = ImageFont.FreeTypeFont | ImageFont.ImageFont

_MEASURE_CANVAS = Image.new("RGB", (10, 10))
_MEASURE_DRAW = ImageDraw.Draw(_MEASURE_CANVAS)


def text_width(text: str, font: TextFont) -> int:
    return int(_MEASURE_DRAW.textlength(text, font=font))


def line_height(font: TextFont) -> int:
    bbox = _MEASURE_DRAW.textbbox((0, 0), "Ag", font=font)
    return int(bbox[3] - bbox[1] + 8)


@lru_cache(maxsize=64)
def _load_font_cached(path: str, size: int) -> TextFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def load_font(size: int, path: str | Path | None = None) -> TextFont:
    return _load_font_cached(str(path or MAPLE_FONT_PATH), size)
