"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 12:32:24
Description: 水王
"""

from typing import NoReturn

from nonebot.adapters.onebot.v11.message import MessageSegment
from nonebot.plugin import PluginMetadata, on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType

from .img import run_mock

name = "吹水记录"
description = """
吹水记录模块
""".strip()

usage = f"""
📖 ===== {name} =====

""".strip()

__plugin_meta__ = PluginMetadata(
    name=name,
    description=description,
    usage=usage,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
    },
)

self_global_water_status = on_command("我有多水", priority=5, block=True)
water_rank = on_command("水王排行榜", aliases={"水王"}, priority=5, block=True)


@water_rank.handle()
async def _() -> NoReturn:
    await water_rank.send("凛凛统计中，请稍后喔……")
    res = await run_mock()
    if res:
        await water_rank.finish(MessageSegment.image(res))
    else:
        await water_rank.finish("凛凛，凛凛凛凛！")


# @water_rank.handle()
# async def _(
#     bot: Bot,
#     event: GroupMessageEvent,
#     session: AsyncSession = Depends(get_session, use_cache=False),
# ):
#     await water_rank.send(NoticeBuilder.info("正在生成图片，请稍后..."))
#     if water_config.use_playwright:
#         await water_rank.finish(
#             await generate_water_rank_image_by_playwright(
#                 event.group_id.__str__(),
#                 await WaterInfoDAO(session).get_water_info_by_time(
#                     datetime.now().replace(hour=0, minute=0, second=0)
#                 ),
#             )
#         )
#     else:
#         await water_rank.finish(
#             await generate_water_rank_image_by_pillow(
#                 bot,
#                 event.group_id.__str__(),
#                 await WaterInfoDAO(session).get_water_info_by_time(
#                     datetime.now().replace(hour=0, minute=0, second=0)
#                 ),
#             )
#         )


# @self_global_water_status.handle()
# async def _(
#     event: GroupMessageEvent,
#     session: AsyncSession = Depends(get_session, use_cache=False),
# ):
#     (
#         global_user_count,
#         global_user_rank,
#         global_beaten_users,
#         global_user_percentage,
#     ) = await WaterInfoDAO(session).get_user_global_stats(
#         event.get_user_id(), datetime.now().replace(hour=0, minute=0, second=0)
#     )

#     (
#         group_user_count,
#         group_user_rank,
#         group_beaten_users,
#         group_user_percentage,
#     ) = await WaterInfoDAO(session).get_user_group_stats(
#         event.get_user_id(),
#         event.group_id.__str__(),
#         datetime.now().replace(hour=0, minute=0, second=0),
#     )
#     await self_global_water_status.finish(
#         "✨ 凛凛的水量检测报告 ✨\n"
#         "ฅ^•ﻌ•^ฅ 让凛凛看看你今天有多水～\n"
#         "━━━━━━━━━━━━━━━━━━\n"
#         "🌍 全局统计：\n"
#         f"   消息数：{global_user_count} 条\n"
#         f"   排名：第 {global_user_rank} 位\n"
#         f"   占比：{global_user_percentage}%\n"
#         f"   击败了 {global_beaten_users} 位用户\n"
#         "\n"
#         "💬 本群统计：\n"
#         f"   消息数：{group_user_count} 条\n"
#         f"   排名：第 {group_user_rank} 位\n"
#         f"   占比：{group_user_percentage}%\n"
#         f"   击败了 {group_beaten_users} 位用户\n"
#         "\n"
#         "哦嚯嚯！下一个水王会是你吗？٩(๑>◡<๑)۶凛凛很期待喔！"
#     )


# @on_message(block=False, priority=5).handle()
# async def _(
#     event: GroupMessageEvent,
#     session: AsyncSession = Depends(get_session, use_cache=False),
# ):
#     await WaterInfoDAO(session).create_water_info(
#         event.get_user_id(), event.group_id.__str__()
#     )
