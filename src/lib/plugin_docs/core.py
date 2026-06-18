"""Core plugin docs data structures and parsing helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import os
import re
from typing import Any, Literal, TypedDict, cast

from markdown_it.token import Token
from nonebot.plugin import PluginMetadata

from src.database.core.consts import Permission
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from src.lib.consts import TriggerType

DEFAULT_HELP_CATEGORY = "general"


type DocNodeKind = Literal["plugin", "overview", "internal"]


class DocSourceMeta(TypedDict):
    kind: Literal["readme"]
    readme_path: str


class DocTreeMeta(TypedDict):
    slug: str
    parent_slug: str | None
    category: str
    order: int


class DocVisibilityMeta(TypedDict):
    visible: bool
    hidden: bool
    internal: bool


class DocsMeta(TypedDict):
    kind: DocNodeKind
    source: DocSourceMeta
    tree: DocTreeMeta
    visibility: DocVisibilityMeta
    permission: Permission | int | str
    aliases: tuple[str, ...]


type DocsMetaValue = DocsMeta | Sequence[DocsMeta]


@dataclass(slots=True, frozen=True)
class DocsRenderContext:
    locale: LocaleCode
    feature_query: str | None = None
    include_demo: bool = True
    view: str = "text"
    actor_permission: Permission = Permission.NORMAL


@dataclass(slots=True, frozen=True)
class DocsDemoTurn:
    speaker: Literal["USER", "BOT", "SYSTEM"]
    text: str


@dataclass(slots=True, frozen=True)
class MarkdownSection:
    title: str
    heading: Token
    tokens: tuple[Token, ...]


@dataclass(slots=True, frozen=True)
class FeatureDoc:
    slug: str
    title: str
    summary: str
    aliases: tuple[str, ...]
    trigger: str
    permission: Permission
    demo_filename: str
    overview: str
    preconditions: str
    flow_notes: str
    failures: str
    demo_turns: tuple[DocsDemoTurn, ...]
    hero: bool = False
    priority: int = 1000
    advanced: bool = False

    @property
    def search_tokens(self) -> set[str]:
        return {
            self.slug.lower(),
            self.title.lower(),
            *(alias.lower() for alias in self.aliases),
        }


@dataclass(slots=True, frozen=True)
class VirtualFeatureDocSpec:
    slug: str
    title: str
    summary: str
    trigger: str
    overview: str
    preconditions: str
    failures: str
    demo_turns: tuple[DocsDemoTurn, ...]
    aliases: tuple[str, ...] = ()
    permission: Permission = Permission.NORMAL
    hero: bool = False
    priority: int = 1000
    advanced: bool = False
    demo_filename: str = ""


@dataclass(slots=True, frozen=True)
class PluginDocBundle:
    title: str
    description: str
    summary: str
    trigger: str
    permission: str
    author: str
    version: str
    impression_color: str
    index: tuple[FeatureDoc, ...]
    source_path: Path


@dataclass(slots=True, frozen=True)
class VirtualPluginDocSpec:
    slug: str
    title: str
    summary: str
    description: str
    trigger: str
    author: str
    version: str
    impression_color: str
    features: tuple[VirtualFeatureDocSpec, ...]
    aliases: tuple[str, ...] = ()
    permission: Permission = Permission.NORMAL
    category: str = DEFAULT_HELP_CATEGORY
    order: int = 100
    visible: bool = True
    hidden: bool = False
    internal: bool = False
    kind: DocNodeKind = "plugin"
    parent_slug: str | None = None
    plugin_name: str = ""
    module_name: str = ""
    origin_plugin_slug: str = ""


@dataclass(slots=True, frozen=True)
class FeatureMatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    feature: FeatureDoc | None = None
    candidates: tuple[FeatureDoc, ...] = ()


@dataclass(slots=True, frozen=True)
class DocNode:
    kind: DocNodeKind
    slug: str
    parent_slug: str | None
    category: str
    order: int
    visible: bool
    hidden: bool
    internal: bool
    permission: Permission
    title: str
    summary: str
    description: str
    aliases: tuple[str, ...]
    source_path: Path
    bundle: PluginDocBundle
    module_name: str = ""
    plugin_name: str = ""

    @property
    def features(self) -> tuple[FeatureDoc, ...]:
        return self.bundle.index

    @property
    def search_tokens(self) -> set[str]:
        tokens = {
            self.slug.lower(),
            self.title.lower(),
            *(alias.lower() for alias in self.aliases),
        }
        leaf = self.slug.split(".")[-1].strip()
        if leaf:
            tokens.add(leaf.lower())
        if self.plugin_name:
            tokens.add(self.plugin_name.lower())
        if self.module_name:
            module = self.module_name.lower()
            tokens.add(module)
            tokens.add(module.split(".")[-1])
        return {token for token in tokens if token}


@dataclass(slots=True, frozen=True)
class HelpDashboardSection:
    kind: str
    title: str
    nodes: tuple[DocNode, ...]
    accent: str | None = None
    panel_bg: str | None = None
    panel_soft_bg: str | None = None
    text: str | None = None
    hint: str | None = None
    command_bg: str | None = None
    command_text: str | None = None
    marker: str = "•"
    column: int = 0


@dataclass(slots=True, frozen=True)
class DocTree:
    nodes: tuple[DocNode, ...]
    children_by_slug: dict[str, tuple[DocNode, ...]]

    def children_of(self, slug: str) -> tuple[DocNode, ...]:
        return self.children_by_slug.get(slug, ())

    def roots(self) -> tuple[DocNode, ...]:
        return tuple(node for node in self.nodes if node.parent_slug is None)

    def root_of(self, slug: str) -> DocNode | None:
        mapping = {node.slug: node for node in self.nodes}
        current = mapping.get(slug)
        while current is not None and current.parent_slug is not None:
            current = mapping.get(current.parent_slug)
        return current


@dataclass(slots=True, frozen=True)
class NodeMatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    node: DocNode | None = None
    candidates: tuple[DocNode, ...] = ()


def _support_note(locale: LocaleCode) -> str:
    main_group_id = _resolve_main_group_id()
    return (
        tr(
            locale,
            "help.index.notice.item2",
            main_group_id=main_group_id,
        )
        .removeprefix("2. ")
        .strip()
    )


def _resolve_main_group_id() -> str:
    env_main_group_id = os.getenv("MAIN_GROUP_ID", "").strip()
    try:
        config_module = import_module("src.config")
    except Exception:
        return env_main_group_id or "未配置"
    runtime_config = getattr(config_module, "config", None)
    config_main_group_id = str(getattr(runtime_config, "MAIN_GROUP_ID", "")).strip()
    return config_main_group_id or env_main_group_id or "未配置"


def _derive_tree_identity_from_source(source_path: Path) -> tuple[str, str | None]:
    if not source_path:
        return "", None
    src_root = next(
        (parent for parent in source_path.parents if parent.name == "src"), None
    )
    if src_root is None:
        return source_path.stem.lower(), None
    try:
        rel_path = source_path.relative_to(src_root)
    except ValueError:
        return source_path.stem.lower(), None

    parts = rel_path.parts
    if not parts:
        return source_path.stem.lower(), None
    namespace = parts[0]
    if namespace == "plugins" and len(parts) >= 4:
        plugin_name = parts[1]
        if parts[2] != "docs":
            return plugin_name, None
        tail = parts[3:-1]
        if not tail:
            return plugin_name, None
        slug = ".".join((plugin_name, *tail))
        return slug, plugin_name
    if namespace == "hooks":
        tail = parts[2:-1] if len(parts) >= 3 and parts[1] == "docs" else parts[1:-1]
        if not tail:
            tail = (parts[-2],) if len(parts) >= 2 else ("hook",)
        return ".".join(("hook", *tail)), None
    return source_path.stem.lower(), None


def create_docs_meta(
    provider: Any | None = None,
    *,
    visible: bool,
    category: str,
    order: int,
    source: str | Path | None = None,
    hidden: bool = False,
    slug: str | None = None,
    parent_slug: str | None = None,
    kind: DocNodeKind = "plugin",
    internal: bool = False,
    aliases: Sequence[str] = (),
) -> DocsMeta:
    del provider
    source_path = Path(source).resolve() if source is not None else Path()
    derived_slug, derived_parent = _derive_tree_identity_from_source(source_path)
    return {
        "kind": kind,
        "source": {
            "kind": "readme",
            "readme_path": str(source_path),
        },
        "tree": {
            "slug": slug or derived_slug,
            "parent_slug": parent_slug if parent_slug is not None else derived_parent,
            "category": category or DEFAULT_HELP_CATEGORY,
            "order": order,
        },
        "visibility": {
            "visible": visible,
            "hidden": hidden,
            "internal": internal,
        },
        "permission": Permission.NORMAL,
        "aliases": tuple(alias.strip() for alias in aliases if alias.strip()),
    }


def read_docs_meta(metadata: PluginMetadata) -> DocsMeta | None:
    metas = read_docs_metas(metadata)
    return metas[0] if metas else None


def read_docs_metas(metadata: PluginMetadata) -> tuple[DocsMeta, ...]:
    raw = metadata.extra.get("docs")
    if isinstance(raw, dict):
        parsed = _normalize_docs_meta(
            raw,
            default_permission=metadata.extra.get("permission", Permission.NORMAL),
        )
        return (parsed,) if parsed is not None else ()
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        metas: list[DocsMeta] = []
        default_permission = metadata.extra.get("permission", Permission.NORMAL)
        for item in raw:
            if not isinstance(item, dict):
                continue
            parsed = _normalize_docs_meta(item, default_permission=default_permission)
            if parsed is not None:
                metas.append(parsed)
        return tuple(metas)
    return ()


def _normalize_docs_meta(
    raw: dict[str, Any],
    *,
    default_permission: Permission | int | str,
) -> DocsMeta | None:
    source = raw.get("source")
    tree = raw.get("tree")
    visibility = raw.get("visibility")
    if not isinstance(source, dict) or not isinstance(tree, dict):
        return None
    if not isinstance(visibility, dict):
        return None
    readme_path = str(source.get("readme_path", "")).strip()
    slug = str(tree.get("slug", "")).strip()
    category = str(tree.get("category", DEFAULT_HELP_CATEGORY)).strip()
    if not readme_path or not slug:
        return None
    aliases = raw.get("aliases", ())
    normalized_aliases = tuple(
        alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()
    )
    permission = raw.get("permission", default_permission)
    return {
        "kind": cast(DocNodeKind, raw.get("kind", "plugin")),
        "source": {
            "kind": "readme",
            "readme_path": readme_path,
        },
        "tree": {
            "slug": slug,
            "parent_slug": (
                str(tree["parent_slug"]).strip()
                if tree.get("parent_slug") is not None
                else None
            ),
            "category": category or DEFAULT_HELP_CATEGORY,
            "order": int(tree.get("order", 100)),
        },
        "visibility": {
            "visible": bool(visibility.get("visible", True)),
            "hidden": bool(visibility.get("hidden", False)),
            "internal": bool(visibility.get("internal", False)),
        },
        "permission": permission,
        "aliases": normalized_aliases,
    }


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
    if len(exact) > 1:
        return NodeMatchResult(status="ambiguous", candidates=_unique_nodes(exact))
    if len(fuzzy) == 1:
        return NodeMatchResult(status="matched", node=fuzzy[0])
    if len(fuzzy) > 1:
        return NodeMatchResult(status="ambiguous", candidates=_unique_nodes(fuzzy))
    return NodeMatchResult(status="not_found")


def _unique_nodes(nodes: Sequence[DocNode]) -> tuple[DocNode, ...]:
    seen: set[str] = set()
    ordered: list[DocNode] = []
    for node in nodes:
        if node.slug in seen:
            continue
        seen.add(node.slug)
        ordered.append(node)
    return tuple(ordered)
