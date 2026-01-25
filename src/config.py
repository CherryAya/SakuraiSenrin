from pydantic import BaseModel

import nonebot


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


config = nonebot.get_plugin_config(GlobalConfig)
