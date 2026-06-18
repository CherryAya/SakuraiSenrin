"""Shared helpers for water image rendering."""

from __future__ import annotations

from math import ceil
from typing import Any

import arrow
from PIL import Image, ImageChops, ImageDraw, ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_NAME
from src.lib.demo_theme import SENRIN_V3_WATER_IMAGE_THEME, WaterImageTheme
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time

SYS_FONT_NAME = MAPLE_FONT_NAME
WATER_THEME = SENRIN_V3_WATER_IMAGE_THEME


def water_podium_themes(
    theme: WaterImageTheme = WATER_THEME,
) -> dict[int, tuple[str, str, str]]:
    return {
        1: (theme.podium_gold_bg, theme.podium_gold_badge, theme.podium_badge_text),
        2: (
            theme.podium_silver_bg,
            theme.podium_silver_badge,
            theme.podium_badge_text,
        ),
        3: (
            theme.podium_bronze_bg,
            theme.podium_bronze_badge,
            theme.podium_badge_text,
        ),
    }


def format_rank(rank: int | None, locale: LocaleCode = "zh-CN") -> str:
    if rank is None:
        return "-"
    return tr(locale, "water.image.rank_format", rank=rank)


def short_exp(exp: int | str) -> str:
    if isinstance(exp, str):
        return exp
    if exp >= 100000000:
        return tr(
            "zh-CN",
            "water.image.exp.short.hundred_million",
            value=f"{exp / 100000000:.1f}",
        )
    if exp >= 10000:
        return tr("zh-CN", "water.image.exp.short.ten_thousand", value=f"{exp / 10000:.1f}")
    return str(exp)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    val = value.lstrip("#")
    if len(val) != 6:
        return (0, 0, 0)
    return (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))


def mix_hex(base: str, target: str, ratio: float) -> str:
    r = max(0.0, min(1.0, ratio))
    br, bg, bb = hex_to_rgb(base)
    tr, tg, tb = hex_to_rgb(target)
    rr = int(br + (tr - br) * r)
    rg = int(bg + (tg - bg) * r)
    rb = int(bb + (tb - bb) * r)
    return f"#{rr:02X}{rg:02X}{rb:02X}"


def format_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def format_trend(trend: int | None) -> tuple[str, str]:
    if trend is None:
        return ("NEW", WATER_THEME.trend_new)
    if trend > 0:
        return (f"↑{trend}", WATER_THEME.trend_up)
    if trend < 0:
        return (f"↓{abs(trend)}", WATER_THEME.trend_down)
    return ("-0", WATER_THEME.trend_flat)


def safe_hourly_counts(hourly_counts: list[int]) -> list[int]:
    if len(hourly_counts) >= 24:
        return [int(item) for item in hourly_counts[:24]]
    return [*map(int, hourly_counts), *([0] * (24 - len(hourly_counts)))]


def build_avatar_fallback(size: int, label: str, bg: str, fg: str) -> BuildImage:
    avatar = BuildImage.new("RGBA", (size, size), (0, 0, 0, 0))
    avatar.draw.ellipse((0, 0, size, size), fill=bg)
    avatar.draw_text(
        (0, 0, size, size),
        label,
        max_fontsize=max(14, int(size * 0.42)),
        min_fontsize=max(10, int(size * 0.28)),
        fill=fg,
        halign="center",
        valign="center",
        font_families=[SYS_FONT_NAME],
    )
    return avatar


def draw_report_footer(
    card: BuildImage,
    *,
    locale: LocaleCode,
    generated_at: int,
    footer_text: str,
    width: int,
    pad: int,
    top: int,
    footer_h: int,
    scale: float,
    copyright_color: str,
    time_color: str,
    footer_color: str,
) -> None:
    footer_time = (
        arrow.get(generated_at).to("Asia/Shanghai")
        if generated_at > 0
        else arrow.get(get_current_time()).to("Asia/Shanghai")
    )
    copyright_bottom = top + int(14 * scale)
    generated_at_bottom = top + int(28 * scale)
    card.draw_text(
        (0, top, width, copyright_bottom),
        f"© 2020-{footer_time.year} SakuraiSenrin",
        max_fontsize=int(10 * scale),
        min_fontsize=int(8 * scale),
        fill=copyright_color,
        halign="center",
        valign="center",
        font_families=[SYS_FONT_NAME],
    )
    card.draw_text(
        (pad, copyright_bottom, width - pad, generated_at_bottom),
        tr(
            locale,
            "water.image.generated_at",
            time=footer_time.format("YYYY-MM-DD HH:mm:ss"),
        ),
        max_fontsize=int(10 * scale),
        min_fontsize=int(8 * scale),
        fill=time_color,
        halign="left",
        valign="center",
        font_families=[SYS_FONT_NAME],
    )
    card.draw_text(
        (0, generated_at_bottom, width, top + footer_h),
        footer_text,
        max_fontsize=int(11 * scale),
        min_fontsize=int(8 * scale),
        fill=footer_color,
        halign="center",
        valign="center",
        font_families=[SYS_FONT_NAME],
    )


