"""Water group report and period report image renderers."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from PIL import ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger

from .common import (
    SYS_FONT_NAME,
    WATER_THEME,
    build_avatar_fallback,
    draw_group_rank_trend_chart,
    draw_hourly_histogram,
    draw_pie_chart,
    draw_report_footer,
    format_delta,
    format_trend,
    mix_hex,
    short_exp,
)
from .models import (
    WaterGroupReportImageData,
    WaterGroupShareSlice,
    WaterPeriodRankCardData,
)
from .rank import WaterRankRenderer
from .report_layout import (
    compute_group_report_right_extra_height,
    estimate_group_rank_card_height,
    estimate_group_report_left_height,
)

FALLBACK_FONT_PATH = MAPLE_FONT_PATH
FontLike = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _load_font(size: int) -> FontLike:
    try:
        return ImageFont.truetype(FALLBACK_FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def _pixel_text_width(text: str, font: FontLike) -> int:
    if not text:
        return 0
    bbox = font.getbbox(text)
    return max(0, int(bbox[2] - bbox[0]))


def _truncate_text_to_width_pixels(
    text: str,
    *,
    font: FontLike,
    max_width: int,
    ellipsis: str = "...",
) -> str:
    normalized = text.replace("\n", " ").replace("\r", "").replace("\t", " ").strip()
    if not normalized:
        return ""
    if _pixel_text_width(normalized, font) <= max_width:
        return normalized

    ellipsis_width = _pixel_text_width(ellipsis, font)
    if ellipsis_width >= max_width:
        return ellipsis

    fitted: list[str] = []
    current_width = 0
    for char in normalized:
        char_width = _pixel_text_width(char, font)
        if fitted and current_width + char_width + ellipsis_width > max_width:
            break
        if not fitted and char_width + ellipsis_width > max_width:
            return ellipsis
        fitted.append(char)
        current_width += char_width

    candidate = "".join(fitted).rstrip()
    while candidate and _pixel_text_width(f"{candidate}{ellipsis}", font) > max_width:
        candidate = candidate[:-1].rstrip()
    return f"{candidate}{ellipsis}" if candidate else ellipsis


def _group_rank_row_fill(
    *,
    is_focus_group: bool,
    base_fill: str,
    accent: str,
) -> str:
    if not is_focus_group:
        return base_fill
    return mix_hex(base_fill, accent, 0.16)


def _group_share_palette(theme: Any) -> tuple[str, ...]:
    return (
        theme.blue,
        theme.mint,
        mix_hex(theme.accent, theme.blue, 0.42),
        mix_hex(theme.accent, theme.mint, 0.42),
        mix_hex(theme.deep, theme.blue, 0.18),
        mix_hex(theme.deep, theme.mint, 0.22),
        mix_hex(theme.podium_gold_badge, theme.blue, 0.35),
        mix_hex(theme.podium_gold_badge, theme.mint, 0.35),
    )


def _group_trend_palette(theme: Any) -> tuple[str, ...]:
    return (
        theme.blue,
        theme.mint,
        mix_hex(theme.accent, theme.blue, 0.25),
        mix_hex(theme.accent, theme.mint, 0.3),
        mix_hex(theme.deep, theme.blue, 0.1),
        mix_hex(theme.deep, theme.mint, 0.14),
        theme.trend_flat,
    )


def _pick_group_share_legend_items(
    slices: list[WaterGroupShareSlice],
    *,
    limit: int,
) -> list[WaterGroupShareSlice]:
    if not slices or limit <= 0:
        return []
    picked = list(slices[:limit])
    focus_slice = next((item for item in slices if item.is_focus_group), None)
    if focus_slice is None or any(
        item.group_id == focus_slice.group_id for item in picked
    ):
        return picked
    if len(picked) >= limit:
        picked[-1] = focus_slice
        return picked
    picked.append(focus_slice)
    return picked


def _render_compact_group_rank_insights(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    data: WaterGroupReportImageData,
    locale: LocaleCode,
    theme: Any,
    scale: float,
    deep: str,
    accent: str,
    strong: str,
) -> None:
    if not data.group_rank_insights:
        return
    card.draw_rounded_rectangle(
        (x, y, x + w, y + h), radius=int(18 * scale), fill=theme.panel_soft_bg
    )
    card.draw_text(
        (
            x + int(18 * scale),
            y + int(8 * scale),
            x + w - int(18 * scale),
            y + int(30 * scale),
        ),
        tr(locale, "water.report.group_rank.insight.title"),
        max_fontsize=int(16 * scale),
        min_fontsize=int(11 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    tile_gap_x = int(10 * scale)
    tile_gap_y = int(8 * scale)
    tile_w = (w - int(36 * scale) - tile_gap_x) // 2
    tile_h = max(int(42 * scale), (h - int(58 * scale) - tile_gap_y) // 2)
    tile_y_base = y + int(36 * scale)
    for idx, insight in enumerate(data.group_rank_insights[:4]):
        row = idx // 2
        col = idx % 2
        tile_x = x + int(18 * scale) + col * (tile_w + tile_gap_x)
        tile_y = tile_y_base + row * (tile_h + tile_gap_y)
        card.draw_rounded_rectangle(
            (tile_x, tile_y, tile_x + tile_w, tile_y + tile_h),
            radius=int(12 * scale),
            fill=theme.rank_row_fill,
        )
        card.draw_text(
            (
                tile_x + int(10 * scale),
                tile_y + int(8 * scale),
                tile_x + tile_w - int(10 * scale),
                tile_y + int(22 * scale),
            ),
            insight.label,
            max_fontsize=int(10 * scale),
            min_fontsize=int(7 * scale),
            fill=accent,
            halign="left",
            font_families=[WATER_THEME.white],
        )
        card.draw_text(
            (
                tile_x + int(10 * scale),
                tile_y + int(18 * scale),
                tile_x + tile_w - int(10 * scale),
                tile_y + tile_h - int(6 * scale),
            ),
            insight.value,
            max_fontsize=int(17 * scale),
            min_fontsize=int(10 * scale),
            fill=strong,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )


def _render_group_rank_share_panel(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    data: WaterGroupReportImageData,
    locale: LocaleCode,
    theme: Any,
    scale: float,
    deep: str,
    accent: str,
    strong: str,
    hint: str,
) -> None:
    slices = data.group_share_slices
    card.draw_rounded_rectangle(
        (x, y, x + w, y + h), radius=int(18 * scale), fill=theme.panel_soft_bg
    )
    card.draw_text(
        (
            x + int(18 * scale),
            y + int(8 * scale),
            x + w - int(18 * scale),
            y + int(30 * scale),
        ),
        tr(locale, "water.report.group_rank.analysis.title"),
        max_fontsize=int(16 * scale),
        min_fontsize=int(11 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    if not slices:
        return

    focus_slice = next((item for item in slices if item.is_focus_group), slices[0])
    total_count = sum(item.msg_count for item in slices)
    card.draw_text(
        (
            x + int(18 * scale),
            y + int(30 * scale),
            x + w - int(18 * scale),
            y + int(50 * scale),
        ),
        tr(
            locale,
            "water.report.group_rank.analysis.subtitle",
            share=f"{focus_slice.share_ratio * 100:.1f}%",
            total_groups=len(slices),
        ),
        max_fontsize=int(10 * scale),
        min_fontsize=int(8 * scale),
        fill=accent,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    pie_size = min(max(int(96 * scale), h - int(92 * scale)), int(w * 0.32))
    pie_x = x + int(18 * scale)
    pie_y = y + max(int(54 * scale), (h - pie_size) // 2)
    palette = _group_share_palette(theme)
    highlight_index = next(
        (index for index, item in enumerate(slices) if item.is_focus_group),
        None,
    )
    slice_colors: list[str] = []
    palette_index = 0
    for item in slices:
        if item.is_focus_group:
            slice_colors.append(theme.podium_gold_badge)
            continue
        slice_colors.append(palette[palette_index % len(palette)])
        palette_index += 1
    draw_pie_chart(
        card,
        x=pie_x,
        y=pie_y,
        size=pie_size,
        ratios=[item.share_ratio for item in slices],
        colors=slice_colors,
        highlight_index=highlight_index,
        highlight_offset=int(4 * scale),
    )
    card.draw.ellipse(
        (pie_x, pie_y, pie_x + pie_size, pie_y + pie_size),
        outline=mix_hex(theme.line, theme.white, 0.3),
        width=max(1, int(2 * scale)),
    )

    metric_x = pie_x + pie_size + int(20 * scale)
    metric_w = x + w - metric_x - int(18 * scale)
    card.draw_text(
        (
            metric_x,
            y + int(46 * scale),
            metric_x + metric_w,
            y + int(80 * scale),
        ),
        f"{focus_slice.share_ratio * 100:.1f}%",
        max_fontsize=int(26 * scale),
        min_fontsize=int(16 * scale),
        fill=strong,
        halign="left",
        valign="center",
        font_families=[WATER_THEME.white],
    )
    card.draw_text(
        (
            metric_x,
            y + int(80 * scale),
            metric_x + metric_w,
            y + int(98 * scale),
        ),
        tr(locale, "water.report.group_rank.insight.share"),
        max_fontsize=int(9 * scale),
        min_fontsize=int(7 * scale),
        fill=hint,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    card.draw_text(
        (
            metric_x,
            y + int(98 * scale),
            metric_x + metric_w,
            y + int(114 * scale),
        ),
        tr(
            locale,
            "water.report.group_rank.analysis.detail",
            focus=short_exp(focus_slice.msg_count),
            total=short_exp(total_count),
        ),
        max_fontsize=int(9 * scale),
        min_fontsize=int(7 * scale),
        fill=hint,
        halign="left",
        font_families=[WATER_THEME.white],
    )

    if h < int(160 * scale):
        legend_limit = 2
    elif h < int(210 * scale):
        legend_limit = 3
    else:
        legend_limit = 4
    legend_items = _pick_group_share_legend_items(slices, limit=legend_limit)
    legend_rank_map = {item.group_id: idx + 1 for idx, item in enumerate(slices)}
    legend_y = y + int(118 * scale)
    legend_gap = int(8 * scale)
    legend_h = max(
        int(34 * scale),
        min(
            int(42 * scale),
            (y + h - legend_y - int(14 * scale) - legend_gap * (len(legend_items) - 1))
            // max(len(legend_items), 1),
        ),
    )
    ratio_font = _load_font(int(10 * scale))
    name_font = _load_font(int(10 * scale))
    for idx, item in enumerate(legend_items):
        item_y = legend_y + idx * (legend_h + legend_gap)
        fill = (
            mix_hex(theme.rank_row_fill, theme.podium_gold_badge, 0.16)
            if item.is_focus_group
            else theme.rank_row_fill
        )
        card.draw_rounded_rectangle(
            (metric_x, item_y, metric_x + metric_w, item_y + legend_h),
            radius=int(12 * scale),
            fill=fill,
        )
        dot_x = metric_x + int(10 * scale)
        dot_y = item_y + int(10 * scale)
        item_color = slice_colors[legend_rank_map[item.group_id] - 1]
        dot_size = int(10 * scale)
        card.draw.ellipse(
            (dot_x, dot_y, dot_x + dot_size, dot_y + dot_size),
            fill=item_color,
        )
        rank_text = f"#{legend_rank_map[item.group_id]}"
        card.draw_text(
            (
                dot_x + dot_size + int(8 * scale),
                item_y + int(5 * scale),
                dot_x + dot_size + int(42 * scale),
                item_y + legend_h - int(4 * scale),
            ),
            rank_text,
            max_fontsize=int(10 * scale),
            min_fontsize=int(7 * scale),
            fill=accent,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        ratio_text = f"{item.share_ratio * 100:.1f}%"
        ratio_width = max(
            int(64 * scale),
            _pixel_text_width(ratio_text, ratio_font) + int(12 * scale),
        )
        ratio_left = metric_x + metric_w - ratio_width - int(10 * scale)
        badge_right = ratio_left - int(6 * scale)
        badge_w = int(48 * scale) if item.is_focus_group else 0
        name_left = dot_x + dot_size + int(48 * scale)
        name_right = max(
            name_left + int(42 * scale),
            badge_right - (badge_w + (int(6 * scale) if badge_w else 0)),
        )
        name_text = _truncate_text_to_width_pixels(
            item.display_name,
            font=name_font,
            max_width=max(int(50 * scale), name_right - name_left),
        )
        card.draw_text(
            (
                name_left,
                item_y + int(5 * scale),
                name_right,
                item_y + legend_h - int(4 * scale),
            ),
            name_text,
            max_fontsize=int(10 * scale),
            min_fontsize=int(7 * scale),
            fill=deep,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        if item.is_focus_group:
            badge_left = badge_right - badge_w
            card.draw_rounded_rectangle(
                (
                    badge_left,
                    item_y + int(7 * scale),
                    badge_right,
                    item_y + legend_h - int(7 * scale),
                ),
                radius=int(8 * scale),
                fill=mix_hex(theme.podium_gold_badge, theme.white, 0.7),
            )
            card.draw_text(
                (
                    badge_left + int(4 * scale),
                    item_y + int(7 * scale),
                    badge_right - int(4 * scale),
                    item_y + legend_h - int(7 * scale),
                ),
                tr(locale, "water.report.group_rank.focus_badge"),
                max_fontsize=int(8 * scale),
                min_fontsize=int(6 * scale),
                fill=theme.deep,
                halign="center",
                valign="center",
                font_families=[WATER_THEME.white],
            )
        card.draw_text(
            (
                ratio_left,
                item_y + int(5 * scale),
                metric_x + metric_w - int(10 * scale),
                item_y + legend_h - int(4 * scale),
            ),
            ratio_text,
            max_fontsize=int(10 * scale),
            min_fontsize=int(7 * scale),
            fill=strong,
            halign="right",
            valign="center",
            font_families=[WATER_THEME.white],
        )


def _render_group_rank_trend_panel(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    data: WaterGroupReportImageData,
    locale: LocaleCode,
    theme: Any,
    scale: float,
    deep: str,
    accent: str,
    hint: str,
) -> None:
    trend_series = data.group_rank_trend_series
    card.draw_rounded_rectangle(
        (x, y, x + w, y + h), radius=int(18 * scale), fill=theme.panel_bg
    )
    card.draw_text(
        (
            x + int(18 * scale),
            y + int(8 * scale),
            x + w - int(18 * scale),
            y + int(30 * scale),
        ),
        tr(locale, "water.report.group_rank.trend.title"),
        max_fontsize=int(16 * scale),
        min_fontsize=int(11 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    if not data.group_rank_trend_labels or not trend_series:
        return

    card.draw_text(
        (
            x + int(18 * scale),
            y + int(30 * scale),
            x + w - int(18 * scale),
            y + int(50 * scale),
        ),
        tr(
            locale,
            "water.report.group_rank.trend.subtitle",
            days=len(data.group_rank_trend_labels),
            radius=max(0, len(trend_series) // 2),
        ),
        max_fontsize=int(10 * scale),
        min_fontsize=int(8 * scale),
        fill=accent,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    palette = _group_trend_palette(theme)
    rendered_series: list[tuple[str, list[int | None], bool]] = []
    legend_specs: list[tuple[str, str, bool]] = []
    palette_index = 0
    for item in trend_series:
        color = (
            theme.podium_gold_badge
            if item.is_focus_group
            else mix_hex(palette[palette_index % len(palette)], theme.white, 0.08)
        )
        if not item.is_focus_group:
            palette_index += 1
        rendered_series.append((color, item.ranks, item.is_focus_group))
        legend_specs.append((color, item.display_name, item.is_focus_group))

    legend_x = x + int(18 * scale)
    legend_y = y + int(54 * scale)
    legend_w = w - int(36 * scale)
    cols = 3 if len(legend_specs) >= 7 else 2
    rows = max(1, (len(legend_specs) + cols - 1) // cols)
    legend_gap_x = int(8 * scale)
    legend_gap_y = int(8 * scale)
    legend_item_h = int(28 * scale)
    legend_item_w = (legend_w - legend_gap_x * (cols - 1)) // max(cols, 1)
    legend_font = _load_font(int(9 * scale))
    for index, (color, label, is_focus_group) in enumerate(legend_specs):
        row = index // cols
        col = index % cols
        item_x = legend_x + col * (legend_item_w + legend_gap_x)
        item_y = legend_y + row * (legend_item_h + legend_gap_y)
        card.draw_rounded_rectangle(
            (
                item_x,
                item_y,
                item_x + legend_item_w,
                item_y + legend_item_h,
            ),
            radius=int(10 * scale),
            fill=(
                mix_hex(theme.panel_soft_bg, theme.podium_gold_badge, 0.1)
                if is_focus_group
                else theme.rank_row_fill
            ),
        )
        dot_size = int(10 * scale)
        dot_x = item_x + int(8 * scale)
        dot_y = item_y + (legend_item_h - dot_size) // 2
        card.draw.ellipse(
            (dot_x, dot_y, dot_x + dot_size, dot_y + dot_size),
            fill=color,
        )
        label_left = dot_x + dot_size + int(8 * scale)
        label_right = item_x + legend_item_w - int(8 * scale)
        label_text = _truncate_text_to_width_pixels(
            label,
            font=legend_font,
            max_width=max(int(40 * scale), label_right - label_left),
        )
        card.draw_text(
            (
                label_left,
                item_y + int(2 * scale),
                label_right,
                item_y + legend_item_h - int(2 * scale),
            ),
            label_text,
            max_fontsize=int(9 * scale),
            min_fontsize=int(7 * scale),
            fill=deep if is_focus_group else accent,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )

    chart_y = (
        legend_y
        + rows * legend_item_h
        + max(0, rows - 1) * legend_gap_y
        + int(10 * scale)
    )
    chart_h_available = max(int(96 * scale), y + h - chart_y - int(18 * scale))
    chart_h = max(
        int(92 * scale),
        min(chart_h_available, int(chart_h_available * 0.78)),
    )
    draw_group_rank_trend_chart(
        card,
        x=x + int(18 * scale),
        y=chart_y,
        w=w - int(36 * scale),
        h=chart_h,
        labels=data.group_rank_trend_labels,
        series=rendered_series,
        axis_color=theme.line,
        label_color=hint,
        scale=scale,
    )


async def build_water_group_report_image(
    data: WaterGroupReportImageData,
    locale: LocaleCode,
) -> bytes | None:
    started = perf_counter()
    if not data.top_items:
        return None

    scale = 2.0
    width = int(1120 * scale)
    pad = int(24 * scale)
    gap = int(12 * scale)
    hero_h = int(178 * scale)
    left_w = int(610 * scale)
    right_w = width - pad * 2 - left_w - gap
    user_card_h = int(118 * scale)
    user_row_gap = int(10 * scale)
    group_rank_header_h = int(58 * scale)
    group_rank_row_h = int(44 * scale)
    group_rank_row_gap = int(6 * scale)
    group_rank_panel_gap = int(10 * scale)
    histogram_h = int(182 * scale)
    footer_h = int(50 * scale)
    group_rank_avatar_size = int(28 * scale)
    group_name_font = _load_font(int(10 * scale))
    group_rank_summary_font = _load_font(int(9 * scale))
    group_rank_summary_max_width = right_w - int(18 * scale)
    visible_group_rank_has_hidden_before = False
    visible_group_rank_has_hidden_after = False

    user_count = len(data.top_items)
    left_h = estimate_group_report_left_height(user_count, scale=scale)
    group_rank_card_h = estimate_group_rank_card_height(
        len(data.group_rank_items),
        has_hidden_before=visible_group_rank_has_hidden_before,
        has_hidden_after=visible_group_rank_has_hidden_after,
        scale=scale,
    )
    right_extra_h = compute_group_report_right_extra_height(
        user_count=user_count,
        rank_item_count=len(data.group_rank_items),
        has_hidden_before=visible_group_rank_has_hidden_before,
        has_hidden_after=visible_group_rank_has_hidden_after,
        scale=scale,
    )
    compact_panel_h = 0
    share_panel_h = 0
    trend_panel_h = 0
    if data.right_panel_layout_tier == "compact" and data.group_rank_insights:
        compact_panel_h = right_extra_h if right_extra_h >= int(96 * scale) else 0
    elif data.right_panel_layout_tier == "balanced":
        share_panel_h = right_extra_h if right_extra_h >= int(120 * scale) else 0
    elif data.right_panel_layout_tier == "expanded":
        share_panel_h = max(
            int(150 * scale), min(int(right_extra_h * 0.34), int(190 * scale))
        )
        trend_panel_h = right_extra_h - group_rank_panel_gap - share_panel_h
        if trend_panel_h < int(150 * scale):
            trend_panel_h = 0
            share_panel_h = right_extra_h
    right_h = group_rank_card_h
    for section_h in (compact_panel_h, share_panel_h, trend_panel_h):
        if section_h > 0:
            right_h += group_rank_panel_gap + section_h
    middle_h = max(left_h, right_h)
    height = pad * 2 + hero_h + gap + middle_h + gap + histogram_h + gap + footer_h

    theme = WATER_THEME
    page_bg = theme.page_bg
    hero_bg = theme.hero_bg
    panel_bg = theme.panel_bg
    panel_soft_bg = theme.panel_soft_bg
    accent = theme.accent
    strong = theme.strong
    deep = theme.deep
    hint = theme.hint
    line = theme.line
    blue = theme.blue
    mint = theme.mint
    card = BuildImage.new("RGB", (width, height), page_bg)
    y = pad
    card.draw_rounded_rectangle(
        (pad, y, width - pad, y + hero_h), radius=int(20 * scale), fill=hero_bg
    )
    card.draw_text(
        (
            pad + int(18 * scale),
            y + int(12 * scale),
            width - pad - int(18 * scale),
            y + int(48 * scale),
        ),
        data.title,
        max_fontsize=int(32 * scale),
        min_fontsize=int(22 * scale),
        fill=strong,
        halign="center",
        font_families=[WATER_THEME.white],
    )
    hero_meta_text = f"{data.range_text}  |  {data.compare_text}"
    card.draw_text(
        (
            pad + int(18 * scale),
            y + int(52 * scale),
            width - pad - int(18 * scale),
            y + int(88 * scale),
        ),
        hero_meta_text,
        max_fontsize=int(14 * scale),
        min_fontsize=int(10 * scale),
        fill=accent,
        halign="center",
        font_families=[WATER_THEME.white],
    )

    stat_top = y + int(96 * scale)
    stat_gap = int(10 * scale)
    stat_w = int((width - pad * 2 - int(36 * scale) - stat_gap * 2) / 3)
    stats = [
        (
            tr(locale, "water.image.period.stats.total_msg_count"),
            short_exp(data.total_msg_count),
            strong,
            theme.stat_total_bg,
        ),
        (
            tr(locale, "water.image.period.stats.active_user_count"),
            str(data.active_user_count),
            blue,
            theme.stat_active_bg,
        ),
        (
            tr(locale, "water.image.period.stats.delta"),
            format_delta(data.total_msg_count - data.previous_total_msg_count),
            mint if data.total_msg_count >= data.previous_total_msg_count else strong,
            theme.stat_delta_positive_bg
            if data.total_msg_count >= data.previous_total_msg_count
            else theme.stat_delta_negative_bg,
        ),
    ]
    for idx, (label, value, value_color, bg) in enumerate(stats):
        sx = pad + int(18 * scale) + idx * (stat_w + stat_gap)
        stat_bottom = y + hero_h - int(18 * scale)
        card.draw_rounded_rectangle(
            (sx, stat_top, sx + stat_w, stat_bottom),
            radius=int(12 * scale),
            fill=bg,
        )
        card.draw_text(
            (
                sx + int(14 * scale),
                stat_top + int(8 * scale),
                sx + stat_w - int(14 * scale),
                stat_top + int(26 * scale),
            ),
            label,
            max_fontsize=int(12 * scale),
            min_fontsize=int(8 * scale),
            fill=deep,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        card.draw_text(
            (
                sx + int(14 * scale),
                stat_top + int(28 * scale),
                sx + stat_w - int(14 * scale),
                stat_bottom - int(6 * scale),
            ),
            value,
            max_fontsize=int(23 * scale),
            min_fontsize=int(14 * scale),
            fill=value_color,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )

    y += hero_h + gap
    left_x = pad
    right_x = left_x + left_w + gap
    card.draw_rounded_rectangle(
        (left_x, y, left_x + left_w, y + left_h),
        radius=int(20 * scale),
        fill=panel_soft_bg,
    )
    card.draw_text(
        (
            left_x + int(18 * scale),
            y + int(8 * scale),
            left_x + left_w,
            y + int(32 * scale),
        ),
        tr(locale, "water.report.board.title"),
        max_fontsize=int(18 * scale),
        min_fontsize=int(12 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
    )

    row_y = y + int(38 * scale)
    rank_themes = {
        rank: {"bg": bg, "badge": badge, "badge_txt": fg}
        for rank, (bg, badge, fg) in {
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
        }.items()
    }
    tile_renderer = WaterRankRenderer()
    board_name_font = _load_font(int(14 * scale))
    for item in data.top_items:
        rank_theme = rank_themes.get(
            item.current_rank,
            {
                "bg": theme.rank_row_fill,
                "badge": theme.row_default_badge_fill,
                "badge_txt": accent,
            },
        )
        bg = rank_theme["bg"]
        badge_fill = rank_theme["badge"]
        badge_fg = rank_theme["badge_txt"]
        card.draw_rounded_rectangle(
            (
                left_x + int(14 * scale),
                row_y,
                left_x + left_w - int(14 * scale),
                row_y + user_card_h,
            ),
            radius=int(14 * scale),
            fill=bg,
        )
        badge_x = left_x + int(24 * scale)
        badge_y = row_y + int(16 * scale)
        badge_w = int(44 * scale)
        badge_h = int(22 * scale)
        card.draw_rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            radius=badge_h // 2,
            fill=badge_fill,
        )
        card.draw_text(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            f"#{item.current_rank}",
            max_fontsize=int(11 * scale),
            min_fontsize=int(8 * scale),
            fill=badge_fg,
            halign="center",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        avatar_size = int(52 * scale)
        avatar_x = badge_x
        avatar_y = row_y + int(44 * scale)
        avatar = item.avatar or build_avatar_fallback(
            avatar_size, item.display_name[:1] or "?", theme.avatar_fallback_bg, strong
        )
        card.paste(
            avatar.circle().resize((avatar_size, avatar_size)),
            (avatar_x, avatar_y),
            alpha=True,
        )
        text_x = avatar_x + avatar_size + int(12 * scale)
        row_inner_right = left_x + left_w - int(14 * scale)
        trend_text, trend_color = format_trend(item.trend)
        trend_w = int(48 * scale)
        trend_h = int(20 * scale)
        trend_x = row_inner_right - int(18 * scale) - trend_w
        trend_y = badge_y + int(1 * scale)
        safe_name = _truncate_text_to_width_pixels(
            item.display_name,
            font=board_name_font,
            max_width=max(int(96 * scale), trend_x - text_x - int(12 * scale)),
        )
        card.draw_text(
            (
                text_x,
                row_y + int(16 * scale),
                trend_x - int(12 * scale),
                row_y + int(42 * scale),
            ),
            safe_name,
            max_fontsize=int(16 * scale),
            min_fontsize=int(10 * scale),
            fill=deep,
            halign="left",
            font_families=[WATER_THEME.white],
        )
        summary_right = left_x + int(300 * scale)
        card.draw_text(
            (
                text_x,
                row_y + int(52 * scale),
                summary_right,
                row_y + int(74 * scale),
            ),
            tr(locale, "water.report.board.summary", msg_count=item.msg_count),
            max_fontsize=int(11 * scale),
            min_fontsize=int(8 * scale),
            fill=accent,
            halign="left",
            font_families=[WATER_THEME.white],
        )
        card.draw_text(
            (
                text_x,
                row_y + int(78 * scale),
                summary_right,
                row_y + int(96 * scale),
            ),
            tr(
                locale,
                "water.report.board.active_hours",
                active_hours=item.active_hours,
            ),
            max_fontsize=int(10 * scale),
            min_fontsize=int(8 * scale),
            fill=hint,
            halign="left",
            font_families=[WATER_THEME.white],
        )
        tile_chart = tile_renderer._generate_tile_chart(item.hourly_counts)
        tile_region_left = summary_right + int(4.5 * scale)
        tile_region_right = row_inner_right - int(18 * scale)
        tile_region_top = row_y + int(40 * scale)
        tile_region_bottom = row_y + user_card_h - int(12 * scale)
        tile_region_w = max(1, tile_region_right - tile_region_left)
        tile_region_h = max(1, tile_region_bottom - tile_region_top)
        tile_scale = min(
            0.72,
            tile_region_w / tile_chart.width,
            tile_region_h / tile_chart.height,
        )
        tile_w = max(1, int(tile_chart.width * tile_scale))
        tile_h = max(1, int(tile_chart.height * tile_scale))
        resized_tile = tile_chart.resize((tile_w, tile_h))
        tile_x = tile_region_left + max(
            0, (tile_region_w - tile_w) // 2
        )
        tile_y = tile_region_top + max(
            0, (tile_region_h - tile_h) // 2
        )
        card.paste(resized_tile, (tile_x, tile_y), alpha=True)
        card.draw_rounded_rectangle(
            (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
            radius=trend_h // 2,
            fill=trend_color,
        )
        card.draw_text(
            (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
            trend_text,
            max_fontsize=int(11 * scale),
            min_fontsize=int(8 * scale),
            fill=theme.white,
            halign="center",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        row_y += user_card_h + user_row_gap

    card.draw_rounded_rectangle(
        (right_x, y, right_x + right_w, y + group_rank_card_h),
        radius=int(20 * scale),
        fill=panel_bg,
    )
    card.draw_text(
        (
            right_x + int(18 * scale),
            y + int(8 * scale),
            right_x + right_w - int(18 * scale),
            y + int(30 * scale),
        ),
        data.group_rank_title,
        max_fontsize=int(18 * scale),
        min_fontsize=int(12 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    card.draw_text(
        (
            right_x + int(18 * scale),
            y + int(30 * scale),
            right_x + right_w - int(18 * scale),
            y + int(52 * scale),
        ),
        _truncate_text_to_width_pixels(
            data.group_rank_summary,
            font=group_rank_summary_font,
            max_width=group_rank_summary_max_width,
        ),
        max_fontsize=int(11 * scale),
        min_fontsize=int(8 * scale),
        fill=accent,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    rank_y = y + group_rank_header_h + int(12 * scale)
    rank_row_left = right_x + int(14 * scale)
    rank_row_right = right_x + right_w - int(14 * scale)
    if data.group_rank_has_hidden_before:
        pass
    for item in data.group_rank_items:
        trend_text, trend_color = format_trend(item.trend)
        trend_w = int(50 * scale)
        trend_h = int(22 * scale)
        trend_x = rank_row_right - trend_w - int(14 * scale)
        count_right = trend_x - int(10 * scale)
        count_left = count_right - int(86 * scale)
        name_x = right_x + int(92 * scale)
        safe_name = _truncate_text_to_width_pixels(
            item.display_name,
            font=group_name_font,
            max_width=max(int(60 * scale), count_left - name_x - int(8 * scale)),
        )
        card.draw_rounded_rectangle(
            (
                rank_row_left,
                rank_y,
                rank_row_right,
                rank_y + group_rank_row_h,
            ),
            radius=int(12 * scale),
            fill=_group_rank_row_fill(
                is_focus_group=item.is_focus_group,
                base_fill=theme.rank_row_fill,
                accent=theme.podium_gold_badge,
            ),
        )
        card.draw_text(
            (
                right_x + int(22 * scale),
                rank_y,
                right_x + int(48 * scale),
                rank_y + group_rank_row_h,
            ),
            f"#{item.current_rank}",
            max_fontsize=int(15 * scale),
            min_fontsize=int(10 * scale),
            fill=strong if item.current_rank <= 3 else accent,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        avatar_x = right_x + int(48 * scale)
        avatar_y = rank_y + (group_rank_row_h - group_rank_avatar_size) // 2
        avatar = item.avatar or build_avatar_fallback(
            group_rank_avatar_size,
            item.display_name[:1] or "?",
            theme.group_avatar_fallback_bg,
            theme.group_avatar_fallback_fg,
        )
        card.paste(
            avatar.circle().resize((group_rank_avatar_size, group_rank_avatar_size)),
            (avatar_x, avatar_y),
            alpha=True,
        )
        card.draw_text(
            (
                name_x,
                rank_y,
                count_left - int(8 * scale),
                rank_y + group_rank_row_h,
            ),
            safe_name,
            max_fontsize=int(13 * scale),
            min_fontsize=int(9 * scale),
            fill=deep,
            halign="left",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        trend_y = rank_y + (group_rank_row_h - trend_h) // 2
        card.draw_rounded_rectangle(
            (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
            radius=trend_h // 2,
            fill=trend_color,
        )
        card.draw_text(
            (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
            trend_text,
            max_fontsize=int(10 * scale),
            min_fontsize=int(8 * scale),
            fill=theme.white,
            halign="center",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        card.draw_text(
            (
                count_left,
                rank_y,
                count_right,
                rank_y + group_rank_row_h,
            ),
            tr(locale, "water.report.group_rank.count", count=item.msg_count),
            max_fontsize=int(10 * scale),
            min_fontsize=int(8 * scale),
            fill=accent,
            halign="right",
            valign="center",
            font_families=[WATER_THEME.white],
        )
        rank_y += group_rank_row_h + group_rank_row_gap
    if data.group_rank_has_hidden_after:
        pass
    extra_y = y + group_rank_card_h + group_rank_panel_gap
    if compact_panel_h > 0:
        _render_compact_group_rank_insights(
            card,
            x=right_x,
            y=extra_y,
            w=right_w,
            h=compact_panel_h,
            data=data,
            locale=locale,
            theme=theme,
            scale=scale,
            deep=deep,
            accent=accent,
            strong=strong,
        )
    elif share_panel_h > 0:
        _render_group_rank_share_panel(
            card,
            x=right_x,
            y=extra_y,
            w=right_w,
            h=share_panel_h,
            data=data,
            locale=locale,
            theme=theme,
            scale=scale,
            deep=deep,
            accent=accent,
            strong=strong,
            hint=hint,
        )
        if trend_panel_h > 0:
            _render_group_rank_trend_panel(
                card,
                x=right_x,
                y=extra_y + share_panel_h + group_rank_panel_gap,
                w=right_w,
                h=trend_panel_h,
                data=data,
                locale=locale,
                theme=theme,
                scale=scale,
                deep=deep,
                accent=accent,
                hint=hint,
            )
    y += middle_h + gap
    card.draw_rounded_rectangle(
        (pad, y, width - pad, y + histogram_h), radius=int(20 * scale), fill=panel_bg
    )
    card.draw_text(
        (pad + int(18 * scale), y + int(8 * scale), width - pad, y + int(30 * scale)),
        tr(locale, "water.report.overview.title"),
        max_fontsize=int(18 * scale),
        min_fontsize=int(12 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    card.draw_text(
        (pad + int(18 * scale), y + int(32 * scale), width - pad, y + int(52 * scale)),
        tr(
            locale,
            "water.image.period.overview.peak_hour",
            start=f"{data.peak_hour:02d}",
            end=f"{data.peak_hour:02d}",
        ),
        max_fontsize=int(11 * scale),
        min_fontsize=int(8 * scale),
        fill=accent,
        halign="left",
        font_families=[WATER_THEME.white],
    )
    draw_hourly_histogram(
        card,
        x=pad + int(18 * scale),
        y=y + int(58 * scale),
        w=width - pad * 2 - int(36 * scale),
        h=histogram_h - int(76 * scale),
        hourly_counts=data.hourly_counts,
        bar_color=strong,
        axis_color=line,
        label_color=hint,
        scale=scale,
    )
    y += histogram_h + gap
    draw_report_footer(
        card,
        locale=locale,
        generated_at=data.generated_at,
        footer_text=tr(locale, "water.image.period.footer"),
        width=width,
        pad=pad,
        top=y,
        footer_h=footer_h,
        scale=scale,
        copyright_color=hint,
        time_color=accent,
        footer_color=strong,
    )
    image = (await asyncio.to_thread(card.save, "PNG")).getvalue()
    logger.debug(
        "[Water][RankRender] type=group_report title={} items={} "
        "rank_items={} elapsed_ms={:.2f} bytes={}",
        data.title,
        len(data.top_items),
        len(data.group_rank_items),
        (perf_counter() - started) * 1000,
        len(image),
    )
    return image


async def build_water_period_rank_image(
    data: WaterPeriodRankCardData,
    locale: LocaleCode,
) -> bytes | None:
    started = perf_counter()
    try:
        theme = WATER_THEME
        scale = 2.0
        width = int(760 * scale)
        pad = int(24 * scale)
        gap = int(12 * scale)
        hero_h = int(160 * scale)
        champion_h = int(130 * scale)
        tiles_h = int(152 * scale)
        overview_h = int(168 * scale)
        footer_h = int(50 * scale)
        row_h = int(86 * scale)
        row_gap = int(8 * scale)
        board_header_h = int(34 * scale)
        group_rank_header_h = int(34 * scale)
        group_rank_row_h = int(42 * scale)
        group_rank_row_gap = int(6 * scale)
        show_tiles = bool(data.report_tile_title)
        show_overview = data.report_show_overview
        board_h = (
            board_header_h
            + int(12 * scale)
            + len(data.top_users) * row_h
            + max(0, len(data.top_users) - 1) * row_gap
            + int(16 * scale)
        )
        group_rank_items = data.report_group_rank_items or []
        visible_tiles_h = tiles_h if show_tiles else 0
        hidden_rows = int(data.report_group_rank_has_hidden_before) + int(
            data.report_group_rank_has_hidden_after
        )
        group_rank_h = 0
        if group_rank_items:
            group_rank_h = (
                group_rank_header_h
                + int(12 * scale)
                + (len(group_rank_items) + hidden_rows) * group_rank_row_h
                + max(0, len(group_rank_items) + hidden_rows - 1) * group_rank_row_gap
                + int(16 * scale)
            )
        height = (
            pad * 2
            + hero_h
            + gap
            + champion_h
            + (gap if show_tiles else 0)
            + visible_tiles_h
            + gap
            + board_h
            + (gap if group_rank_h else 0)
            + group_rank_h
            + (gap if show_overview else 0)
            + (overview_h if show_overview else 0)
            + gap
            + footer_h
        )

        page_bg = theme.page_bg
        hero_bg = theme.hero_bg
        panel_bg = theme.panel_bg
        panel_soft_bg = theme.panel_soft_bg
        accent = theme.accent
        strong = theme.strong
        deep = theme.deep
        hint = theme.hint
        line = theme.line
        blue = theme.blue
        mint = theme.mint
        card = BuildImage.new("RGB", (width, height), page_bg)
        y = pad

        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + hero_h),
            radius=int(20 * scale),
            fill=hero_bg,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(12 * scale),
                width - pad - int(180 * scale),
                y + int(40 * scale),
            ),
            data.title,
            max_fontsize=int(28 * scale),
            min_fontsize=int(18 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(42 * scale),
                width - pad - int(18 * scale),
                y + int(64 * scale),
            ),
            data.range_text,
            max_fontsize=int(13 * scale),
            min_fontsize=int(10 * scale),
            fill=accent,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(64 * scale),
                width - pad - int(18 * scale),
                y + int(84 * scale),
            ),
            data.compare_text,
            max_fontsize=int(11 * scale),
            min_fontsize=int(8 * scale),
            fill=hint,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )

        stat_top = y + int(92 * scale)
        stat_gap = int(10 * scale)
        stat_w = int((width - pad * 2 - int(36 * scale) - stat_gap * 2) / 3)
        stats = [
            (
                tr(locale, "water.image.period.stats.total_msg_count"),
                short_exp(data.total_msg_count),
                strong,
                theme.stat_total_bg,
            ),
            (
                tr(locale, "water.image.period.stats.active_user_count"),
                str(data.active_user_count),
                blue,
                theme.stat_active_bg,
            ),
            (
                tr(locale, "water.image.period.stats.delta"),
                format_delta(data.total_msg_count - data.previous_total_msg_count),
                mint
                if data.total_msg_count >= data.previous_total_msg_count
                else strong,
                theme.stat_delta_positive_bg
                if data.total_msg_count >= data.previous_total_msg_count
                else theme.stat_delta_negative_bg,
            ),
        ]
        for idx, (label, value, value_color, bg) in enumerate(stats):
            sx = pad + int(18 * scale) + idx * (stat_w + stat_gap)
            stat_bottom = y + hero_h - int(18 * scale)
            card.draw_rounded_rectangle(
                (sx, stat_top, sx + stat_w, stat_bottom),
                radius=int(12 * scale),
                fill=bg,
            )
            card.draw_text(
                (
                    sx + int(10 * scale),
                    stat_top + int(8 * scale),
                    sx + stat_w - int(10 * scale),
                    stat_top + int(20 * scale),
                ),
                label,
                max_fontsize=int(10 * scale),
                min_fontsize=int(7 * scale),
                fill=accent,
                halign="left",
                valign="center",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    sx + int(10 * scale),
                    stat_top + int(18 * scale),
                    sx + stat_w - int(10 * scale),
                    stat_bottom - int(4 * scale),
                ),
                value,
                max_fontsize=int(17 * scale),
                min_fontsize=int(10 * scale),
                fill=value_color,
                halign="left",
                valign="center",
                font_families=[SYS_FONT_NAME],
            )

        y += hero_h + gap
        left_x = pad
        left_w = width - pad * 2
        card.draw_rounded_rectangle(
            (left_x, y, left_x + left_w, y + board_h),
            radius=int(20 * scale),
            fill=panel_soft_bg,
        )
        card.draw_text(
            (
                left_x + int(18 * scale),
                y + int(8 * scale),
                left_x + left_w,
                y + int(32 * scale),
            ),
            tr(locale, "water.report.board.title"),
            max_fontsize=int(18 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )

        row_y = y + int(38 * scale)
        rank_themes = {
            rank: {"bg": bg, "badge": badge, "badge_txt": fg}
            for rank, (bg, badge, fg) in {
                1: (
                    theme.podium_gold_bg,
                    theme.podium_gold_badge,
                    theme.podium_badge_text,
                ),
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
            }.items()
        }
        tile_renderer = WaterRankRenderer()
        for item in data.top_items:
            rank_theme = rank_themes.get(
                item.current_rank,
                {
                    "bg": theme.rank_row_fill,
                    "badge": theme.row_default_badge_fill,
                    "badge_txt": accent,
                },
            )
            bg = rank_theme["bg"]
            badge_fill = rank_theme["badge"]
            badge_fg = rank_theme["badge_txt"]
            card.draw_rounded_rectangle(
                (
                    left_x + int(14 * scale),
                    row_y,
                    left_x + left_w - int(14 * scale),
                    row_y + row_h,
                ),
                radius=int(14 * scale),
                fill=bg,
            )
            badge_x = left_x + int(24 * scale)
            badge_y = row_y + int(16 * scale)
            badge_w = int(44 * scale)
            badge_h = int(22 * scale)
            card.draw_rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                radius=badge_h // 2,
                fill=badge_fill,
            )
            card.draw_text(
                (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                f"#{item.current_rank}",
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=badge_fg,
                halign="center",
                valign="center",
                font_families=[SYS_FONT_NAME],
            )
            avatar_size = int(52 * scale)
            avatar_x = badge_x
            avatar_y = row_y + int(44 * scale)
            avatar = item.avatar or build_avatar_fallback(
                avatar_size,
                item.display_name[:1] or "?",
                theme.avatar_fallback_bg,
                strong,
            )
            card.paste(
                avatar.circle().resize((avatar_size, avatar_size)),
                (avatar_x, avatar_y),
                alpha=True,
            )
            text_x = avatar_x + avatar_size + int(12 * scale)
            card.draw_text(
                (
                    text_x,
                    row_y + int(16 * scale),
                    left_x + int(300 * scale),
                    row_y + int(40 * scale),
                ),
                item.display_name,
                max_fontsize=int(16 * scale),
                min_fontsize=int(10 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    text_x,
                    row_y + int(42 * scale),
                    left_x + int(300 * scale),
                    row_y + int(64 * scale),
                ),
                tr(locale, "water.report.board.summary", msg_count=item.msg_count),
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    text_x,
                    row_y + int(66 * scale),
                    left_x + int(300 * scale),
                    row_y + int(84 * scale),
                ),
                tr(
                    locale,
                    "water.report.board.active_hours",
                    active_hours=item.active_hours,
                ),
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=hint,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            tile_chart = tile_renderer._generate_tile_chart(item.hourly_counts)
            tile_w = int(tile_chart.width * 0.72)
            tile_h = int(tile_chart.height * 0.72)
            card.paste(
                tile_chart.resize((tile_w, tile_h)),
                (
                    left_x + left_w - tile_w - int(28 * scale),
                    row_y + (row_h - tile_h) // 2,
                ),
                alpha=True,
            )
            trend_text, trend_color = format_trend(item.trend)
            trend_w = int(56 * scale)
            trend_h = int(24 * scale)
            trend_x = left_x + left_w - trend_w - int(28 * scale)
            trend_y = row_y + int(12 * scale)
            card.draw_rounded_rectangle(
                (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
                radius=trend_h // 2,
                fill=trend_color,
            )
            card.draw_text(
                (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
                trend_text,
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=theme.white,
                halign="center",
                valign="center",
                font_families=[SYS_FONT_NAME],
            )
            row_y += row_h + row_gap

        y += board_h
        if group_rank_h:
            report_group_name_font = _load_font(int(9 * scale))
            report_group_name_max_width = width - pad * 2 - int(232 * scale)
            report_group_rank_summary_font = _load_font(int(8 * scale))
            report_group_rank_summary_max_width = width - pad * 2 - int(36 * scale)
            report_group_rank_avatar_size = int(28 * scale)
            y += gap
            card.draw_rounded_rectangle(
                (pad, y, width - pad, y + group_rank_h),
                radius=int(20 * scale),
                fill=panel_bg,
            )
            card.draw_text(
                (
                    pad + int(18 * scale),
                    y + int(8 * scale),
                    width - pad,
                    y + int(30 * scale),
                ),
                data.report_group_rank_title,
                max_fontsize=int(18 * scale),
                min_fontsize=int(12 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    pad + int(18 * scale),
                    y + int(30 * scale),
                    width - pad,
                    y + int(52 * scale),
                ),
                _truncate_text_to_width_pixels(
                    data.report_group_rank_summary,
                    font=report_group_rank_summary_font,
                    max_width=report_group_rank_summary_max_width,
                ),
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            rank_y = y + group_rank_header_h + int(12 * scale)
            if data.report_group_rank_has_hidden_before:
                card.draw_text(
                    (
                        pad + int(18 * scale),
                        rank_y,
                        width - pad - int(18 * scale),
                        rank_y + group_rank_row_h,
                    ),
                    tr(locale, "water.report.group_rank.ellipsis"),
                    max_fontsize=int(16 * scale),
                    min_fontsize=int(12 * scale),
                    fill=hint,
                    halign="center",
                    valign="center",
                    font_families=[SYS_FONT_NAME],
                )
                rank_y += group_rank_row_h + group_rank_row_gap
            for item in group_rank_items:
                safe_name = _truncate_text_to_width_pixels(
                    item.display_name,
                    font=report_group_name_font,
                    max_width=report_group_name_max_width,
                )
                card.draw_rounded_rectangle(
                    (
                        pad + int(14 * scale),
                        rank_y,
                        width - pad - int(14 * scale),
                        rank_y + group_rank_row_h,
                    ),
                    radius=int(12 * scale),
                    fill=_group_rank_row_fill(
                        is_focus_group=item.is_focus_group,
                        base_fill=theme.rank_row_fill,
                        accent=theme.podium_gold_badge,
                    ),
                )
                card.draw_text(
                    (
                        pad + int(24 * scale),
                        rank_y,
                        pad + int(56 * scale),
                        rank_y + group_rank_row_h,
                    ),
                    f"#{item.current_rank}",
                    max_fontsize=int(15 * scale),
                    min_fontsize=int(10 * scale),
                    fill=strong if item.current_rank <= 3 else accent,
                    halign="left",
                    valign="center",
                    font_families=[SYS_FONT_NAME],
                )
                avatar_x = pad + int(54 * scale)
                avatar_y = (
                    rank_y + (group_rank_row_h - report_group_rank_avatar_size) // 2
                )
                avatar = item.avatar or build_avatar_fallback(
                    report_group_rank_avatar_size,
                    item.display_name[:1] or "?",
                    theme.group_avatar_fallback_bg,
                    theme.group_avatar_fallback_fg,
                )
                card.paste(
                    avatar.circle().resize(
                        (
                            report_group_rank_avatar_size,
                            report_group_rank_avatar_size,
                        )
                    ),
                    (avatar_x, avatar_y),
                    alpha=True,
                )
                card.draw_text(
                    (
                        pad + int(100 * scale),
                        rank_y,
                        width - pad - int(104 * scale),
                        rank_y + group_rank_row_h,
                    ),
                    safe_name,
                    max_fontsize=int(13 * scale),
                    min_fontsize=int(9 * scale),
                    fill=deep,
                    halign="left",
                    valign="center",
                    font_families=[SYS_FONT_NAME],
                )
                trend_text, trend_color = format_trend(item.trend)
                trend_w = int(54 * scale)
                trend_h = int(22 * scale)
                trend_x = width - pad - trend_w - int(22 * scale)
                trend_y = rank_y + (group_rank_row_h - trend_h) // 2
                card.draw_rounded_rectangle(
                    (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
                    radius=trend_h // 2,
                    fill=trend_color,
                )
                card.draw_text(
                    (trend_x, trend_y, trend_x + trend_w, trend_y + trend_h),
                    trend_text,
                    max_fontsize=int(10 * scale),
                    min_fontsize=int(8 * scale),
                    fill=theme.white,
                    halign="center",
                    valign="center",
                    font_families=[SYS_FONT_NAME],
                )
                card.draw_text(
                    (
                        width - pad - int(176 * scale),
                        rank_y,
                        width - pad - int(86 * scale),
                        rank_y + group_rank_row_h,
                    ),
                    tr(locale, "water.report.group_rank.count", count=item.msg_count),
                    max_fontsize=int(10 * scale),
                    min_fontsize=int(8 * scale),
                    fill=accent,
                    halign="right",
                    valign="center",
                    font_families=[SYS_FONT_NAME],
                )
                rank_y += group_rank_row_h + group_rank_row_gap
            if data.report_group_rank_has_hidden_after:
                card.draw_text(
                    (
                        pad + int(18 * scale),
                        rank_y,
                        width - pad - int(18 * scale),
                        rank_y + group_rank_row_h,
                    ),
                    tr(locale, "water.report.group_rank.ellipsis"),
                    max_fontsize=int(16 * scale),
                    min_fontsize=int(12 * scale),
                    fill=hint,
                    halign="center",
                    valign="center",
                    font_families=[SYS_FONT_NAME],
                )

        if show_overview:
            y += gap
            card.draw_rounded_rectangle(
                (pad, y, width - pad, y + overview_h),
                radius=int(20 * scale),
                fill=panel_bg,
            )
            card.draw_text(
                (
                    pad + int(18 * scale),
                    y + int(8 * scale),
                    width - pad,
                    y + int(30 * scale),
                ),
                data.overview_title,
                max_fontsize=int(18 * scale),
                min_fontsize=int(12 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    pad + int(18 * scale),
                    y + int(32 * scale),
                    width - pad,
                    y + int(52 * scale),
                ),
                tr(
                    locale,
                    "water.image.period.overview.peak_hour",
                    start=f"{data.peak_hour:02d}",
                    end=f"{data.peak_hour:02d}",
                ),
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            draw_hourly_histogram(
                card,
                x=pad + int(18 * scale),
                y=y + int(58 * scale),
                w=width - pad * 2 - int(36 * scale),
                h=overview_h - int(76 * scale),
                hourly_counts=data.hourly_counts,
                bar_color=strong,
                axis_color=line,
                label_color=hint,
                scale=scale,
            )
            y += overview_h + gap
        else:
            y += gap

        draw_report_footer(
            card,
            locale=locale,
            generated_at=data.generated_at,
            footer_text=tr(locale, "water.image.period.footer"),
            width=width,
            pad=pad,
            top=y,
            footer_h=footer_h,
            scale=scale,
            copyright_color=hint,
            time_color=accent,
            footer_color=strong,
        )
        image = (await asyncio.to_thread(card.save, "PNG")).getvalue()
        logger.debug(
            "[Water][RankRender] type=period period={} title={} items={} "
            "elapsed_ms={:.2f} bytes={}",
            data.period,
            data.title,
            len(data.top_items),
            (perf_counter() - started) * 1000,
            len(image),
        )
        return image
    except Exception as e:
        logger.exception(f"[Water] build period rank image failed: {e}")
        return None
