"""
Author: SakuraiCora
Date: 2024-12-21 02:03:13
LastEditors: SakuraiCora
LastEditTime: 2024-12-21 12:59:58
Description: 启动文件
"""

import nonebot

nonebot.init()

from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter  # noqa:E402

from src.scripts.init_database import (  # noqa:E402
    create_database,
    init_system_database,
    init_system_table,
)
from src.scripts.init_memory_cache import init_memory_cache  # noqa:E402

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)


@driver.on_startup
async def _():
    await create_database()


@driver.on_bot_connect
async def startup():
    await init_system_table()
    await init_system_database()

    await init_memory_cache()


@driver.on_shutdown
async def shutdown(): ...


nonebot.load_plugins("src/plugins/")
if __name__ == "__main__":
    nonebot.run()
