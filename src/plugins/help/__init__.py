"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:22:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:06:18
Description: 帮助插件
"""

from collections.abc import Awaitable
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any, Literal, cast

import nonebot
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import Plugin, PluginMetadata, on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import (
    DocsMeta,
    DocsProvider,
    DocsRenderContext,
    build_readme_docs,
    create_docs_meta,
    load_demo_bytes,
    load_plugin_doc_bundle,
    match_feature,
)
from src.lib.plugin_meta import create_plugin_metadata

name = tr("zh-CN", "plugin.help.name")
description = tr("zh-CN", "plugin.help.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


@dataclass(slots=True)
class DocsEntry:
    plugin: Plugin
    metadata: PluginMetadata
    docs: DocsMeta
    display_name: str
    summary: str
    permission: Permission


@dataclass(slots=True)
class MatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    entry: DocsEntry | None = None
    candidates: list[DocsEntry] | None = None


def build_docs(ctx: DocsRenderContext | None = None) -> Message:
    return build_readme_docs(
        source=DOCS_SOURCE,
        name=name,
        description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=ctx,
    )


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.help.name",
            "description_key": "plugin.help.description",
        },
        "docs": create_docs_meta(
            build_docs,
            visible=True,
            category="core",
            order=10,
            source=DOCS_SOURCE,
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
        "source": _normalize_text(docs.get("source", "")),
        "hidden": bool(docs.get("hidden", False)),
    }


def _resolve_metadata_text(
    metadata: PluginMetadata,
    locale: LocaleCode,
    field: Literal["name", "description"],
) -> str:
    extra = metadata.extra
    raw_i18n = extra.get("i18n")
    if isinstance(raw_i18n, dict):
        key_name = f"{field}_key"
        maybe_key = raw_i18n.get(key_name)
        if isinstance(maybe_key, str):
            return tr(locale, cast(Any, maybe_key))
    raw_value = getattr(metadata, field, "")
    return _normalize_text(raw_value)


def _iter_docs_entries(locale: LocaleCode) -> list[DocsEntry]:
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

        display_name = _resolve_metadata_text(metadata, locale, "name") or plugin.name
        summary = _resolve_metadata_text(metadata, locale, "description")
        entries.append(
            DocsEntry(
                plugin=plugin,
                metadata=metadata,
                docs=docs,
                display_name=display_name,
                summary=summary,
                permission=_read_plugin_permission(metadata),
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


def _read_plugin_permission(metadata: PluginMetadata) -> Permission:
    raw_permission = metadata.extra.get("permission", Permission.NORMAL)
    if isinstance(raw_permission, Permission):
        return raw_permission
    if isinstance(raw_permission, str):
        try:
            return Permission[raw_permission]
        except KeyError:
            return Permission.NORMAL
    try:
        return Permission(raw_permission)
    except (TypeError, ValueError):
        return Permission.NORMAL


async def _resolve_actor_permission(bot: Bot, event: MessageEvent) -> Permission:
    if await SUPERUSER(bot, event):
        return (
            Permission.NORMAL
            | Permission.GROUP_ADMIN
            | Permission.GROUP_OWNER
            | Permission.SUPERUSER
        )
    if isinstance(event, GroupMessageEvent):
        role = getattr(event.sender, "role", "")
        if role == "owner":
            return Permission.NORMAL | Permission.GROUP_ADMIN | Permission.GROUP_OWNER
        if role == "admin":
            return Permission.NORMAL | Permission.GROUP_ADMIN
    return Permission.NORMAL


def _can_view_entry(entry: DocsEntry, actor_permission: Permission) -> bool:
    if entry.permission == Permission.NONE:
        return True
    return actor_permission.has(entry.permission)


def _filter_authorized_entries(
    entries: list[DocsEntry],
    actor_permission: Permission,
) -> list[DocsEntry]:
    return [entry for entry in entries if _can_view_entry(entry, actor_permission)]


def _build_index_message(entries: list[DocsEntry], locale: LocaleCode) -> Message:
    lines = [
        "📖 ===== 帮助文档 =====",
        "",
        "命令前缀: #help / #帮助",
        "",
        "帮助信息",
        "  示例: #help <插件名 / 别名>",
        "",
        "⚠️ 注意事项:",
        "1. 请确保输入的插件名称存在。",
        "2. 如需进一步支持，请联系管理员，或加入反馈群「427842039」💬。",
    ]

    visible_entries = [entry for entry in entries if entry.docs["visible"]]
    if not visible_entries:
        lines.append("")
        lines.append(tr(locale, "help.index.empty"))
        return Message("\n".join(lines))

    lines.extend(["", "🔧 当前可用插件如下:", ""])
    for index, entry in enumerate(visible_entries, start=1):
        lines.append(f"{index}. {entry.display_name}")
        lines.append(f"  #help {entry.display_name}")

    message = Message("\n".join(lines).strip())
    demo_message = _load_help_index_demo()
    if not demo_message:
        return message
    return message + demo_message


def _load_help_index_demo() -> Message:
    bundle = load_plugin_doc_bundle(
        source=DOCS_SOURCE,
        default_name=name,
        default_description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    match = match_feature(bundle.index, "index")
    if match.status != "matched" or match.feature is None:
        return Message()
    demo_bytes = load_demo_bytes(bundle, match.feature)
    if demo_bytes is None:
        return Message()
    return Message(MessageSegment.image(demo_bytes))


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


def _is_exact_entry_match(entry: DocsEntry, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    candidates = {
        entry.display_name.lower(),
        entry.plugin.name.lower(),
        entry.plugin.module_name.lower(),
        entry.plugin.module_name.split(".")[-1].lower(),
    }
    return q in candidates


def _match_exact_entry(entries: list[DocsEntry], query: str) -> MatchResult:
    exact = [entry for entry in entries if _is_exact_entry_match(entry, query)]
    if len(exact) == 1:
        return MatchResult(status="matched", entry=exact[0])
    if len(exact) > 1:
        return MatchResult(status="ambiguous", candidates=_unique_entries(exact))
    return MatchResult(status="not_found")


def _build_fallback_docs(
    entry: DocsEntry,
    locale: LocaleCode,
    reason: str,
) -> Message:
    return Message(
        (
            f"===== {entry.display_name} =====\n"
            f"{entry.summary or tr(locale, 'docs.default.no_description')}\n\n"
            f"{tr(locale, 'help.fallback.reason', reason=reason)}"
        ).strip()
    )


def _build_ambiguous_message(
    query: str,
    candidates: list[DocsEntry],
    locale: LocaleCode,
) -> Message:
    lines = [
        tr(locale, "help.query.ambiguous.title", query=query),
        tr(locale, "help.query.ambiguous.hint"),
        "",
        tr(locale, "help.query.ambiguous.candidates"),
    ]
    for entry in candidates:
        lines.append(f"- {entry.display_name} ({entry.plugin.module_name})")
    return Message("\n".join(lines))


def _build_permission_denied_message(entry: DocsEntry) -> Message:
    return Message(
        (
            f"无权限查看插件文档: {entry.display_name}\n"
            f"需要权限: {entry.permission.label}"
        ).strip()
    )


async def _resolve_docs_message(
    entry: DocsEntry,
    locale: LocaleCode,
    *,
    feature_query: str | None = None,
) -> Message:
    provider = entry.docs["provider"]
    try:
        if len(inspect.signature(provider).parameters) == 0:
            result = provider()
        else:
            view: Literal["plugin", "feature"] = (
                "feature" if feature_query else "plugin"
            )
            result = provider(
                DocsRenderContext(
                    locale=locale,
                    feature_query=feature_query,
                    view=view,
                )
            )
        if inspect.isawaitable(result):
            awaited = cast(Awaitable[object], result)
            result = await awaited
        return _to_message(result)
    except Exception as e:
        return _build_fallback_docs(entry, locale, f"{type(e).__name__}: {e}")


def _split_query(query: str) -> tuple[str, str | None]:
    normalized = query.strip()
    if not normalized:
        return "", None
    if " " not in normalized:
        return normalized, None
    plugin_query, feature_query = normalized.split(maxsplit=1)
    return plugin_query.strip(), feature_query.strip() or None


@help_matcher.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: MessageEvent,
    arg: Message = CommandArg(),
) -> None:
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    entries = _iter_docs_entries(locale)
    actor_permission = await _resolve_actor_permission(bot, event)
    authorized_entries = _filter_authorized_entries(entries, actor_permission)
    query = arg.extract_plain_text().strip()
    if not query:
        await matcher.finish(_build_index_message(authorized_entries, locale))

    match_result = _match_entry(authorized_entries, query)
    feature_query: str | None = None
    if match_result.status == "not_found" and " " in query:
        plugin_query, feature_query = _split_query(query)
        match_result = _match_entry(authorized_entries, plugin_query)

    if match_result.status == "not_found":
        denied_match = _match_exact_entry(entries, query)
        if denied_match.status == "not_found" and " " in query:
            plugin_query, _ = _split_query(query)
            denied_match = _match_exact_entry(entries, plugin_query)
        if (
            denied_match.status == "matched"
            and denied_match.entry is not None
            and not _can_view_entry(denied_match.entry, actor_permission)
        ):
            await matcher.finish(_build_permission_denied_message(denied_match.entry))
        await matcher.finish(Message(tr(locale, "help.query.not_found", query=query)))
    if match_result.status == "ambiguous":
        await matcher.finish(
            _build_ambiguous_message(query, match_result.candidates or [], locale)
        )

    assert match_result.entry is not None
    if not _can_view_entry(match_result.entry, actor_permission):
        await matcher.finish(_build_permission_denied_message(match_result.entry))

    docs_message = await _resolve_docs_message(
        match_result.entry,
        locale,
        feature_query=feature_query,
    )
    await matcher.finish(docs_message)
