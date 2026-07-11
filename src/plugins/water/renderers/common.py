"""Shared helpers for water image rendering."""

from __future__ import annotations

import math

import arrow
from PIL import Image, ImageChops, ImageDraw
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
        return tr(
            "zh-CN", "water.image.exp.short.ten_thousand", value=f"{exp / 10000:.1f}"
        )
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
        texture.draw.line(
            (sx, h - 2, sx + stripe_span, 2), fill=stripe_color, width=stripe_width
        )
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
    card.draw.line(
        (start_x, top_y, end_x, top_y), fill=gloss, width=max(1, int(h * 0.03))
    )
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
    card.draw.line(
        (x, y + chart_h, x + w, y + chart_h),
        fill=axis_color,
        width=max(1, int(1 * scale)),
    )
    for hour, count in enumerate(hourly):
        bx = x + hour * (bar_w + bar_gap)
        bh = (
            max(2, int((count / max_count) * (chart_h - int(6 * scale))))
            if count > 0
            else 2
        )
        by = y + chart_h - bh
        fill = (
            bar_color
            if hour != max(range(24), key=lambda idx: hourly[idx])
            else WATER_THEME.overview_highlight_bar
        )
        card.draw_rounded_rectangle(
            (bx, by, bx + bar_w, y + chart_h), radius=max(2, int(3 * scale)), fill=fill
        )
        if hour in {0, 6, 12, 18, 23}:
            card.draw_text(
                (
                    bx - int(6 * scale),
                    y + chart_h + int(4 * scale),
                    bx + bar_w + int(6 * scale),
                    y + h,
                ),
                f"{hour:02d}",
                max_fontsize=int(9 * scale),
                min_fontsize=int(7 * scale),
                fill=label_color,
                halign="center",
                font_families=[SYS_FONT_NAME],
            )


def draw_donut_chart(
    card: BuildImage,
    *,
    x: int,
    y: int,
    size: int,
    ratio: float,
    primary_color: str,
    secondary_color: str,
    inner_color: str,
    ring_width: int,
) -> None:
    clamped_ratio = max(0.0, min(1.0, ratio))
    bbox = (x, y, x + size, y + size)
    card.draw.ellipse(bbox, fill=secondary_color)
    if clamped_ratio > 0:
        card.draw.pieslice(
            bbox,
            start=-90,
            end=-90 + int(360 * clamped_ratio),
            fill=primary_color,
        )
    inner_inset = max(4, ring_width)
    inner_bbox = (
        x + inner_inset,
        y + inner_inset,
        x + size - inner_inset,
        y + size - inner_inset,
    )
    card.draw.ellipse(inner_bbox, fill=inner_color)


def draw_pie_chart(
    card: BuildImage,
    *,
    x: int,
    y: int,
    size: int,
    ratios: list[float],
    colors: list[str],
    highlight_index: int | None = None,
    highlight_offset: int = 0,
) -> None:
    bbox = (x, y, x + size, y + size)
    start_angle = -90.0
    for index, ratio in enumerate(ratios):
        span = max(0.0, min(1.0, ratio)) * 360.0
        if span <= 0:
            continue
        draw_bbox = bbox
        if (
            highlight_index is not None
            and index == highlight_index
            and highlight_offset > 0
        ):
            mid_angle = start_angle + span / 2
            dx = round(math.cos(math.radians(mid_angle)) * highlight_offset)
            dy = round(math.sin(math.radians(mid_angle)) * highlight_offset)
            draw_bbox = (x + dx, y + dy, x + size + dx, y + size + dy)
        card.draw.pieslice(
            draw_bbox,
            start=start_angle,
            end=start_angle + span,
            fill=colors[index % len(colors)],
        )
        start_angle += span


