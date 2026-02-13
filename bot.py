import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot

from src.services.db import init_db
from src.services.sync import (
    load_blacklist_from_db,
    load_groups_from_db,
    load_members_from_db,
    load_users_from_db,
    sync_groups_from_api,
    sync_members_from_api,
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
    await load_users_from_db()
    await load_groups_from_db()
    await load_members_from_db()
    await load_blacklist_from_db()

    await sync_users_from_api(bot)
    await sync_groups_from_api(bot)


nonebot.load_plugins("src/hooks/")
nonebot.load_plugins("src/plugins/")

if __name__ == "__main__":
    nonebot.run()
