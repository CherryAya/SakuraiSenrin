"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-06-11 23:35:00
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-11 23:35:00
Description: 词库审核文档入口
"""

from pathlib import Path

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "词库审核"
description = "词库待审、通过、拒绝与审批回复流程文档。"
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.GROUP_ADMIN,
        "docs": create_docs_meta(
            visible=False,
            category="fun",
            order=180,
            source=DOCS_SOURCE,
            slug="wordbank.approval",
            parent_slug="wordbank",
            aliases=("词库审核", "wordbank approval"),
        ),
    },
)
