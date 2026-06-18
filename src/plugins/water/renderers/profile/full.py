"""Detailed profile image renderer for water plugin."""

from __future__ import annotations

import asyncio

import arrow
from PIL import ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.plugins.water.renderers.common import (
    SYS_FONT_NAME,
    WATER_THEME,
    draw_gloss_lines,
    draw_progress_bar,
)
from src.plugins.water.renderers.models import WaterProfileCardData
from src.repositories import member_repo
from src.services.info import resolve_group_card

from .shared import (
    build_copyright_text,
    compute_profile_progress_metrics,
    format_profile_exp,
    format_profile_rank,
    seasonal_total_count,
    split_achievement_views,
)

FALLBACK_FONT_PATH = MAPLE_FONT_PATH


async def build_my_water_image(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> bytes | None:
    try:
        theme = WATER_THEME
        scale = 2.2
        width = int(680 * scale)
        pad = int(24 * scale)
        gap = int(10 * scale)
        page_bg = theme.page_bg
        title_panel_bg = theme.title_panel_bg
        panel_bg = theme.panel_bg
        panel_soft_bg = theme.profile_panel_soft_bg
        chip_bg = theme.chip_bg
        chip_alt_bg = theme.chip_alt_bg
        accent = theme.accent
        strong = theme.strong
        deep = theme.profile_deep
        title_main = theme.title_main
        title_sub = theme.title_sub
        title_hint = theme.title_hint
        season = theme.season
        success = theme.success
        my_value = theme.my_value
        group_value = theme.group_value

        current_achievements, history_achievements = split_achievement_views(
            data.achievement_items,
            locale,
        )
        seasonal_total = max(1, seasonal_total_count(len(current_achievements)))
        seasonal_progress = len(current_achievements) / seasonal_total
        preview_items = current_achievements
        preview_rows = max(1, (len(preview_items) + 1) // 2)

        chip_h = int(20 * scale)
        chip_gap_y = int(6 * scale)
        chip_top_rel = int(134 * scale)
        chip_block_end_rel = (
            chip_top_rel + preview_rows * chip_h + (preview_rows - 1) * chip_gap_y
        )

        exp_row_h = int(14 * scale)
        exp_title_h = int(15 * scale)
        exp_meta_h = int(14 * scale)
        exp_row_gap = int(8 * scale)
        exp_row_block = exp_title_h + exp_meta_h + exp_row_h + exp_row_gap
        exp_header_h = int(20 * scale)
        progress_rows_h = 3 * exp_row_block - exp_row_gap
        col_panel_h = exp_header_h + int(8 * scale) + progress_rows_h + int(10 * scale)
        exp_panel_top_rel = chip_block_end_rel + int(16 * scale)

        history_show = history_achievements[:7]
        history_rows = max(1, len(history_show))
        matrix_groups = data.matrix_groups or [(data.group_id, data.group_name)]
        group_rows = max(1, (len(matrix_groups) + 1) // 2)
        group_chip_h = int(24 * scale)
        group_chip_gap_y = int(6 * scale)
        group_block_h = (
            int(18 * scale)
            + group_rows * group_chip_h
            + (group_rows - 1) * group_chip_gap_y
        )
        title_h = int(104 * scale) + group_block_h
        status_h = exp_panel_top_rel + col_panel_h + int(14 * scale)
        rank_h = int(162 * scale)
        history_h = int((56 + history_rows * 28) * scale)
        footer_h = int(34 * scale)
        height = (
            pad * 2
            + title_h
            + gap
            + status_h
            + gap
            + rank_h
            + gap
            + history_h
            + footer_h
        )

        card = BuildImage.new("RGB", (width, height), page_bg)

        avatar, member, matrix_group_avatars = await asyncio.gather(
            QQAvatar.fetch_user(data.user_id, size=int(92 * scale)),
            member_repo.get_member(data.user_id, data.group_id),
            asyncio.gather(
                *(
                    QQAvatar.fetch_group(group_id, size=int(24 * scale))
                    for group_id, _ in matrix_groups
                ),
                return_exceptions=True,
            ),
        )
        if member:
            username = await resolve_group_card(None, data.user_id, data.group_id)
        else:
            username = data.username

        y = pad
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + title_h),
            radius=int(18 * scale),
            fill=title_panel_bg,
        )
        card.paste(
            avatar.circle(),
            (pad + int(14 * scale), y + int(12 * scale)),
            alpha=True,
        )
        title_x = pad + int(112 * scale)
        card.draw_text(
            (title_x, y + int(10 * scale), width - pad, y + int(42 * scale)),
            tr(locale, "water.profile.image.full.title"),
            max_fontsize=int(26 * scale),
            min_fontsize=int(14 * scale),
            fill=title_main,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (title_x, y + int(40 * scale), width - pad, y + int(72 * scale)),
            f"{username}  |  {data.group_name}",
            max_fontsize=int(15 * scale),
            min_fontsize=int(12 * scale),
            fill=title_sub,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (title_x, y + int(68 * scale), width - pad, y + int(96 * scale)),
            tr(locale, "water.profile.image.current_matrix", matrix_id=data.matrix_id),
            max_fontsize=int(12 * scale),
            min_fontsize=int(10 * scale),
            fill=title_hint,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        group_block_top = y + int(96 * scale)
        card.draw_text(
            (
                title_x,
                group_block_top,
                width - pad - int(12 * scale),
                group_block_top + int(16 * scale),
            ),
            tr(locale, "water.profile.image.matrix_groups"),
            max_fontsize=int(11 * scale),
            min_fontsize=int(9 * scale),
            fill=title_hint,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        chip_top = group_block_top + int(16 * scale)
        chip_gap_x = int(8 * scale)
        chip_w = int((width - title_x - pad - int(12 * scale) - chip_gap_x) / 2)
        for idx, (group_id, group_name) in enumerate(matrix_groups):
            row = idx // 2
            col = idx % 2
            chip_x = title_x + col * (chip_w + chip_gap_x)
            chip_y = chip_top + row * (group_chip_h + group_chip_gap_y)
            card.draw_rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_w, chip_y + group_chip_h),
                radius=int(8 * scale),
                fill=(
                    theme.matrix_group_active_bg
                    if group_id == data.group_id
                    else theme.matrix_group_inactive_bg
                ),
            )
            avatar_item = matrix_group_avatars[idx]
            if isinstance(avatar_item, BuildImage):
                avatar_size = int(group_chip_h - int(6 * scale))
                card.paste(
                    avatar_item.circle().resize((avatar_size, avatar_size)),
                    (chip_x + int(4 * scale), chip_y + int(3 * scale)),
                    alpha=True,
                )
            card.draw_text(
                (
                    chip_x + int(30 * scale),
                    chip_y + int(2 * scale),
                    chip_x + chip_w - int(8 * scale),
                    chip_y + group_chip_h - int(2 * scale),
                ),
                group_name,
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )

        y += title_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + status_h),
            radius=int(18 * scale),
            fill=panel_bg,
        )
        card.draw_rounded_rectangle(
            (
                pad + int(10 * scale),
                y + int(14 * scale),
                pad + int(16 * scale),
                y + int(44 * scale),
            ),
            radius=int(3 * scale),
            fill=season,
        )
        progress_metrics = compute_profile_progress_metrics(data)

        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(8 * scale),
                width - pad,
                y + int(30 * scale),
            ),
            tr(locale, "water.profile.image.level_overview"),
            max_fontsize=int(17 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        metric_top = y + int(34 * scale)
        metric_h = int(34 * scale)
        metric_gap = int(10 * scale)
        metric_w = int((width - pad * 2 - int(40 * scale) - metric_gap) / 2)
        left_metric_x = pad + int(20 * scale)
        right_metric_x = left_metric_x + metric_w + metric_gap
        card.draw_rounded_rectangle(
            (
                left_metric_x,
                metric_top,
                left_metric_x + metric_w,
                metric_top + metric_h,
            ),
            radius=int(8 * scale),
            fill=theme.matrix_panel,
        )
        card.draw_rounded_rectangle(
            (
                right_metric_x,
                metric_top,
                right_metric_x + metric_w,
                metric_top + metric_h,
            ),
            radius=int(8 * scale),
            fill=theme.global_panel,
        )
        card.draw_text(
            (
                left_metric_x + int(8 * scale),
                metric_top + int(5 * scale),
                left_metric_x + metric_w - int(8 * scale),
                metric_top + metric_h - int(5 * scale),
            ),
            tr(
                locale,
                "water.profile.image.metric.matrix_summary",
                level=progress_metrics.matrix_lv,
                total=format_profile_exp(progress_metrics.matrix_exp),
                season=format_profile_exp(progress_metrics.matrix_season),
            ),
            max_fontsize=int(11 * scale),
            min_fontsize=int(8 * scale),
            fill=accent,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                right_metric_x + int(8 * scale),
                metric_top + int(5 * scale),
                right_metric_x + metric_w - int(8 * scale),
                metric_top + metric_h - int(5 * scale),
            ),
            tr(
                locale,
                "water.profile.image.metric.global_summary",
                level=progress_metrics.global_lv,
                total=format_profile_exp(progress_metrics.global_exp),
                season=format_profile_exp(progress_metrics.global_season),
            ),
            max_fontsize=int(11 * scale),
            min_fontsize=int(8 * scale),
            fill=accent,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                pad + int(20 * scale),
                y + int(72 * scale),
                width - pad - int(20 * scale),
                y + int(92 * scale),
            ),
            tr(
                locale,
                "water.profile.fallback.season_achievement",
                count=len(current_achievements),
                total=seasonal_total,
            ),
            max_fontsize=int(13 * scale),
            min_fontsize=int(10 * scale),
            fill=season,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        draw_progress_bar(
            card=card,
            x=pad + int(20 * scale),
            y=y + int(96 * scale),
            w=width - pad * 2 - int(40 * scale),
            h=int(14 * scale),
            progress=seasonal_progress,
            bg=chip_bg,
            fg=season,
        )
        card.draw_text(
            (
                pad + int(20 * scale),
                y + int(114 * scale),
                width - pad - int(20 * scale),
                y + int(132 * scale),
            ),
            tr(locale, "water.profile.image.recent_achievements"),
            max_fontsize=int(12 * scale),
            min_fontsize=int(9 * scale),
            fill=accent,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        chip_block_end = y + int(160 * scale)
        if not preview_items:
            card.draw_text(
                (
                    pad + int(20 * scale),
                    y + int(134 * scale),
                    width - pad - int(20 * scale),
                    y + int(160 * scale),
                ),
                tr(locale, "water.profile.image.recent_achievements.empty"),
                max_fontsize=int(12 * scale),
                min_fontsize=int(9 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
        else:
            chip_top = y + int(134 * scale)
            chip_gap_x = int(8 * scale)
            chip_w = int((width - pad * 2 - int(40 * scale) - chip_gap_x) / 2)
            chip_rows = (len(preview_items) + 1) // 2
            chip_block_end = (
                chip_top + chip_rows * chip_h + (chip_rows - 1) * chip_gap_y
            )
            for idx, title in enumerate(preview_items):
                row = idx // 2
                col = idx % 2
                cx = pad + int(20 * scale) + col * (chip_w + chip_gap_x)
                cy = chip_top + row * (chip_h + chip_gap_y)
                card.draw_rounded_rectangle(
                    (cx, cy, cx + chip_w, cy + chip_h),
                    radius=int(8 * scale),
                    fill=theme.achievement_chip_bg,
                )
                card.draw_text(
                    (
                        cx + int(10 * scale),
                        cy + int(2 * scale),
                        cx + chip_w - int(10 * scale),
                        cy + chip_h - int(2 * scale),
                    ),
                    title,
                    max_fontsize=int(10 * scale),
                    min_fontsize=int(8 * scale),
                    fill=theme.achievement_chip_text,
                    halign="center",
                    font_families=[SYS_FONT_NAME],
                )

        seasonal_progress_items = [
            (
                tr(locale, "water.profile.image.progress.global_season"),
                progress_metrics.sg_gap,
                progress_metrics.sg_ratio,
            ),
            (
                tr(locale, "water.profile.image.progress.matrix_season"),
                progress_metrics.sm_gap,
                progress_metrics.sm_ratio,
            ),
            (
                tr(locale, "water.profile.image.progress.matrix_total_season"),
                progress_metrics.smt_gap,
                progress_metrics.smt_ratio,
            ),
        ]
        global_progress_items = [
            (
                tr(locale, "water.profile.image.progress.global_total"),
                progress_metrics.gg_gap,
                progress_metrics.gg_ratio,
            ),
            (
                tr(locale, "water.profile.image.progress.matrix_total"),
                progress_metrics.gm_gap,
                progress_metrics.gm_ratio,
            ),
            (
                tr(locale, "water.profile.image.progress.matrix_total_all"),
                progress_metrics.gmt_gap,
                progress_metrics.gmt_ratio,
            ),
        ]
        exp_panel_top = chip_block_end + int(16 * scale)
        col_gap = int(14 * scale)
        col_w = int((width - pad * 2 - int(40 * scale) - col_gap) / 2)
        left_x = pad + int(20 * scale)
        right_x = left_x + col_w + col_gap
        text_color = theme.text_color_dark
        meta_color = theme.meta_color_dark

        card.draw_rounded_rectangle(
            (left_x, exp_panel_top, left_x + col_w, exp_panel_top + col_panel_h),
            radius=int(10 * scale),
            fill=theme.status_global_panel,
        )
        draw_gloss_lines(
            card,
            left_x,
            exp_panel_top,
            col_w,
            col_panel_h,
            tone=theme.gloss_global_tone,
            strength=0.8,
        )
        card.draw_rounded_rectangle(
            (right_x, exp_panel_top, right_x + col_w, exp_panel_top + col_panel_h),
            radius=int(10 * scale),
            fill=theme.status_season_panel,
        )
        draw_gloss_lines(
            card,
            right_x,
            exp_panel_top,
            col_w,
            col_panel_h,
            tone=theme.gloss_season_tone,
            strength=0.8,
        )

        card.draw_text(
            (
                left_x + int(10 * scale),
                exp_panel_top + int(3 * scale),
                left_x + col_w - int(10 * scale),
                exp_panel_top + exp_header_h,
            ),
            tr(locale, "water.profile.image.progress.global_section"),
            max_fontsize=int(12 * scale),
            min_fontsize=int(10 * scale),
            fill=theme.status_global_title,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                right_x + int(10 * scale),
                exp_panel_top + int(3 * scale),
                right_x + col_w - int(10 * scale),
                exp_panel_top + exp_header_h,
            ),
            tr(locale, "water.profile.image.progress.season_section"),
            max_fontsize=int(12 * scale),
            min_fontsize=int(10 * scale),
            fill=theme.status_season_title,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        exp_rows_top = exp_panel_top + exp_header_h + int(10 * scale)

        for idx, (label, gap_value, ratio) in enumerate(global_progress_items):
            row_top = exp_rows_top + idx * exp_row_block
            pct = f"{int(max(0.0, min(1.0, ratio)) * 100)}%"
            card.draw_text(
                (
                    left_x + int(10 * scale),
                    row_top,
                    left_x + col_w - int(74 * scale),
                    row_top + exp_title_h,
                ),
                label,
                max_fontsize=int(12 * scale),
                min_fontsize=int(10 * scale),
                fill=text_color,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    left_x + col_w - int(74 * scale),
                    row_top,
                    left_x + col_w - int(10 * scale),
                    row_top + exp_title_h,
                ),
                pct,
                max_fontsize=int(12 * scale),
                min_fontsize=int(10 * scale),
                fill=theme.status_global_title,
                halign="right",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    left_x + int(10 * scale),
                    row_top + exp_title_h,
                    left_x + col_w - int(10 * scale),
                    row_top + exp_title_h + exp_meta_h,
                ),
                tr(
                    locale,
                    "water.profile.image.progress.gap",
                    exp=format_profile_exp(gap_value),
                ),
                max_fontsize=int(10 * scale),
                min_fontsize=int(9 * scale),
                fill=meta_color,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            draw_progress_bar(
                card=card,
                x=left_x + int(10 * scale),
                y=row_top + exp_title_h + exp_meta_h,
                w=col_w - int(20 * scale),
                h=exp_row_h,
                progress=ratio,
                bg=theme.progress_global_bg,
                fg=theme.global_color,
            )
        for idx, (label, gap_value, ratio) in enumerate(seasonal_progress_items):
            row_top = exp_rows_top + idx * exp_row_block
            pct = f"{int(max(0.0, min(1.0, ratio)) * 100)}%"
            card.draw_text(
                (
                    right_x + int(10 * scale),
                    row_top,
                    right_x + col_w - int(74 * scale),
                    row_top + exp_title_h,
                ),
                label,
                max_fontsize=int(12 * scale),
                min_fontsize=int(10 * scale),
                fill=text_color,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    right_x + col_w - int(74 * scale),
                    row_top,
                    right_x + col_w - int(10 * scale),
                    row_top + exp_title_h,
                ),
                pct,
                max_fontsize=int(12 * scale),
                min_fontsize=int(10 * scale),
                fill=theme.status_season_title,
                halign="right",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    right_x + int(10 * scale),
                    row_top + exp_title_h,
                    right_x + col_w - int(10 * scale),
                    row_top + exp_title_h + exp_meta_h,
                ),
                tr(
                    locale,
                    "water.profile.image.progress.gap",
                    exp=format_profile_exp(gap_value),
                ),
                max_fontsize=int(10 * scale),
                min_fontsize=int(9 * scale),
                fill=meta_color,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            draw_progress_bar(
                card=card,
                x=right_x + int(10 * scale),
                y=row_top + exp_title_h + exp_meta_h,
                w=col_w - int(20 * scale),
                h=exp_row_h,
                progress=ratio,
                bg=theme.progress_season_bg,
                fg=theme.matrix_color,
            )

        y = max(y + status_h, exp_panel_top + col_panel_h + int(12 * scale)) + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + rank_h),
            radius=int(18 * scale),
            fill=panel_soft_bg,
        )
        draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            rank_h,
            tone=theme.gloss_profile_soft_tone,
            strength=0.72,
        )
        card.draw_rounded_rectangle(
            (
                pad + int(10 * scale),
                y + int(14 * scale),
                pad + int(16 * scale),
                y + int(44 * scale),
            ),
            radius=int(3 * scale),
            fill=strong,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(10 * scale),
                width - pad,
                y + int(34 * scale),
            ),
            tr(locale, "water.profile.image.rank_panel"),
            max_fontsize=int(17 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        global_meta = (
            tr(
                locale,
                "water.profile.image.rank.meta.level",
                level=data.global_level[2],
                exp=format_profile_exp(data.global_level[0]),
            )
            if data.global_level is not None
            else tr(
                locale,
                "water.profile.image.rank.meta.level",
                level="-",
                exp="-",
            )
        )
        matrix_user_meta = (
            tr(
                locale,
                "water.profile.image.rank.meta.level",
                level=data.matrix_level[2],
                exp=format_profile_exp(data.matrix_level[0]),
            )
            if data.matrix_level is not None
            else tr(
                locale,
                "water.profile.image.rank.meta.level",
                level="-",
                exp="-",
            )
        )
        matrix_total_meta = (
            tr(
                locale,
                "water.profile.image.rank.meta.level",
                level=data.matrix_total_level[2],
                exp=format_profile_exp(data.matrix_total_level[0]),
            )
            if data.matrix_total_level is not None
            else tr(
                locale,
                "water.profile.image.rank.meta.level",
                level="-",
                exp="-",
            )
        )
        group_total_meta = tr(locale, "water.profile.image.rank.meta.msg_count")
        my_rank_items = [
            (
                tr(locale, "water.profile.image.rank.global"),
                format_profile_rank(data.global_rank, locale),
                global_meta,
            ),
            (
                tr(locale, "water.profile.image.rank.group"),
                format_profile_rank(data.group_user_rank, locale),
                tr(locale, "water.profile.image.rank.meta.msg_count"),
            ),
            (
                tr(locale, "water.profile.image.rank.matrix"),
                format_profile_rank(data.matrix_user_rank, locale),
                matrix_user_meta,
            ),
        ]
        group_rank_items = [
            (
                tr(locale, "water.profile.image.rank.matrix_total"),
                format_profile_rank(data.matrix_rank, locale),
                matrix_total_meta,
            ),
            (
                tr(locale, "water.profile.image.rank.group_active"),
                format_profile_rank(data.group_rank, locale),
                group_total_meta,
            ),
        ]
        section_y = y + int(34 * scale)
        side_gap = int(16 * scale)
        col_gap = int(12 * scale)
        col_w = int((width - pad * 2 - side_gap * 2 - col_gap) / 2)
        col_x_left = pad + side_gap
        col_x_right = col_x_left + col_w + col_gap

        chip_gap = int(6 * scale)
        left_chip_h = int(34 * scale)
        left_total_h = left_chip_h * 3 + chip_gap * 2
        right_chip_h = int((left_total_h - chip_gap) / 2)
        left_start = section_y
        right_start = section_y

        for idx, (label, value, meta) in enumerate(my_rank_items):
            lx = col_x_left
            ly = left_start + idx * (left_chip_h + chip_gap)
            card.draw_rounded_rectangle(
                (lx, ly, lx + col_w, ly + left_chip_h),
                radius=int(10 * scale),
                fill=chip_bg if idx % 2 == 0 else theme.left_chip_alt_bg,
            )
            card.draw_text(
                (
                    lx + int(12 * scale),
                    ly + int(3 * scale),
                    lx + col_w - int(86 * scale),
                    ly + int(14 * scale),
                ),
                label,
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    lx + int(12 * scale),
                    ly + int(14 * scale),
                    lx + col_w - int(86 * scale),
                    ly + left_chip_h - int(3 * scale),
                ),
                meta,
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=theme.left_chip_meta,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    lx + col_w - int(82 * scale),
                    ly + int(3 * scale),
                    lx + col_w - int(12 * scale),
                    ly + left_chip_h - int(4 * scale),
                ),
                value,
                max_fontsize=int(14 * scale),
                min_fontsize=int(10 * scale),
                fill=my_value,
                halign="right",
                font_families=[SYS_FONT_NAME],
            )

        for idx, (label, value, meta) in enumerate(group_rank_items):
            rx = col_x_right
            ry = right_start + idx * (right_chip_h + chip_gap)
            card.draw_rounded_rectangle(
                (rx, ry, rx + col_w, ry + right_chip_h),
                radius=int(10 * scale),
                fill=chip_alt_bg if idx % 2 == 0 else theme.right_chip_alt_bg,
            )
            card.draw_text(
                (
                    rx + int(12 * scale),
                    ry + int(3 * scale),
                    rx + col_w - int(86 * scale),
                    ry + int(14 * scale),
                ),
                label,
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    rx + int(12 * scale),
                    ry + int(14 * scale),
                    rx + col_w - int(86 * scale),
                    ry + right_chip_h - int(3 * scale),
                ),
                meta,
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=theme.right_chip_meta,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    rx + col_w - int(82 * scale),
                    ry + int(3 * scale),
                    rx + col_w - int(12 * scale),
                    ry + right_chip_h - int(4 * scale),
                ),
                value,
                max_fontsize=int(14 * scale),
                min_fontsize=int(10 * scale),
                fill=group_value,
                halign="right",
                font_families=[SYS_FONT_NAME],
            )

        y += rank_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + history_h),
            radius=int(18 * scale),
            fill=panel_bg,
        )
        draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            history_h,
            tone=theme.gloss_profile_tone,
            strength=0.72,
        )
        card.draw_rounded_rectangle(
            (
                pad + int(10 * scale),
                y + int(14 * scale),
                pad + int(16 * scale),
                y + int(44 * scale),
            ),
            radius=int(3 * scale),
            fill=success,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(10 * scale),
                width - pad,
                y + int(32 * scale),
            ),
            tr(locale, "water.profile.image.history_title"),
            max_fontsize=int(17 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        if not history_show:
            card.draw_text(
                (
                    pad + int(18 * scale),
                    y + int(34 * scale),
                    width - pad - int(18 * scale),
                    y + int(62 * scale),
                ),
                tr(locale, "water.profile.image.history_empty"),
                max_fontsize=int(12 * scale),
                min_fontsize=int(10 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
        else:
            row_h = int(28 * scale)
            list_start = y + int(34 * scale)
            date_col_w = int(80 * scale)
            for idx, (title, date_text) in enumerate(history_show):
                row_top = list_start + idx * row_h
                if idx > 0:
                    card.draw.line(
                        (
                            pad + int(18 * scale),
                            row_top,
                            width - pad - int(18 * scale),
                            row_top,
                        ),
                        fill=theme.history_divider,
                        width=max(1, int(1.2 * scale)),
                    )
                card.draw_text(
                    (
                        pad + int(18 * scale),
                        row_top + int(3 * scale),
                        width - pad - date_col_w - int(10 * scale),
                        row_top + row_h - int(3 * scale),
                    ),
                    title,
                    max_fontsize=int(11 * scale),
                    min_fontsize=int(9 * scale),
                    fill=accent,
                    halign="left",
                    font_families=[SYS_FONT_NAME],
                )
                card.draw_text(
                    (
                        width - pad - date_col_w,
                        row_top + int(3 * scale),
                        width - pad - int(18 * scale),
                        row_top + row_h - int(3 * scale),
                    ),
                    date_text,
                    max_fontsize=int(11 * scale),
                    min_fontsize=int(9 * scale),
                    fill=strong,
                    halign="right",
                    font_families=[SYS_FONT_NAME],
                )

        now = arrow.get(get_current_time()).datetime
        card.draw.text(
            (width / 2, height - int(36 * scale)),
            build_copyright_text(now.year),
            fill=accent,
            font=ImageFont.truetype(FALLBACK_FONT_PATH, int(13 * scale)),
            anchor="ms",
        )
        card.draw.text(
            (pad, height - int(18 * scale)),
            tr(
                locale,
                "water.image.generated_at",
                time=now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            fill=accent,
            font=ImageFont.truetype(FALLBACK_FONT_PATH, int(13 * scale)),
            anchor="ls",
        )

        return (await asyncio.to_thread(card.save, "PNG")).getvalue()
    except Exception as e:
        logger.exception(f"[Water] build_my_water_image failed: {e}")
        return None
