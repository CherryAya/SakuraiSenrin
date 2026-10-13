"""展示层小工具：i18n catalog 在函数体内懒导入，避开 NoneBot 运行时依赖。"""

from __future__ import annotations

from src.lib.i18n.types import LocaleCode


def fallback_group_name(
    group_id: str,
    *,
    locale: LocaleCode = "zh-CN",
) -> str:
    """取群名占位文案（真实群名缺失时）。"""
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
    """渲染 OneBot 风格 [id|name] 群标签。"""
    if not group_id:
        return "-"
    safe_name = group_name or fallback_group_name(group_id, locale=locale)
    return f"[{group_id}|{safe_name}]"


__all__ = ["fallback_group_name", "format_group_label"]
