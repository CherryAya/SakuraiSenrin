"""Compact profile image renderer for water plugin."""

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
from src.repositories import member_repo
from src.services.info import resolve_group_card

from ..models import WaterProfileCardData
from ..common import SYS_FONT_NAME, WATER_THEME, draw_gloss_lines, draw_progress_bar
from .shared import (
    build_copyright_text,
    format_profile_exp,
    format_profile_rank,
    level_progress,
    seasonal_total_count,
    split_achievement_views,
)

FALLBACK_FONT_PATH = MAPLE_FONT_PATH


async def build_my_water_simple_image(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> bytes | None:
    try:
        theme = WATER_THEME
        scale = 2.0
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
        global_color = theme.global_color
        matrix_color = theme.matrix_color
        global_panel = theme.global_panel
        matrix_panel = theme.matrix_panel

        title_h = int(118 * scale)
        exp_title_h = int(18 * scale)
        exp_block_h = int(62 * scale)
        exp_block_gap = int(10 * scale)
        exp_panel_h = (
            exp_title_h
            + int(10 * scale)
            + exp_block_h * 2
            + exp_block_gap
            + int(12 * scale)
        )
        ach_title_h = int(18 * scale)
        ach_meta_h = int(14 * scale)
        ach_bar_h = int(10 * scale)
        ach_chip_h = int(20 * scale)
        ach_chip_gap_y = int(6 * scale)
        rank_title_h = int(18 * scale)
        rank_chip_h = int(32 * scale)
        rank_chip_gap = int(6 * scale)
        rank_panel_h = (
            rank_title_h
            + int(10 * scale)
            + rank_chip_h * 3
            + rank_chip_gap * 2
            + int(12 * scale)
        )
        footer_h = int(26 * scale)

        current_achievements, history_achievements = split_achievement_views(
            data.achievement_items,
            locale,
        )
        seasonal_total = max(1, seasonal_total_count(len(current_achievements)))
        seasonal_progress = len(current_achievements) / seasonal_total
        preview_items = current_achievements[:4]
        if not preview_items and history_achievements:
            preview_items = [title for title, _ in history_achievements[:4]]
        ach_rows = max(1, (len(preview_items) + 1) // 2) if preview_items else 1
        history_show = history_achievements[:4]
        history_rows = max(1, len(history_show))
        ach_panel_h = (
            ach_title_h
            + int(8 * scale)
            + ach_meta_h
            + int(6 * scale)
            + ach_bar_h
            + int(10 * scale)
            + ach_rows * ach_chip_h
            + (ach_rows - 1) * ach_chip_gap_y
            + int(12 * scale)
        )
        history_h = int((52 + history_rows * 24) * scale)

        height = (
            pad * 2
            + title_h
            + gap
            + exp_panel_h
            + gap
            + ach_panel_h
            + gap
            + rank_panel_h
            + gap
            + history_h
            + footer_h
        )

        card = BuildImage.new("RGB", (width, height), page_bg)

        avatar, group_avatar, member = await asyncio.gather(
            QQAvatar.fetch_user(data.user_id, size=int(80 * scale)),
            QQAvatar.fetch_group(data.group_id, size=int(24 * scale)),
            member_repo.get_member(data.user_id, data.group_id),
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
        avatar_size = int(80 * scale)
        card.paste(
            avatar.circle().resize((avatar_size, avatar_size)),
            (pad + int(12 * scale), y + int(10 * scale)),
            alpha=True,
        )
        title_x = pad + int(12 * scale) + avatar_size + int(12 * scale)
        title_right = width - pad
        card.draw_text(
            (title_x, y + int(8 * scale), title_right, y + int(30 * scale)),
            tr(locale, "water.profile.image.simple.title"),
            max_fontsize=int(22 * scale),
            min_fontsize=int(14 * scale),
            fill=title_main,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (title_x, y + int(30 * scale), title_right, y + int(52 * scale)),
            f"{username} | {data.group_name}",
            max_fontsize=int(14 * scale),
            min_fontsize=int(10 * scale),
            fill=title_sub,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (title_x, y + int(52 * scale), title_right, y + int(72 * scale)),
            tr(locale, "water.profile.image.matrix", matrix_id=data.matrix_id),
            max_fontsize=int(11 * scale),
            min_fontsize=int(9 * scale),
            fill=title_hint,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        chip_h = int(24 * scale)
        chip_w = min(
            int(260 * scale),
            width - title_x - pad - int(12 * scale),
        )
        chip_x = title_x
        chip_y = y + int(72 * scale)
        card.draw_rounded_rectangle(
            (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h),
            radius=int(8 * scale),
            fill=theme.matrix_group_active_bg,
        )
        if isinstance(group_avatar, BuildImage):
            g_avatar_size = chip_h - int(6 * scale)
            card.paste(
                group_avatar.circle().resize((g_avatar_size, g_avatar_size)),
                (chip_x + int(4 * scale), chip_y + int(3 * scale)),
                alpha=True,
            )
        card.draw_text(
            (
                chip_x + int(30 * scale),
                chip_y + int(2 * scale),
                chip_x + chip_w - int(8 * scale),
                chip_y + chip_h - int(2 * scale),
            ),
            data.group_name,
            max_fontsize=int(10 * scale),
            min_fontsize=int(8 * scale),
            fill=accent,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )

        y += title_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + exp_panel_h),
            radius=int(18 * scale),
            fill=panel_bg,
        )
        draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            exp_panel_h,
            tone=theme.gloss_profile_tone,
            strength=0.72,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(6 * scale),
                width - pad,
                y + exp_title_h + int(6 * scale),
            ),
            tr(locale, "water.profile.image.exp_overview"),
            max_fontsize=int(16 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )

        block_x = pad + int(18 * scale)
        block_w = width - pad * 2 - int(36 * scale)
        block_y = y + exp_title_h + int(8 * scale)

        global_exp = data.global_level[0] if data.global_level is not None else 0
        global_lv = data.global_level[2] if data.global_level is not None else 1
        matrix_exp = data.matrix_level[0] if data.matrix_level is not None else 0
        matrix_lv = data.matrix_level[2] if data.matrix_level is not None else 1
        global_ratio = (
            level_progress(global_exp, global_lv, 100)[0]
            if data.global_level is not None
            else 0.0
        )
        matrix_ratio = (
            level_progress(matrix_exp, matrix_lv, 100)[0]
            if data.matrix_level is not None
            else 0.0
        )
        global_text = format_profile_exp(global_exp) if data.global_level is not None else "-"
        matrix_text = format_profile_exp(matrix_exp) if data.matrix_level is not None else "-"

        for idx, (label, value, ratio, bg, fg) in enumerate(
            [
                (
                    tr(locale, "water.profile.image.exp.global"),
                    global_text,
                    global_ratio,
                    global_panel,
                    global_color,
                ),
                (
                    tr(locale, "water.profile.image.exp.matrix"),
                    matrix_text,
                    matrix_ratio,
                    matrix_panel,
                    matrix_color,
                ),
            ]
        ):
            by = block_y + idx * (exp_block_h + exp_block_gap)
            card.draw_rounded_rectangle(
                (block_x, by, block_x + block_w, by + exp_block_h),
                radius=int(10 * scale),
                fill=bg,
            )
            pct = f"{int(max(0.0, min(1.0, ratio)) * 100)}%"
            card.draw_text(
                (
                    block_x + int(10 * scale),
                    by + int(6 * scale),
                    block_x + block_w - int(80 * scale),
                    by + int(20 * scale),
                ),
                label,
                max_fontsize=int(12 * scale),
                min_fontsize=int(9 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    block_x + block_w - int(80 * scale),
                    by + int(6 * scale),
                    block_x + block_w - int(10 * scale),
                    by + int(20 * scale),
                ),
                pct,
                max_fontsize=int(12 * scale),
                min_fontsize=int(9 * scale),
                fill=fg,
                halign="right",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    block_x + int(10 * scale),
                    by + int(24 * scale),
                    block_x + block_w - int(10 * scale),
                    by + int(42 * scale),
                ),
                value,
                max_fontsize=int(18 * scale),
                min_fontsize=int(12 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            draw_progress_bar(
                card=card,
                x=block_x + int(10 * scale),
                y=by + int(44 * scale),
                w=block_w - int(20 * scale),
                h=int(10 * scale),
                progress=ratio,
                bg=theme.progress_global_bg if idx == 0 else theme.progress_season_bg,
                fg=fg,
            )

        y += exp_panel_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + ach_panel_h),
            radius=int(18 * scale),
            fill=panel_bg,
        )
        draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            ach_panel_h,
            tone=theme.gloss_profile_tone,
            strength=0.72,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(6 * scale),
                width - pad,
                y + ach_title_h + int(6 * scale),
            ),
            tr(locale, "water.profile.image.achievement_overview"),
            max_fontsize=int(16 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        meta_top = y + ach_title_h + int(6 * scale)
        card.draw_text(
            (
                pad + int(18 * scale),
                meta_top,
                width - pad - int(18 * scale),
                meta_top + ach_meta_h,
            ),
            tr(
                locale,
                "water.profile.fallback.season_achievement",
                count=len(current_achievements),
                total=seasonal_total,
            ),
            max_fontsize=int(12 * scale),
            min_fontsize=int(9 * scale),
            fill=accent,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        draw_progress_bar(
            card=card,
            x=pad + int(18 * scale),
            y=meta_top + ach_meta_h + int(2 * scale),
            w=width - pad * 2 - int(36 * scale),
            h=ach_bar_h,
            progress=seasonal_progress,
            bg=chip_bg,
            fg=strong,
        )
        chip_top = meta_top + ach_meta_h + ach_bar_h + int(10 * scale)
        chip_gap_x = int(8 * scale)
        chip_w = int((width - pad * 2 - int(36 * scale) - chip_gap_x) / 2)
        if not preview_items:
            card.draw_text(
                (
                    pad + int(18 * scale),
                    chip_top,
                    width - pad - int(18 * scale),
                    chip_top + ach_chip_h,
                ),
                tr(locale, "water.profile.image.achievement_empty"),
                max_fontsize=int(11 * scale),
                min_fontsize=int(9 * scale),
                fill=title_hint,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
        else:
            for idx, title in enumerate(preview_items):
                row = idx // 2
                col = idx % 2
                cx = pad + int(18 * scale) + col * (chip_w + chip_gap_x)
                cy = chip_top + row * (ach_chip_h + ach_chip_gap_y)
                card.draw_rounded_rectangle(
                    (cx, cy, cx + chip_w, cy + ach_chip_h),
                    radius=int(8 * scale),
                    fill=theme.achievement_chip_bg,
                )
                card.draw_text(
                    (
                        cx + int(8 * scale),
                        cy + int(2 * scale),
                        cx + chip_w - int(6 * scale),
                        cy + ach_chip_h - int(2 * scale),
                    ),
                    title,
                    max_fontsize=int(10 * scale),
                    min_fontsize=int(8 * scale),
                    fill=theme.achievement_chip_text,
                    halign="left",
                    font_families=[SYS_FONT_NAME],
                )

        y += ach_panel_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + rank_panel_h),
            radius=int(18 * scale),
            fill=panel_soft_bg,
        )
        draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            rank_panel_h,
            tone=theme.gloss_profile_soft_tone,
            strength=0.72,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(6 * scale),
                width - pad,
                y + rank_title_h + int(6 * scale),
            ),
            tr(locale, "water.profile.image.rank_overview"),
            max_fontsize=int(16 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        rank_items = [
            (
                tr(locale, "water.profile.image.rank.global"),
                format_profile_rank(data.global_rank, locale),
            ),
            (
                tr(locale, "water.profile.image.rank.group"),
                format_profile_rank(data.group_user_rank, locale),
            ),
            (
                tr(locale, "water.profile.image.rank.matrix"),
                format_profile_rank(data.matrix_user_rank, locale),
            ),
        ]
        chip_x = pad + int(18 * scale)
        chip_w = width - pad * 2 - int(36 * scale)
        chip_y = y + rank_title_h + int(8 * scale)
        for idx, (label, value) in enumerate(rank_items):
            cy = chip_y + idx * (rank_chip_h + rank_chip_gap)
            card.draw_rounded_rectangle(
                (chip_x, cy, chip_x + chip_w, cy + rank_chip_h),
                radius=int(10 * scale),
                fill=chip_bg if idx % 2 == 0 else chip_alt_bg,
            )
            card.draw_text(
                (
                    chip_x + int(10 * scale),
                    cy + int(4 * scale),
                    chip_x + chip_w - int(80 * scale),
                    cy + rank_chip_h - int(4 * scale),
                ),
                label,
                max_fontsize=int(11 * scale),
                min_fontsize=int(9 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    chip_x + chip_w - int(80 * scale),
                    cy + int(4 * scale),
                    chip_x + chip_w - int(10 * scale),
                    cy + rank_chip_h - int(4 * scale),
                ),
                value,
                max_fontsize=int(13 * scale),
                min_fontsize=int(10 * scale),
                fill=strong,
                halign="right",
                font_families=[SYS_FONT_NAME],
            )

        y += rank_panel_h + gap
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
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(6 * scale),
                width - pad,
                y + int(24 * scale),
            ),
            tr(locale, "water.profile.image.history_title"),
            max_fontsize=int(16 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        if not history_show:
            card.draw_text(
                (
                    pad + int(18 * scale),
                    y + int(28 * scale),
                    width - pad - int(18 * scale),
                    y + int(50 * scale),
                ),
                tr(locale, "water.profile.image.history_empty"),
                max_fontsize=int(11 * scale),
                min_fontsize=int(9 * scale),
                fill=title_hint,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
        else:
            row_h = int(24 * scale)
            list_start = y + int(30 * scale)
            date_col_w = int(86 * scale)
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
            (width / 2, height - int(30 * scale)),
            build_copyright_text(now.year),
            fill=accent,
            font=ImageFont.truetype(FALLBACK_FONT_PATH, int(12 * scale)),
            anchor="ms",
        )
        card.draw.text(
            (pad, height - int(12 * scale)),
            tr(
                locale,
                "water.image.generated_at",
                time=now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            fill=accent,
            font=ImageFont.truetype(FALLBACK_FONT_PATH, int(12 * scale)),
            anchor="ls",
        )

        return (await asyncio.to_thread(card.save, "PNG")).getvalue()
    except Exception as e:
        logger.exception(f"[Water] build_my_water_simple_image failed: {e}")
        return None
