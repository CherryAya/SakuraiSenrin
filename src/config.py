"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-24 18:13:05
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-20 00:00:56
Description: bot 全局配置类
"""

import json

import nonebot
from pydantic import BaseModel


class BackupRemoteProfile(BaseModel):
    name: str
    repository: str
    password: str
    access_key_id: str | None = None
    secret_access_key: str | None = None
    allowed_app_envs_for_backup: tuple[str, ...] = ()
    allowed_app_envs_for_restore: tuple[str, ...] = ()
    allow_backup: bool = True
    allow_restore: bool = True


class GlobalConfig(BaseModel):
    APP_ENV: str = "development"
    SUPERUSERS: set[str]
    IGNORED_USERS: set[str]
    MAIN_GROUP_ID: str
    HELP_SUPPORT_GROUPS: str = ""
    DEBUG: bool = False
    DEV_TEST_GROUPS: set[str] = set()
    DEV_TEST_USERS: set[str] = set()
    DEBUG_SQL_ECHO: bool = False

    HTTP_PROXY: str | None = None

    OBJECT_STORAGE_DEFAULT_PROVIDER: str | None = None

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
    BACKUP_REMOTE_PROFILE: str | None = None
    BACKUP_PROFILES_JSON: str | None = None
    BACKUP_PROD_RESTIC_REPOSITORY: str | None = None
    BACKUP_PROD_RESTIC_PASSWORD: str | None = None
    BACKUP_DEV_RESTIC_REPOSITORY: str | None = None
    BACKUP_DEV_RESTIC_PASSWORD: str | None = None
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

    def backup_profiles(self) -> dict[str, BackupRemoteProfile]:
        profiles = self._backup_profiles_from_flat_env()
        if profiles:
            return profiles

        raw = (self.BACKUP_PROFILES_JSON or "").strip()
        if raw:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("BACKUP_PROFILES_JSON must be a JSON object")
            profiles: dict[str, BackupRemoteProfile] = {}
            for name, item in payload.items():
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("backup profile name must be non-empty")
                if not isinstance(item, dict):
                    raise ValueError(f"backup profile {name!r} must be a JSON object")
                profile_payload = dict(item)
                profile_payload.setdefault("name", name)
                profile = BackupRemoteProfile(**profile_payload)
                profiles[name] = profile
            return profiles

        if self.BACKUP_RESTIC_REPOSITORY and self.BACKUP_RESTIC_PASSWORD:
            profile_name = (
                self.BACKUP_REMOTE_PROFILE or "default"
            ).strip() or "default"
            return {
                profile_name: BackupRemoteProfile(
                    name=profile_name,
                    repository=self.BACKUP_RESTIC_REPOSITORY,
                    password=self.BACKUP_RESTIC_PASSWORD,
                    access_key_id=self.R2_ACCESS_KEY_ID,
                    secret_access_key=self.R2_SECRET_ACCESS_KEY,
                    allowed_app_envs_for_backup=(self.APP_ENV,),
                    allowed_app_envs_for_restore=(self.APP_ENV,),
                    allow_backup=True,
                    allow_restore=True,
                )
            }
        return {}

    def _backup_profiles_from_flat_env(self) -> dict[str, BackupRemoteProfile]:
        profiles: dict[str, BackupRemoteProfile] = {}
        prod_repository = (self.BACKUP_PROD_RESTIC_REPOSITORY or "").strip()
        prod_password = (self.BACKUP_PROD_RESTIC_PASSWORD or "").strip()
        if prod_repository and prod_password:
            profiles["prod"] = BackupRemoteProfile(
                name="prod",
                repository=prod_repository,
                password=prod_password,
                access_key_id=self.R2_ACCESS_KEY_ID,
                secret_access_key=self.R2_SECRET_ACCESS_KEY,
                allowed_app_envs_for_backup=("production",),
                allowed_app_envs_for_restore=("production", "development"),
                allow_backup=True,
                allow_restore=True,
            )

        dev_repository = (self.BACKUP_DEV_RESTIC_REPOSITORY or "").strip()
        dev_password = (self.BACKUP_DEV_RESTIC_PASSWORD or "").strip()
        if dev_repository and dev_password:
            profiles["dev"] = BackupRemoteProfile(
                name="dev",
                repository=dev_repository,
                password=dev_password,
                access_key_id=self.R2_ACCESS_KEY_ID,
                secret_access_key=self.R2_SECRET_ACCESS_KEY,
                allowed_app_envs_for_backup=("development",),
                allowed_app_envs_for_restore=("development",),
                allow_backup=True,
                allow_restore=True,
            )
        return profiles


config: GlobalConfig = nonebot.get_plugin_config(GlobalConfig)
