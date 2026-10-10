"""文字绘制加速。

`pil_utils.BuildImage.draw_text` 在传入 4 元组区域时，内部会把**整张画布**
转成 bytes 再建一个全尺寸 Skia surface（见 pil_utils/text2image.py 的
`draw_on_image`）。耗时与画布面积成正比：

    560x350   16 ms/次
    2240x1400 225 ms/次

报告图是 2240 宽、1000-2800 高的长图，且单张要画 30+ 次文字，
累计可达 9 秒。

这里改为「先画到目标区域大小的局部图，再贴回原位」，结果一致而
无需每行文字转换全画布，实测约 13 倍加速。
"""

from __future__ import annotations

from pil_utils import BuildImage


def draw_text_in_region(
    card: BuildImage,
    box: tuple[int, int, int, int],
    text: str,
    **kwargs: object,
) -> None:
    """在 box 指定的区域内绘制文字。

    与 `card.draw_text(box, text, **kwargs)` 等价，但不做全画布往返。
    box 为 (left, top, right, bottom)。
    """
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return
    patch = BuildImage.new("RGBA", (width, height), (0, 0, 0, 0))
    patch.draw_text((0, 0, width, height), text, **kwargs)  # type: ignore[arg-type]
    card.paste(patch, (left, top), alpha=True)
