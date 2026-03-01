"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-27 12:18:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:16:16
Description: 图片渲染组件，AI 神力！
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import arrow
from PIL import ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.repositories import member_repo
from src.services.info import resolve_group_card, resolve_group_name

from .database import water_repo

SYS_FONT_NAME = MAPLE_FONT_NAME
FALLBACK_FONT_PATH = MAPLE_FONT_PATH


@dataclass
class WaterInfo:
    user_id: str
    group_id: str
    created_at: int


class WaterRankRenderer:
    def __init__(self) -> None:
        self.BG_COLOR = "#F9DEE2"
        self.TEXT_COLOR = "#B44C4C"
        self.ITEM_BG_COLOR = "#FFF0F5"
        self.HIGHLIGHT_COLOR = "#DC5A64"
        self.RANK_THEMES = {
            1: {"bg": "#FBD089", "badge": "#FFAE00", "badge_txt": "#FFFFFF"},
            2: {"bg": "#D2CECE", "badge": "#ABABAB", "badge_txt": "#FFFFFF"},
            3: {"bg": "#F4D7C5", "badge": "#C4835F", "badge_txt": "#FFFFFF"},
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
            color_hex = f"{self.TEXT_COLOR}{alpha:02X}"

            chart.draw_rounded_rectangle(
                (x0, y0, x0 + tile_size, y0 + tile_size),
                radius=int(4 * self.SCALE),
                fill=color_hex,
                outline=f"{self.TEXT_COLOR}FF",
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

    def _render_user_row(self, rank: int, user: dict[str, Any]) -> BuildImage:
        item_h = int(110 * self.SCALE)
        avatar_size = int(64 * self.SCALE)
        base_y = int(10 * self.SCALE)

        row = BuildImage.new("RGBA", (self.RENDER_WIDTH, item_h + base_y), (0, 0, 0, 0))
        theme = self.RANK_THEMES.get(rank, {"bg": self.ITEM_BG_COLOR, "badge": None})

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
            ("NEW", (255, 180, 50))
            if trend is None
            else (f"↑ {trend}", (255, 110, 110))
            if trend > 0
            else (f"↓ {abs(trend)}", (90, 200, 100))
            if trend < 0
            else ("− 0", (180, 180, 190))
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
            outline="white",
            width=int(2.5 * self.SCALE),
        )
        row.draw.text(
            (badge_x + pill_w / 2, badge_y + pill_h / 2),
            t_str,
            fill="white",
            font=self.num_small_font,
            anchor="mm",
        )

        text_x = avatar_x + avatar_size + int(20 * self.SCALE)
        chart = self._generate_tile_chart(user["hourly_data"])
        chart_x = self.RENDER_WIDTH - self.PADDING - chart.width - int(20 * self.SCALE)
        max_name_width = chart_x - text_x - int(10 * self.SCALE)
        display_name = self._safe_truncate(user["username"], max_len=16)
        name_box_top = base_y + int(10 * self.SCALE)
        name_box_bottom = base_y + int(55 * self.SCALE)
        b_pad_x, b_pad_y = int(8 * self.SCALE), int(4 * self.SCALE)
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
                fill="#A58A8D",
                font=self.num_small_font,
                anchor="lm",
            )
            name_x = text_x + b_width + int(8 * self.SCALE)

        skia_fix_y = int(-0.5 * self.SCALE)
        name_box_top = row_center_y - int(20 * self.SCALE) + skia_fix_y
        name_box_bottom = row_center_y + int(20 * self.SCALE) + skia_fix_y

        box_coords = (
            name_x,
            name_box_top,
            name_x + max_name_width - (name_x - text_x),
            name_box_bottom,
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
                "🏳️名字过于混沌🏳️",
                max_fontsize=int(20 * self.SCALE),
                min_fontsize=int(12 * self.SCALE),
                fill=self.TEXT_COLOR,
                halign="left",
                valign="center",
            )

        row.draw.text(
            (text_x, base_y + int(65 * self.SCALE)),
            f"今日发言: {user['count']} 条",
            fill=self.HIGHLIGHT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )
        chart_y = base_y + (item_h - chart.height) // 2
        row.paste(chart, (chart_x, chart_y), alpha=True)

        return row

    async def render_async(
        self,
        group_name: str,
        today_king: str,
        group_rank: int,
        users_data: dict[str, dict[str, Any]],
    ) -> bytes:
        item_h, item_spacing = int(110 * self.SCALE), int(20 * self.SCALE)
        header_height = int(260 * self.SCALE)
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
            "🐉🐉🐉🐉🐉🐉🐉🐉🐉水王排行榜🐉🐉🐉🐉🐉🐉🐉🐉🐉",
            max_fontsize=int(40 * self.SCALE),
            fill=self.TEXT_COLOR,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        y += int(70 * self.SCALE)

        safe_group_name = self._safe_truncate(group_name, max_len=30)
        main_img.draw_text(
            (
                self.PADDING,
                y,
                self.RENDER_WIDTH - self.PADDING,
                y + int(40 * self.SCALE),
            ),
            safe_group_name,
            max_fontsize=int(26 * self.SCALE),
            min_fontsize=int(16 * self.SCALE),
            fill=self.TEXT_COLOR,
            halign="center",
            font_families=[SYS_FONT_NAME],
        )
        y += int(50 * self.SCALE)

        info_text = (
            f"今日水王: {users_data[today_king]['username']}\n"
            f"本群排名: 第{group_rank}名"
        )
        main_img.draw_text(
            (
                self.PADDING,
                y,
                self.RENDER_WIDTH - self.PADDING,
                y + int(60 * self.SCALE),
            ),
            info_text,
            max_fontsize=int(20 * self.SCALE),
            fill=self.TEXT_COLOR,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        y += int(80 * self.SCALE)

        tasks = [
            asyncio.to_thread(self._render_user_row, rank, user)
            for rank, user in enumerate(users_data.values(), 1)
        ]
        row_images = await asyncio.gather(*tasks)

        base_y_offset = int(10 * self.SCALE)
        for row in row_images:
            main_img.paste(row, (0, y - base_y_offset), alpha=True)
            y += item_h + item_spacing

        now = arrow.get(get_current_time()).datetime
        footer_y = y + int(20 * self.SCALE)
        main_img.draw.text(
            (self.PADDING, footer_y),
            f"© 2020-{now.year} SakuraiSenrin. All rights reserved.",
            fill=self.TEXT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )
        time_footer_y = footer_y + int(30 * self.SCALE)
        main_img.draw.text(
            (self.PADDING, time_footer_y),
            f"生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            fill=self.TEXT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )

        msg_y = time_footer_y + int(50 * self.SCALE)
        main_img.draw_text(
            (0, msg_y, self.RENDER_WIDTH, msg_y + int(30 * self.SCALE)),
            "------------哦嚯嚯！下一个水王会是你吗？٩(๑>◡<๑)۶凛凛很期待喔！------------",
            max_fontsize=int(18 * self.SCALE),
            fill=self.HIGHLIGHT_COLOR,
            halign="center",
            valign="center",
            font_families=[SYS_FONT_NAME],
        )

        final_img = main_img.crop(
            (0, 0, self.RENDER_WIDTH, msg_y + int(80 * self.SCALE))
        )
        return (await asyncio.to_thread(final_img.save, "PNG")).getvalue()


async def build_water_rank_image(group_id: str) -> bytes | None:
    group_name = await resolve_group_name(None, group_id)
    top_users = await water_repo.get_today_leaderboard(group_id, limit=10)
    if not top_users:
        return None

    user_ids = [u.user_id for u in top_users]
    (
        group_rank,
        user_hourly_dict,
        avatars,
    ) = await asyncio.gather(
        water_repo.get_today_group_rank(group_id),
        water_repo.get_users_hourly_distribution(group_id, user_ids),
        asyncio.gather(
            *(QQAvatar.fetch_user(uid) for uid in user_ids), return_exceptions=True
        ),
    )

    users_data = {}
    for idx, rank_item in enumerate(top_users):
        uid = rank_item.user_id

        avatar_bytes = avatars[idx]
        if isinstance(avatar_bytes, Exception) or not avatar_bytes:
            avatar_bytes = b""

        member = await member_repo.get_member(uid, group_id)
        username = (
            await resolve_group_card(None, uid, group_id)
            if member
            else f"群员_{uid[-4:]}"
        )
        users_data[uid] = {
            "user_id": uid,
            "username": username,
            "count": rank_item.msg_count,
            "hourly_data": user_hourly_dict.get(uid, [0] * 24),
            "avatar_img": avatar_bytes,
            "trend": rank_item.trend or 0,
        }

    king = top_users[0]
    renderer = WaterRankRenderer()
    img_bytes = await renderer.render_async(
        group_name=group_name,
        today_king=king.user_id,
        group_rank=group_rank,
        users_data=users_data,
    )
    return img_bytes
