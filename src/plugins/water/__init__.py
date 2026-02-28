"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-28 12:30:25
Description: 水王
"""

import asyncio
from typing import NoReturn

from nonebot import on_message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown, CooldownIsolateLevel
from nonebot.adapters.onebot.v11.message import MessageSegment
from nonebot.plugin import PluginMetadata, on_command

from src.database.consts import WritePolicy
from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.plugins.water.img import build_water_rank_image

from .database import water_repo

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
asyncio.run(water_repo.init_all_tables())

self_global_water_status = on_command("我有多水", priority=5, block=True)
water_rank = on_command("水王排行榜", aliases={"水王"}, priority=5, block=True)
water_recorder = on_message(priority=99, block=False)


@water_recorder.handle()
async def _(event: GroupMessageEvent) -> None:
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    created_at = event.time

    await water_repo.save_message(
        group_id=group_id,
        user_id=user_id,
        created_at=created_at,
        policy=WritePolicy.BUFFERED,
    )


@water_rank.handle(
    parameterless=[
        Cooldown(
            cooldown=30,
            isolate_level=CooldownIsolateLevel.USER,
            prompt="冷却时间 30s，请耐心等待 qwq",
        )
    ]
)
async def _(event: GroupMessageEvent) -> NoReturn:
    await water_rank.send("凛凛统计中，请稍后喔……")
    res = await build_water_rank_image(str(event.group_id))
    if res:
        await water_rank.finish(MessageSegment.image(res))
    else:
        await water_rank.finish("凛凛，凛凛凛凛！无水无水！🏳️")
