import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()


driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)


@driver.on_startup
async def _on_startup():
    pass


@driver.on_bot_connect
async def _on_bot_connect():
    pass


@driver.on_shutdown
async def shutdown():
    pass


nonebot.load_plugins("src/core/")
nonebot.load_plugins("src/plugins/")

if __name__ == "__main__":
    nonebot.run()
