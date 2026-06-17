"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-26 01:10:21
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:06
Description: 插件类 hook
"""

from pathlib import Path

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.hook_plugin.name")
description = tr("zh-CN", "plugin.hook_plugin.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "plugin" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#15AABF",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.hook_plugin.name",
            "description_key": "plugin.hook_plugin.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=20,
            source=DOCS_SOURCE,
            slug="hook.plugin",
            kind="overview",
            aliases=("插件钩子扩展点", "插件钩子", "hook.plugin"),
        ),
    },
)
