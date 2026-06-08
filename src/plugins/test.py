"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-07 22:12:20
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:58
Description: 测试 matcher
"""

from nonebot.adapters.onebot.v11.message import Message
from nonebot.plugin import on_message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_static_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.test.name")
description = tr("zh-CN", "plugin.test.description")


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    return build_static_docs(
        name_key="plugin.test.name",
        description_key="plugin.test.description",
        content_key="plugin.test.docs",
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
        locale=locale,
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
            "name_key": "plugin.test.name",
            "description_key": "plugin.test.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="internal",
            order=99,
        ),
    },
)

test_matcher = on_message(block=False)


@test_matcher.handle()
async def _() -> None:
    pass
