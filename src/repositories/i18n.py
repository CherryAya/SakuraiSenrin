from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from src.database.core.ops import GroupLocaleSettingOps, PluginConfigOps
from src.database.instances import core_db
from src.lib.i18n.types import LocaleCode

DEFAULT_LOCALE: LocaleCode = "zh-CN"
PLUGIN_NAME = "core.i18n"
SUPPORTED_LOCALES = {"zh-CN", "lzh", "x-meme"}


@dataclass(slots=True)
class I18nRepository:
    default_locale: LocaleCode = DEFAULT_LOCALE
    group_locales: dict[str, LocaleCode] = field(default_factory=dict)
    missing_group_locales: set[str] = field(default_factory=set)
    loaded_default: bool = False

    async def _ensure_default_loaded(self) -> None:
        if self.loaded_default:
            return
        async with core_db.session(commit=False) as session:
            config = await PluginConfigOps(session).get_by_plugin_name(PLUGIN_NAME)
        locale = config.get("default_locale")
        if isinstance(locale, str) and locale in SUPPORTED_LOCALES:
            self.default_locale = cast(LocaleCode, locale)
        self.loaded_default = True

    async def get_default_locale(self) -> LocaleCode:
        await self._ensure_default_loaded()
        return self.default_locale

    async def set_default_locale(self, locale: LocaleCode) -> None:
        async with core_db.session() as session:
            await PluginConfigOps(session).upsert_config(
                PLUGIN_NAME,
                {"default_locale": locale},
            )
        self.default_locale = locale
        self.loaded_default = True

    async def get_group_locale(self, group_id: str) -> LocaleCode | None:
        if group_id in self.group_locales:
            return self.group_locales[group_id]
        if group_id in self.missing_group_locales:
            return None
        async with core_db.session(commit=False) as session:
            value = await GroupLocaleSettingOps(session).get_locale(group_id)
        if value is not None and value in SUPPORTED_LOCALES:
            locale = cast(LocaleCode, value)
            self.group_locales[group_id] = locale
            self.missing_group_locales.discard(group_id)
            return locale
        self.missing_group_locales.add(group_id)
        return None

    async def set_group_locale(self, group_id: str, locale: LocaleCode) -> None:
        async with core_db.session() as session:
            await GroupLocaleSettingOps(session).upsert_locale(group_id, locale)
        self.group_locales[group_id] = locale
        self.missing_group_locales.discard(group_id)

    async def clear_group_locale(self, group_id: str) -> bool:
        async with core_db.session() as session:
            changed = await GroupLocaleSettingOps(session).delete_locale(group_id)
        self.group_locales.pop(group_id, None)
        self.missing_group_locales.discard(group_id)
        return changed

    async def list_group_locales(self) -> list[tuple[str, LocaleCode]]:
        async with core_db.session(commit=False) as session:
            rows = await GroupLocaleSettingOps(session).list_locales()
        return [
            (group_id, cast(LocaleCode, locale))
            for group_id, locale in rows
            if locale in SUPPORTED_LOCALES
        ]

    async def resolve_locale(self, group_id: str | None) -> LocaleCode:
        if group_id:
            if group_locale := await self.get_group_locale(group_id):
                return group_locale
        return await self.get_default_locale()