def draw_progress_bar(
    card: BuildImage,
    x: int,
    y: int,
    w: int,
    h: int,
    progress: float,
    bg: str,
    fg: str,
) -> None:
    radius = max(8, h // 2)
    card.draw_rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=bg)
    fill_w = int(max(0.0, min(1.0, progress)) * w)
    if fill_w <= 0:
        return
    fill_right = x + fill_w
    card.draw_rounded_rectangle((x, y, fill_right, y + h), radius=radius, fill=fg)
    texture = BuildImage.new("RGBA", (fill_w, h), (0, 0, 0, 0))
    stripe_color = mix_hex(fg, WATER_THEME.white, 0.52)
    stripe_step = max(7, int(h * 0.55))
    stripe_span = max(10, int(h * 1.3))
    stripe_width = max(1, int(h * 0.22))
    for sx in range(-h, fill_w + h, stripe_step):
        texture.draw.line((sx, h - 2, sx + stripe_span, 2), fill=stripe_color, width=stripe_width)
    bubble_color = mix_hex(fg, WATER_THEME.white, 0.72)
    bubble_r = max(2, int(h * 0.2))
    bubble_y = h // 2
    for offset in (int(h * 0.8), int(h * 1.8)):
        bubble_x = fill_w - offset
        if bubble_x > bubble_r:
            texture.draw.ellipse(
                (
                    bubble_x - bubble_r,
                    bubble_y - bubble_r,
                    bubble_x + bubble_r,
                    bubble_y + bubble_r,
                ),
                fill=bubble_color,
            )
    texture_mask = Image.new("L", (fill_w, h), 0)
    mask_draw = ImageDraw.Draw(texture_mask)
    mask_draw.rounded_rectangle((0, 0, fill_w, h), radius=radius, fill=255)
    raw_alpha = texture.image.split()[-1]
    clipped_alpha = ImageChops.multiply(raw_alpha, texture_mask)
    texture.image.putalpha(clipped_alpha)
    card.paste(texture, (x, y), alpha=True)


def draw_gloss_lines(
    card: BuildImage,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    tone: str = WATER_THEME.white,
    strength: float = 0.7,
) -> None:
    gloss = mix_hex(tone, WATER_THEME.white, strength)
    top_y = y + max(2, int(h * 0.13))
    mid_y = y + max(4, int(h * 0.23))
    start_x = x + max(8, int(w * 0.05))
    end_x = x + w - max(8, int(w * 0.08))
    card.draw.line((start_x, top_y, end_x, top_y), fill=gloss, width=max(1, int(h * 0.03)))
    card.draw.line(
        (start_x + int(w * 0.04), mid_y, end_x - int(w * 0.08), mid_y),
        fill=gloss,
        width=max(1, int(h * 0.02)),
    )


def draw_hourly_histogram(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    hourly_counts: list[int],
    bar_color: str,
    axis_color: str,
    label_color: str,
    scale: float,
) -> None:
    hourly = safe_hourly_counts(hourly_counts)
    chart_h = h - int(18 * scale)
    max_count = max(hourly) or 1
    bar_gap = max(2, int(2 * scale))
    bar_w = max(4, int((w - bar_gap * 23) / 24))
    card.draw.line((x, y + chart_h, x + w, y + chart_h), fill=axis_color, width=max(1, int(1 * scale)))
    for hour, count in enumerate(hourly):
        bx = x + hour * (bar_w + bar_gap)
        bh = max(2, int((count / max_count) * (chart_h - int(6 * scale)))) if count > 0 else 2
        by = y + chart_h - bh
        fill = bar_color if hour != max(range(24), key=lambda idx: hourly[idx]) else WATER_THEME.overview_highlight_bar
        card.draw_rounded_rectangle((bx, by, bx + bar_w, y + chart_h), radius=max(2, int(3 * scale)), fill=fill)
        if hour in {0, 6, 12, 18, 23}:
            card.draw_text(
                (bx - int(6 * scale), y + chart_h + int(4 * scale), bx + bar_w + int(6 * scale), y + h),
                f"{hour:02d}",
                max_fontsize=int(9 * scale),
                min_fontsize=int(7 * scale),
                fill=label_color,
                halign="center",
                font_families=[SYS_FONT_NAME],
            )
