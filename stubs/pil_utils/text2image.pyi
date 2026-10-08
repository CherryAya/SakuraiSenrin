"""``pil_utils.text2image.Text2Image`` 的最小类型存根。

``pil_utils`` 未随包发布 ``py.typed``，strict 模式下 pyright 会把全部成员判为
Unknown 并向下传播（plugin_docs 的演示/文档渲染因此产生 reportMissingTypeStubs
与 reportUnknownMemberType）。这里只描述本仓库实际用到的成员签名，参数与默认值
对齐 ``.venv/.../pil_utils/text2image.py`` 的真实定义。
"""

from typing import TypeAlias, Union

ColorType: TypeAlias = Union[str, tuple[int, int, int], tuple[int, int, int, int]]
FontStyle: TypeAlias = str
HAlignType: TypeAlias = str

class Text2Image:
    @classmethod
    def from_text(
        cls,
        text: str,
        font_size: float,
        *,
        font_style: FontStyle = "normal",
        fill: ColorType = "black",
        align: HAlignType = "left",
        stroke_width: float = 0,
        stroke_fill: ColorType | None = None,
        font_families: list[str] = [],
        fallback_fonts_families: list[str] = [],
    ) -> "Text2Image": ...
    @property
    def longest_line(self) -> float: ...
    @property
    def height(self) -> float: ...
    def wrap(self, width: float) -> "Text2Image": ...

__all__ = ["Text2Image"]
