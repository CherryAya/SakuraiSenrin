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
    """统一构造 PluginMetadata。

    本项目已将详细帮助文档统一迁移到 `extra.docs`，
    因此不再维护独立 usage 文本。
    """
    # PluginMetadata.extra 声明为 dict[Any, Any]；直接传 Mapping 会因不变性报错，
    # 逐项拷贝成 dict[str, object] 后既满足契约又保留键值类型。
    resolved_extra: dict[str, object] = {}
    for key, value in extra.items():
        resolved_extra[key] = value
    return PluginMetadata(
        name=name,
        description=description,
        usage="",
        extra=resolved_extra,
    )
