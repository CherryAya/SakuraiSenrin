"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 21:31:57
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:15
Description: 插件入口
"""

from pathlib import Path

import nonebot

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.admin_overview.name")
description = tr("zh-CN", "plugin.admin_overview.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#FF922B",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.admin_overview.name",
            "description_key": "plugin.admin_overview.description",
        },
        "docs": create_docs_meta(
            visible=False,
            category="admin",
            order=10,
            source=DOCS_SOURCE,
            slug="admin",
            kind="overview",
            aliases=("管理模块总览", "admin"),
        ),
    },
)

sub_plugins = nonebot.load_plugins(str(Path(__file__).parent.resolve()))
