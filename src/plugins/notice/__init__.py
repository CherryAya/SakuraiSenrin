"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 23:19:49
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:24
Description: 插件入口
"""

from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import build_static_docs, create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "通知模块总览"
description = """
通知事件处理合集入口，含好友请求、群组事件、邀请事件。
""".strip()

docs_content = "被动触发"


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
            category="system",
            order=10,
        ),
    },
)

sub_plugins = nonebot.load_plugins(str(Path(__file__).parent.resolve()))
