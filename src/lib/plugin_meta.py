"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-04-04 15:17:04
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:17:04
Description: plugin metadata 构造工具
"""

from typing import Any

from nonebot.plugin import PluginMetadata


def create_plugin_metadata(
    *,
    name: str,
    description: str,
    extra: dict[str, Any],
) -> PluginMetadata:
    """统一构造 PluginMetadata。

    本项目已将详细帮助文档统一迁移到 `extra.docs`，
    因此不再维护独立 usage 文本。
    """
    return PluginMetadata(
        name=name,
        description=description,
        usage="",
        extra=extra,
    )
