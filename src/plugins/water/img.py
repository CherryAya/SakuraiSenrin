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

from pil_utils import BuildImage

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
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
from .renderers.models import WaterDayRankCardData
from .renderers.models import WaterProfileCardData as WaterProfileCardData
from .renderers.rank import WaterRankRenderer, WaterRankRowData

FALLBACK_FONT_PATH = MAPLE_FONT_PATH
_water_podium_themes = water_podium_themes

# _build_copyright_text 的调用方是测试（tests/plugins/wordbank/test_search_cards.py），
# 显式列入 __all__ 声明该跨模块导出意图，避免被误判为私有死代码。
__all__ = ["_build_copyright_text"]


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

    users_data: dict[str, WaterRankRowData] = {}
    for idx, rank_item in enumerate(top_users):
        uid = rank_item.user_id

        member = await member_repo.get_member(uid, group_id)
        username = (
            await resolve_group_card(None, uid, group_id)
            if member
            else tr(locale, "water.image.day_rank.member_fallback", tail=uid[-4:])
        )

        # gather(return_exceptions=True) 下失败项为异常；渲染层要求 avatar_img
        # 具备 circle()，故降级为占位头像而非空字节（后者会在渲染时崩溃）。
        avatar_result = avatars[idx]
        avatar_img = (
            avatar_result
            if isinstance(avatar_result, BuildImage)
            else _build_avatar_fallback(
                100,
                username[:1] or "?",
                WATER_THEME.avatar_fallback_bg,
                WATER_THEME.avatar_fallback_fg,
            )
        )

        users_data[uid] = {
            "user_id": uid,
            "username": username,
            "count": rank_item.msg_count,
            "hourly_data": user_hourly_dict.get(uid, [0] * 24),
            "avatar_img": avatar_img,
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

    users_data: dict[str, WaterRankRowData] = {}
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
    header_avatar: BuildImage = await QQAvatar.fetch_group(data.group_id, size=256)
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


def _build_copyright_text(year: int) -> str:
    return f"© 2020-{year} SakuraiSenrin"


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
