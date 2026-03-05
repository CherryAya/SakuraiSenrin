"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-27 12:18:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-05 12:57:15
Description: 图片渲染组件，AI 神力！
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import floor, sqrt
from typing import Any

import arrow
from PIL import Image, ImageChops, ImageDraw, ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.repositories import member_repo
from src.services.info import resolve_group_card, resolve_group_name

from .database import water_repo
from .services.achievement import ACHIEVEMENT_RULES, AchievementService

SYS_FONT_NAME = MAPLE_FONT_NAME
FALLBACK_FONT_PATH = MAPLE_FONT_PATH


@dataclass
class WaterInfo:
    user_id: str
    group_id: str
    created_at: int


@dataclass
class WaterProfileCardData:
    user_id: str
    group_id: str
    matrix_id: str
    group_name: str
    username: str
    global_level: tuple[int, int, int] | None
    matrix_level: tuple[int, int, int] | None
    global_rank: int | None
    group_user_rank: int | None
    matrix_user_rank: int | None
    matrix_rank: int | None
    group_rank: int | None
    matrix_total_level: tuple[int, int, int] | None
    matrix_groups: list[tuple[str, str]]
    achievement_items: list[tuple[str, str, str, int]]


class WaterRankRenderer:
    def __init__(self) -> None:
        self.BG_COLOR = "#FFF4F7"
        self.TEXT_COLOR = "#8F3D56"
        self.ITEM_BG_COLOR = "#FFF9FB"
        self.HIGHLIGHT_COLOR = "#E45A84"
        self.MUTED_COLOR = "#A77A88"
        self.HEADER_BG = "#FFE3ED"
        self.HEADER_TEXT = "#7A2F4A"
        self.SUBTEXT_COLOR = "#B05A79"
        self.TILE_BASE_COLORS = ("#E987AE", "#F1A58E", "#C8B5FF", "#9FDCE8")
        self.RANK_THEMES = {
            1: {"bg": "#FFE9C7", "badge": "#E2A243", "badge_txt": "#FFFFFF"},
            2: {"bg": "#EEE8FF", "badge": "#8D7AD8", "badge_txt": "#FFFFFF"},
            3: {"bg": "#E8F8F3", "badge": "#57A89A", "badge_txt": "#FFFFFF"},
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

    def _render_user_row(self, rank: int, user: dict[str, Any]) -> BuildImage:
        item_h = int(110 * self.SCALE)
        avatar_size = int(64 * self.SCALE)
        base_y = int(10 * self.SCALE)

        row = BuildImage.new("RGBA", (self.RENDER_WIDTH, item_h + base_y), (0, 0, 0, 0))
        default_bg = self.ITEM_BG_COLOR if rank % 2 == 1 else "#FFF1F6"
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
            ("NEW", (244, 171, 120))
            if trend is None
            else (f"↑ {trend}", (236, 109, 150))
            if trend > 0
            else (f"↓ {abs(trend)}", (93, 171, 159))
            if trend < 0
            else ("− 0", (177, 160, 176))
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
                fill=self.MUTED_COLOR,
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
        group_avatar: BuildImage,
        today_king: str,
        group_rank: int,
        users_data: dict[str, dict[str, Any]],
    ) -> bytes:
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
            "呃啊不知道写什么总之来看看大水怪们吧……",
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
                group_card_top + int(6 * self.SCALE),
                self.RENDER_WIDTH - self.PADDING - int(16 * self.SCALE),
                group_card_top + group_card_h - int(6 * self.SCALE),
            ),
            f"当前群: {safe_group_name}",
            max_fontsize=int(22 * self.SCALE),
            min_fontsize=int(16 * self.SCALE),
            fill=self.HEADER_TEXT,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        y += group_card_h + int(10 * self.SCALE)

        info_text = (
            f"今日水王: {users_data[today_king]['username']}\n"
            f"本群排名: 第{group_rank}名"
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
            fill="#FFF0F5",
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
        group_avatar,
        avatars,
    ) = await asyncio.gather(
        water_repo.get_today_group_rank(group_id),
        water_repo.get_users_hourly_distribution(group_id, user_ids),
        QQAvatar.fetch_group(group_id),
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
        group_avatar=group_avatar,
        today_king=king.user_id,
        group_rank=group_rank,
        users_data=users_data,
    )
    return img_bytes


