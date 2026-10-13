"""i18n 词典查表与格式化。

仅依赖 locales 目录与 logger，需要文案的模块直接 import 即可。
与事件绑定的发送能力见 ``src.lib.i18n.delivery``。
"""

from __future__ import annotations

from collections.abc import Mapping

from src.locales.lzh import CATALOG as LZH_CATALOG
from src.locales.x_meme import CATALOG as X_MEME_CATALOG
from src.locales.zh_cn import CATALOG as ZH_CATALOG
from src.logger import logger

from .keys import MessageKey
from .types import LocaleCode

DEFAULT_LOCALE: LocaleCode = "zh-CN"

LOCALE_NAME_KEYS: dict[LocaleCode, MessageKey] = {
    "zh-CN": "locale.name.zh_cn",
    "lzh": "locale.name.lzh",
    "x-meme": "locale.name.x_meme",
}

LOCALE_NAMES: dict[LocaleCode, str] = {
    "zh-CN": ZH_CATALOG["locale.name.zh_cn"],
    "lzh": ZH_CATALOG["locale.name.lzh"],
    "x-meme": ZH_CATALOG["locale.name.x_meme"],
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
    return CATALOGS.get(locale_code, ZH_CATALOG)


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


def tr_template(locale_code: LocaleCode, key: MessageKey) -> str:
    requested_catalog = get_catalog(locale_code)
    if key in requested_catalog:
        return requested_catalog[key]

    fallback = ZH_CATALOG.get(key)
    if fallback is not None:
        logger.warning(f"[i18n] missing key in locale {locale_code}: {key}")
        return fallback

    logger.error(f"[i18n] missing key in default locale: {key}")
    return ZH_CATALOG["i18n.missing"]


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
