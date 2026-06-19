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
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import Plugin, PluginMetadata, on_command

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.demo_theme import DEFAULT_IMPRESSION_COLOR, normalize_hex_color
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.messages import image_message, text_message
from src.lib.plugin_docs import (
    DocNode,
    DocsMeta,
    HelpDashboardSection,
    VirtualPluginDocSpec,
    build_doc_tree,
    build_feature_copy_text,
    build_plugin_guide_copy_text,
    build_simple_leaf_copy_text,
    build_static_entry_copy_text,
    can_view_node,
    create_docs_meta,
    filter_features_by_permission,
    load_doc_node,
    load_virtual_doc_node,
    match_doc_node,
    match_feature,
    node_help_command,
    read_docs_metas,
    render_doc_node_overview,
    render_feature_deep_dive,
    render_help_dashboard,
    render_plugin_guide,
    render_static_entry,
    resolve_help_entry_shape,
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


type RootSection = Literal["system", "developer", "community"]


@dataclass(slots=True, frozen=True)
class SectionStyle:
    accent: str
    panel_bg: str
    panel_soft_bg: str
    text: str
    hint: str
    command_bg: str
    command_text: str
    marker: str


SECTION_STYLES: dict[RootSection, SectionStyle] = {
    "system": SectionStyle(
        accent="#8AB4F8",
        panel_bg="#F4F8FF",
        panel_soft_bg="#EAF2FF",
        text="#2F5E9E",
        hint="#5F7EA8",
        command_bg="#EAF2FF",
        command_text="#315F9D",
        marker="square",
    ),
    "developer": SectionStyle(
        accent="#FFB067",
        panel_bg="#FFF9F2",
        panel_soft_bg="#FFF1DE",
        text="#9A5B1D",
        hint="#8C7254",
        command_bg="#FFF0DE",
        command_text="#8A531F",
        marker="diamond",
    ),
    "community": SectionStyle(
        accent="#FF9EBB",
        panel_bg="#FFF5F8",
        panel_soft_bg="#FFE8EF",
        text="#A24E6A",
        hint="#946476",
        command_bg="#FFE7EF",
        command_text="#9A4B68",
        marker="ring",
    ),
}

__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#5C7CFA",
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


def _section_title(section: RootSection, locale: LocaleCode) -> str:
    key = {
        "system": "help.dashboard.section.system",
        "developer": "help.dashboard.section.developer",
        "community": "help.dashboard.section.community",
    }[section]
    return tr(locale, key)


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


def _read_plugin_impression_color(metadata: PluginMetadata) -> str | None:
    raw_color = metadata.extra.get("impression_color")
    if isinstance(raw_color, str) and raw_color.strip():
        return normalize_hex_color(raw_color, fallback=DEFAULT_IMPRESSION_COLOR)
    return None


def _resolve_docs_impression_color(
    plugin: Plugin,
    metadata: PluginMetadata,
    loaded_plugins: list[Plugin],
) -> str | None:
    explicit_color = _read_plugin_impression_color(metadata)
    if explicit_color is not None:
        return explicit_color

    parts = plugin.module_name.split(".")
    for index in range(len(parts) - 1, 0, -1):
        parent_module = ".".join(parts[:index])
        if parent_module == plugin.module_name:
            continue
        parent_plugin = next(
            (
                candidate
                for candidate in loaded_plugins
                if candidate.module_name == parent_module
            ),
            None,
        )
        if parent_plugin is None or parent_plugin.metadata is None:
            continue
        inherited_color = _read_plugin_impression_color(parent_plugin.metadata)
        if inherited_color is not None:
            return inherited_color
    return None


def _iter_docs_entries(locale: LocaleCode) -> list[DocsEntry]:
    entries: list[DocsEntry] = []
    loaded_plugins = list(nonebot.get_loaded_plugins())
    for plugin in loaded_plugins:
        if not _is_project_plugin(plugin):
            continue
        metadata = plugin.metadata
        if metadata is None:
            continue
        docs_metas = read_docs_metas(metadata)
        derived_specs = _read_derived_help_specs(metadata, locale)
        if not docs_metas and not derived_specs:
            continue
        display_name = _resolve_metadata_text(metadata, locale, "name") or plugin.name
        summary = _resolve_metadata_text(metadata, locale, "description")
        permission = _read_plugin_permission(metadata)
        impression_color = _resolve_docs_impression_color(
            plugin,
            metadata,
            loaded_plugins,
        )
        for docs in docs_metas:
            node = load_doc_node(
                source=docs["source"]["readme_path"],
                default_name=display_name,
                default_description=summary,
                trigger=metadata.extra.get("trigger", TriggerType.COMMAND),
                permission=permission,
                impression_color=impression_color,
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
                    display_name=node.title,
                    summary=node.summary,
                    permission=permission,
                    node=node,
                )
            )
        for spec in derived_specs:
            spec_permission = spec.permission
            node = load_virtual_doc_node(
                VirtualPluginDocSpec(
                    slug=spec.slug,
                    title=spec.title,
                    summary=spec.summary,
                    description=spec.description,
                    trigger=spec.trigger,
                    author=spec.author,
                    version=spec.version,
                    impression_color=spec.impression_color
                    or impression_color
                    or DEFAULT_IMPRESSION_COLOR,
                    features=spec.features,
                    aliases=spec.aliases,
                    permission=spec_permission,
                    category=spec.category,
                    order=spec.order,
                    visible=spec.visible,
                    hidden=spec.hidden,
                    internal=spec.internal,
                    kind=spec.kind,
                    parent_slug=spec.parent_slug,
                    plugin_name=plugin.name,
                    module_name=plugin.module_name,
                    origin_plugin_slug=spec.origin_plugin_slug or plugin.name,
                )
            )
            entries.append(
                DocsEntry(
                    plugin=plugin,
                    metadata=metadata,
                    docs=create_docs_meta(
                        visible=spec.visible,
                        hidden=spec.hidden,
                        internal=spec.internal,
                        kind=spec.kind,
                        category=spec.category,
                        order=spec.order,
                        source=node.source_path,
                        slug=spec.slug,
                        parent_slug=spec.parent_slug,
                        aliases=spec.aliases,
                    ),
                    display_name=spec.title,
                    summary=spec.summary,
                    permission=spec_permission,
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


def _read_derived_help_specs(
    metadata: PluginMetadata,
    locale: LocaleCode,
) -> tuple[VirtualPluginDocSpec, ...]:
    raw = metadata.extra.get("derived_help_provider")
    if not callable(raw):
        return ()
    specs = raw(locale)
    if not isinstance(specs, (list, tuple)):
        return ()
    return tuple(spec for spec in specs if isinstance(spec, VirtualPluginDocSpec))


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


def _root_entries_for_index(
    entries: list[DocsEntry],
    actor_permission: Permission,
) -> list[DocsEntry]:
    tree = build_doc_tree([entry.node for entry in entries])
    root_slugs = {node.slug for node in tree.roots()}
    return [
        entry
        for entry in entries
        if entry.node.slug in root_slugs
        and entry.node.visible
        and not entry.node.hidden
        and not entry.node.internal
        and _can_view_entry(entry, actor_permission)
    ]


def _classify_root_section(entry: DocsEntry) -> RootSection:
    slug = entry.node.slug
    module_name = entry.node.module_name
    if entry.node.category == "community" or slug.startswith("derived."):
        return "community"
    if module_name.startswith("src.hooks.") or slug.startswith("hook."):
        return "system"
    if slug in {"help", "notice", "admin"}:
        return "system"
    return "developer"


def _build_index_sections(
    entries: list[DocsEntry],
    actor_permission: Permission,
) -> list[tuple[RootSection, list[DocsEntry]]]:
    buckets: dict[RootSection, list[DocsEntry]] = {
        "system": [],
        "developer": [],
        "community": [],
    }
    for entry in _root_entries_for_index(entries, actor_permission):
        buckets[_classify_root_section(entry)].append(entry)
    return [
        (section, buckets[section])
        for section in ("system", "developer", "community")
        if buckets[section]
    ]


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
    grouped_sections = _build_index_sections(entries, actor_permission)
    if not grouped_sections:
        return text_message(tr(locale, "help.index.empty"))

    dashboard_bytes = render_help_dashboard(
        tuple(
            HelpDashboardSection(
                kind=section,
                title=_section_title(section, locale),
                nodes=tuple(entry.node for entry in section_entries),
                accent=SECTION_STYLES[section].accent,
                panel_bg=SECTION_STYLES[section].panel_bg,
                panel_soft_bg=SECTION_STYLES[section].panel_soft_bg,
                text=SECTION_STYLES[section].text,
                hint=SECTION_STYLES[section].hint,
                command_bg=SECTION_STYLES[section].command_bg,
                command_text=SECTION_STYLES[section].command_text,
                marker=SECTION_STYLES[section].marker,
            )
            for section, section_entries in grouped_sections
        ),
        locale=locale,
        actor_permission=actor_permission,
    )
    lines = [
        tr(locale, "help.dashboard.title"),
        "----------",
        tr(locale, "help.dashboard.lead.line1"),
        tr(locale, "help.dashboard.lead.line2"),
        "----------",
    ]
    for section, section_entries in grouped_sections:
        lines.append(_section_title(section, locale))
        for entry in section_entries:
            lines.append(node_help_command(entry.node))
        lines.append("----------")
    return _compose_help_reply(dashboard_bytes, "\n".join(lines).strip())


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
        lines.append(f"{entry.display_name} ({entry.node.slug})")
    return text_message("\n".join(lines))


def _build_permission_denied_message(entry: DocsEntry, locale: LocaleCode) -> Message:
    return text_message(
        tr(
            locale,
            "help.query.permission_denied",
            name=entry.display_name,
            permission=entry.permission.label,
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


def _compose_help_reply(image_bytes: bytes, text: str) -> Message:
    message = text_message(f"{text.strip()}")
    message += image_message(image_bytes)
    return message


async def _resolve_docs_message(
    entry: DocsEntry,
    locale: LocaleCode,
    *,
    feature_query: str | None = None,
    actor_permission: Permission = Permission.NORMAL,
    all_entries: list[DocsEntry] | None = None,
) -> Message:
    tree = build_doc_tree([item.node for item in (all_entries or [entry])])
    children = tuple(
        child
        for child in tree.children_of(entry.node.slug)
        if can_view_node(child, actor_permission)
    )
    entry_shape = resolve_help_entry_shape(
        entry.node,
        actor_permission=actor_permission,
        children=children,
    )
    if feature_query:
        visible_features = tuple(
            feature
            for feature in entry.node.features
            if feature.permission == Permission.NONE
            or actor_permission.has(feature.permission)
        )
        match = match_feature(visible_features, feature_query)
        if match.status == "matched" and match.feature is not None:
            image_bytes = render_feature_deep_dive(
                entry.node,
                match.feature,
                locale=locale,
            )
            return _compose_help_reply(
                image_bytes,
                build_feature_copy_text(
                    entry.node,
                    match.feature,
                    locale=locale,
                ),
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
                features = filter_features_by_permission(
                    child_entry.node.features,
                    actor_permission=actor_permission,
                )
                child_shape = resolve_help_entry_shape(
                    child_entry.node,
                    actor_permission=actor_permission,
                    children=tree.children_of(child_entry.node.slug),
                )
                if child_shape == "static_entry":
                    image_bytes = render_static_entry(
                        child_entry.node,
                        locale=locale,
                        actor_permission=actor_permission,
                    )
                    return _compose_help_reply(
                        image_bytes,
                        build_static_entry_copy_text(
                            child_entry.node,
                            locale=locale,
                        ),
                    )
                image_bytes = render_plugin_guide(
                    child_entry.node,
                    locale=locale,
                    actor_permission=actor_permission,
                )
                return _compose_help_reply(
                    image_bytes,
                    build_plugin_guide_copy_text(
                        child_entry.node,
                        features=features,
                        locale=locale,
                    ),
                )
        if match.status == "ambiguous":
            return text_message(
                "\n".join(
                    [
                        tr(
                            locale,
                            "help.query.feature_ambiguous.title",
                            query=feature_query,
                        ),
                        tr(locale, "help.query.feature_ambiguous.hint"),
                        "",
                        *(
                            f"- {feature.title} ({feature.slug})"
                            for feature in match.candidates
                        ),
                    ]
                ).strip()
            )
        return text_message(tr(locale, "help.query.not_found", query=feature_query))
    features = filter_features_by_permission(
        entry.node.features,
        actor_permission=actor_permission,
    )
    if entry_shape == "static_entry":
        image_bytes = render_static_entry(
            entry.node,
            locale=locale,
            actor_permission=actor_permission,
        )
        return _compose_help_reply(
            image_bytes,
            build_static_entry_copy_text(
                entry.node,
                locale=locale,
            ),
        )
    if entry_shape == "overview_group":
        image_bytes = render_plugin_guide(
            entry.node,
            locale=locale,
            actor_permission=actor_permission,
        )
        overview_text = str(
            render_doc_node_overview(
                entry.node,
                locale=locale,
                include_demo=False,
                actor_permission=actor_permission,
                children=children,
            )
        )
        return _compose_help_reply(image_bytes, overview_text)
    if entry_shape == "simple_leaf" and features:
        feature = features[0]
        image_bytes = render_feature_deep_dive(
            entry.node,
            feature,
            locale=locale,
            actor_permission=actor_permission,
        )
        return _compose_help_reply(
            image_bytes,
            build_simple_leaf_copy_text(
                entry.node,
                feature,
                locale=locale,
            ),
        )
    image_bytes = render_plugin_guide(
        entry.node,
        locale=locale,
        actor_permission=actor_permission,
    )
    return _compose_help_reply(
        image_bytes,
        build_plugin_guide_copy_text(
            entry.node,
            features=features,
            locale=locale,
        ),
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
            await matcher.finish(
                _build_permission_denied_message(denied_match.entry, locale)
            )
        await matcher.finish(
            text_message(tr(locale, "help.query.not_found", query=query))
        )

    if match_result.status == "ambiguous":
        await matcher.finish(
            _build_ambiguous_message(query, match_result.candidates or [], locale)
        )

    assert match_result.entry is not None
    if not _can_view_entry(match_result.entry, actor_permission):
        await matcher.finish(
            _build_permission_denied_message(match_result.entry, locale)
        )

    docs_message = await _resolve_docs_message(
        match_result.entry,
        locale,
        feature_query=feature_query,
        actor_permission=actor_permission,
        all_entries=entries,
    )
    await matcher.finish(docs_message)
