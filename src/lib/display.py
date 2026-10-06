"""Small shared display helpers.

These are presentation-layer utilities that must not pull in NoneBot runtime
dependencies, so they import the i18n catalog lazily inside the function body.
"""

from __future__ import annotations

from src.lib.i18n.types import LocaleCode


def fallback_group_name(
    group_id: str,
    *,
    locale: LocaleCode = "zh-CN",
) -> str:
    """Return a stable placeholder group name when the real one is unavailable."""
    from src.locales.zh_cn import CATALOG

    if not group_id:
        return CATALOG["common.group_name_fallback_plain"]
    return CATALOG["common.group_name_fallback"].format(suffix=group_id[-4:])


def format_group_label(
    group_id: str,
    group_name: str,
    *,
    locale: LocaleCode = "zh-CN",
) -> str:
    """Render a OneBot-style `[id|name]` group label."""
    if not group_id:
        return "-"
    safe_name = group_name or fallback_group_name(group_id, locale=locale)
    return f"[{group_id}|{safe_name}]"


__all__ = ["fallback_group_name", "format_group_label"]
