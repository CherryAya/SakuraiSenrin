"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:22:29
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:46
Description: 引导式退群
"""

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

name = tr("zh-CN", "plugin.remove.name")
description = tr("zh-CN", "plugin.remove.description")


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    return build_static_docs(
        name_key="plugin.remove.name",
        description_key="plugin.remove.description",
        content_key="plugin.remove.docs",
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
        locale=locale,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.GROUP_ADMIN,
        "i18n": {
            "name_key": "plugin.remove.name",
            "description_key": "plugin.remove.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="system",
            order=90,
        ),
    },
)
