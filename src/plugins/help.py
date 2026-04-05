"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:22:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:06:18
Description: 帮助插件
"""

from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass
import inspect
from typing import Literal, cast

import nonebot
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import Plugin, PluginMetadata, on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DocsMeta,
    DocsProvider,
    create_docs_meta,
)
from src.lib.plugin_meta import create_plugin_metadata

name = "帮助中心"
description = "统一汇总插件文档，并按插件 metadata.docs 自动注册。"


@dataclass(slots=True)
class DocsEntry:
    plugin: Plugin
    metadata: PluginMetadata
    docs: DocsMeta
    display_name: str


@dataclass(slots=True)
class MatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    entry: DocsEntry | None = None
    candidates: list[DocsEntry] | None = None


def build_docs() -> Message:
    return Message(
        (
            "===== 帮助中心 =====\n"
            "触发方式: 指令触发\n"
            "权限: 普通用户\n\n"
            "统一汇总插件文档，并按 metadata.docs 自动注册。\n\n"
            "用法:\n"
            "#help\n"
            "#help <插件名>"
        ).strip()
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "docs": create_docs_meta(
            build_docs,
            visible=True,
            category="core",
            order=10,
        ),
    },
)

help_matcher = on_command(
    "help",
    aliases={"帮助"},
    priority=5,
    block=True,
)


def _is_project_plugin(plugin: Plugin) -> bool:
    return plugin.module_name.startswith("src.")


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _to_message(raw: object) -> Message:
    if isinstance(raw, Message):
        return raw
    return Message(_normalize_text(raw))


def _read_docs_meta(metadata: PluginMetadata) -> DocsMeta | None:
    docs = metadata.extra.get("docs")
    if not isinstance(docs, dict):
        return None

    provider = docs.get("provider")
    if not callable(provider):
        return None

    return {
        "provider": cast(DocsProvider, provider),
        "visible": bool(docs.get("visible", True)),
        "category": _normalize_text(docs.get("category", "general")) or "general",
        "order": int(docs.get("order", 100)),
    }


def _iter_docs_entries() -> list[DocsEntry]:
    entries: list[DocsEntry] = []

    for plugin in nonebot.get_loaded_plugins():
        if not _is_project_plugin(plugin):
            continue
        metadata = plugin.metadata
        if metadata is None:
            continue
        docs = _read_docs_meta(metadata)
        if docs is None:
            continue

        display_name = _normalize_text(metadata.name) or plugin.name
        entries.append(
            DocsEntry(
                plugin=plugin,
                metadata=metadata,
                docs=docs,
                display_name=display_name,
            )
        )

    return sorted(
        entries,
        key=lambda e: (
            e.docs["category"],
            e.docs["order"],
            e.display_name.lower(),
        ),
    )


def _build_index_message(entries: list[DocsEntry]) -> Message:
    grouped: dict[str, list[DocsEntry]] = defaultdict(list)
    for entry in entries:
        if not entry.docs["visible"]:
            continue
        grouped[entry.docs["category"]].append(entry)

    lines = [
        "===== 插件帮助索引 =====",
        "发送 #help <插件名> 查看详细文档。",
    ]

    if not grouped:
        lines.append("当前暂无可展示插件文档。")
        return Message("\n".join(lines))

    for category in sorted(grouped.keys()):
        lines.append("")
        lines.append(f"[{category}]")
        for entry in grouped[category]:
            summary = _normalize_text(entry.metadata.description).splitlines()[0]
            if not summary:
                summary = "暂无描述"
            lines.append(f"- {entry.display_name}: {summary}")

    return Message("\n".join(lines).strip())


def _unique_entries(entries: list[DocsEntry]) -> list[DocsEntry]:
    seen: set[str] = set()
    unique: list[DocsEntry] = []
    for entry in entries:
        key = entry.plugin.module_name
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _match_entry(entries: list[DocsEntry], query: str) -> MatchResult:
    q = query.strip().lower()
    if not q:
        return MatchResult(status="not_found")

    exact: list[DocsEntry] = []
    fuzzy: list[DocsEntry] = []

    for entry in entries:
        candidates = {
            entry.display_name.lower(),
            entry.plugin.name.lower(),
            entry.plugin.module_name.lower(),
            entry.plugin.module_name.split(".")[-1].lower(),
        }
        if q in candidates:
            exact.append(entry)
            continue
        if q in entry.display_name.lower() or q in entry.plugin.name.lower():
            fuzzy.append(entry)

    if len(exact) == 1:
        return MatchResult(status="matched", entry=exact[0])
    if len(exact) > 1:
        return MatchResult(status="ambiguous", candidates=_unique_entries(exact))
    if len(fuzzy) == 1:
        return MatchResult(status="matched", entry=fuzzy[0])
    if len(fuzzy) > 1:
        return MatchResult(status="ambiguous", candidates=_unique_entries(fuzzy))
    return MatchResult(status="not_found")


def _build_fallback_docs(entry: DocsEntry, reason: str) -> Message:
    return Message(
        (
            f"===== {entry.display_name} =====\n"
            f"{_normalize_text(entry.metadata.description) or '暂无描述'}\n\n"
            f"文档降级原因: {reason}"
        ).strip()
    )


def _build_ambiguous_message(query: str, candidates: list[DocsEntry]) -> Message:
    lines = [
        f"插件查询存在歧义: {query}",
        "请使用更精确的插件名。",
        "",
        "候选项:",
    ]
    for entry in candidates:
        lines.append(f"- {entry.display_name} ({entry.plugin.module_name})")
    return Message("\n".join(lines))


async def _resolve_docs_message(entry: DocsEntry) -> Message:
    provider = entry.docs["provider"]
    try:
        result = provider()
        if inspect.isawaitable(result):
            awaited = cast(Awaitable[object], result)
            result = await awaited
        return _to_message(result)
    except Exception as e:
        return _build_fallback_docs(entry, f"{type(e).__name__}: {e}")


@help_matcher.handle()
async def _(
    matcher: Matcher,
    arg: Message = CommandArg(),
) -> None:
    entries = _iter_docs_entries()
    query = arg.extract_plain_text().strip()
    if not query:
        await matcher.finish(_build_index_message(entries))

    match_result = _match_entry(entries, query)
    if match_result.status == "not_found":
        await matcher.finish(
            Message(
                (f"未找到插件文档: {query}\n请先发送 #help 查看可用插件列表。").strip()
            )
        )
    if match_result.status == "ambiguous":
        await matcher.finish(
            _build_ambiguous_message(query, match_result.candidates or [])
        )

    assert match_result.entry is not None
    docs_message = await _resolve_docs_message(match_result.entry)
    await matcher.finish(docs_message)