def _format_rank(rank: int | None) -> str:
    return f"第 {rank} 名" if rank is not None else "-"


def _short_exp(exp: int | str) -> str:
    if isinstance(exp, str):
        return exp
    if exp >= 100000000:
        return f"{exp / 100000000:.1f}亿"
    if exp >= 10000:
        return f"{exp / 10000:.1f}w"
    return str(exp)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    val = value.lstrip("#")
    if len(val) != 6:
        return (0, 0, 0)
    return (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))


def _mix_hex(base: str, target: str, ratio: float) -> str:
    r = max(0.0, min(1.0, ratio))
    br, bg, bb = _hex_to_rgb(base)
    tr, tg, tb = _hex_to_rgb(target)
    rr = int(br + (tr - br) * r)
    rg = int(bg + (tg - bg) * r)
    rb = int(bb + (tb - bb) * r)
    return f"#{rr:02X}{rg:02X}{rb:02X}"


SEASONAL_ACHIEVEMENT_CAP = 10


def _seasonal_total_count(current_unlocked: int = 0) -> int:
    current_defined = sum(
        1 for rule in ACHIEVEMENT_RULES.values() if rule.track_type == "seasonal"
    )
    return max(current_defined, current_unlocked, SEASONAL_ACHIEVEMENT_CAP)


def _split_achievement_views(
    achievement_items: list[tuple[str, str, str, int]],
) -> tuple[list[str], list[tuple[str, str]]]:
    current_season = AchievementService.current_season_id()
    current: list[str] = []
    history_raw: list[tuple[int, str, str]] = []
    for achievement_id, track_type, season_id, unlocked_at in achievement_items:
        rule = ACHIEVEMENT_RULES.get(achievement_id)
        fallback_name = (
            achievement_id.replace("_", " ").strip().title() or achievement_id
        )
        name = rule.name if rule is not None else fallback_name
        if track_type == "seasonal" and season_id == current_season:
            current.append(name)
        if track_type == "seasonal":
            title = f"{season_id}赛季·{name}"
        else:
            title = f"永久成就 {name}"
        date_text = arrow.get(unlocked_at).to("Asia/Shanghai").format("YYYY-MM-DD")
        history_raw.append((int(unlocked_at), title, date_text))

    history_raw.sort(key=lambda item: item[0], reverse=True)
    history = [(title, date_text) for _, title, date_text in history_raw]
    return current, history


def _build_my_water_text_fallback(data: WaterProfileCardData) -> str:
    current_season = AchievementService.current_season_id()
    current_achievements, history_achievements = _split_achievement_views(
        data.achievement_items
    )
    seasonal_total = max(1, _seasonal_total_count(len(current_achievements)))

    lines = ["===== 我的水王资产 ====="]
    if data.matrix_level is not None:
        lines.extend(
            [
                f"矩阵等级: Lv{data.matrix_level[2]}",
                f"矩阵总经验: {data.matrix_level[0]}",
                f"矩阵赛季经验: {data.matrix_level[1]}",
            ]
        )
    if data.global_level is not None:
        lines.extend(
            [
                f"全局等级: Lv{data.global_level[2]}",
                f"全局总经验: {data.global_level[0]}",
                f"全局赛季经验: {data.global_level[1]}",
            ]
        )
    lines.extend(
        [
            "-----",
            (
                f"当前赛季成就({current_season}): "
                f"{len(current_achievements)}/{seasonal_total}"
            ),
            "当前赛季已达成: "
            + ("、".join(current_achievements[:3]) if current_achievements else "暂无"),
            "-----",
            f"我的全局排名: {_format_rank(data.global_rank)}",
            f"我在本群活跃排名: {_format_rank(data.group_user_rank)}",
            f"我在当前矩阵排名: {_format_rank(data.matrix_user_rank)}",
            "-----",
            f"当前矩阵总榜排名: {_format_rank(data.matrix_rank)}",
            f"当前群活跃排名: {_format_rank(data.group_rank)}",
            "-----",
            "成就记录:",
        ]
    )
    if not history_achievements:
        lines.append("- 暂无")
    else:
        for title, date_text in history_achievements[:6]:
            lines.append(f"- {title}  |  {date_text}")
    return "\n".join(lines)


