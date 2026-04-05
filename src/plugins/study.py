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
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "学习词库（传统版）"
description = """
历史词库学习模块（兼容保留）。
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
        permission=Permission.NORMAL,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="fun",
            order=85,
        ),
    },
)
