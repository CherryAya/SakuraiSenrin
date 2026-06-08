"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-27 12:18:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-05 19:40:05
Description: 图片渲染组件，AI 神力！
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import floor, sqrt
from typing import Any, Literal

import arrow
from PIL import Image, ImageChops, ImageDraw, ImageFont
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.repositories import member_repo
from src.services.info import resolve_group_card, resolve_group_name

from .database import water_repo
from .services.achievement import ACHIEVEMENT_RULES

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


@dataclass(frozen=True)
class WaterPeriodRankUserItem:
    user_id: str
    username: str
    avatar: BuildImage | None
    msg_count: int
    active_days: int
    active_hours: int
    hourly_counts: list[int]
    current_rank: int
    trend: int | None


@dataclass(frozen=True)
class WaterPeriodRankCardData:
    period: Literal["week", "month", "season", "year"]
    title: str
    badge: str
    range_text: str
    compare_text: str
    generated_at: int
    total_msg_count: int
    active_user_count: int
    hourly_counts: list[int]
    peak_hour: int
    previous_total_msg_count: int
    top_users: list[WaterPeriodRankUserItem]
    champion_gap: int
    champion_share: float


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
        group_name: str,
        group_avatar: BuildImage,
        today_king: str,
        group_rank: int,
        users_data: dict[str, dict[str, Any]],
        locale: LocaleCode,
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
            tr(locale, "water.image.day_rank.header"),
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
            tr(
                locale,
                "water.image.day_rank.current_group",
                group_name=safe_group_name,
            ),
            max_fontsize=int(22 * self.SCALE),
            min_fontsize=int(16 * self.SCALE),
            fill=self.HEADER_TEXT,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        y += group_card_h + int(10 * self.SCALE)

        info_text = tr(
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
            asyncio.to_thread(self._render_user_row, rank, user, locale)
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
            (self.RENDER_WIDTH / 2, footer_y),
            _build_copyright_text(now.year),
            fill=self.TEXT_COLOR,
            font=self.num_small_font,
            anchor="ma",
        )
        time_footer_y = footer_y + int(30 * self.SCALE)
        main_img.draw.text(
            (self.PADDING, time_footer_y),
            tr(
                locale,
                "water.image.generated_at",
                time=now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            fill=self.TEXT_COLOR,
            font=self.num_small_font,
            anchor="la",
        )

        msg_y = time_footer_y + int(50 * self.SCALE)
        main_img.draw_text(
            (0, msg_y, self.RENDER_WIDTH, msg_y + int(30 * self.SCALE)),
            tr(locale, "water.image.day_rank.footer"),
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


async def build_water_rank_image(
    group_id: str,
    locale: LocaleCode,
) -> bytes | None:
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
            else tr(locale, "water.image.day_rank.member_fallback", tail=uid[-4:])
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
        locale=locale,
    )
    return img_bytes


def _format_rank(rank: int | None, locale: LocaleCode = "zh-CN") -> str:
    if rank is None:
        return "-"
    return tr(locale, "water.image.rank_format", rank=rank)


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
    locale: LocaleCode,
) -> tuple[list[str], list[tuple[str, str]]]:
    latest_season = ""
    seasonal_items = [
        (achievement_id, season_id, unlocked_at)
        for achievement_id, track_type, season_id, unlocked_at in achievement_items
        if track_type == "seasonal" and season_id
    ]
    if seasonal_items:
        latest_season = max(
            seasonal_items,
            key=lambda item: int(item[2]),
        )[1]
    current: list[str] = []
    history_raw: list[tuple[int, str, str]] = []
    for achievement_id, track_type, season_id, unlocked_at in achievement_items:
        rule = ACHIEVEMENT_RULES.get(achievement_id)
        fallback_name = (
            achievement_id.replace("_", " ").strip().title() or achievement_id
        )
        name = rule.name if rule is not None else fallback_name
        if track_type == "seasonal" and season_id == latest_season:
            current.append(name)
        if track_type == "seasonal":
            title = tr(
                locale,
                "water.profile.fallback.achievement_history.seasonal",
                season_id=season_id,
                name=name,
            )
        else:
            title = tr(
                locale,
                "water.profile.fallback.achievement_history.permanent",
                name=name,
            )
        date_text = arrow.get(unlocked_at).to("Asia/Shanghai").format("YYYY-MM-DD")
        history_raw.append((int(unlocked_at), title, date_text))

    history_raw.sort(key=lambda item: item[0], reverse=True)
    history = [(title, date_text) for _, title, date_text in history_raw]
    return current, history