def _draw_progress_bar(
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
    if fill_w > 0:
        fill_right = x + fill_w
        card.draw_rounded_rectangle((x, y, fill_right, y + h), radius=radius, fill=fg)
        # 预绘纹理层：方向恒定、密度可控，再覆盖到填充段，避免边界硬裁导致的反向感。
        texture = BuildImage.new("RGBA", (fill_w, h), (0, 0, 0, 0))
        stripe_color = _mix_hex(fg, "#FFFFFF", 0.52)
        stripe_step = max(7, int(h * 0.55))
        stripe_span = max(10, int(h * 1.3))
        stripe_width = max(1, int(h * 0.22))
        for sx in range(-h, fill_w + h, stripe_step):
            texture.draw.line(
                (sx, h - 2, sx + stripe_span, 2),
                fill=stripe_color,
                width=stripe_width,
            )

        bubble_color = _mix_hex(fg, "#FFFFFF", 0.72)
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
        # 用与填充区一致的圆角蒙版做裁切，避免矩形纹理溢出圆角边界。
        texture_mask = Image.new("L", (fill_w, h), 0)
        mask_draw = ImageDraw.Draw(texture_mask)
        mask_draw.rounded_rectangle(
            (0, 0, fill_w, h),
            radius=radius,
            fill=255,
        )
        raw_alpha = texture.image.split()[-1]
        clipped_alpha = ImageChops.multiply(raw_alpha, texture_mask)
        texture.image.putalpha(clipped_alpha)
        card.paste(texture, (x, y), alpha=True)


def _draw_gloss_lines(
    card: BuildImage,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    tone: str = "#FFFFFF",
    strength: float = 0.7,
) -> None:
    gloss = _mix_hex(tone, "#FFFFFF", strength)
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


def _next_level_target(level: int, base: int) -> int:
    cur = max(1, level)
    return base * (cur + 1) * (cur + 1)


def _level_progress(exp: int, level: int, base: int) -> tuple[float, int]:
    next_exp = _next_level_target(level, base)
    prev_exp = base * level * level if level > 1 else 0
    span = max(1, next_exp - prev_exp)
    ratio = (exp - prev_exp) / span
    return max(0.0, min(1.0, ratio)), max(0, next_exp - exp)


async def build_my_water_image(data: WaterProfileCardData) -> bytes | None:
    try:
        scale = 2.2
        width = int(680 * scale)
        pad = int(24 * scale)
        gap = int(10 * scale)
        page_bg = "#FFF4F7"
        title_panel_bg = "#FFE8F0"
        panel_bg = "#FFF9FB"
        panel_soft_bg = "#FFF5F9"
        chip_bg = "#F3E8FF"
        chip_alt_bg = "#E6F4FF"
        accent = "#7A2F4A"
        strong = "#D84E7A"
        deep = "#3F1A29"
        title_main = "#5E2138"
        title_sub = "#7A2F4A"
        title_hint = "#A54A6B"
        season = "#D4973C"
        success = "#43A396"
        my_value = "#8B4FD4"
        group_value = "#2F83C9"

        current_season = AchievementService.current_season_id()
        current_achievements, history_achievements = _split_achievement_views(
            data.achievement_items
        )
        seasonal_total = max(1, _seasonal_total_count(len(current_achievements)))
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
            "我的水王资产",
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
            f"当前矩阵: {data.matrix_id}",
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
            "矩阵群聊",
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
                fill="#FFF0F6" if group_id == data.group_id else "#F8EEF4",
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
        matrix_lv = data.matrix_level[2] if data.matrix_level is not None else "-"
        matrix_exp = data.matrix_level[0] if data.matrix_level is not None else "-"
        matrix_season = data.matrix_level[1] if data.matrix_level is not None else "-"
        global_lv = data.global_level[2] if data.global_level is not None else "-"
        global_exp = data.global_level[0] if data.global_level is not None else "-"
        global_season = data.global_level[1] if data.global_level is not None else "-"
        personal_exp = data.matrix_level[0] if data.matrix_level is not None else 0
        personal_lv = data.matrix_level[2] if data.matrix_level is not None else 1
        matrix_season_exp = data.matrix_level[1] if data.matrix_level is not None else 0
        global_season_exp = data.global_level[1] if data.global_level is not None else 0
        matrix_total_exp = (
            data.matrix_total_level[0] if data.matrix_total_level is not None else 0
        )
        matrix_total_lv = (
            data.matrix_total_level[2] if data.matrix_total_level is not None else 1
        )
        matrix_total_season_exp = (
            data.matrix_total_level[1] if data.matrix_total_level is not None else 0
        )

        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(8 * scale),
                width - pad,
                y + int(30 * scale),
            ),
            "等级与赛季概览",
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
            fill="#FFF0F6",
        )
        card.draw_rounded_rectangle(
            (
                right_metric_x,
                metric_top,
                right_metric_x + metric_w,
                metric_top + metric_h,
            ),
            radius=int(8 * scale),
            fill="#F0F7FF",
        )
        card.draw_text(
            (
                left_metric_x + int(8 * scale),
                metric_top + int(5 * scale),
                left_metric_x + metric_w - int(8 * scale),
                metric_top + metric_h - int(5 * scale),
            ),
            (
                f"矩阵 Lv{matrix_lv} · 总{_short_exp(matrix_exp)}"
                f" · 季{_short_exp(matrix_season)}"
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
            (
                f"全局 Lv{global_lv} · 总{_short_exp(global_exp)}"
                f" · 季{_short_exp(global_season)}"
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
            (
                f"当前赛季成就 {current_season}: "
                f"{len(current_achievements)}/{seasonal_total}"
            ),
            max_fontsize=int(13 * scale),
            min_fontsize=int(10 * scale),
            fill=season,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        _draw_progress_bar(
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
            "当前赛季达成",
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
                "暂无，继续加油冲成就~",
                max_fontsize=int(12 * scale),
                min_fontsize=int(9 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            chip_block_end = y + int(160 * scale)
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
                    fill="#FFEFD6",
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
                    fill="#B0712A",
                    halign="center",
                    font_families=[SYS_FONT_NAME],
                )

        season_global_lv = max(1, floor(sqrt(max(0, global_season_exp) / 100)))
        season_matrix_lv = max(1, floor(sqrt(max(0, matrix_season_exp) / 100)))
        season_matrix_total_lv = max(
            1,
            floor(sqrt(max(0, matrix_total_season_exp) / 2000)),
        )

        sg_ratio, sg_gap = _level_progress(global_season_exp, season_global_lv, 100)
        sm_ratio, sm_gap = _level_progress(matrix_season_exp, season_matrix_lv, 100)
        smt_ratio, smt_gap = _level_progress(
            matrix_total_season_exp,
            season_matrix_total_lv,
            2000,
        )
        gg_ratio, gg_gap = _level_progress(
            data.global_level[0] if data.global_level is not None else 0,
            data.global_level[2] if data.global_level is not None else 1,
            100,
        )
        gm_ratio, gm_gap = _level_progress(personal_exp, personal_lv, 100)
        gmt_ratio, gmt_gap = _level_progress(matrix_total_exp, matrix_total_lv, 2000)

        seasonal_progress_items = [
            ("全局赛季", sg_gap, sg_ratio),
            ("矩阵赛季", sm_gap, sm_ratio),
            ("矩阵总赛季", smt_gap, smt_ratio),
        ]
        global_progress_items = [
            ("全局累计", gg_gap, gg_ratio),
            ("矩阵累计", gm_gap, gm_ratio),
            ("矩阵总累计", gmt_gap, gmt_ratio),
        ]
        exp_panel_top = chip_block_end + int(16 * scale)
        col_gap = int(14 * scale)
        col_w = int((width - pad * 2 - int(40 * scale) - col_gap) / 2)
        left_x = pad + int(20 * scale)
        right_x = left_x + col_w + col_gap
        text_color = "#151015"
        meta_color = "#382430"
        progress_rows_h = len(global_progress_items) * exp_row_block - exp_row_gap
        col_panel_h = exp_header_h + int(8 * scale) + progress_rows_h + int(10 * scale)

        card.draw_rounded_rectangle(
            (left_x, exp_panel_top, left_x + col_w, exp_panel_top + col_panel_h),
            radius=int(10 * scale),
            fill="#F5F9FF",
        )
        _draw_gloss_lines(
            card,
            left_x,
            exp_panel_top,
            col_w,
            col_panel_h,
            tone="#D7E6FF",
            strength=0.8,
        )
        card.draw_rounded_rectangle(
            (right_x, exp_panel_top, right_x + col_w, exp_panel_top + col_panel_h),
            radius=int(10 * scale),
            fill="#FFF8F1",
        )
        _draw_gloss_lines(
            card,
            right_x,
            exp_panel_top,
            col_w,
            col_panel_h,
            tone="#FFE8D0",
            strength=0.8,
        )

        card.draw_text(
            (
                left_x + int(10 * scale),
                exp_panel_top + int(3 * scale),
                left_x + col_w - int(10 * scale),
                exp_panel_top + exp_header_h,
            ),
            "全局累计进度",
            max_fontsize=int(12 * scale),
            min_fontsize=int(10 * scale),
            fill="#1E40AF",
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
            "当前赛季进度",
            max_fontsize=int(12 * scale),
            min_fontsize=int(10 * scale),
            fill="#B45309",
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
                fill="#1E40AF",
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
                f"距下一级还差 {_short_exp(gap_value)} 经验",
                max_fontsize=int(10 * scale),
                min_fontsize=int(9 * scale),
                fill=meta_color,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            _draw_progress_bar(
                card=card,
                x=left_x + int(10 * scale),
                y=row_top + exp_title_h + exp_meta_h,
                w=col_w - int(20 * scale),
                h=exp_row_h,
                progress=ratio,
                bg="#E5EEFF",
                fg="#4F7DF3",
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
                fill="#B45309",
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
                f"距下一级还差 {_short_exp(gap_value)} 经验",
                max_fontsize=int(10 * scale),
                min_fontsize=int(9 * scale),
                fill=meta_color,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            _draw_progress_bar(
                card=card,
                x=right_x + int(10 * scale),
                y=row_top + exp_title_h + exp_meta_h,
                w=col_w - int(20 * scale),
                h=exp_row_h,
                progress=ratio,
                bg="#FCEEDC",
                fg="#F28A3B",
            )

        y = max(y + status_h, exp_panel_top + col_panel_h + int(12 * scale)) + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + rank_h),
            radius=int(18 * scale),
            fill=panel_soft_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            rank_h,
            tone="#F2E8F3",
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
            "排名面板",
            max_fontsize=int(17 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        global_meta = (
            f"Lv{data.global_level[2]} · {_short_exp(data.global_level[0])}经验"
            if data.global_level is not None
            else "Lv- · -"
        )
        matrix_user_meta = (
            f"Lv{data.matrix_level[2]} · {_short_exp(data.matrix_level[0])}经验"
            if data.matrix_level is not None
            else "Lv- · -"
        )
        matrix_total_meta = (
            (
                f"Lv{data.matrix_total_level[2]} · "
                f"{_short_exp(data.matrix_total_level[0])}经验"
            )
            if data.matrix_total_level is not None
            else "Lv- · -"
        )
        group_total_meta = "按消息次数统计"
        my_rank_items = [
            ("我的全局排名", _format_rank(data.global_rank), global_meta),
            ("我在本群排名", _format_rank(data.group_user_rank), "按消息次数统计"),
            ("我在本矩阵排名", _format_rank(data.matrix_user_rank), matrix_user_meta),
        ]
        group_rank_items = [
            ("矩阵总榜排名", _format_rank(data.matrix_rank), matrix_total_meta),
            ("群聊活跃排名", _format_rank(data.group_rank), group_total_meta),
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
                fill=chip_bg if idx % 2 == 0 else "#EFE4FC",
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
                fill="#5A3A74",
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
                fill=chip_alt_bg if idx % 2 == 0 else "#DDF0FF",
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
                fill="#355A78",
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
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            history_h,
            tone="#F7EAF1",
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
            "成就记录",
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
                "暂无成就记录",
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
                        fill="#EFD2DD",
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
            (pad, height - int(18 * scale)),
            f"生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            fill=accent,
            font=ImageFont.truetype(FALLBACK_FONT_PATH, int(13 * scale)),
            anchor="ls",
        )

        return (await asyncio.to_thread(card.save, "PNG")).getvalue()
    except Exception as e:
        logger.exception(f"[Water] build_my_water_image failed: {e}")
        return None


async def build_my_water_fallback_text(data: WaterProfileCardData) -> str:
    return _build_my_water_text_fallback(data)
