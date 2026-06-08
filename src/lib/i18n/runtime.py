from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import Event
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher

from src.locales.lzh import CATALOG as LZH_CATALOG
from src.locales.x_meme import CATALOG as X_MEME_CATALOG
from src.locales.zh_cn import CATALOG as ZH_CATALOG
from src.logger import logger
from src.repositories import i18n_repo

from .keys import MessageKey
from .types import LocaleCode

DEFAULT_LOCALE: LocaleCode = "zh-CN"
LOCALE_NAMES: dict[LocaleCode, str] = {
    "zh-CN": "简体中文",
    "lzh": "文言文",
    "x-meme": "抽象文",
}
LOCALE_ALIASES: dict[str, LocaleCode] = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "cn": "zh-CN",
    "lzh": "lzh",
    "wyw": "lzh",
    "classical": "lzh",
    "meme": "x-meme",
    "abstract": "x-meme",
    "x-meme": "x-meme",
    "抽象": "x-meme",
    "梗文": "x-meme",
}
CATALOGS: dict[LocaleCode, Mapping[str, str]] = {
    "zh-CN": ZH_CATALOG,
    "lzh": LZH_CATALOG,
    "x-meme": X_MEME_CATALOG,
}


def get_catalog(locale_code: LocaleCode) -> Mapping[str, str]:
    return CATALOGS[locale_code]


def get_user_facing_locale_name(locale_code: LocaleCode) -> str:
    return LOCALE_NAMES[locale_code]


def normalize_locale(raw_locale: str) -> LocaleCode | None:
    normalized = raw_locale.strip().lower()
    if not normalized:
        return None
    if normalized in LOCALE_ALIASES:
        return LOCALE_ALIASES[normalized]
    if raw_locale in LOCALE_NAMES:
        return raw_locale
    return None


def _safe_format(template: str, message_key: str, **params: object) -> str:
    try:
        return template.format(**params)
    except Exception as exc:
        logger.warning(
            f"[i18n] format failed for {message_key}: {type(exc).__name__}: {exc}"
        )
        return template


def tr(locale_code: LocaleCode, key: MessageKey, **params: object) -> str:
    requested_catalog = get_catalog(locale_code)
    if key in requested_catalog:
        return _safe_format(requested_catalog[key], key, **params)

    fallback = ZH_CATALOG.get(key)
    if fallback is not None:
        logger.warning(f"[i18n] missing key in locale {locale_code}: {key}")
        return _safe_format(fallback, key, **params)

    logger.error(f"[i18n] missing key in default locale: {key}")
    return _safe_format(
        ZH_CATALOG["i18n.missing"],
        "i18n.missing",
        key=key,
    )


def msg(locale_code: LocaleCode, key: MessageKey, **params: object) -> Message:
    return Message(tr(locale_code, key, **params))


def format_duration(locale_code: LocaleCode, seconds: int) -> str:
    total_seconds = max(0, int(seconds))
    if total_seconds <= 0:
        return tr(locale_code, "i18n.duration.zero")

    day, remain = divmod(total_seconds, 86400)
    hour, remain = divmod(remain, 3600)
    minute, second = divmod(remain, 60)

    parts: list[str] = []
    if day:
        parts.append(tr(locale_code, "i18n.duration.day", count=day))
    if hour:
        parts.append(tr(locale_code, "i18n.duration.hour", count=hour))
    if minute:
        parts.append(tr(locale_code, "i18n.duration.minute", count=minute))
    if second:
        parts.append(tr(locale_code, "i18n.duration.second", count=second))

    return " ".join(parts) if parts else tr(locale_code, "i18n.duration.zero")


def get_group_locale(event: Event | None) -> str | None:
    if event is None:
        return None
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None
    return str(group_id)


async def resolve_locale(group_id: str | None = None) -> LocaleCode:
    return await i18n_repo.resolve_locale(group_id)


async def send_i18n(
    matcher: Matcher,
    event: Event | None,
    key: MessageKey,
    **params: object,
) -> Any:
    locale = await resolve_locale(get_group_locale(event))
    return await matcher.send(msg(locale, key, **params))


async def finish_i18n(
    matcher: Matcher,
    event: Event | None,
    key: MessageKey,
    **params: object,
) -> None:
    locale = await resolve_locale(get_group_locale(event))
    await matcher.finish(msg(locale, key, **params))


async def send_private_i18n(
    bot: Bot,
    target_user_id: int,
    key: MessageKey,
    *,
    locale_group_id: str | None = None,
    **params: object,
) -> dict[str, Any]:
    locale = await resolve_locale(locale_group_id)
    return await bot.send_private_msg(
        user_id=target_user_id,
        message=msg(locale, key, **params),
    )


async def send_group_i18n(
    bot: Bot,
    group_id: int,
    key: MessageKey,
    **params: object,
) -> dict[str, Any]:
    locale = await resolve_locale(str(group_id))
    return await bot.send_group_msg(
        group_id=group_id,
        message=msg(locale, key, **params),
    )
