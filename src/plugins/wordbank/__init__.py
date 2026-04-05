"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "词库模块"
description = """
词条学习与检索模块。
""".strip()

docs_content = """
1. #wordbank.add / #加词条 / #添加词条
2. #wordbank.search / #搜词条 / #搜索词条
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
            visible=True,
            category="fun",
            order=80,
        ),
    },
)

sub_plugins = nonebot.load_plugins(str(Path(__file__).parent.resolve()))