def _build_my_water_text_fallback(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> str:
    current_achievements, history_achievements = _split_achievement_views(
        data.achievement_items,
        locale,
    )
    seasonal_total = max(1, _seasonal_total_count(len(current_achievements)))

    lines = [tr(locale, "water.profile.fallback.title")]
    if data.matrix_level is not None:
        lines.extend(
            [
                tr(
                    locale,
                    "water.profile.fallback.matrix_level",
                    level=data.matrix_level[2],
                ),
                tr(
                    locale,
                    "water.profile.fallback.matrix_total_exp",
                    exp=data.matrix_level[0],
                ),
                tr(
                    locale,
                    "water.profile.fallback.matrix_season_exp",
                    exp=data.matrix_level[1],
                ),
            ]
        )
    if data.global_level is not None:
        lines.extend(
            [
                tr(
                    locale,
                    "water.profile.fallback.global_level",
                    level=data.global_level[2],
                ),
                tr(
                    locale,
                    "water.profile.fallback.global_total_exp",
                    exp=data.global_level[0],
                ),
                tr(
                    locale,
                    "water.profile.fallback.global_season_exp",
                    exp=data.global_level[1],
                ),
            ]
        )
    lines.extend(
        [
            "-----",
            tr(
                locale,
                "water.profile.fallback.season_achievement",
                count=len(current_achievements),
                total=seasonal_total,
            ),
            tr(
                locale,
                "water.profile.fallback.season_achievement_names",
                items=(
                    "、".join(current_achievements[:3])
                    if current_achievements
                    else tr(locale, "water.profile.fallback.none")
                ),
            ),
            "-----",
            tr(
                locale,
                "water.profile.fallback.global_rank",
                rank=_format_rank(data.global_rank),
            ),
            tr(
                locale,
                "water.profile.fallback.group_user_rank",
                rank=_format_rank(data.group_user_rank),
            ),
            tr(
                locale,
                "water.profile.fallback.matrix_user_rank",
                rank=_format_rank(data.matrix_user_rank),
            ),
            "-----",
            tr(
                locale,
                "water.profile.fallback.matrix_rank",
                rank=_format_rank(data.matrix_rank),
            ),
            tr(
                locale,
                "water.profile.fallback.group_rank",
                rank=_format_rank(data.group_rank),
            ),
            "-----",
            tr(locale, "water.profile.fallback.achievement_history"),
        ]
    )
    if not history_achievements:
        lines.append(tr(locale, "water.achievement.unlocked.none"))
    else:
        for title, date_text in history_achievements[:6]:
            lines.append(
                tr(
                    locale,
                    "water.profile.fallback.achievement_history_item",
                    title=title,
                    date_text=date_text,
                )
            )
    return "\n".join(lines)


def _build_copyright_text(year: int) -> str:
    return f"© 2020-{year} SakuraiSenrin"


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


def _format_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def _format_trend(trend: int | None) -> tuple[str, str]:
    if trend is None:
        return ("NEW", "#F0B36D")
    if trend > 0:
        return (f"↑{trend}", "#E96A96")
    if trend < 0:
        return (f"↓{abs(trend)}", "#66B3A5")
    return ("-0", "#B8A1AE")


def _safe_hourly_counts(hourly_counts: list[int]) -> list[int]:
    if len(hourly_counts) >= 24:
        return [int(item) for item in hourly_counts[:24]]
    return [*map(int, hourly_counts), *([0] * (24 - len(hourly_counts)))]


def _draw_hourly_histogram(
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
    hourly = _safe_hourly_counts(hourly_counts)
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
            else "#F5A340"
        )
        card.draw_rounded_rectangle(
            (bx, by, bx + bar_w, y + chart_h),
            radius=max(2, int(3 * scale)),
            fill=fill,
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


def _draw_user_distribution(
    card: BuildImage,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    hourly_counts: list[int],
    base_color: str,
    scale: float,
) -> None:
    hourly = _safe_hourly_counts(hourly_counts)
    max_count = max(hourly) or 1
    gap = max(1, int(2 * scale))
    bar_w = max(2, int((w - gap * 23) / 24))
    for hour, count in enumerate(hourly):
        bx = x + hour * (bar_w + gap)
        bh = max(2, int((count / max_count) * h)) if count > 0 else 2
        by = y + h - bh
        tone = base_color if count > 0 else _mix_hex(base_color, "#FFFFFF", 0.55)
        card.draw_rounded_rectangle(
            (bx, by, bx + bar_w, y + h),
            radius=max(1, int(2 * scale)),
            fill=tone,
        )


def _build_avatar_fallback(size: int, label: str, bg: str, fg: str) -> BuildImage:
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


async def build_water_period_rank_image(
    data: WaterPeriodRankCardData,
    locale: LocaleCode,
) -> bytes | None:
    try:
        scale = 2.0
        width = int(760 * scale)
        pad = int(24 * scale)
        gap = int(12 * scale)
        hero_h = int(144 * scale)
        champion_h = int(128 * scale)
        overview_h = int(168 * scale)
        footer_h = int(42 * scale)
        row_h = int(86 * scale)
        row_gap = int(8 * scale)
        board_header_h = int(34 * scale)
        board_h = (
            board_header_h
            + int(12 * scale)
            + len(data.top_users) * row_h
            + max(0, len(data.top_users) - 1) * row_gap
            + int(16 * scale)
        )
        height = (
            pad * 2
            + hero_h
            + gap
            + champion_h
            + gap
            + board_h
            + gap
            + overview_h
            + gap
            + footer_h
        )

        page_bg = "#FFF4F7"
        hero_bg = "#FFE8F0"
        panel_bg = "#FFF9FB"
        panel_soft_bg = "#FFF3F8"
        accent = "#7A2F4A"
        strong = "#D84E7A"
        deep = "#401828"
        hint = "#AA6B82"
        line = "#F6D9E6"
        blue = "#5B8CFF"
        gold = "#D4973C"
        mint = "#67BAA6"
        badge_bg = "#FFF0C7"
        badge_fg = "#9A6723"

        card = BuildImage.new("RGB", (width, height), page_bg)
        y = pad

        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + hero_h),
            radius=int(20 * scale),
            fill=hero_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            hero_h,
            tone="#FFF6FA",
            strength=0.85,
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

        badge_w = int(112 * scale)
        badge_h = int(34 * scale)
        badge_x = width - pad - badge_w - int(18 * scale)
        badge_y = y + int(18 * scale)
        card.draw_rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            radius=int(12 * scale),
            fill=badge_bg,
        )
        card.draw_text(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            data.badge,
            max_fontsize=int(13 * scale),
            min_fontsize=int(10 * scale),
            fill=badge_fg,
            halign="center",
            valign="center",
            font_families=[SYS_FONT_NAME],
        )

        stat_top = y + int(92 * scale)
        stat_gap = int(10 * scale)
        stat_w = int((width - pad * 2 - int(36 * scale) - stat_gap * 2) / 3)
        try:
            stat_value_font = ImageFont.truetype(FALLBACK_FONT_PATH, int(20 * scale))
        except OSError:
            stat_value_font = ImageFont.load_default()
        stats = [
            (
                tr(locale, "water.image.period.stats.total_msg_count"),
                _short_exp(data.total_msg_count),
                strong,
                "#FFF0F6",
            ),
            (
                tr(locale, "water.image.period.stats.active_user_count"),
                str(data.active_user_count),
                blue,
                "#F0F6FF",
            ),
            (
                tr(locale, "water.image.period.stats.delta"),
                _format_delta(data.total_msg_count - data.previous_total_msg_count),
                mint
                if data.total_msg_count >= data.previous_total_msg_count
                else strong,
                "#F1FFF9"
                if data.total_msg_count >= data.previous_total_msg_count
                else "#FFF0F6",
            ),
        ]
        for idx, (label, value, value_color, bg) in enumerate(stats):
            sx = pad + int(18 * scale) + idx * (stat_w + stat_gap)
            card.draw_rounded_rectangle(
                (sx, stat_top, sx + stat_w, y + hero_h - int(18 * scale)),
                radius=int(12 * scale),
                fill=bg,
            )
            card.draw_text(
                (
                    sx + int(10 * scale),
                    stat_top + int(6 * scale),
                    sx + stat_w - int(10 * scale),
                    stat_top + int(24 * scale),
                ),
                label,
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw.text(
                (sx + int(10 * scale), stat_top + int(50 * scale)),
                value,
                fill=value_color,
                font=stat_value_font,
                anchor="lm",
            )

        y += hero_h + gap
        champion = data.top_users[0]
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + champion_h),
            radius=int(20 * scale),
            fill=panel_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            champion_h,
            tone="#FFF7FB",
            strength=0.82,
        )
        avatar_size = int(88 * scale)
        avatar_x = pad + int(18 * scale)
        avatar_y = y + int(18 * scale)
        avatar = champion.avatar
        if avatar is None:
            avatar = _build_avatar_fallback(
                avatar_size,
                "1",
                "#F6C65B",
                "#FFFFFF",
            )
        card.paste(
            avatar.circle().resize((avatar_size, avatar_size)),
            (avatar_x, avatar_y),
            alpha=True,
        )
        card.draw_text(
            (
                avatar_x + avatar_size + int(14 * scale),
                y + int(18 * scale),
                width - pad - int(18 * scale),
                y + int(42 * scale),
            ),
            tr(locale, "water.image.period.champion.title"),
            max_fontsize=int(14 * scale),
            min_fontsize=int(10 * scale),
            fill=gold,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                avatar_x + avatar_size + int(14 * scale),
                y + int(40 * scale),
                width - pad - int(18 * scale),
                y + int(68 * scale),
            ),
            champion.username,
            max_fontsize=int(24 * scale),
            min_fontsize=int(15 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                avatar_x + avatar_size + int(14 * scale),
                y + int(70 * scale),
                width - pad - int(18 * scale),
                y + int(92 * scale),
            ),
            tr(
                locale,
                "water.image.period.champion.summary",
                msg_count=champion.msg_count,
                active_days=champion.active_days,
            ),
            max_fontsize=int(12 * scale),
            min_fontsize=int(9 * scale),
            fill=accent,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        info_chip_w = int(150 * scale)
        info_chip_h = int(28 * scale)
        chip_specs = [
            (
                tr(locale, "water.image.period.champion.gap", gap=data.champion_gap),
                "#FFF0D8",
                "#B67828",
            ),
            (
                tr(
                    locale,
                    "water.image.period.champion.share",
                    share=f"{data.champion_share * 100:.1f}",
                ),
                "#F0F6FF",
                blue,
            ),
        ]
        for idx, (text, bg, fg) in enumerate(chip_specs):
            cx = (
                avatar_x
                + avatar_size
                + int(14 * scale)
                + idx * (info_chip_w + int(8 * scale))
            )
            cy = y + int(94 * scale)
            card.draw_rounded_rectangle(
                (cx, cy, cx + info_chip_w, cy + info_chip_h),
                radius=int(10 * scale),
                fill=bg,
            )
            card.draw_text(
                (
                    cx + int(8 * scale),
                    cy,
                    cx + info_chip_w - int(8 * scale),
                    cy + info_chip_h,
                ),
                text,
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=fg,
                halign="center",
                valign="center",
                font_families=[SYS_FONT_NAME],
            )

        y += champion_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + board_h),
            radius=int(20 * scale),
            fill=panel_soft_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            board_h,
            tone="#FFF6FA",
            strength=0.8,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(8 * scale),
                width - pad,
                y + board_header_h,
            ),
            tr(locale, "water.image.period.board.title"),
            max_fontsize=int(18 * scale),
            min_fontsize=int(12 * scale),
            fill=deep,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )

        row_y = y + board_header_h + int(10 * scale)
        rank_themes = {
            1: ("#FFE9C7", "#E0A141", "#FFFFFF"),
            2: ("#EEE8FF", "#8D7AD8", "#FFFFFF"),
            3: ("#E8F8F3", "#57A89A", "#FFFFFF"),
        }
        for item in data.top_users:
            bg, badge_fill, badge_fg = rank_themes.get(
                item.current_rank,
                ("#FFF9FB", "#F4D8E5", accent),
            )
            card.draw_rounded_rectangle(
                (
                    pad + int(14 * scale),
                    row_y,
                    width - pad - int(14 * scale),
                    row_y + row_h,
                ),
                radius=int(14 * scale),
                fill=bg,
            )
            badge_x = pad + int(26 * scale)
            badge_y = row_y + int(20 * scale)
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

            item_avatar_size = int(52 * scale)
            item_avatar_x = badge_x + badge_w + int(12 * scale)
            item_avatar_y = row_y + (row_h - item_avatar_size) // 2
            item_avatar = item.avatar
            if item_avatar is None:
                item_avatar = _build_avatar_fallback(
                    item_avatar_size,
                    item.username[:1] or "?",
                    "#FFDDE9",
                    strong,
                )
            card.paste(
                item_avatar.circle().resize((item_avatar_size, item_avatar_size)),
                (item_avatar_x, item_avatar_y),
                alpha=True,
            )

            text_x = item_avatar_x + item_avatar_size + int(12 * scale)
            card.draw_text(
                (
                    text_x,
                    row_y + int(10 * scale),
                    width - pad - int(300 * scale),
                    row_y + int(34 * scale),
                ),
                item.username,
                max_fontsize=int(16 * scale),
                min_fontsize=int(10 * scale),
                fill=deep,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            avg_daily = item.msg_count / max(1, item.active_days)
            card.draw_text(
                (
                    text_x,
                    row_y + int(36 * scale),
                    width - pad - int(300 * scale),
                    row_y + int(58 * scale),
                ),
                tr(
                    locale,
                    "water.image.period.board.summary",
                    msg_count=item.msg_count,
                    active_days=item.active_days,
                    avg_daily=f"{avg_daily:.1f}",
                ),
                max_fontsize=int(11 * scale),
                min_fontsize=int(8 * scale),
                fill=accent,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    text_x,
                    row_y + int(56 * scale),
                    width - pad - int(300 * scale),
                    row_y + int(74 * scale),
                ),
                tr(
                    locale,
                    "water.image.period.board.active_hours",
                    active_hours=item.active_hours,
                ),
                max_fontsize=int(10 * scale),
                min_fontsize=int(8 * scale),
                fill=hint,
                halign="left",
                font_families=[SYS_FONT_NAME],
            )

            spark_x = width - pad - int(230 * scale)
            _draw_user_distribution(
                card,
                x=spark_x,
                y=row_y + int(18 * scale),
                w=int(108 * scale),
                h=int(40 * scale),
                hourly_counts=item.hourly_counts,
                base_color=strong if item.current_rank <= 3 else "#C0829B",
                scale=scale,
            )

            trend_text, trend_color = _format_trend(item.trend)
            trend_w = int(56 * scale)
            trend_h = int(24 * scale)
            trend_x = width - pad - int(100 * scale)
            trend_y = row_y + int(16 * scale)
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
                fill="#FFFFFF",
                halign="center",
                valign="center",
                font_families=[SYS_FONT_NAME],
            )
            card.draw_text(
                (
                    width - pad - int(120 * scale),
                    row_y + int(48 * scale),
                    width - pad - int(20 * scale),
                    row_y + int(68 * scale),
                ),
                tr(locale, "water.image.period.board.trend_label"),
                max_fontsize=int(9 * scale),
                min_fontsize=int(7 * scale),
                fill=hint,
                halign="center",
                font_families=[SYS_FONT_NAME],
            )

            row_y += row_h + row_gap

        y += board_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + overview_h),
            radius=int(20 * scale),
            fill=panel_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            overview_h,
            tone="#FFF7FB",
            strength=0.82,
        )
        card.draw_text(
            (
                pad + int(18 * scale),
                y + int(8 * scale),
                width - pad,
                y + int(30 * scale),
            ),
            tr(locale, "water.image.period.overview.title"),
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
        _draw_hourly_histogram(
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
        generated_at = arrow.get(data.generated_at).to("Asia/Shanghai")
        footer_left = tr(locale, "water.image.period.footer")
        card.draw_text(
            (
                pad,
                y,
                width - pad,
                y + int(18 * scale),
            ),
            footer_left,
            max_fontsize=int(10 * scale),
            min_fontsize=int(8 * scale),
            fill=hint,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        card.draw_text(
            (
                pad,
                y + int(18 * scale),
                width - pad,
                y + footer_h,
            ),
            tr(
                locale,
                "water.image.generated_at",
                time=generated_at.format("YYYY-MM-DD HH:mm:ss"),
            ),
            max_fontsize=int(10 * scale),
            min_fontsize=int(8 * scale),
            fill=accent,
            halign="left",
            font_families=[SYS_FONT_NAME],
        )
        return (await asyncio.to_thread(card.save, "PNG")).getvalue()
    except Exception as e:
        logger.exception(f"[Water] build period rank image failed: {e}")
        return None


def _next_level_target(level: int, base: int) -> int:
    cur = max(1, level)
    return base * (cur + 1) * (cur + 1)


def _level_progress(exp: int, level: int, base: int) -> tuple[float, int]:
    next_exp = _next_level_target(level, base)
    prev_exp = base * level * level if level > 1 else 0
    span = max(1, next_exp - prev_exp)
    ratio = (exp - prev_exp) / span
    return max(0.0, min(1.0, ratio)), max(0, next_exp - exp)


async def build_my_water_simple_image(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> bytes | None:
    try:
        scale = 2.0
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
        global_color = "#4F7DF3"
        matrix_color = "#F28A3B"
        global_panel = "#F0F7FF"
        matrix_panel = "#FFF0F6"

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

        current_achievements, history_achievements = _split_achievement_views(
            data.achievement_items,
            locale,
        )
        seasonal_total = max(1, _seasonal_total_count(len(current_achievements)))
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
            fill="#FFF0F6",
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
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            exp_panel_h,
            tone="#F7EAF1",
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
            _level_progress(global_exp, global_lv, 100)[0]
            if data.global_level is not None
            else 0.0
        )
        matrix_ratio = (
            _level_progress(matrix_exp, matrix_lv, 100)[0]
            if data.matrix_level is not None
            else 0.0
        )
        global_text = _short_exp(global_exp) if data.global_level is not None else "-"
        matrix_text = _short_exp(matrix_exp) if data.matrix_level is not None else "-"

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
            _draw_progress_bar(
                card=card,
                x=block_x + int(10 * scale),
                y=by + int(44 * scale),
                w=block_w - int(20 * scale),
                h=int(10 * scale),
                progress=ratio,
                bg="#E5EEFF" if idx == 0 else "#FCEEDC",
                fg=fg,
            )

        y += exp_panel_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + ach_panel_h),
            radius=int(18 * scale),
            fill=panel_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            ach_panel_h,
            tone="#F7EAF1",
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
        _draw_progress_bar(
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
                    fill="#FFEFD6",
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
                    fill="#B0712A",
                    halign="left",
                    font_families=[SYS_FONT_NAME],
                )

        y += ach_panel_h + gap
        card.draw_rounded_rectangle(
            (pad, y, width - pad, y + rank_panel_h),
            radius=int(18 * scale),
            fill=panel_soft_bg,
        )
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            rank_panel_h,
            tone="#F2E8F3",
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
                _format_rank(data.global_rank, locale),
            ),
            (
                tr(locale, "water.profile.image.rank.group"),
                _format_rank(data.group_user_rank, locale),
            ),
            (
                tr(locale, "water.profile.image.rank.matrix"),
                _format_rank(data.matrix_user_rank, locale),
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
        _draw_gloss_lines(
            card,
            pad,
            y,
            width - pad * 2,
            history_h,
            tone="#F7EAF1",
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
            (width / 2, height - int(30 * scale)),
            _build_copyright_text(now.year),
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


async def build_my_water_image(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> bytes | None:
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

        current_achievements, history_achievements = _split_achievement_views(
            data.achievement_items,
            locale,
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
            tr(
                locale,
                "water.profile.image.metric.matrix_summary",
                level=matrix_lv,
                total=_short_exp(matrix_exp),
                season=_short_exp(matrix_season),
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
                level=global_lv,
                total=_short_exp(global_exp),
                season=_short_exp(global_season),
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
            (
                tr(locale, "water.profile.image.progress.global_season"),
                sg_gap,
                sg_ratio,
            ),
            (
                tr(locale, "water.profile.image.progress.matrix_season"),
                sm_gap,
                sm_ratio,
            ),
            (
                tr(locale, "water.profile.image.progress.matrix_total_season"),
                smt_gap,
                smt_ratio,
            ),
        ]
        global_progress_items = [
            (tr(locale, "water.profile.image.progress.global_total"), gg_gap, gg_ratio),
            (tr(locale, "water.profile.image.progress.matrix_total"), gm_gap, gm_ratio),
            (
                tr(locale, "water.profile.image.progress.matrix_total_all"),
                gmt_gap,
                gmt_ratio,
            ),
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
            tr(locale, "water.profile.image.progress.global_section"),
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
            tr(locale, "water.profile.image.progress.season_section"),
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
                tr(
                    locale,
                    "water.profile.image.progress.gap",
                    exp=_short_exp(gap_value),
                ),
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
                tr(
                    locale,
                    "water.profile.image.progress.gap",
                    exp=_short_exp(gap_value),
                ),
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
                exp=_short_exp(data.global_level[0]),
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
                exp=_short_exp(data.matrix_level[0]),
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
                exp=_short_exp(data.matrix_total_level[0]),
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
                _format_rank(data.global_rank, locale),
                global_meta,
            ),
            (
                tr(locale, "water.profile.image.rank.group"),
                _format_rank(data.group_user_rank, locale),
                tr(locale, "water.profile.image.rank.meta.msg_count"),
            ),
            (
                tr(locale, "water.profile.image.rank.matrix"),
                _format_rank(data.matrix_user_rank, locale),
                matrix_user_meta,
            ),
        ]
        group_rank_items = [
            (
                tr(locale, "water.profile.image.rank.matrix_total"),
                _format_rank(data.matrix_rank, locale),
                matrix_total_meta,
            ),
            (
                tr(locale, "water.profile.image.rank.group_active"),
                _format_rank(data.group_rank, locale),
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
            (width / 2, height - int(36 * scale)),
            _build_copyright_text(now.year),
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


async def build_my_water_fallback_text(
    data: WaterProfileCardData,
    locale: LocaleCode,
) -> str:
    return _build_my_water_text_fallback(data, locale)
