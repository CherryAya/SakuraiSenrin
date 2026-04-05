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
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "测试插件"
description = """
开发期测试 matcher，用于验证消息链路。
""".strip()

docs_content = """
被动触发（开发环境）
""".strip()


def build_docs() -> Message:
    return build_static_docs(
        name=name,
        description=description,
        content=docs_content,
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
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
