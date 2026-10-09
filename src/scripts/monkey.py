"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-25 01:58:04
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-25 15:57:07
Description: Gemini 写的 🐒 猴子补丁

# deadcode-keep: manual-entry | 需要彩色 emoji 时由启动流程手工调用；当前未接线

⚠️ 现状说明（deadcode 审计发现，待决策）：
- ``patch_pil()`` 全仓零调用，``git log -S patch_pil`` 显示**从未接线**；
- ``pilmoji`` 是 pyproject 声明依赖，但除本文件外全仓无人使用。

即：本模块与 ``pilmoji`` 依赖目前都是「有实现、无使用」。两条路——
1. 确定不用彩色 emoji → 删除本文件并移除 ``pilmoji`` 依赖（依赖瘦身）；
2. 确实要用 → 在 ``bot.py`` 启动流程里调用 ``patch_pil()`` 接上线。
决策前先标记保留，避免门禁红灯掩盖真实意图。
"""


# ruff: noqa
# pyright: reportAttributeAccessIssue=false
# pyright: reportPrivateUsage=false

from PIL import ImageDraw
from pilmoji import Pilmoji


def patch_pil() -> None:
    """
    全局劫持 PIL 的 ImageDraw 方法，为其注入彩色 Emoji 支持。
    自带防重复补丁与防无限递归机制。
    """
    if getattr(ImageDraw.ImageDraw, "_is_emoji_patched", False):
        return

    _orig_text = ImageDraw.ImageDraw.text
    _orig_textbbox = ImageDraw.ImageDraw.textbbox
    _orig_textlength = getattr(ImageDraw.ImageDraw, "textlength", None)

    BLACKLIST_KWARGS = [
        "stroke_width",
        "stroke_fill",
        "align",
        "direction",
        "features",
        "language",
        "embedded_color",
    ]

    def _patched_text(self, xy, text, *args, **kwargs):
        if getattr(self, "_pilmoji_bypass", False):
            return _orig_text(self, xy, text, *args, **kwargs)

        with Pilmoji(self._image) as pm:
            pm.draw._pilmoji_bypass = True
            return pm.text(xy, text, *args, **kwargs)

    def _patched_textlength(self, text, font=None, *args, **kwargs):
        if getattr(self, "_pilmoji_bypass", False):
            return _orig_textlength(self, text, font=font, *args, **kwargs)  # type: ignore

        safe_kwargs = kwargs.copy()
        for k in BLACKLIST_KWARGS:
            safe_kwargs.pop(k, None)

        with Pilmoji(self._image) as pm:
            pm.draw._pilmoji_bypass = True
            return pm.getsize(text, font=font, *args, **safe_kwargs)[0]  # type: ignore

    def _patched_textbbox(self, xy, text, font=None, anchor=None, *args, **kwargs):
        if getattr(self, "_pilmoji_bypass", False):
            return _orig_textbbox(
                self, xy, text, font=font, anchor=anchor, *args, **kwargs
            )

        orig_bbox = _orig_textbbox(
            self, xy, text, font=font, anchor=anchor, *args, **kwargs
        )

        safe_kwargs = kwargs.copy()
        for k in BLACKLIST_KWARGS:
            safe_kwargs.pop(k, None)

        with Pilmoji(self._image) as pm:
            pm.draw._pilmoji_bypass = True
            real_width, real_height = pm.getsize(text, font=font, *args, **safe_kwargs)  # type: ignore

        left, top, right, bottom = orig_bbox
        anchor = anchor or "la"
        h_anchor = anchor[0]

        if h_anchor == "l":
            right = left + real_width
        elif h_anchor == "r":
            left = right - real_width
        elif h_anchor == "m":
            center_x = (left + right) / 2
            left = center_x - real_width / 2
            right = center_x + real_width / 2
        else:
            right = left + real_width

        return (left, top, right, bottom)

    ImageDraw.ImageDraw.text = _patched_text
    ImageDraw.ImageDraw.textbbox = _patched_textbbox
    if _orig_textlength:
        ImageDraw.ImageDraw.textlength = _patched_textlength

    ImageDraw.ImageDraw._is_emoji_patched = True