def draw_dual_hourly_trend(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    current_hourly_counts: list[int],
    previous_hourly_counts: list[int],
    current_color: str,
    previous_color: str,
    axis_color: str,
    label_color: str,
    scale: float,
) -> None:
    current_hourly = safe_hourly_counts(current_hourly_counts)
    previous_hourly = safe_hourly_counts(previous_hourly_counts)
    chart_h = h - int(20 * scale)
    baseline_y = y + chart_h
    max_count = max([*current_hourly, *previous_hourly]) or 1

    card.draw.line(
        (x, baseline_y, x + w, baseline_y),
        fill=axis_color,
        width=max(1, int(1 * scale)),
    )
    grid_step = max(1, int(chart_h / 3))
    for idx in range(1, 3):
        grid_y = baseline_y - idx * grid_step
        card.draw.line(
            (x, grid_y, x + w, grid_y),
            fill=mix_hex(axis_color, WATER_THEME.white, 0.25),
            width=1,
        )

    def _points(hourly: list[int]) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        for hour, count in enumerate(hourly):
            px = x + int((w - 1) * hour / 23) if hour < 23 else x + w - 1
            py = baseline_y - int(
                (count / max_count) * max(2, chart_h - int(8 * scale))
            )
            points.append((px, py))
        return points

    previous_points = _points(previous_hourly)
    current_points = _points(current_hourly)
    card.draw.line(
        previous_points,
        fill=previous_color,
        width=max(2, int(2 * scale)),
        joint="curve",
    )
    card.draw.line(
        current_points,
        fill=current_color,
        width=max(2, int(3 * scale)),
        joint="curve",
    )
    dot_r = max(2, int(2 * scale))
    for points, fill in (
        (previous_points, previous_color),
        (current_points, current_color),
    ):
        for hour in (0, 6, 12, 18, 23):
            px, py = points[hour]
            card.draw.ellipse(
                (px - dot_r, py - dot_r, px + dot_r, py + dot_r),
                fill=fill,
            )
            card.draw_text(
                (
                    px - int(8 * scale),
                    baseline_y + int(4 * scale),
                    px + int(8 * scale),
                    y + h,
                ),
                f"{hour:02d}",
                max_fontsize=int(8 * scale),
                min_fontsize=int(6 * scale),
                fill=label_color,
                halign="center",
                font_families=[SYS_FONT_NAME],
            )


