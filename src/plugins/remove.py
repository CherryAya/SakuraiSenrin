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
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "引导式退群"
description = """
用于引导管理员以规范流程让机器人退出群聊。
""".strip()

docs_content = """
命令触发（待实现）
""".strip()


def build_docs() -> Message:
    return build_static_docs(
        name=name,
        description=description,
        content=docs_content,
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.GROUP_ADMIN,
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="system",
            order=90,
        ),
    },
)
