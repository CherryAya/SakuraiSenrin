"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-04-04 15:17:04
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:17:04
Description: plugin metadata 构造工具
"""

from collections.abc import Mapping

from nonebot.plugin import PluginMetadata


def create_plugin_metadata(
    *,
    name: str,
    description: str,
    extra: Mapping[str, object],
) -> PluginMetadata:
    """统一构造 PluginMetadata；详细帮助文档统一放在 extra.docs。"""
    # extra 为 dict[Any, Any]：拷贝收窄键值
    resolved_extra: dict[str, object] = {}
    for key, value in extra.items():
        resolved_extra[key] = value
    return PluginMetadata(
        name=name,
        description=description,
        usage="",
        extra=resolved_extra,
    )
