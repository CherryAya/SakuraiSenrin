"""社区邀请说明插件入口。"""

from __future__ import annotations

from pathlib import Path

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "邀请凛凛入群须知"
description = "输入 #invite 查看邀请凛凛入群前需要了解的说明。"
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "Senrin-Community",
        "version": "0.1.0",
        "impression_color": "#7FB069",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="community",
            order=86,
            source=DOCS_SOURCE,
            slug="community.invite-guide",
            aliases=("邀请凛凛入群须知", "邀请须知", "invite", "入群邀请"),
        ),
    },
)
