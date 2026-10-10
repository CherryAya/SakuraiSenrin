"""help 条目匹配：精确/模糊/歧义判定与可见性过滤。"""

from __future__ import annotations

from src.database.core.consts import Permission
from src.lib.plugin_docs import can_view_node, match_doc_node

from .models import DocsEntry, MatchResult


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


__all__ = [
    "_can_view_entry",
    "_filter_authorized_entries",
    "_is_exact_entry_match",
    "_match_entry",
    "_match_exact_entry",
    "_unique_entries",
]
