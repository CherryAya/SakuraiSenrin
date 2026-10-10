"""help 数据模型与共享常量。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nonebot.plugin import Plugin, PluginMetadata

from src.database.core.consts import Permission
from src.lib.plugin_docs import DocNode, DocsMeta


@dataclass(slots=True)
class DocsEntry:
    plugin: Plugin
    metadata: PluginMetadata
    docs: DocsMeta
    display_name: str
    summary: str
    permission: Permission
    node: DocNode


@dataclass(slots=True)
class MatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    entry: DocsEntry | None = None
    candidates: list[DocsEntry] | None = None


type RootSection = Literal["system", "developer", "community"]


DOCS_SOURCE = Path(__file__).resolve().parent / "docs" / "README.MD"
HELP_FORWARD_FALLBACK_NICKNAME = "SakuraiSenrin"
