"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-24 18:13:05
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-20 00:00:56
Description: bot 全局配置类
"""

import nonebot
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HelpSupportGroupConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1)
    group_id: str = ""
    url: str = Field(min_length=1)


class GlobalConfig(BaseModel):
    SUPERUSERS: set[str]
    IGNORED_USERS: set[str]
    MAIN_GROUP_ID: str
    HELP_SUPPORT_GROUPS: tuple[HelpSupportGroupConfig, ...] = ()
    ADMIN_NOTIFY_PRIVATE_ENABLED: bool = True
    ADMIN_NOTIFY_GROUP_ENABLED: bool = False
    ADMIN_NOTIFY_GROUP_IDS: tuple[str, ...] = ()
    DEBUG: bool = False
    DEV_TEST_GROUPS: set[str] = set()
    DEV_TEST_USERS: set[str] = set()
    DEBUG_SQL_ECHO: bool = False

    HTTP_PROXY: str | None = None

    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None
    R2_PUBLIC_BASE_URL: str | None = None
    R2_ENDPOINT: str | None = None

    GITHUB_TOKEN: str
    GITHUB_REPO: str
    GITHUB_BRANCH: str

    WORDBANK_MEDIA_PROVIDER: str = "local"
    WORDBANK_MEDIA_CACHE_ENABLED: bool = True
    WORDBANK_MEDIA_CACHE_ROOT: str = "./data/wordbank/media_cache"
    WORDBANK_MEDIA_CACHE_MAX_BYTES: int = 512 * 1024 * 1024
    WORDBANK_MEDIA_CACHE_TRIM_TO_BYTES: int = 460 * 1024 * 1024
    WORDBANK_MEDIA_CACHE_MAX_FILES: int = 5_000
    WORDBANK_MEDIA_REMOTE_REQUIRED: bool = False
    WORDBANK_MEDIA_REMOTE_SYNC_MODE: str = "deferred"
    WORDBANK_MEDIA_MIGRATION_BATCH_SIZE: int = 200

    BACKUP_ENABLED: bool = False
    BACKUP_RESTIC_REPOSITORY: str | None = None
    BACKUP_RESTIC_PASSWORD: str | None = None
    BACKUP_LOCAL_ROOT: str = "./data/backup"
    BACKUP_CRON_HOUR: int = 3
    BACKUP_CRON_MINUTE: int = 20
    BACKUP_RETENTION_DAILY: int = 7
    BACKUP_RETENTION_WEEKLY: int = 4
    BACKUP_RETENTION_MONTHLY: int = 6
    BACKUP_REQUIRE_RESTIC: bool = True

    SAUCENAO_KEY: str | None = None
    ASCII2D_KEY: str | None = None
    SENTRY_DSN: str | None = None

    @field_validator("ADMIN_NOTIFY_GROUP_IDS")
    @classmethod
    def normalize_admin_notification_group_ids(
        cls, group_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(value for item in group_ids if (value := item.strip()))
        )


config: GlobalConfig = nonebot.get_plugin_config(GlobalConfig)
