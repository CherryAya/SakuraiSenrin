"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 21:31:57
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:15
Description: 插件入口
"""

from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_static_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.admin_overview.name")
description = tr("zh-CN", "plugin.admin_overview.description")


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    return build_static_docs(
        name_key="plugin.admin_overview.name",
        description_key="plugin.admin_overview.description",
        content_key="plugin.admin_overview.docs",
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
        locale=locale,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.admin_overview.name",
            "description_key": "plugin.admin_overview.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="admin",
            order=10,
        ),
    },
)

sub_plugins = nonebot.load_plugins(str(Path(__file__).parent.resolve()))
