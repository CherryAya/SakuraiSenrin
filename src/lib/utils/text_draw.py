"""文字绘制加速：先画局部图再贴回原位。

pil_utils 的 draw_text 传 4 元组区域时会把整张画布转 bytes 再建全尺寸 Skia
surface（见 pil_utils/text2image.py `draw_on_image`）；报告图 2240 宽、
单张 30+ 次文字，累计可达 9 秒，本包装实测约 13 倍加速。
"""

from __future__ import annotations

from pil_utils import BuildImage


def draw_text_in_region(
    card: BuildImage,
    box: tuple[int, int, int, int],
    text: str,
    **kwargs: object,
) -> None:
    """在 box 区域内绘制文字；box 为 (left, top, right, bottom)。"""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return
    patch = BuildImage.new("RGBA", (width, height), (0, 0, 0, 0))
    patch.draw_text((0, 0, width, height), text, **kwargs)  # type: ignore[arg-type]
    card.paste(patch, (left, top), alpha=True)
