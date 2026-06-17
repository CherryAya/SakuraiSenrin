"""Derived help registry for wordbank-powered community entrypoints."""

from __future__ import annotations

from src.database.core.consts import Permission
from src.lib.plugin_docs import VirtualPluginDocSpec


def build_wordbank_derived_help(locale: str) -> tuple[VirtualPluginDocSpec, ...]:
    _ = locale
    return (
        VirtualPluginDocSpec(
            slug="derived.wordbank.miaomiao-toolkit",
            title="凛凛的妙妙小工具目录",
            summary="基于 wordbank 的社区创作入口，实际内容由社区词条决定。",
            description=(
                "这是一个基于 wordbank 的社区创作入口。实际触发内容由社区词条决定，"
                "help 只暴露这个入口本身，不为每个社区词条派生独立命令说明。"
            ),
            trigger="词条触发 / #help 查询",
            author="SakuraiSenrin",
            version="0.1.0",
            impression_color="#74C0FC",
            aliases=("凛凛的妙妙小工具", "妙妙小工具", "妙妙小工具目录"),
            permission=Permission.NORMAL,
            category="community",
            order=85,
            visible=True,
            origin_plugin_slug="wordbank",
            features=(),
        ),
    )
