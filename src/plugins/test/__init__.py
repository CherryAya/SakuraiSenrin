"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-07 22:12:20
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:58
Description: 测试 matcher
"""

from pathlib import Path

from nonebot.plugin import on_message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.test.name")
description = tr("zh-CN", "plugin.test.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#40C057",
        "trigger": TriggerType.PASSIVE,
        "permission": Permission.SUPERUSER,
        "i18n": {
            "name_key": "plugin.test.name",
            "description_key": "plugin.test.description",
        },
        "docs": create_docs_meta(
            visible=False,
            category="internal",
            order=99,
            source=DOCS_SOURCE,
        ),
    },
)

test_matcher = on_message(block=False)


@test_matcher.handle()
async def _() -> None:
    pass
