"""Doc tree and feature matching helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from src.database.core.consts import Permission

from .models import (
    DocNode,
    DocTree,
    FeatureDoc,
    FeatureMatchResult,
    NodeMatchResult,
    PluginDocBundle,
    VirtualPluginDocSpec,
)


def build_doc_tree(nodes: Sequence[DocNode]) -> DocTree:
    ordered = tuple(
        sorted(
            nodes,
            key=lambda node: (
                node.category,
                node.parent_slug or "",
                node.order,
                node.title.lower(),
            ),
        )
    )
    child_map: defaultdict[str, list[DocNode]] = defaultdict(list)
    for node in ordered:
        if node.parent_slug is not None:
            child_map[node.parent_slug].append(node)
    normalized = {
        slug: tuple(
            sorted(children, key=lambda child: (child.order, child.title.lower()))
        )
        for slug, children in child_map.items()
    }
    return DocTree(nodes=ordered, children_by_slug=normalized)


def match_doc_node(nodes: Sequence[DocNode], query: str) -> NodeMatchResult:
    normalized = query.strip().lower()
    if not normalized:
        return NodeMatchResult(status="not_found")

    exact: list[DocNode] = []
    fuzzy: list[DocNode] = []
    for node in nodes:
        tokens = node.search_tokens
        if normalized in tokens:
            exact.append(node)
            continue
        if any(normalized in token for token in tokens):
            fuzzy.append(node)

    if len(exact) == 1:
        return NodeMatchResult(status="matched", node=exact[0])
    if exact:
        return NodeMatchResult(status="ambiguous", candidates=unique_nodes(exact))
    if len(fuzzy) == 1:
        return NodeMatchResult(status="matched", node=fuzzy[0])
    if fuzzy:
        return NodeMatchResult(status="ambiguous", candidates=unique_nodes(fuzzy))
    return NodeMatchResult(status="not_found")


def build_virtual_plugin_doc_bundle(spec: VirtualPluginDocSpec) -> PluginDocBundle:
    features = tuple(
        FeatureDoc(
            slug=feature.slug,
            title=feature.title,
            summary=feature.summary,
            aliases=feature.aliases,
            trigger=feature.trigger,
            permission=feature.permission,
            demo_filename=feature.demo_filename,
            overview=feature.overview,
            preconditions=feature.preconditions,
            flow_notes="",
            failures=feature.failures,
            demo_turns=feature.demo_turns,
            hero=feature.hero,
            priority=feature.priority,
            advanced=feature.advanced,
        )
        for feature in spec.features
    )
    return PluginDocBundle(
        title=spec.title,
        description=spec.description,
        summary=spec.summary,
        trigger=spec.trigger,
        help_query="",
        permission=spec.permission.label,
        author=spec.author,
        version=spec.version,
        impression_color=spec.impression_color,
        index=features,
        source_path=Path(f"<virtual:{spec.slug}>"),
    )


def load_virtual_doc_node(spec: VirtualPluginDocSpec) -> DocNode:
    bundle = build_virtual_plugin_doc_bundle(spec)
    return DocNode(
        kind=spec.kind,
        slug=spec.slug,
        parent_slug=spec.parent_slug,
        category=spec.category,
        order=spec.order,
        visible=spec.visible,
        hidden=spec.hidden,
        internal=spec.internal,
        permission=spec.permission,
        title=spec.title,
        summary=spec.summary,
        description=spec.description,
        help_query="",
        aliases=spec.aliases,
        source_path=bundle.source_path,
        bundle=bundle,
        module_name=spec.module_name,
        plugin_name=spec.plugin_name,
    )


def can_view_node(node: DocNode, actor_permission: Permission) -> bool:
    return permission_allows(actor_permission, node.permission)


def filter_features_by_permission(
    features: Sequence[FeatureDoc],
    actor_permission: Permission,
) -> tuple[FeatureDoc, ...]:
    return tuple(
        feature
        for feature in features
        if permission_allows(actor_permission, feature.permission)
    )


def rank_features_for_disclosure(
    features: Sequence[FeatureDoc],
) -> tuple[FeatureDoc, ...]:
    indexed = tuple(enumerate(features))
    ranked = sorted(
        indexed,
        key=lambda item: (
            not item[1].hero,
            item[1].advanced and not item[1].hero,
            item[1].priority,
            item[0],
        ),
    )
    return tuple(feature for _, feature in ranked)


def split_features_for_disclosure(
    features: Sequence[FeatureDoc],
    *,
    actor_permission: Permission,
    hero_limit: int = 3,
) -> tuple[tuple[FeatureDoc, ...], tuple[FeatureDoc, ...]]:
    visible = filter_features_by_permission(features, actor_permission)
    if not visible:
        return (), ()

    ranked = rank_features_for_disclosure(visible)
    hero_features: list[FeatureDoc] = []
    remainder: list[FeatureDoc] = []

    for feature in ranked:
        if len(hero_features) < hero_limit and (
            feature.hero or not feature.advanced or not hero_features
        ):
            hero_features.append(feature)
            continue
        remainder.append(feature)

    if not hero_features and ranked:
        hero_features.append(ranked[0])
        remainder = list(ranked[1:])

    seen = {feature.slug for feature in hero_features}
    advanced = tuple(feature for feature in ranked if feature.slug not in seen)
    return tuple(hero_features), advanced


def permission_allows(
    actor_permission: Permission,
    required_permission: Permission,
) -> bool:
    if required_permission == Permission.NONE:
        return True
    permission_levels = {
        Permission.NONE: 0,
        Permission.NORMAL: 1,
        Permission.GROUP_ADMIN: 2,
        Permission.GROUP_OWNER: 3,
        Permission.SUPERUSER: 4,
    }
    actor_level = permission_levels.get(actor_permission, 0)
    required_level = permission_levels.get(required_permission, 0)
    if actor_level and required_level:
        return actor_level >= required_level
    return actor_permission.has(required_permission)


def match_feature(
    features: Sequence[FeatureDoc],
    query: str,
) -> FeatureMatchResult:
    normalized = query.strip().lower()
    if not normalized:
        return FeatureMatchResult(status="not_found")

    exact_primary: list[FeatureDoc] = []
    exact_alias: list[FeatureDoc] = []
    fuzzy: list[FeatureDoc] = []
    for feature in features:
        if normalized == feature.slug.lower() or normalized == feature.title.lower():
            exact_primary.append(feature)
            continue
        if normalized in {alias.lower() for alias in feature.aliases}:
            exact_alias.append(feature)
            continue
        if any(normalized in token for token in feature.search_tokens):
            fuzzy.append(feature)

    if len(exact_primary) == 1:
        return FeatureMatchResult(status="matched", feature=exact_primary[0])
    if exact_primary:
        return FeatureMatchResult(status="ambiguous", candidates=tuple(exact_primary))
    if len(exact_alias) == 1:
        return FeatureMatchResult(status="matched", feature=exact_alias[0])
    if exact_alias:
        return FeatureMatchResult(status="ambiguous", candidates=tuple(exact_alias))
    if len(fuzzy) == 1:
        return FeatureMatchResult(status="matched", feature=fuzzy[0])
    if fuzzy:
        unique: dict[str, FeatureDoc] = {feature.slug: feature for feature in fuzzy}
        return FeatureMatchResult(status="ambiguous", candidates=tuple(unique.values()))
    return FeatureMatchResult(status="not_found")


def unique_nodes(nodes: Sequence[DocNode]) -> tuple[DocNode, ...]:
    unique: dict[str, DocNode] = {}
    for node in nodes:
        unique[node.slug] = node
    return tuple(unique.values())
