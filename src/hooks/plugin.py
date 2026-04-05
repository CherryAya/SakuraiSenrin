"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-01-26 01:10:21
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:06
Description: 插件类 hook
"""

from nonebot.adapters.onebot.v11.message import Message

from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "插件钩子扩展点"
description = """
预留插件生命周期 hook 扩展点。
""".strip()

docs_content = "被动触发"


def build_docs() -> Message:
    return build_static_docs(
        name=name,
        description=description,
        content=docs_content,
        trigger="PASSIVE",
        permission="SUPERUSER",
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": "PASSIVE",
        "permission": "SUPERUSER",
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="internal",
            order=20,
        ),
    },
)
