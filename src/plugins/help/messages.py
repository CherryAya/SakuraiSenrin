"""help 消息构造：索引页、歧义提示、权限拒绝与子节点解析。"""

from __future__ import annotations

from src.database.core.consts import Permission
from src.lib.i18n.catalog import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import (
    DeliveryPlan,
    MessagePlanEntry,
    MessagePlanInput,
    append_image_plan_entry,
    build_image_plan_entry,
    build_text_plan_entry,
)
from src.lib.plugin_docs import (
    build_doc_tree,
    build_help_home_sections,
    build_help_home_text,
    can_view_node,
    match_doc_node,
    render_help_dashboard,
)

from .models import (
    DOCS_SOURCE,
    HELP_FORWARD_FALLBACK_NICKNAME,
    DocsEntry,
)


def _build_index_message(
    entries: list[DocsEntry],
    locale: LocaleCode,
    actor_permission: Permission = Permission.NORMAL,
) -> MessagePlanInput:
    sections = build_help_home_sections(
        [entry.node for entry in entries],
        locale=locale,
        actor_permission=actor_permission,
    )
    if not sections:
        return build_text_plan_entry(tr(locale, "help.index.empty"))

    dashboard_bytes = render_help_dashboard(
        sections,
        locale=locale,
        actor_permission=actor_permission,
        source_path=DOCS_SOURCE,
    )
    return _compose_help_reply(
        dashboard_bytes,
        build_help_home_text(sections, locale=locale),
    )


def _single_message_plan(message: MessagePlanInput) -> DeliveryPlan:
    return DeliveryPlan(
        messages=(message,),
        source_kind="help",
        fallback_nickname=HELP_FORWARD_FALLBACK_NICKNAME,
    )


def _build_ambiguous_message(
    query: str,
    candidates: list[DocsEntry],
    locale: LocaleCode,
) -> MessagePlanEntry:
    lines = [
        tr(locale, "help.query.ambiguous.title", query=query),
        tr(locale, "help.query.ambiguous.hint"),
        "",
        tr(locale, "help.query.ambiguous.candidates"),
    ]
    for entry in candidates:
        lines.append(f"{entry.display_name} ({entry.node.slug})")
    return build_text_plan_entry("\n".join(lines))


def _build_permission_denied_message(
    entry: DocsEntry,
    locale: LocaleCode,
) -> MessagePlanEntry:
    return build_text_plan_entry(
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


def _resolve_child_entry(
    parent_entry: DocsEntry,
    query: str,
    *,
    actor_permission: Permission,
    all_entries: list[DocsEntry],
) -> tuple[DocsEntry | None, str | None]:
    tree = build_doc_tree([item.node for item in all_entries])
    children = tuple(
        child
        for child in tree.children_of(parent_entry.node.slug)
        if can_view_node(child, actor_permission)
    )
    normalized = query.strip()
    if not normalized or not children:
        return None, None

    if " " in normalized:
        child_query, feature_query = normalized.split(maxsplit=1)
    else:
        child_query, feature_query = normalized, None
    child_query = child_query.strip()
    feature_query = feature_query.strip() if feature_query else None

    direct_child = next(
        (
            item
            for item in all_entries
            if item.node.parent_slug == parent_entry.node.slug
            and (
                item.node.slug == child_query
                or item.node.slug.endswith(f".{child_query}")
                or child_query in item.node.search_tokens
            )
            and can_view_node(item.node, actor_permission)
        ),
        None,
    )
    if direct_child is not None:
        return direct_child, feature_query

    child_match = match_doc_node(children, child_query)
    if child_match.status != "matched" or child_match.node is None:
        return None, None
    child_entry = next(
        (
            item
            for item in all_entries
            if item.node.slug == child_match.node.slug
            and can_view_node(item.node, actor_permission)
        ),
        None,
    )
    return child_entry, feature_query


def _compose_help_reply(image_bytes: bytes, text: str) -> MessagePlanEntry:
    text_value = text.strip()
    if not text_value:
        return build_image_plan_entry(image_bytes)
    return append_image_plan_entry(build_text_plan_entry(text_value), image_bytes)


__all__ = [
    "_build_ambiguous_message",
    "_build_index_message",
    "_build_permission_denied_message",
    "_compose_help_reply",
    "_resolve_child_entry",
    "_single_message_plan",
    "_split_query",
]
