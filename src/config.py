"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-24 18:13:05
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-20 00:00:56
Description: bot 全局配置类
"""

import nonebot
from pydantic import BaseModel


class GlobalConfig(BaseModel):
    SUPERUSERS: set[str]
    IGNORED_USERS: set[str]
    MAIN_GROUP_ID: str

    HTTP_PROXY: str | None = None

    GITHUB_TOKEN: str
    GITHUB_REPO: str
    GITHUB_BRANCH: str

    SAUCENAO_KEY: str | None = None
    ASCII2D_KEY: str | None = None
    SENTRY_DSN: str | None = None


config: GlobalConfig = nonebot.get_plugin_config(GlobalConfig)
