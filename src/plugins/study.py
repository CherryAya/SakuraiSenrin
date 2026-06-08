"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-18 23:51:56
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:52
Description: 学习词库-传统版
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

name = tr("zh-CN", "plugin.study.name")
description = tr("zh-CN", "plugin.study.description")


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    return build_static_docs(
        name_key="plugin.study.name",
        description_key="plugin.study.description",
        content_key="plugin.study.docs",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        locale=locale,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.study.name",
            "description_key": "plugin.study.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="fun",
            order=85,
        ),
    },
)
