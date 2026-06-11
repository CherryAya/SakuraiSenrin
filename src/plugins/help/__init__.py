"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:22:09
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-06-12 01:10:00
Description: 帮助插件
"""

from dataclasses import dataclass
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
    DocNode,
    DocsMeta,
    build_doc_tree,
    can_view_node,
    create_docs_meta,
    load_demo_bytes,
    load_doc_node,
    match_doc_node,
    match_feature,
    read_docs_metas,
    render_doc_feature,
    render_doc_node_overview,
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
    node: DocNode


@dataclass(slots=True)
class MatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    entry: DocsEntry | None = None
    candidates: list[DocsEntry] | None = None


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
            visible=True,
            category="core",
            order=10,
            source=DOCS_SOURCE,
            slug="help",
            aliases=("帮助文档", "帮助中心"),
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


def _iter_docs_entries(locale: LocaleCode) -> list[DocsEntry]:
    entries: list[DocsEntry] = []
    for plugin in nonebot.get_loaded_plugins():
        if not _is_project_plugin(plugin):
            continue
        metadata = plugin.metadata
        if metadata is None:
            continue
        docs_metas = read_docs_metas(metadata)
        if not docs_metas:
            continue
        display_name = _resolve_metadata_text(metadata, locale, "name") or plugin.name
        summary = _resolve_metadata_text(metadata, locale, "description")
        permission = _read_plugin_permission(metadata)
        for docs in docs_metas:
            node = load_doc_node(
                source=docs["source"]["readme_path"],
                default_name=display_name,
                default_description=summary,
                trigger=metadata.extra.get("trigger", TriggerType.COMMAND),
                permission=permission,
                docs_meta={
                    **docs,
                    "permission": docs.get("permission", permission),
                },
                module_name=plugin.module_name,
                plugin_name=plugin.name,
            )
            entries.append(
                DocsEntry(
                    plugin=plugin,
                    metadata=metadata,
                    docs=docs,
                    display_name=display_name,
                    summary=summary,
                    permission=permission,
                    node=node,
                )
            )

    return sorted(
        entries,
        key=lambda entry: (
            entry.node.category,
            entry.node.order,
            entry.display_name.lower(),
        ),
    )


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
    return can_view_node(entry.node, actor_permission)


def _filter_authorized_entries(
    entries: list[DocsEntry],
    actor_permission: Permission,
) -> list[DocsEntry]:
    return [entry for entry in entries if _can_view_entry(entry, actor_permission)]


def _unique_entries(entries: list[DocsEntry]) -> list[DocsEntry]:
    seen: set[str] = set()
    unique: list[DocsEntry] = []
    for entry in entries:
        key = entry.node.slug
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _match_entry(entries: list[DocsEntry], query: str) -> MatchResult:
    result = match_doc_node([entry.node for entry in entries], query)
    if result.status == "matched" and result.node is not None:
        for entry in entries:
            if entry.node.slug == result.node.slug:
                return MatchResult(status="matched", entry=entry)
    if result.status == "ambiguous":
        candidates = [
            entry
            for entry in entries
            if any(entry.node.slug == node.slug for node in result.candidates)
        ]
        return MatchResult(status="ambiguous", candidates=_unique_entries(candidates))
    return MatchResult(status="not_found")


def _is_exact_entry_match(entry: DocsEntry, query: str) -> bool:
    q = query.strip().lower()
    return q in entry.node.search_tokens if q else False


def _match_exact_entry(entries: list[DocsEntry], query: str) -> MatchResult:
    exact = [entry for entry in entries if _is_exact_entry_match(entry, query)]
    if len(exact) == 1:
        return MatchResult(status="matched", entry=exact[0])
    if len(exact) > 1:
        return MatchResult(status="ambiguous", candidates=_unique_entries(exact))
    return MatchResult(status="not_found")


def _build_index_message(
    entries: list[DocsEntry],
    locale: LocaleCode,
    actor_permission: Permission = Permission.NORMAL,
) -> Message:
    tree = build_doc_tree([entry.node for entry in entries])
    roots = [
        node
        for node in tree.roots()
        if node.visible and can_view_node(node, actor_permission) and not node.hidden
    ]
    lines = [
        "📖 ===== 帮助文档 =====",
        "",
        "命令前缀: #help / #帮助",
        "",
        "帮助信息",
        "  示例: #help <节点名>",
        "  示例: #help <节点名> <子功能名>",
        "",
        "⚠️ 注意事项:",
        "1. 请确保输入的插件名称或节点名称存在。",
        "2. 如需进一步支持，请联系管理员，或加入反馈群「427842039」💬。",
    ]

    if not roots:
        lines.extend(["", tr(locale, "help.index.empty")])
        return Message("\n".join(lines))

    lines.extend(["", "🔧 当前可用模块如下:", ""])
    for index, node in enumerate(roots, start=1):
        lines.append(f"{index}. {node.title}")
        lines.append(f"  #help {node.title}")

    message = Message("\n".join(lines).strip())
    demo_message = _load_help_index_demo()
    if not demo_message:
        return message
    return message + demo_message


def _load_help_index_demo() -> Message:
    node = load_doc_node(
        source=DOCS_SOURCE,
        default_name=name,
        default_description=description,
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )
    match = match_feature(node.features, "index")
    if match.status != "matched" or match.feature is None:
        return Message()
    demo_bytes = load_demo_bytes(node.bundle, match.feature)
    if demo_bytes is None:
        return Message()
    return Message(MessageSegment.image(demo_bytes))


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
        lines.append(f"- {entry.display_name} ({entry.node.slug})")
    return Message("\n".join(lines))


def _build_permission_denied_message(entry: DocsEntry) -> Message:
    return Message(
        (
            f"无权限查看插件文档: {entry.display_name}\n"
            f"需要权限: {entry.permission.label}"
        ).strip()
    )


def _split_query(query: str) -> tuple[str, str | None]:
    normalized = query.strip()
    if not normalized:
        return "", None
    if " " not in normalized:
        return normalized, None
    plugin_query, feature_query = normalized.split(maxsplit=1)
    return plugin_query.strip(), feature_query.strip() or None


async def _resolve_docs_message(
    entry: DocsEntry,
    locale: LocaleCode,
    *,
    feature_query: str | None = None,
    actor_permission: Permission = Permission.NORMAL,
    all_entries: list[DocsEntry] | None = None,
) -> Message:
    tree = build_doc_tree([item.node for item in (all_entries or [entry])])
    children = tree.children_of(entry.node.slug)
    if feature_query:
        visible_features = tuple(
            feature
            for feature in entry.node.features
            if feature.permission == Permission.NONE
            or actor_permission.has(feature.permission)
        )
        match = match_feature(visible_features, feature_query)
        if match.status == "matched" and match.feature is not None:
            return render_doc_feature(
                entry.node,
                match.feature,
                locale=locale,
                include_demo=True,
            )
        child_match = match_doc_node(children, feature_query)
        if child_match.status == "matched" and child_match.node is not None:
            child_entry = next(
                (
                    item
                    for item in (all_entries or [])
                    if item.node.slug == child_match.node.slug
                ),
                None,
            )
            if child_entry is not None:
                return render_doc_node_overview(
                    child_entry.node,
                    locale=locale,
                    include_demo=True,
                    actor_permission=actor_permission,
                    children=tree.children_of(child_entry.node.slug),
                )
        if match.status == "ambiguous":
            return Message(
                "\n".join(
                    [
                        f"子功能查询存在歧义: {feature_query}",
                        "请使用更精确的子功能名。",
                        "",
                        *(
                            f"- {feature.title} ({feature.slug})"
                            for feature in match.candidates
                        ),
                    ]
                ).strip()
            )
        return Message(tr(locale, "help.query.not_found", query=feature_query))

    return render_doc_node_overview(
        entry.node,
        locale=locale,
        include_demo=True,
        actor_permission=actor_permission,
        children=children,
    )


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
        await matcher.finish(
            _build_index_message(authorized_entries, locale, actor_permission)
        )

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
        actor_permission=actor_permission,
        all_entries=entries,
    )
    await matcher.finish(docs_message)
