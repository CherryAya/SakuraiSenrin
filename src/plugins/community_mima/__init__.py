"""社区衍生插件入口。"""

from __future__ import annotations

from pathlib import Path

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "用户反馈群密码"
description = "输入 #密码 即可查看加入用户反馈群所需的密码说明。"
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "Senrin-Community",
        "version": "0.1.0",
        "impression_color": "#FC8BDE",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="community",
            order=85,
            source=DOCS_SOURCE,
            slug="community.feedback-password",
            aliases=("用户反馈群密码", "反馈群密码", "密码", "入群密码"),
        ),
    },
)