def draw_group_rank_trend_chart(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    labels: list[str],
    series: list[tuple[str, list[int | None], bool]],
    axis_color: str,
    label_color: str,
    scale: float,
) -> None:
    if not labels or not series:
        return
    label_band_h = int(20 * scale)
    chart_h = h - label_band_h
    baseline_y = y + chart_h
    plot_top_pad = int(14 * scale)
    plot_bottom_pad = int(18 * scale)
    usable_h = max(16, chart_h - plot_top_pad - plot_bottom_pad)
    all_ranks = [
        rank for _name, ranks, _focus in series for rank in ranks if rank is not None
    ]
    max_rank = max(all_ranks) if all_ranks else 1
    min_rank = min(all_ranks) if all_ranks else 1
    if min_rank == max_rank:
        max_rank += 1

    card.draw.line(
        (x, baseline_y, x + w, baseline_y),
        fill=axis_color,
        width=max(1, int(1 * scale)),
    )
    left_axis_x = x + int(20 * scale)
    card.draw.line(
        (left_axis_x, y, left_axis_x, baseline_y),
        fill=axis_color,
        width=max(1, int(1 * scale)),
    )
    tick_values = sorted({1, min_rank, (min_rank + max_rank) // 2, max_rank})
    for tick in tick_values:
        relative = (
            0.0 if max_rank == min_rank else (tick - min_rank) / (max_rank - min_rank)
        )
        tick_y = y + plot_top_pad + int(relative * usable_h)
        card.draw.line(
            (left_axis_x, tick_y, x + w, tick_y),
            fill=mix_hex(axis_color, WATER_THEME.white, 0.22),
            width=1,
        )
        card.draw_text(
            (
                x,
                tick_y - int(6 * scale),
                left_axis_x - int(4 * scale),
                tick_y + int(6 * scale),
            ),
            str(tick),
            max_fontsize=int(8 * scale),
            min_fontsize=int(6 * scale),
            fill=label_color,
            halign="right",
            valign="center",
            font_families=[SYS_FONT_NAME],
        )

    plot_x = left_axis_x + int(10 * scale)
    plot_w = w - (plot_x - x)
    label_indexes = sorted(
        {0, len(labels) // 3, (len(labels) * 2) // 3, len(labels) - 1}
    )
    for idx in label_indexes:
        px = plot_x + int((plot_w - 1) * idx / max(1, len(labels) - 1))
        card.draw_text(
            (
                px - int(20 * scale),
                baseline_y + int(4 * scale),
                px + int(20 * scale),
                y + h - int(2 * scale),
            ),
            labels[idx],
            max_fontsize=int(8 * scale),
            min_fontsize=int(6 * scale),
            fill=label_color,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )

    def _smooth_segment(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if len(points) < 3:
            return points
        samples_per_segment = max(8, int(6 * scale))
        padded = [points[0], *points, points[-1]]
        smoothed = [points[0]]
        for idx in range(1, len(padded) - 2):
            p0, p1, p2, p3 = (
                padded[idx - 1],
                padded[idx],
                padded[idx + 1],
                padded[idx + 2],
            )
            for step in range(1, samples_per_segment + 1):
                t = step / samples_per_segment
                tt = t * t
                ttt = tt * t
                px = 0.5 * (
                    (2 * p1[0])
                    + (-p0[0] + p2[0]) * t
                    + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * tt
                    + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * ttt
                )
                py = 0.5 * (
                    (2 * p1[1])
                    + (-p0[1] + p2[1]) * t
                    + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * tt
                    + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * ttt
                )
                smoothed.append((round(px), round(py)))
        return smoothed

    for color, ranks, is_focus in series:
        points: list[tuple[int, int]] = []
        for idx, rank in enumerate(ranks):
            if rank is None:
                if len(points) >= 2:
                    smooth_points = _smooth_segment(points)
                    card.draw.line(
                        smooth_points,
                        fill=mix_hex(color, WATER_THEME.white, 0.48)
                        if is_focus
                        else mix_hex(color, WATER_THEME.white, 0.5),
                        width=max(4, int((7 if is_focus else 5) * scale / 2)),
                        joint="curve",
                    )
                    card.draw.line(
                        smooth_points,
                        fill=color,
                        width=max(3, int((5 if is_focus else 3) * scale / 2)),
                        joint="curve",
                    )
                points = []
                continue
            px = plot_x + int((plot_w - 1) * idx / max(1, len(labels) - 1))
            relative = (
                0.0
                if max_rank == min_rank
                else (rank - min_rank) / (max_rank - min_rank)
            )
            py = y + plot_top_pad + int(relative * usable_h)
            points.append((px, py))
        if len(points) >= 2:
            smooth_points = _smooth_segment(points)
            card.draw.line(
                smooth_points,
                fill=mix_hex(color, WATER_THEME.white, 0.48)
                if is_focus
                else mix_hex(color, WATER_THEME.white, 0.5),
                width=max(4, int((7 if is_focus else 5) * scale / 2)),
                joint="curve",
            )
            card.draw.line(
                smooth_points,
                fill=color,
                width=max(3, int((5 if is_focus else 3) * scale / 2)),
                joint="curve",
            )
            if is_focus:
                end_x, end_y = smooth_points[-1]
                halo_r = max(4, int(5 * scale))
                dot_r = max(3, int(3 * scale))
                card.draw.ellipse(
                    (
                        end_x - halo_r,
                        end_y - halo_r,
                        end_x + halo_r,
                        end_y + halo_r,
                    ),
                    fill=mix_hex(color, WATER_THEME.white, 0.55),
                )
                card.draw.ellipse(
                    (
                        end_x - dot_r,
                        end_y - dot_r,
                        end_x + dot_r,
                        end_y + dot_r,
                    ),
                    fill=color,
                )
