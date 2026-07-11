"""Water group report and period report image renderers."""

from __future__ import annotations

import asyncio
from time import perf_counter

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
    draw_hourly_histogram,
    draw_report_footer,
    format_delta,
    format_trend,
    short_exp,
)
from .models import WaterGroupReportImageData, WaterPeriodRankCardData
from .rank import WaterRankRenderer

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
    from .common import mix_hex

    return mix_hex(base_fill, accent, 0.16)


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
    hero_h = int(148 * scale)
    left_w = int(610 * scale)
    right_w = width - pad * 2 - left_w - gap
    user_card_h = int(118 * scale)
    user_row_gap = int(10 * scale)
    group_rank_header_h = int(58 * scale)
    group_rank_row_h = int(44 * scale)
    group_rank_row_gap = int(6 * scale)
    group_rank_panel_gap = int(10 * scale)
    group_rank_insight_header_h = int(40 * scale)
    group_rank_insight_tile_h = int(52 * scale)
    group_rank_insight_tile_gap = int(8 * scale)
    histogram_h = int(182 * scale)
    footer_h = int(50 * scale)
    group_rank_avatar_size = int(28 * scale)
    group_name_font = _load_font(int(9 * scale))
    group_rank_summary_font = _load_font(int(8 * scale))
    group_rank_summary_max_width = right_w - int(18 * scale)

    user_count = len(data.top_items)
    left_h = (
        int(40 * scale)
        + user_count * user_card_h
        + max(0, user_count - 1) * user_row_gap
        + int(18 * scale)
    )
    hidden_rows = int(data.group_rank_has_hidden_before) + int(
        data.group_rank_has_hidden_after
    )
    rank_row_count = len(data.group_rank_items) + hidden_rows
    group_rank_card_h = (
        group_rank_header_h
        + int(12 * scale)
        + rank_row_count * group_rank_row_h
        + max(0, rank_row_count - 1) * group_rank_row_gap
        + int(16 * scale)
    )
    group_rank_insight_h = 0
    if data.group_rank_insights:
        insight_rows = (len(data.group_rank_insights) + 1) // 2
        group_rank_insight_h = (
            group_rank_insight_header_h
            + int(8 * scale)
            + insight_rows * group_rank_insight_tile_h
            + max(0, insight_rows - 1) * group_rank_insight_tile_gap
            + int(14 * scale)
        )
    right_h = group_rank_card_h
    if group_rank_insight_h:
        right_h += group_rank_panel_gap + group_rank_insight_h
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
            width - pad - int(180 * scale),
            y + int(40 * scale),
        ),
        data.title,
        max_fontsize=int(28 * scale),
        min_fontsize=int(18 * scale),
        fill=deep,
        halign="left",
        font_families=[WATER_THEME.white],
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
        font_families=[WATER_THEME.white],
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
        font_families=[WATER_THEME.white],
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
            font_families=[WATER_THEME.white],
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
            font_families=[WATER_THEME.white],
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
            font_families=[WATER_THEME.white],
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
            font_families=[WATER_THEME.white],
        )
        tile_chart = tile_renderer._generate_tile_chart(item.hourly_counts)
        tile_w = int(tile_chart.width * 0.72)
        tile_h = int(tile_chart.height * 0.72)
        resized_tile = tile_chart.resize((tile_w, tile_h))
        tile_x = left_x + left_w - tile_w - int(28 * scale)
        tile_y = row_y + (user_card_h - tile_h) // 2
        card.paste(resized_tile, (tile_x, tile_y), alpha=True)
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
    if group_rank_insight_h:
        insight_y = y + group_rank_card_h + group_rank_panel_gap
        card.draw_rounded_rectangle(
            (right_x, insight_y, right_x + right_w, insight_y + group_rank_insight_h),
            radius=int(18 * scale),
            fill=panel_soft_bg,
        )
        card.draw_text(
            (
                right_x + int(18 * scale),
                insight_y + int(8 * scale),
                right_x + right_w - int(18 * scale),
                insight_y + int(28 * scale),
            ),
            tr(locale, "water.report.group_rank.insight.title"),
            max_fontsize=int(15 * scale),
            min_fontsize=int(10 * scale),
            fill=deep,
            halign="left",
            font_families=[WATER_THEME.white],
        )
        insight_tile_gap_x = int(10 * scale)
        insight_tile_w = (right_w - int(36 * scale) - insight_tile_gap_x) // 2
        insight_y_cursor = insight_y + group_rank_insight_header_h
        for idx, insight in enumerate(data.group_rank_insights):
            row = idx // 2
            col = idx % 2
            tile_x = (
                right_x + int(18 * scale) + col * (insight_tile_w + insight_tile_gap_x)
            )
            tile_y = insight_y_cursor + row * (
                group_rank_insight_tile_h + group_rank_insight_tile_gap
            )
            card.draw_rounded_rectangle(
                (
                    tile_x,
                    tile_y,
                    tile_x + insight_tile_w,
                    tile_y + group_rank_insight_tile_h,
                ),
                radius=int(12 * scale),
                fill=theme.rank_row_fill,
            )
            card.draw_text(
                (
                    tile_x + int(10 * scale),
                    tile_y + int(8 * scale),
                    tile_x + insight_tile_w - int(10 * scale),
                    tile_y + int(20 * scale),
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
                    tile_x + insight_tile_w - int(10 * scale),
                    tile_y + group_rank_insight_tile_h - int(6 * scale),
                ),
                insight.value,
                max_fontsize=int(16 * scale),
                min_fontsize=int(10 * scale),
                fill=strong,
                halign="left",
                valign="center",
                font_families=[WATER_THEME.white],
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
