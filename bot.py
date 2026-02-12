import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot

from src.services.db import init_db

nonebot.init()


driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)


@driver.on_startup
async def _on_startup() -> None:
    await init_db()


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    pass


nonebot.load_plugins("src/hooks/")
nonebot.load_plugins("src/plugins/")

if __name__ == "__main__":
    nonebot.run()
