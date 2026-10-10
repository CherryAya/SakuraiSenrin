"""help 条目索引：从已加载插件收集可展示的帮助节点。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

import nonebot
from nonebot.plugin import Plugin, PluginMetadata

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.demo_theme import DEFAULT_IMPRESSION_COLOR, normalize_hex_color
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import (
    VirtualPluginDocSpec,
    create_docs_meta,
    load_doc_node,
    load_virtual_doc_node,
    read_docs_metas,
)

from .models import DocsEntry


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
        i18n_map = cast("dict[object, object]", raw_i18n)
        key_name = f"{field}_key"
        maybe_key = i18n_map.get(key_name)
        if isinstance(maybe_key, str):
            return tr(locale, cast(MessageKey, maybe_key))
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
    provider: Callable[[LocaleCode], object] = raw
    specs = provider(locale)
    if isinstance(specs, list):
        spec_values = cast("list[object]", specs)
    elif isinstance(specs, tuple):
        spec_values = list(cast("tuple[object, ...]", specs))
    else:
        return ()
    return tuple(spec for spec in spec_values if isinstance(spec, VirtualPluginDocSpec))


__all__ = [
    "_is_project_plugin",
    "_iter_docs_entries",
    "_read_plugin_permission",
]
