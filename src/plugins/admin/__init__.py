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
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "管理模块总览"
description = """
管理员命令合集入口，含群组管理、用户管理、邀请管理。
""".strip()

docs_content = """
1. #admin.group ...
2. #admin.user ...
3. #admin.invite ...
""".strip()


def build_docs() -> Message:
    return build_static_docs(
        name=name,
        description=description,
        content=docs_content,
        trigger=TriggerType.COMMAND,
        permission=Permission.SUPERUSER,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.SUPERUSER,
        "docs": create_docs_meta(
            build_docs,
            visible=False,
            category="admin",
            order=10,
        ),
    },
)

sub_plugins = nonebot.load_plugins(str(Path(__file__).parent.resolve()))
