"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-26 01:10:21
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:06
Description: 插件类 hook
"""

from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.hook_plugin.name")
description = tr("zh-CN", "plugin.hook_plugin.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "plugin" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
        ctx=ctx,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.hook_plugin.name",
            "description_key": "plugin.hook_plugin.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="internal",
            order=20,
            source=DOCS_SOURCE,
        ),
    },
)
