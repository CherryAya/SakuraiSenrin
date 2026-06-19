"""社区衍生插件入口。"""

from __future__ import annotations

from pathlib import Path

from src.database.core.consts import Permission
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata

name = "凛凛的妙妙小工具"
description = "运势、jrlp、猜拳、漂流瓶……爱来自阿绫老师。"
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "Senrin-Community",
        "version": "0.1.0",
        "impression_color": "#FC8BDE",
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            visible=True,
            category="community",
            order=85,
            source=DOCS_SOURCE,
            slug="community.miaomiao-toolkit",
            aliases=("凛凛的妙妙小工具", "妙妙小工具", "妙妙小工具目录", "小工具"),
        ),
    },
)
