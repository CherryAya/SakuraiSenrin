from __future__ import annotations

from pathlib import Path

from nonebot.matcher import Matcher
from nonebot.plugin import on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

from .handlers import register_handlers

name = tr("zh-CN", "plugin.self_unban.name")
description = tr("zh-CN", "plugin.self_unban.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#20C997",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.self_unban.name",
            "description_key": "plugin.self_unban.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=95,
            source=DOCS_SOURCE,
            slug="self_unban",
            aliases=("自助解封", "self.unban", "self_unban"),
        ),
    },
)

self_unban_matcher: type[Matcher] = on_command(
    "self.unban",
    aliases={"自助解封"},
    priority=5,
    block=True,
)

register_handlers(self_unban_matcher)
