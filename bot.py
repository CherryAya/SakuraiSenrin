"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2025-11-02 23:26:30
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-19 22:30:46
Description: 入口文件
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot

from src.repositories import blacklist_repo, group_repo, member_repo, user_repo
from src.services.db import init_db
from src.services.sync import (
    sync_groups_from_api,
    sync_users_from_api,
)

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)


@driver.on_startup
async def _on_startup() -> None:
    await init_db()


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    await user_repo.warm_up()
    await group_repo.warm_up()
    await member_repo.warm_up()
    await blacklist_repo.warm_up()

    await sync_users_from_api(bot)
    await sync_groups_from_api(bot)
    # await sync_members_from_api(bot, "1107576103")


nonebot.load_plugins("src/hooks/")
nonebot.load_plugins("src/plugins/")

if __name__ == "__main__":
    nonebot.run()
