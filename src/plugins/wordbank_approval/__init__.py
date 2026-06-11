"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-06-11 23:35:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-11 23:35:00
Description: 词库审核文档入口
"""

from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

name = "词库审核"
description = "词库待审、通过、拒绝与审批回复流程文档。"
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.GROUP_ADMIN,
        ctx=ctx,
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
            visible=True,
            category="admin",
            order=85,
            source=DOCS_SOURCE,
        ),
    },
)
