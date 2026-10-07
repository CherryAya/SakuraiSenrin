"""``pil_utils.BuildImage`` 的最小类型存根。

``pil_utils`` 未随包发布 ``py.typed``，strict 模式下 pyright 会把它的全部成员
判为 Unknown，并顺着每次调用向下传播——water 插件的 renderer 树因此产生 125 条
``draw_text`` 相关错误，与本仓库代码质量无关。

这里只描述本仓库实际用到的成员签名，参数与默认值对齐
``.venv/.../pil_utils/build_image.py`` 的真实定义，不追求完整覆盖。
"""

from io import BytesIO
from pathlib import Path
from typing import Literal, TypeAlias, Union

from PIL.Image import Image as IMG
from PIL.Image import Resampling, Transpose
from PIL.ImageDraw import ImageDraw as Draw

ColorType: TypeAlias = Union[str, tuple[int, int, int], tuple[int, int, int, int]]
ModeType: TypeAlias = Literal[
    "1", "CMYK", "F", "HSV", "I", "L", "LAB", "P", "RGB", "RGBA", "RGBX", "YCbCr"
]
SizeType: TypeAlias = tuple[int, int]
BoxType: TypeAlias = tuple[int, int, int, int]
PosTypeInt: TypeAlias = tuple[int, int]
PosTypeFloat: TypeAlias = tuple[float, float]
XYType: TypeAlias = tuple[float, float, float, float]
PointsType: TypeAlias = tuple[PosTypeFloat, PosTypeFloat, PosTypeFloat, PosTypeFloat]
DirectionType: TypeAlias = Literal[
    "center",
    "north",
    "south",
    "west",
    "east",
    "northwest",
    "northeast",
    "southwest",
    "southeast",
]
HAlignType: TypeAlias = Literal["left", "right", "center"]
VAlignType: TypeAlias = Literal["top", "bottom", "center"]
FontStyle: TypeAlias = Literal["normal", "italic", "bold", "bold_italic"]

class BuildImage:
    image: IMG

    def __init__(self, image: IMG) -> None: ...
    @classmethod
    def new(
        cls,
        mode: ModeType,
        size: SizeType,
        color: ColorType = "black",
        *,
        font: object = None,
    ) -> "BuildImage": ...
    @classmethod
    def open(cls, file: Union[str, BytesIO, Path]) -> "BuildImage": ...
    def copy(self) -> "BuildImage": ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def size(self) -> SizeType: ...
    @property
    def mode(self) -> ModeType: ...
    @property
    def draw(self) -> Draw: ...
    def resize(
        self,
        size: SizeType,
        resample: Resampling = ...,
        keep_ratio: bool = False,
        inside: bool = False,
        direction: DirectionType = "center",
        bg_color: ColorType | None = None,
        **kwargs: object,
    ) -> "BuildImage": ...
    def resize_canvas(
        self,
        size: SizeType,
        direction: DirectionType = "center",
        bg_color: ColorType | None = None,
    ) -> "BuildImage": ...
    def resize_width(self, width: int, **kwargs: object) -> "BuildImage": ...
    def resize_height(self, height: int, **kwargs: object) -> "BuildImage": ...
    def rotate(
        self,
        angle: float,
        resample: Resampling = ...,
        expand: bool = False,
        **kwargs: object,
    ) -> "BuildImage": ...
    def square(self) -> "BuildImage": ...
    def circle(self) -> "BuildImage": ...
    def circle_corner(self, r: float) -> "BuildImage": ...
    def crop(self, box: BoxType) -> "BuildImage": ...
    def convert(self, mode: ModeType, **kwargs: object) -> "BuildImage": ...
    def paste(
        self,
        img: Union[IMG, "BuildImage"],
        pos: PosTypeInt = (0, 0),
        alpha: bool = False,
        below: bool = False,
        **kwargs: object,
    ) -> "BuildImage": ...
    def alpha_composite(
        self,
        img: Union[IMG, "BuildImage"],
        pos: PosTypeInt = (0, 0),
        **kwargs: object,
    ) -> "BuildImage": ...
    def transpose(self, method: Transpose) -> "BuildImage": ...
    def perspective(self, points: PointsType) -> "BuildImage": ...
    def gradient_color(self, gradient: object) -> "BuildImage": ...
    def draw_point(
        self, pos: PosTypeFloat, fill: ColorType | None = None
    ) -> "BuildImage": ...
    def draw_line(
        self,
        xy: XYType,
        fill: ColorType | None = None,
        width: int = 1,
    ) -> "BuildImage": ...
    def draw_rectangle(
        self,
        xy: XYType,
        fill: ColorType | None = None,
        outline: ColorType | None = None,
        width: int = 1,
    ) -> "BuildImage": ...
    def draw_rounded_rectangle(
        self,
        xy: XYType,
        radius: int = 0,
        fill: ColorType | None = None,
        outline: ColorType | None = None,
        width: int = 1,
    ) -> "BuildImage": ...
    def draw_polygon(
        self,
        xy: list[PosTypeFloat],
        fill: ColorType | None = None,
        outline: ColorType | None = None,
        width: int = 1,
    ) -> "BuildImage": ...
    def draw_arc(
        self,
        xy: XYType,
        start: float,
        end: float,
        fill: ColorType | None = None,
        width: int = 1,
    ) -> "BuildImage": ...
    def draw_ellipse(
        self,
        xy: XYType,
        fill: ColorType | None = None,
        outline: ColorType | None = None,
        width: int = 1,
    ) -> "BuildImage": ...
    def draw_text(
        self,
        xy: PosTypeFloat | XYType,
        text: str,
        *,
        font_size: int = 16,
        max_fontsize: int = 30,
        min_fontsize: int = 12,
        allow_wrap: bool = False,
        font_style: FontStyle = "normal",
        fill: ColorType = "black",
        halign: HAlignType = "center",
        valign: VAlignType = "center",
        lines_align: HAlignType = "left",
        stroke_ratio: float = 0.02,
        stroke_fill: ColorType | None = None,
        font_families: list[str] = [],
        fallback_fonts_families: list[str] = [],
    ) -> "BuildImage": ...
    def draw_bbcode_text(
        self,
        xy: PosTypeFloat | XYType,
        text: str,
        *,
        font_size: int = 16,
        max_fontsize: int = 30,
        min_fontsize: int = 12,
        allow_wrap: bool = False,
        font_style: FontStyle = "normal",
        fill: ColorType = "black",
        halign: HAlignType = "center",
        valign: VAlignType = "center",
        lines_align: HAlignType = "left",
        stroke_ratio: float = 0.02,
        stroke_fill: ColorType | None = None,
        font_families: list[str] = [],
        fallback_fonts_families: list[str] = [],
    ) -> "BuildImage": ...
    def save(self, format: str, **params: object) -> BytesIO: ...
    def save_jpg(self, bg_color: ColorType = "white") -> BytesIO: ...
    def save_png(self) -> BytesIO: ...

class Text2Image: ...

def text2image(*args: object, **kwargs: object) -> BytesIO: ...

__all__ = ["BuildImage", "Text2Image", "text2image"]
