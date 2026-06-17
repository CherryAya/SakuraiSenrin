"""Standalone day-rank renderer for water plugin images."""

from __future__ import annotations

import asyncio
from typing import Any

from PIL import ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .image_primitives import (
    SYS_FONT_NAME,
    WATER_THEME,
    draw_report_footer,
    water_podium_themes,
)

FALLBACK_FONT_PATH = MAPLE_FONT_PATH


class WaterRankRenderer:
    def __init__(self) -> None:
        self.theme = WATER_THEME
        self.BG_COLOR = self.theme.page_bg
        self.TEXT_COLOR = self.theme.text_color
        self.ITEM_BG_COLOR = self.theme.item_bg
        self.HIGHLIGHT_COLOR = self.theme.highlight_color
        self.MUTED_COLOR = self.theme.muted_color
        self.HEADER_BG = self.theme.header_bg
        self.HEADER_TEXT = self.theme.header_text
        self.SUBTEXT_COLOR = self.theme.subtext_color
        self.TILE_BASE_COLORS = self.theme.tile_base_colors
        self.RANK_THEMES = {
            rank: {"bg": bg, "badge": badge, "badge_txt": fg}
            for rank, (bg, badge, fg) in water_podium_themes(self.theme).items()
        }
        self.SCALE = 3.2
        self.RENDER_WIDTH = int(900 * self.SCALE)
        self.PADDING = int(60 * self.SCALE)

        try:
            self.num_small_font = ImageFont.truetype(
                FALLBACK_FONT_PATH, int(18 * self.SCALE)
            )
            self.num_tiny_font = ImageFont.truetype(
                FALLBACK_FONT_PATH, int(22 * self.SCALE * 0.55)
            )
        except OSError:
            self.num_small_font = ImageFont.load_default()
            self.num_tiny_font = ImageFont.load_default()

    def _safe_truncate(self, text: str, max_len: int = 16) -> str:
        text = text.replace("\n", " ").replace("\r", "").replace("\t", " ")
        return text[:max_len] + "..." if len(text) > max_len else text

    def _generate_tile_chart(self, hourly_data: list[int]) -> BuildImage:
        rows, cols = 2, 12
        tile_spacing = int(4 * self.SCALE)
        tile_size = int(22 * self.SCALE)
        chart_width = cols * tile_size + (cols - 1) * tile_spacing
        chart_height = rows * tile_size + (rows - 1) * tile_spacing

        chart = BuildImage.new("RGBA", (chart_width, chart_height), (0, 0, 0, 0))
        max_count = max(hourly_data) or 1

        for hour in range(24):
            row, col = hour // cols, hour % cols
            x0 = col * (tile_size + tile_spacing)
            y0 = row * (tile_size + tile_spacing)

            alpha = int(40 + (255 - 40) * (hourly_data[hour] / max_count))
            base_color = self.TILE_BASE_COLORS[(hour // 6) % len(self.TILE_BASE_COLORS)]
            color_hex = f"{base_color}{alpha:02X}"

            chart.draw_rounded_rectangle(
                (x0, y0, x0 + tile_size, y0 + tile_size),
                radius=int(4 * self.SCALE),
                fill=color_hex,
                outline=f"{base_color}FF",
                width=int(1 * self.SCALE),
            )

            text_fill = (
                (255, 255, 255, 255 if alpha < 150 else min(alpha + 100, 255))
                if alpha > 128
                else f"{self.TEXT_COLOR}FF"
            )
            center_x, center_y = (
                x0 + tile_size / 2,
                y0 + tile_size / 2,
            )
            chart.draw.text(
                (center_x, center_y),
                f"{hour:02d}",
                fill=text_fill,
                font=self.num_tiny_font,
                anchor="mm",
            )

        return chart

    def _render_user_row(
        self,
        rank: int,
        user: dict[str, Any],
        locale: LocaleCode,
    ) -> BuildImage:
        item_h = int(110 * self.SCALE)
        avatar_size = int(64 * self.SCALE)
        base_y = int(10 * self.SCALE)

        row = BuildImage.new("RGBA", (self.RENDER_WIDTH, item_h + base_y), (0, 0, 0, 0))
        default_bg = self.ITEM_BG_COLOR if rank % 2 == 1 else self.theme.item_bg_alt
        theme = self.RANK_THEMES.get(rank, {"bg": default_bg, "badge": None})

        row.draw_rounded_rectangle(
            (self.PADDING, base_y, self.RENDER_WIDTH - self.PADDING, base_y + item_h),
            radius=int(15 * self.SCALE),
            fill=theme["bg"],
        )
        avatar_x = self.PADDING + int(20 * self.SCALE)
        avatar_y = base_y + (item_h - avatar_size) // 2
        row.paste(
            user["avatar_img"].circle().resize((avatar_size, avatar_size)),
            (avatar_x, avatar_y),
            alpha=True,
        )

        trend = user.get("trend")
        t_str, t_color = (
            ("NEW", self.theme.trend_new)
            if trend is None
            else (f"↑ {trend}", self.theme.trend_up)
            if trend > 0
            else (f"↓ {abs(trend)}", self.theme.trend_down)
            if trend < 0
            else ("− 0", self.theme.trend_flat)
        )

        badge_x, badge_y = (
            self.PADDING - int(10 * self.SCALE),
            base_y - int(10 * self.SCALE),
        )
        pill_w, pill_h = int(52 * self.SCALE), int(24 * self.SCALE)
        row.draw_rounded_rectangle(
            (badge_x, badge_y, badge_x + pill_w, badge_y + pill_h),
            radius=pill_h // 2,
            fill=t_color,
            outline=self.theme.white,
            width=int(2.5 * self.SCALE),
        )
        row.draw.text(
            (badge_x + pill_w / 2, badge_y + pill_h / 2),
            t_str,
            fill=self.theme.white,
            font=self.num_small_font,
            anchor="mm",
        )

        text_x = avatar_x + avatar_size + int(20 * self.SCALE)
        chart = self._generate_tile_chart(user["hourly_data"])
        chart_x = self.RENDER_WIDTH - self.PADDING - chart.width - int(20 * self.SCALE)
        max_name_width = chart_x - text_x - int(10 * self.SCALE)
        display_name = self._safe_truncate(user["username"], max_len=16)
        row_center_y = base_y + int(35 * self.SCALE)

        if theme["badge"]:
            badge_text = f"TOP {rank}"
            b_pad_x, b_pad_y = int(8 * self.SCALE), int(5 * self.SCALE)
            b_width = int(row.draw.textlength(badge_text, font=self.num_small_font))
            badge_h = int(18 * self.SCALE) + b_pad_y * 2
            box_rect = (
                text_x,
                row_center_y - badge_h // 2,
                text_x + b_width + b_pad_x * 2,
                row_center_y + badge_h // 2,
            )
            row.draw_rounded_rectangle(
                box_rect, radius=int(6 * self.SCALE), fill=theme["badge"]
            )
            row.draw.text(
                ((box_rect[0] + box_rect[2]) / 2, row_center_y + int(0.5 * self.SCALE)),
                badge_text,
                fill=theme["badge_txt"],
                font=self.num_small_font,
                anchor="mm",
            )
            name_x = box_rect[2] + int(10 * self.SCALE)
        else:
            badge_text = f"#{rank:02d}"
            b_width = int(row.draw.textlength(badge_text, font=self.num_small_font))
            row.draw.text(
                (text_x, row_center_y),
                badge_text,
                fill=self.MUTED_COLOR,
                font=self.num_small_font,
                anchor="lm",
            )
            name_x = text_x + b_width + int(8 * self.SCALE)

        skia_fix_y = int(-0.5 * self.SCALE)
        box_coords = (
            name_x,
            row_center_y - int(20 * self.SCALE) + skia_fix_y,
            name_x + max_name_width - (name_x - text_x),
            row_center_y + int(20 * self.SCALE) + skia_fix_y,
        )

        try:
            row.draw_text(
                box_coords,
                display_name,
                max_fontsize=int(24 * self.SCALE),
                min_fontsize=int(12 * self.SCALE),
                fill=self.TEXT_COLOR,
                halign="left",
                valign="center",
            )
        except ValueError:
            row.draw_text(
                box_coords,
                tr(locale, "water.image.name_chaos"),
                max_fontsize=int(20 * self.SCALE),
                min_fontsize=int(12 * self.SCALE),
                fill=self.TEXT_COLOR,
                halign="left",
                valign="center",
            )

        row.draw.text(
            (text_x, base_y + int(65 * self.SCALE)),
            tr(locale, "water.image.day_rank.row_count", count=user["count"]),
            fill=self.HIGHLIGHT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )
        chart_y = base_y + (item_h - chart.height) // 2
        row.paste(chart, (chart_x, chart_y), alpha=True)
        return row

    async def render_async(
        self,
        group_id: str,
        group_name: str,
        group_avatar: BuildImage,
        today_king: str,
        group_rank: int,
        users_data: dict[str, dict[str, Any]],
        locale: LocaleCode,
        *,
        header_title: str | None = None,
        summary_text: str | None = None,
        footer_text: str | None = None,
        scope_label: str | None = None,
    ) -> bytes:
        del scope_label
        item_h, item_spacing = int(110 * self.SCALE), int(20 * self.SCALE)
        header_height = int(300 * self.SCALE)
        total_height = (
            header_height
            + len(users_data) * (item_h + item_spacing)
            + int(200 * self.SCALE)
        )

        main_img = BuildImage.new(
            "RGB", (self.RENDER_WIDTH, total_height), self.BG_COLOR
        )
        y = self.PADDING

        main_img.draw_text(
            (
                self.PADDING,
                y,
                self.RENDER_WIDTH - self.PADDING,
                y + int(50 * self.SCALE),
            ),
            header_title or tr(locale, "water.image.day_rank.header"),
            max_fontsize=int(40 * self.SCALE),
            fill=self.HEADER_TEXT,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        y += int(62 * self.SCALE)

        safe_group_name = self._safe_truncate(group_name, max_len=30)
        group_card_h = int(44 * self.SCALE)
        group_card_top = y
        group_avatar_size = int(30 * self.SCALE)
        group_avatar_x = self.PADDING + int(10 * self.SCALE)
        group_avatar_y = group_card_top + (group_card_h - group_avatar_size) // 2
        main_img.draw_rounded_rectangle(
            (
                self.PADDING,
                group_card_top,
                self.RENDER_WIDTH - self.PADDING,
                group_card_top + group_card_h,
            ),
            radius=int(12 * self.SCALE),
            fill=self.HEADER_BG,
        )
        main_img.paste(
            group_avatar.circle().resize((group_avatar_size, group_avatar_size)),
            (group_avatar_x, group_avatar_y),
            alpha=True,
        )
        main_img.draw_text(
            (
                group_avatar_x + group_avatar_size + int(12 * self.SCALE),
                group_card_top + int(2 * self.SCALE),
                self.RENDER_WIDTH - self.PADDING - int(16 * self.SCALE),
                group_card_top + int(22 * self.SCALE),
            ),
            safe_group_name,
            max_fontsize=int(18 * self.SCALE),
            min_fontsize=int(13 * self.SCALE),
            fill=self.HEADER_TEXT,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        main_img.draw_text(
            (
                group_avatar_x + group_avatar_size + int(12 * self.SCALE),
                group_card_top + int(20 * self.SCALE),
                self.RENDER_WIDTH - self.PADDING - int(16 * self.SCALE),
                group_card_top + group_card_h - int(2 * self.SCALE),
            ),
            tr(locale, "water.rank.secondary.group", entity_id=group_id),
            max_fontsize=int(12 * self.SCALE),
            min_fontsize=int(9 * self.SCALE),
            fill=self.SUBTEXT_COLOR,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        y += group_card_h + int(10 * self.SCALE)

        info_text = summary_text or tr(
            locale,
            "water.image.day_rank.summary",
            username=users_data[today_king]["username"],
            rank=group_rank,
        )
        info_card_h = int(54 * self.SCALE)
        info_card_top = y
        main_img.draw_rounded_rectangle(
            (
                self.PADDING,
                info_card_top,
                self.RENDER_WIDTH - self.PADDING,
                info_card_top + info_card_h,
            ),
            radius=int(12 * self.SCALE),
            fill=self.theme.info_card_bg,
        )
        main_img.draw_text(
            (
                self.PADDING + int(16 * self.SCALE),
                info_card_top + int(8 * self.SCALE),
                self.RENDER_WIDTH - self.PADDING - int(16 * self.SCALE),
                info_card_top + info_card_h - int(8 * self.SCALE),
            ),
            info_text,
            max_fontsize=int(18 * self.SCALE),
            fill=self.SUBTEXT_COLOR,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        y += info_card_h + int(14 * self.SCALE)

        tasks = [
            asyncio.to_thread(self._render_user_row, rank, user, locale)
            for rank, user in enumerate(users_data.values(), 1)
        ]
        row_images = await asyncio.gather(*tasks)

        base_y_offset = int(10 * self.SCALE)
        for row in row_images:
            main_img.paste(row, (0, y - base_y_offset), alpha=True)
            y += item_h + item_spacing

        footer_top = y + int(20 * self.SCALE)
        footer_h = int(80 * self.SCALE)
        draw_report_footer(
            main_img,
            locale=locale,
            generated_at=0,
            footer_text=footer_text or tr(locale, "water.image.day_rank.footer"),
            width=self.RENDER_WIDTH,
            pad=self.PADDING,
            top=footer_top,
            footer_h=footer_h,
            scale=self.SCALE,
            copyright_color=self.TEXT_COLOR,
            time_color=self.TEXT_COLOR,
            footer_color=self.HIGHLIGHT_COLOR,
        )

        final_img = main_img.crop((0, 0, self.RENDER_WIDTH, footer_top + footer_h))
        return (await asyncio.to_thread(final_img.save, "PNG")).getvalue()
