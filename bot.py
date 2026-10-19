"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2025-11-02 23:26:30
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-02 20:00:06
Description: 入口文件
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot

from src.lib.docs_assets import init_docs_assets
from src.lib.message_api_hooks import install_message_delivery_hooks
from src.lib.trace_log import configure_logging, shutdown_logging
from src.scripts.install import init_fonts
from src.services.db import init_db
from src.services.message_asset_startup import run_startup_message_asset_check

nonebot.init()
configure_logging(log_role="app")
init_fonts()
init_docs_assets()
install_message_delivery_hooks()

from src.services.backup_scheduler import install_backup_scheduler
from src.services.shard_archiver import install_shard_archiver_scheduler

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
install_backup_scheduler()
install_shard_archiver_scheduler()


@driver.on_startup
async def _on_startup() -> None:
    await init_db()
    await run_startup_message_asset_check()


@driver.on_shutdown
async def _on_shutdown() -> None:
    from src.services.writer_health import (
        drain_all_writers,
        flush_dead_letters,
        report_writer_health,
    )

    # 关机顺序固定：日志最后关
    await drain_all_writers()
    await flush_dead_letters()
    await report_writer_health()
    await shutdown_logging()


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    from src.repositories import blacklist_repo, group_repo, member_repo, user_repo
    from src.services.startup_sync import run_startup_backup_freshness_check
    from src.services.sync import sync_groups_from_api, sync_users_from_api

    await user_repo.warm_up()
    await group_repo.warm_up()
    await member_repo.warm_up()
    await blacklist_repo.warm_up()

    await run_startup_backup_freshness_check(bot)

    await sync_users_from_api(bot)
    await sync_groups_from_api(bot)


nonebot.load_plugins("src/hooks/")
nonebot.load_plugins("src/plugins/")

if __name__ == "__main__":
    nonebot.run()
