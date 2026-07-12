"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-27 12:18:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-05 19:40:05
Description: 图片渲染组件，AI 神力！
"""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import arrow
from PIL import Image, ImageChops, ImageDraw
from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.lib.utils.img import QQAvatar
from src.logger import logger
from src.repositories import member_repo
from src.services.info import resolve_group_card, resolve_group_name

from .database import water_repo
from .renderers.common import (
    SYS_FONT_NAME,
    WATER_THEME,
    water_podium_themes,
)
from .renderers.models import (
    WaterDayRankCardData,
    WaterProfileCardData,
)
from .renderers.rank import WaterRankRenderer
from .services.achievement import ACHIEVEMENT_RULES

FALLBACK_FONT_PATH = MAPLE_FONT_PATH
_water_podium_themes = water_podium_themes


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
        group_id=group_id,
        group_name=group_name,
        group_avatar=group_avatar,
        today_king=king.user_id,
        group_rank=group_rank,
        users_data=users_data,
        locale=locale,
    )
    return img_bytes


async def build_water_day_rank_image(
    data: WaterDayRankCardData,
    locale: LocaleCode,
) -> bytes | None:
    started = perf_counter()
    if not data.top_items:
        return None

    users_data: dict[str, dict[str, Any]] = {}
    for item in data.top_items:
        avatar_img = item.avatar or _build_avatar_fallback(
            128,
            item.display_name[:1] or "?",
            WATER_THEME.avatar_fallback_bg,
            WATER_THEME.avatar_fallback_fg,
        )
        users_data[item.entity_id] = {
            "user_id": item.entity_id,
            "username": item.display_name,
            "secondary_label": item.secondary_label,
            "count": item.msg_count,
            "hourly_data": item.hourly_counts,
            "avatar_img": avatar_img,
            "trend": item.trend,
        }

    renderer = WaterRankRenderer()
    header_avatar = await QQAvatar.fetch_group(data.group_id, size=256)
    if not isinstance(header_avatar, BuildImage):
        header_avatar = _build_avatar_fallback(
            96,
            data.group_name[:1] or "?",
            WATER_THEME.group_avatar_fallback_bg,
            WATER_THEME.group_avatar_fallback_fg,
        )
    image = await renderer.render_async(
        group_id=data.group_id,
        group_name=data.group_name,
        group_avatar=header_avatar,
        today_king=data.top_items[0].entity_id,
        group_rank=1,
        users_data=users_data,
        locale=locale,
        header_title=data.title,
        summary_text=data.summary_label,
        footer_text=data.footer_label,
        scope_label=data.scope_label,
    )
    logger.debug(
        "[Water][RankRender] type=day title={} items={} elapsed_ms={:.2f} bytes={}",
        data.title,
        len(data.top_items),
        (perf_counter() - started) * 1000,
        len(image),
    )
    return image


def _format_rank(rank: int | None, locale: LocaleCode = "zh-CN") -> str:
    if rank is None:
        return "-"
    return tr(locale, "water.image.rank_format", rank=rank)


def _short_exp(exp: int | str) -> str:
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
        name = rule.name(locale) if rule is not None else fallback_name
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


def _draw_report_footer(
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
        _build_copyright_text(footer_time.year),
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
        stripe_color = _mix_hex(fg, WATER_THEME.white, 0.52)
        stripe_step = max(7, int(h * 0.55))
        stripe_span = max(10, int(h * 1.3))
        stripe_width = max(1, int(h * 0.22))
        for sx in range(-h, fill_w + h, stripe_step):
            texture.draw.line(
                (sx, h - 2, sx + stripe_span, 2),
                fill=stripe_color,
                width=stripe_width,
            )

        bubble_color = _mix_hex(fg, WATER_THEME.white, 0.72)
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
    tone: str = WATER_THEME.white,
    strength: float = 0.7,
) -> None:
    gloss = _mix_hex(tone, WATER_THEME.white, strength)
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
        return ("NEW", WATER_THEME.trend_new)
    if trend > 0:
        return (f"↑{trend}", WATER_THEME.trend_up)
    if trend < 0:
        return (f"↓{abs(trend)}", WATER_THEME.trend_down)
    return ("-0", WATER_THEME.trend_flat)


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
            else WATER_THEME.overview_highlight_bar
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
        tone = (
            base_color if count > 0 else _mix_hex(base_color, WATER_THEME.white, 0.55)
        )
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
