"""Project-wide plugin docs engine, README parser, and demo rendering helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import import_module
from io import BytesIO
from math import ceil
import os
from pathlib import Path
import re
from typing import Any, ClassVar, Literal, TypedDict, cast

from markdown_it import MarkdownIt
from markdown_it.token import Token
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.plugin import PluginMetadata
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pil_utils import BuildImage
from pil_utils.text2image import Text2Image

from src.database.core.consts import Permission
from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.demo_theme import (
    DEFAULT_DEMO_THEME,
    DEFAULT_IMPRESSION_COLOR,
    SENRIN_V3_THEME,
    get_demo_theme,
    normalize_hex_color,
)
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time

from .consts import TriggerType

DEMO_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEMO_AVATAR_PATH = DEMO_ASSETS_DIR / "senrin-demo-avatar.png"
DEMO_STANDEE_PATH = DEMO_ASSETS_DIR / "senrin-demo-standee.png"
DEFAULT_HELP_CATEGORY = "general"

type DocsResult = Message | Awaitable[Message] | str | Awaitable[str]
type DocsProvider = Callable[..., DocsResult]
type DocNodeKind = Literal["plugin", "overview", "internal"]
type DocRenderView = Literal["text", "index", "plugin", "feature"]
type CommandLineKind = Literal["root", "flag", "continuation", "alternative"]
type HelpEntryShape = Literal["simple_leaf", "plugin_guide", "overview_group"]


@dataclass(slots=True, frozen=True)
class DocsRenderContext:
    locale: LocaleCode
    feature_query: str | None = None
    include_demo: bool = True
    view: DocRenderView = "text"
    actor_permission: Permission = Permission.NORMAL


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
class DocsDemoTurn:
    speaker: Literal["USER", "BOT", "SYSTEM"]
    text: str


@dataclass(slots=True, frozen=True)
class InlineTextSpan:
    text: str
    code: bool = False
    fill: str | None = None


@dataclass(slots=True, frozen=True)
class CommandLayoutLine:
    segments: tuple[InlineTextSpan, ...]
    indent_level: int
    kind: CommandLineKind


@dataclass(slots=True, frozen=True)
class CommandLayout:
    lines: tuple[CommandLayoutLine, ...]
    line_height: int
    indent_px: int
    max_line_width: int
    total_height: int
    has_guide: bool


@dataclass(slots=True, frozen=True)
class CommandPalette:
    root: str
    text: str
    param: str
    flag: str


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


def create_docs_meta(
    provider: DocsProvider | None = None,
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


def build_static_docs(
    *,
    name: str | None = None,
    description: str | None = None,
    content: str | None = None,
    name_key: MessageKey | None = None,
    description_key: MessageKey | None = None,
    content_key: MessageKey | None = None,
    trigger: TriggerType,
    permission: Permission,
    locale: LocaleCode = "zh-CN",
) -> Message:
    body = (
        tr(locale, content_key).strip()
        if content_key is not None
        else (content or "").strip()
    ) or tr(locale, "docs.default.empty")
    desc = (
        tr(locale, description_key).strip()
        if description_key is not None
        else (description or "").strip()
    ) or tr(locale, "docs.default.no_description")
    title = tr(locale, name_key) if name_key is not None else (name or "")
    return Message(
        (
            f"===== {title} =====\n"
            f"{tr(locale, 'docs.default.trigger')}: {trigger}\n"
            f"{tr(locale, 'docs.default.permission')}: {permission}\n\n"
            f"{desc}\n\n"
            f"{tr(locale, 'docs.default.usage')}:\n"
            f"{body}"
        ).strip()
    )


def build_readme_docs(
    *,
    source: str | Path,
    name: str,
    description: str,
    trigger: TriggerType,
    permission: Permission,
    ctx: DocsRenderContext | None = None,
) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    actor_permission = ctx.actor_permission if ctx is not None else Permission.NORMAL
    node = load_doc_node(
        source=source,
        default_name=name,
        default_description=description,
        trigger=trigger,
        permission=permission,
    )
    if ctx is not None and ctx.feature_query:
        visible_features = filter_features_by_permission(
            node.features, actor_permission
        )
        match = match_feature(visible_features, ctx.feature_query)
        if match.status == "matched" and match.feature is not None:
            return render_doc_feature(
                node,
                match.feature,
                locale=locale,
                include_demo=ctx.include_demo,
            )
        if match.status == "ambiguous":
            return Message(
                "\n".join(
                    [
                        tr(
                            locale,
                            "help.query.feature_ambiguous.title",
                            query=ctx.feature_query,
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
        return Message(
            tr(
                locale,
                "docs.feature.not_found",
                query=ctx.feature_query,
            ).strip()
        )
    include_demo = (
        ctx.include_demo if ctx is not None and ctx.view == "plugin" else False
    )
    return render_doc_node_overview(
        node,
        locale=locale,
        include_demo=include_demo,
        actor_permission=actor_permission,
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
    if len(exact) > 1:
        return NodeMatchResult(status="ambiguous", candidates=_unique_nodes(exact))
    if len(fuzzy) == 1:
        return NodeMatchResult(status="matched", node=fuzzy[0])
    if len(fuzzy) > 1:
        return NodeMatchResult(status="ambiguous", candidates=_unique_nodes(fuzzy))
    return NodeMatchResult(status="not_found")


def load_doc_node(
    *,
    source: str | Path,
    default_name: str,
    default_description: str,
    trigger: TriggerType,
    permission: Permission,
    impression_color: str | None = None,
    docs_meta: DocsMeta | None = None,
    module_name: str = "",
    plugin_name: str = "",
) -> DocNode:
    source_path = Path(source).resolve()
    meta = docs_meta or create_docs_meta(
        visible=True,
        category=DEFAULT_HELP_CATEGORY,
        order=100,
        source=source_path,
    )
    bundle = load_plugin_doc_bundle(
        source=source_path,
        default_name=default_name,
        default_description=default_description,
        trigger=trigger,
        permission=permission,
        impression_color=impression_color,
    )
    slug = meta["tree"]["slug"]
    return DocNode(
        kind=meta["kind"],
        slug=slug,
        parent_slug=meta["tree"]["parent_slug"],
        category=meta["tree"]["category"],
        order=meta["tree"]["order"],
        visible=meta["visibility"]["visible"],
        hidden=meta["visibility"]["hidden"],
        internal=meta["visibility"]["internal"],
        permission=_coerce_permission(meta["permission"] or permission),
        title=bundle.title or default_name,
        summary=bundle.summary or default_description,
        description=default_description,
        aliases=meta["aliases"],
        source_path=source_path,
        bundle=bundle,
        module_name=module_name,
        plugin_name=plugin_name,
    )


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
    virtual_origin = spec.origin_plugin_slug or spec.plugin_name or "plugin"
    virtual_source = Path(f"/virtual/{virtual_origin}/{spec.slug}")
    return PluginDocBundle(
        title=spec.title,
        description=spec.description,
        summary=spec.summary,
        trigger=spec.trigger,
        permission=spec.permission.label,
        author=spec.author,
        version=spec.version,
        impression_color=normalize_hex_color(spec.impression_color),
        index=features,
        source_path=virtual_source,
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
        aliases=spec.aliases,
        source_path=bundle.source_path,
        bundle=bundle,
        module_name=spec.module_name,
        plugin_name=spec.plugin_name,
    )


def render_doc_node_overview(
    node: DocNode,
    *,
    locale: LocaleCode,
    include_demo: bool = False,
    actor_permission: Permission = Permission.NORMAL,
    children: Sequence[DocNode] = (),
) -> Message:
    lines = [f"📖 ===== {node.title} =====", ""]
    if node.summary:
        lines.extend([node.summary, ""])

    visible_children = tuple(
        child for child in children if can_view_node(child, actor_permission)
    )
    visible_features = filter_features_by_permission(node.features, actor_permission)

    if visible_children:
        lines.append(tr(locale, "docs.node.children"))
        for index, child in enumerate(visible_children, start=1):
            lines.append(f"{index}. {child.title}")
            lines.append(f"  #help {child.title}")
        lines.append("")
    elif visible_features:
        for index, feature in enumerate(visible_features, start=1):
            lines.append(f"{index}. {feature.title}")
            lines.extend(
                _format_feature_command_lines(
                    node.bundle,
                    feature,
                    node.title,
                    locale=locale,
                )
            )
            lines.append("")
    else:
        lines.extend([tr(locale, "docs.node.empty"), ""])

    lines.extend(
        [
            tr(locale, "docs.node.notice"),
            tr(locale, "docs.node.notice.item1"),
            f"2. {_support_note(locale)}",
        ]
    )
    message = Message("\n".join(lines).strip())
    if not include_demo:
        return message
    demo_bytes = load_representative_demo_bytes(
        bundle=node.bundle,
        actor_permission=actor_permission,
    )
    if demo_bytes is None:
        return message
    return message + MessageSegment.image(demo_bytes)


def resolve_help_entry_shape(
    node: DocNode,
    *,
    actor_permission: Permission = Permission.NORMAL,
    children: Sequence[DocNode] = (),
) -> HelpEntryShape:
    visible_children = tuple(
        child for child in children if can_view_node(child, actor_permission)
    )
    if node.kind == "overview" or visible_children:
        return "overview_group"

    visible_features = filter_features_by_permission(node.features, actor_permission)
    if len(visible_features) <= 1:
        return "simple_leaf"

    if len(visible_features) <= 2 and all(
        not feature.advanced for feature in visible_features
    ):
        return "simple_leaf"

    return "plugin_guide"


def render_doc_feature(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    include_demo: bool = True,
) -> Message:
    lines = [
        f"📖 ===== {node.title} / {feature.title} =====",
        "",
        tr(locale, "docs.feature.name", name=feature.title),
        "",
        tr(locale, "docs.feature.commands"),
        *_format_feature_command_lines(
            node.bundle,
            feature,
            node.title,
            locale=locale,
        ),
        "",
        tr(locale, "docs.node.notice"),
    ]
    for index, note in enumerate(
        _feature_notice_items(feature, locale=locale),
        start=1,
    ):
        lines.append(f"{index}. {note}")
    message = Message("\n".join(lines).strip())
    if not include_demo:
        return message
    demo_bytes = load_demo_bytes(node.bundle, feature)
    if demo_bytes is None:
        return message
    return message + MessageSegment.image(demo_bytes)


def can_view_node(node: DocNode, actor_permission: Permission) -> bool:
    return _permission_allows(actor_permission, node.permission)


def filter_features_by_permission(
    features: Sequence[FeatureDoc],
    actor_permission: Permission,
) -> tuple[FeatureDoc, ...]:
    return tuple(
        feature
        for feature in features
        if _permission_allows(actor_permission, feature.permission)
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


def _permission_allows(
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


def load_plugin_doc_bundle(
    *,
    source: str | Path,
    default_name: str,
    default_description: str,
    trigger: TriggerType,
    permission: Permission,
    impression_color: str | None = None,
) -> PluginDocBundle:
    source_path = Path(source).resolve()
    return _load_plugin_doc_bundle_cached(
        source_path,
        default_name,
        default_description,
        trigger,
        permission,
        normalize_hex_color(impression_color)
        if impression_color is not None
        else _resolve_doc_impression_color(source_path),
    )


@lru_cache(maxsize=256)
def _load_plugin_doc_bundle_cached(
    source_path: Path,
    default_name: str,
    default_description: str,
    trigger: TriggerType,
    permission: Permission,
    impression_color: str,
) -> PluginDocBundle:
    raw_text = source_path.read_text(encoding="utf-8")
    tokens = tuple(_markdown_parser().parse(raw_text))
    sections = {
        section.title: section
        for section in _extract_heading_sections(tokens, tag="h2")
    }
    title = _extract_title(tokens) or default_name
    summary = _render_markdown_blocks(
        sections.get("概览", _EMPTY_SECTION).tokens
    ).strip()
    meta = _parse_meta_block_tokens(sections.get("权限与触发", _EMPTY_SECTION).tokens)
    feature_index = _parse_feature_index_tokens(
        sections.get("子功能目录", _EMPTY_SECTION).tokens
    )
    details = _parse_feature_details_tokens(
        sections.get("子功能详情", _EMPTY_SECTION).tokens,
        source_path,
    )
    features = _merge_features(feature_index, details)
    author, version = _resolve_doc_signature(source_path)
    return PluginDocBundle(
        title=title,
        description=default_description,
        summary=summary or default_description,
        trigger=meta.get("触发方式", trigger.label),
        permission=meta.get("权限", permission.label),
        author=author,
        version=version,
        impression_color=impression_color,
        index=features,
        source_path=source_path,
    )


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
        slug = feature.slug.lower()
        title = feature.title.lower()
        aliases = tuple(alias.lower() for alias in feature.aliases)
        if normalized == slug or normalized == title:
            exact_primary.append(feature)
            continue
        if normalized in aliases:
            exact_alias.append(feature)
            continue
        if normalized in slug or normalized in title:
            fuzzy.append(feature)
            continue
        if any(normalized in alias for alias in aliases):
            fuzzy.append(feature)

    if len(exact_primary) == 1:
        return FeatureMatchResult(status="matched", feature=exact_primary[0])
    if len(exact_primary) > 1:
        return FeatureMatchResult(status="ambiguous", candidates=tuple(exact_primary))
    if len(exact_alias) == 1:
        return FeatureMatchResult(status="matched", feature=exact_alias[0])
    if len(exact_alias) > 1:
        return FeatureMatchResult(status="ambiguous", candidates=tuple(exact_alias))
    if len(fuzzy) == 1:
        return FeatureMatchResult(status="matched", feature=fuzzy[0])
    if len(fuzzy) > 1:
        unique = {feature.slug: feature for feature in fuzzy}
        return FeatureMatchResult(status="ambiguous", candidates=tuple(unique.values()))
    return FeatureMatchResult(status="not_found")


def _unique_nodes(nodes: Sequence[DocNode]) -> tuple[DocNode, ...]:
    unique: dict[str, DocNode] = {}
    for node in nodes:
        unique.setdefault(node.slug, node)
    return tuple(unique.values())


def _coerce_permission(value: Permission | int | str) -> Permission:
    if isinstance(value, Permission):
        return value
    if isinstance(value, str):
        try:
            return Permission[value]
        except KeyError:
            for permission in Permission:
                if permission.label == value:
                    return permission
            return Permission.NORMAL
    try:
        return Permission(value)
    except (TypeError, ValueError):
        return Permission.NORMAL


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


@lru_cache(maxsize=1)
def _markdown_parser() -> MarkdownIt:
    return MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": False,
            "breaks": False,
        },
    )


def _normalize_inline_text(value: str) -> str:
    text = "".join(span.text for span in split_inline_text_spans(value.strip()))
    return re.sub(r"\s+", " ", text).strip()


def split_inline_text_spans(text: str) -> tuple[InlineTextSpan, ...]:
    if not text:
        return ()

    spans: list[InlineTextSpan] = []
    for token in _parse_inline_tokens(text):
        if token.type == "text":
            _append_inline_text_span(spans, token.content, code=False)
            continue
        if token.type == "code_inline":
            _append_inline_text_span(spans, token.content, code=True)
            continue
        if token.type in {"softbreak", "hardbreak"}:
            _append_inline_text_span(spans, "\n", code=False)
            continue
        if token.type == "image":
            _append_inline_text_span(spans, token.content, code=False)
    return tuple(span for span in spans if span.text)


def build_command_layout(
    text: str,
    *,
    max_width: int,
    line_height: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> CommandLayout:
    inline_code_variants = _split_inline_code_command_variants(text)
    if inline_code_variants is not None:
        raw_lines: list[CommandLayoutLine] = []
        for index, group in enumerate(inline_code_variants):
            raw_lines.extend(
                _format_command_tokens(
                    group,
                    base_indent=0 if index == 0 else 1,
                    first_kind="root" if index == 0 else "alternative",
                    max_width=max_width,
                    indent_px=indent_px,
                    measure_text=measure_text,
                    palette=palette,
                )
            )
        max_line_width = max(
            (
                line.indent_level * indent_px
                + _command_segments_width(line.segments, measure_text)
                for line in raw_lines
            ),
            default=0,
        )
        has_guide = sum(1 for line in raw_lines if line.indent_level > 0) >= 2
        return CommandLayout(
            lines=tuple(raw_lines),
            line_height=line_height,
            indent_px=indent_px,
            max_line_width=max_line_width,
            total_height=len(raw_lines) * line_height,
            has_guide=has_guide,
        )

    normalized = _normalize_inline_text(text)
    if not normalized:
        return CommandLayout(
            lines=(),
            line_height=line_height,
            indent_px=indent_px,
            max_line_width=0,
            total_height=0,
            has_guide=False,
        )

    tokens = _split_command_tokens(normalized)
    raw_lines: list[CommandLayoutLine] = []
    alternative_groups = _split_command_alternatives(tokens)
    if alternative_groups is not None:
        for index, group in enumerate(alternative_groups):
            raw_lines.extend(
                _format_command_tokens(
                    group,
                    base_indent=0 if index == 0 else 1,
                    first_kind="root" if index == 0 else "alternative",
                    max_width=max_width,
                    indent_px=indent_px,
                    measure_text=measure_text,
                    palette=palette,
                )
            )
    else:
        raw_lines.extend(
            _format_command_tokens(
                tokens,
                base_indent=0,
                first_kind="root",
                max_width=max_width,
                indent_px=indent_px,
                measure_text=measure_text,
                palette=palette,
            )
        )

    max_line_width = max(
        (
            line.indent_level * indent_px
            + _command_segments_width(line.segments, measure_text)
            for line in raw_lines
        ),
        default=0,
    )
    has_guide = sum(1 for line in raw_lines if line.indent_level > 0) >= 2
    return CommandLayout(
        lines=tuple(raw_lines),
        line_height=line_height,
        indent_px=indent_px,
        max_line_width=max_line_width,
        total_height=len(raw_lines) * line_height,
        has_guide=has_guide,
    )


def _split_inline_code_command_variants(
    text: str,
) -> tuple[tuple[str, ...], ...] | None:
    spans = split_inline_text_spans(text.strip())
    code_values = [
        span.text.strip() for span in spans if span.code and span.text.strip()
    ]
    if len(code_values) < 2:
        return None
    if any(not span.code and span.text.strip() for span in spans):
        return None
    return (
        tuple(tokens for raw in code_values if (tokens := _split_command_tokens(raw)))
        or None
    )


def _split_command_tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    buffer: list[str] = []
    bracket_depth = 0
    angle_depth = 0
    brace_depth = 0
    paren_depth = 0
    for char in text:
        if char in {"；", ";"} and not any(
            (bracket_depth, angle_depth, brace_depth, paren_depth)
        ):
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
            tokens.append(char)
            continue
        if char.isspace() and not any(
            (bracket_depth, angle_depth, brace_depth, paren_depth)
        ):
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
            continue
        buffer.append(char)
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth > 0:
            bracket_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth > 0:
            angle_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth > 0:
            brace_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth > 0:
            paren_depth -= 1
    if buffer:
        tokens.append("".join(buffer))
    return tuple(tokens)


def _split_command_alternatives(
    tokens: Sequence[str],
) -> tuple[tuple[str, ...], ...] | None:
    if not tokens:
        return None
    groups: list[tuple[str, ...]] = []
    current: list[str] = []
    saw_separator = False
    for token in tokens:
        if token in {"/", "|"}:
            if current:
                groups.append(tuple(current))
                current = []
            saw_separator = True
            continue
        current.append(token)
    if current:
        groups.append(tuple(current))
    if not saw_separator or len(groups) < 2:
        return None
    return tuple(group for group in groups if group)


def _format_command_tokens(
    tokens: Sequence[str],
    *,
    base_indent: int,
    first_kind: CommandLineKind,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> list[CommandLayoutLine]:
    root_tokens, flag_clauses = _split_command_flag_clauses(tokens)
    lines: list[CommandLayoutLine] = []
    if root_tokens:
        annotated_root = _annotate_root_tokens(root_tokens)
        available_width = max(0, max_width - base_indent * indent_px)
        if (
            _command_role_width(annotated_root, measure_text, palette)
            <= available_width
        ):
            lines.append(
                CommandLayoutLine(
                    segments=_command_segments_for_roles(
                        annotated_root,
                        palette=palette,
                    ),
                    indent_level=base_indent,
                    kind=first_kind,
                )
            )
        else:
            lines.extend(
                _wrap_command_token_roles(
                    annotated_root,
                    base_indent=base_indent,
                    continuation_indent=base_indent + 1,
                    first_kind=first_kind,
                    continuation_kind="continuation",
                    max_width=max_width,
                    indent_px=indent_px,
                    measure_text=measure_text,
                    palette=palette,
                )
            )
    for clause in flag_clauses:
        lines.extend(
            _format_flag_clause(
                clause,
                indent_level=base_indent + 1,
                max_width=max_width,
                indent_px=indent_px,
                measure_text=measure_text,
                palette=palette,
            )
        )
    if lines:
        return lines
    return [
        CommandLayoutLine(
            segments=_command_segments_for_roles(
                ((token, "root") for token in tokens),
                palette=palette,
            ),
            indent_level=base_indent,
            kind=first_kind,
        )
    ]


def _split_command_flag_clauses(
    tokens: Sequence[str],
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    normalized_tokens: list[str] = []
    for token in tokens:
        expanded = _expand_bracketed_flag_token(token)
        if expanded is not None:
            normalized_tokens.extend(expanded)
            continue
        normalized_tokens.append(token)

    root_tokens: list[str] = []
    clauses: list[tuple[str, ...]] = []
    current_clause: list[str] | None = None
    for token in normalized_tokens:
        if _is_option_flag(token):
            if current_clause:
                clauses.append(tuple(current_clause))
            current_clause = [token]
            continue
        if current_clause is None:
            root_tokens.append(token)
            continue
        current_clause.append(token)
    if current_clause:
        clauses.append(tuple(current_clause))
    return tuple(root_tokens), tuple(clauses)


def _expand_bracketed_flag_token(token: str) -> tuple[str, ...] | None:
    if not (token.startswith("[") and token.endswith("]")):
        return None
    inner_tokens = _split_command_tokens(token[1:-1].strip())
    if not inner_tokens:
        return None
    if not inner_tokens[0].startswith("-"):
        return None
    return inner_tokens


def _is_option_flag(token: str) -> bool:
    if not token.startswith("-") or token in {"-", "--"}:
        return False
    if token.startswith("--"):
        return len(token) > 2 and token[2].isalpha()
    return len(token) > 1 and token[1].isalpha()


def _annotate_root_tokens(
    tokens: Sequence[str],
) -> tuple[tuple[str, Literal["root", "text", "param", "flag"]], ...]:
    parameter_indexes = {
        index
        for index, token in enumerate(tokens)
        if _is_placeholder_token(token)
        or (index > 0 and tokens[index - 1] == "=>")
        or (index + 1 < len(tokens) and tokens[index + 1] == "=>")
    }
    annotated: list[tuple[str, Literal["root", "text", "param", "flag"]]] = []
    for index, token in enumerate(tokens):
        if token == "=>":
            annotated.append((token, "text"))
            continue
        if index in parameter_indexes:
            annotated.append((token, "param"))
            continue
        annotated.append((token, "root"))
    return tuple(annotated)


def _annotate_value_tokens(
    tokens: Sequence[str],
) -> tuple[tuple[str, Literal["root", "text", "param", "flag"]], ...]:
    annotated: list[tuple[str, Literal["root", "text", "param", "flag"]]] = []
    for token in tokens:
        if token in {"=>", "/", "|"}:
            annotated.append((token, "text"))
            continue
        annotated.append((token, "param"))
    return tuple(annotated)


def _is_placeholder_token(token: str) -> bool:
    return bool(re.fullmatch(r"(\[[^\]]+\]|<[^>]+>)", token))


def _format_flag_clause(
    clause: Sequence[str],
    *,
    indent_level: int,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> list[CommandLayoutLine]:
    if not clause:
        return []
    flag_token = clause[0]
    value_tokens = clause[1:]
    flag_role: Literal["flag"] = "flag"
    full_tokens: tuple[
        tuple[str, Literal["root", "text", "param", "flag"]],
        ...,
    ] = ((flag_token, flag_role), *_annotate_value_tokens(value_tokens))
    available_width = max(0, max_width - indent_level * indent_px)
    if _command_role_width(full_tokens, measure_text, palette) <= available_width:
        return [
            CommandLayoutLine(
                segments=_command_segments_for_roles(full_tokens, palette=palette),
                indent_level=indent_level,
                kind="flag",
            )
        ]

    lines = [
        CommandLayoutLine(
            segments=_command_segments_for_roles(
                ((flag_token, flag_role),),
                palette=palette,
            ),
            indent_level=indent_level,
            kind="flag",
        )
    ]
    if value_tokens:
        lines.extend(
            _wrap_command_token_roles(
                _annotate_value_tokens(value_tokens),
                base_indent=indent_level + 1,
                continuation_indent=indent_level + 1,
                first_kind="continuation",
                continuation_kind="continuation",
                max_width=max_width,
                indent_px=indent_px,
                measure_text=measure_text,
                palette=palette,
            )
        )
    return lines


def _wrap_command_token_roles(
    roles: Sequence[tuple[str, Literal["root", "text", "param", "flag"]]],
    *,
    base_indent: int,
    continuation_indent: int,
    first_kind: CommandLineKind,
    continuation_kind: CommandLineKind,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> list[CommandLayoutLine]:
    if not roles:
        return []

    lines: list[CommandLayoutLine] = []
    current: list[tuple[str, Literal["root", "text", "param", "flag"]]] = []
    current_indent = base_indent
    current_kind = first_kind
    index = 0
    while index < len(roles):
        token, role = roles[index]
        available_width = max(0, max_width - current_indent * indent_px)
        candidate = [*current, (token, role)]
        if current and (
            _command_role_width(candidate, measure_text, palette) <= available_width
        ):
            current = candidate
            index += 1
            continue
        if not current and (
            _command_role_width(candidate, measure_text, palette) <= available_width
        ):
            current = candidate
            index += 1
            continue

        if current:
            lines.append(
                CommandLayoutLine(
                    segments=_command_segments_for_roles(current, palette=palette),
                    indent_level=current_indent,
                    kind=current_kind,
                )
            )
            current = []
            current_indent = continuation_indent
            current_kind = continuation_kind
            continue

        split_tokens = _split_oversized_command_token(
            token,
            role=role,
            max_width=available_width,
            measure_text=measure_text,
            palette=palette,
        )
        if not split_tokens:
            current = [(token, role)]
            index += 1
            continue
        current = [(split_tokens[0], role)]
        for piece in split_tokens[1:]:
            lines.append(
                CommandLayoutLine(
                    segments=_command_segments_for_roles(current, palette=palette),
                    indent_level=current_indent,
                    kind=current_kind,
                )
            )
            current = [(piece, role)]
            current_indent = continuation_indent
            current_kind = continuation_kind
        index += 1

    if current:
        lines.append(
            CommandLayoutLine(
                segments=_command_segments_for_roles(current, palette=palette),
                indent_level=current_indent,
                kind=current_kind,
            )
        )
    return lines


def _split_oversized_command_token(
    token: str,
    *,
    role: Literal["root", "text", "param", "flag"],
    max_width: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> tuple[str, ...]:
    if max_width <= 0:
        return (token,)

    chunks: list[str] = []
    current = ""
    for unit in _split_command_token_units(token):
        candidate = current + unit
        if (
            current
            and _command_role_width(((candidate, role),), measure_text, palette)
            > max_width
        ):
            chunks.append(current)
            current = ""
        if _command_role_width(((unit, role),), measure_text, palette) <= max_width:
            current += unit
            continue
        for char in unit:
            candidate = current + char
            if (
                current
                and _command_role_width(((candidate, role),), measure_text, palette)
                > max_width
            ):
                chunks.append(current)
                current = char
                continue
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunk for chunk in chunks if chunk) or (token,)


def _split_command_token_units(token: str) -> tuple[str, ...]:
    parts = re.split(r"(=>|\||/|:|,|_)", token)
    units: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in {"=>", "|", "/", ":", ",", "_"} and units:
            units[-1] += part
            continue
        units.append(part)
    return tuple(units)


def _command_role_width(
    roles: Sequence[tuple[str, Literal["root", "text", "param", "flag"]]],
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> int:
    return _command_segments_width(
        _command_segments_for_roles(roles, palette=palette),
        measure_text,
    )


def _command_segments_for_roles(
    roles: Iterable[tuple[str, Literal["root", "text", "param", "flag"]]],
    *,
    palette: CommandPalette,
) -> tuple[InlineTextSpan, ...]:
    segments: list[InlineTextSpan] = []
    ordered = list(roles)
    for index, (token, role) in enumerate(ordered):
        fill = {
            "root": palette.root,
            "text": palette.text,
            "param": palette.param,
            "flag": palette.flag,
        }[role]
        _append_inline_command_segment(segments, token, fill=fill)
        if index < len(ordered) - 1:
            _append_inline_command_segment(segments, " ", fill=palette.text)
    return tuple(segment for segment in segments if segment.text)


def _append_inline_command_segment(
    spans: list[InlineTextSpan],
    text: str,
    *,
    fill: str,
) -> None:
    if not text:
        return
    if spans and spans[-1].code is False and spans[-1].fill == fill:
        previous = spans[-1]
        spans[-1] = InlineTextSpan(previous.text + text, code=False, fill=fill)
        return
    spans.append(InlineTextSpan(text, code=False, fill=fill))


def _command_segments_width(
    segments: Sequence[InlineTextSpan],
    measure_text: Callable[[str], int],
) -> int:
    return sum(measure_text(span.text) for span in segments if span.text)


def _parse_inline_tokens(text: str) -> tuple[Token, ...]:
    tokens = _markdown_parser().parseInline(text)
    if not tokens:
        return ()
    return tuple(tokens[0].children or ())


def _append_inline_text_span(
    spans: list[InlineTextSpan],
    text: str,
    *,
    code: bool,
) -> None:
    if not text:
        return
    if spans and spans[-1].code is code:
        previous = spans[-1]
        spans[-1] = InlineTextSpan(previous.text + text, code=code)
        return
    spans.append(InlineTextSpan(text, code=code))


def load_demo_bytes(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes | None:
    if feature.demo_filename:
        demo_path = bundle.source_path.parent / "demos" / feature.demo_filename
        if demo_path.is_file():
            return demo_path.read_bytes()
    if not feature.demo_turns:
        return None
    return render_demo_png(bundle, feature)


def load_collection_demo_bytes(bundle: PluginDocBundle) -> bytes | None:
    demo_path = (
        bundle.source_path.parent
        / "demos"
        / collection_demo_filename(bundle.source_path)
    )
    if demo_path.is_file():
        return demo_path.read_bytes()
    return None


def load_representative_demo_bytes(
    bundle: PluginDocBundle,
    actor_permission: Permission = Permission.NORMAL,
    prefer_collection: bool = True,
) -> bytes | None:
    """加载代表性 demo 图片，优先使用集合图片，否则使用第一个有权限的功能 demo"""
    if prefer_collection:
        collection_demo = load_collection_demo_bytes(bundle)
        if collection_demo is not None:
            return collection_demo
    # 按权限过滤功能，只加载用户有权限查看的 demo
    visible_features = filter_features_by_permission(bundle.index, actor_permission)
    for feature in visible_features:
        demo_bytes = load_demo_bytes(bundle, feature)
        if demo_bytes is not None:
            return demo_bytes
    return None


def _permission_label(permission: Permission) -> str:
    labels = {
        Permission.NONE: "权限开放",
        Permission.NORMAL: "普通用户",
        Permission.GROUP_ADMIN: "群管理",
        Permission.GROUP_OWNER: "群主",
        Permission.SUPERUSER: "超级用户",
    }
    return labels.get(permission, "普通用户")


def render_demo_png(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    *,
    generated_at: datetime | None = None,
) -> bytes:
    return DemoImageRenderer(impression_color=bundle.impression_color).render(
        plugin_title=bundle.title,
        feature_title=feature.title,
        feature_summary=feature.summary,
        feature_trigger=feature.trigger,
        feature_overview=feature.overview,
        feature_preconditions=feature.preconditions,
        feature_failures=feature.failures,
        feature_flow_notes=feature.flow_notes,
        plugin_trigger=bundle.trigger,
        feature_permission=_permission_label(feature.permission),
        plugin_version=bundle.version,
        plugin_author=bundle.author,
        turns=feature.demo_turns,
        locale="zh-CN",
        generated_at=generated_at,
    )


def render_feature_deep_dive(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
) -> bytes:
    _ = locale
    return render_demo_png(node.bundle, feature, generated_at=generated_at)


def render_plugin_guide(
    node: DocNode,
    *,
    actor_permission: Permission,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
) -> bytes:
    hero_features, advanced_features = split_features_for_disclosure(
        node.features,
        actor_permission=actor_permission,
    )
    return ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    ).render_plugin_guide(
        node=node,
        hero_features=hero_features,
        advanced_features=advanced_features,
        locale=locale,
        generated_at=generated_at,
    )


def render_help_dashboard(
    nodes: Sequence[DocNode],
    *,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
) -> bytes:
    theme_color = (
        nodes[0].bundle.impression_color if nodes else DEFAULT_IMPRESSION_COLOR
    )
    return ProgressiveDisclosureRenderer(impression_color=theme_color).render_dashboard(
        nodes=nodes,
        locale=locale,
        generated_at=generated_at,
    )


def collection_demo_filename(source_path: Path) -> str:
    return f"{doc_asset_prefix(source_path)}-collection.png"


def doc_asset_prefix(source_path: Path) -> str:
    src_root = next(
        (parent for parent in source_path.parents if parent.name == "src"), None
    )
    if src_root is None:
        return source_path.parent.name

    try:
        rel_path = source_path.relative_to(src_root)
    except ValueError:
        return source_path.parent.name

    parts = rel_path.parts
    namespace = parts[0] if parts else ""
    try:
        docs_index = parts.index("docs")
    except ValueError:
        return source_path.parent.name

    if namespace == "plugins" and len(parts) > 1:
        name_parts = (parts[1], *parts[docs_index + 1 : -1])
    elif namespace == "hooks":
        name_parts = ("hook", *parts[docs_index + 1 : -1])
    else:
        name_parts = (*parts[docs_index + 1 : -1],)

    return "-".join(_slugify_doc_path_part(part) for part in name_parts if part) or (
        source_path.parent.name
    )


def _slugify_doc_path_part(value: str) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value.lower())
    return "-".join(part for part in slug.split("-") if part)


def audit_demo_layout(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    *,
    generated_at: datetime | None = None,
) -> tuple[str, ...]:
    return DemoImageRenderer(impression_color=bundle.impression_color).audit(
        plugin_title=bundle.title,
        feature_title=feature.title,
        feature_summary=feature.summary,
        feature_trigger=feature.trigger,
        feature_overview=feature.overview,
        feature_preconditions=feature.preconditions,
        feature_failures=feature.failures,
        feature_flow_notes=feature.flow_notes,
        plugin_trigger=bundle.trigger,
        feature_permission=_permission_label(feature.permission),
        plugin_version=bundle.version,
        plugin_author=bundle.author,
        turns=feature.demo_turns,
        locale="zh-CN",
        generated_at=generated_at,
    )


def _feature_command_for_display(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
) -> str:
    command = _normalize_inline_text(feature.trigger)
    if command:
        return command
    return f"#help {node_title} {feature.slug}"


def feature_demo_help_command(node: DocNode, feature: FeatureDoc) -> str:
    return f"#help {node.title} {feature.slug}"


def _format_feature_command_lines(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
    *,
    locale: LocaleCode = "zh-CN",
) -> list[str]:
    command = _feature_command_for_display(bundle, feature, node_title)
    sections = [
        part.strip() for part in re.split(r"\s*[；;]\s*", command) if part.strip()
    ]
    if len(sections) <= 1:
        return [f"  {command}"]

    lines: list[str] = []
    shortcut_groups: list[str] = []
    shortcut_sections: list[str] = []
    in_shortcut_section = False
    for section in sections:
        if match := re.match(r"快捷入口[:：]\s*(.+)", section):
            shortcut_groups.append(match.group(1).strip())
            in_shortcut_section = True
            continue
        if match := re.match(r"快捷入口分组[:：]\s*(.+)", section):
            shortcut_sections.append(match.group(1).strip())
            in_shortcut_section = False
            continue
        if shortcut_sections:
            shortcut_sections.append(section)
            continue
        if in_shortcut_section:
            shortcut_groups.append(section)
            continue
        lines.append(f"  {section}")

    if shortcut_sections:
        lines.append(f"  {tr(locale, 'docs.feature.shortcuts')}")
        lines.extend(_format_shortcut_section_lines(shortcut_sections))

    if shortcut_groups:
        lines.append(f"  {tr(locale, 'docs.feature.shortcuts')}")
        lines.extend(f"    {group}" for group in shortcut_groups)

    return lines or [f"  {command}"]


def _format_shortcut_section_lines(sections: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for index, section in enumerate(sections):
        if ":" not in section:
            lines.append(f"    {section}")
            continue
        _, commands = section.split(":", 1)
        summarized = _summarize_shortcut_commands(
            commands.strip(),
            keep_full=index == 0,
        )
        lines.append(f"    {summarized}")
    return lines


def _summarize_shortcut_commands(commands: str, *, keep_full: bool = False) -> str:
    if keep_full:
        return commands
    parts = [part.strip() for part in commands.split("/") if part.strip()]
    if not parts:
        return commands
    primary = parts[0]
    if len(parts) == 1:
        return primary
    return f"{primary} / ..."


def _feature_notice_items(feature: FeatureDoc, *, locale: LocaleCode) -> list[str]:
    notes: list[str] = []
    preconditions = _normalize_inline_text(feature.preconditions)
    if preconditions and preconditions != "无":
        notes.append(preconditions)
    else:
        notes.append(tr(locale, "docs.node.notice.item1"))
    notes.append(_support_note(locale))
    return notes


def feature_command_sections(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
) -> tuple[str, ...]:
    command = _feature_command_for_display(bundle, feature, node_title)
    sections = [
        part.strip() for part in re.split(r"\s*[；;]\s*", command) if part.strip()
    ]
    return tuple(sections) or (command,)


def build_doc_demo_message(
    *,
    source: str | Path,
    name: str,
    description: str,
    trigger: TriggerType,
    permission: Permission,
    locale: LocaleCode,
    actor_permission: Permission | None = None,
    feature_query: str | None = None,
    prefix_text: str | None = None,
) -> Message:
    node = load_doc_node(
        source=source,
        default_name=name,
        default_description=description,
        trigger=trigger,
        permission=permission,
    )
    viewer_permission = actor_permission or permission
    image_bytes: bytes | None = None
    if feature_query:
        visible_features = filter_features_by_permission(
            node.features,
            viewer_permission,
        )
        match = match_feature(visible_features, feature_query)
        if match.status == "matched" and match.feature is not None:
            image_bytes = render_feature_deep_dive(
                node,
                match.feature,
                locale=locale,
            )
    if image_bytes is None:
        image_bytes = render_plugin_guide(
            node,
            actor_permission=viewer_permission,
            locale=locale,
        )

    text = (prefix_text or "").strip()
    if not text:
        return Message(MessageSegment.image(image_bytes))
    return Message(f"{text}\n参考示例如下：\n") + MessageSegment.image(image_bytes)


def build_feature_copy_text(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
) -> str:
    lines = [
        f"👉 {feature.title}",
        *(
            section
            for section in feature_command_sections(node.bundle, feature, node.title)
        ),
    ]
    note_items = _feature_notice_items(feature, locale=locale)
    if note_items:
        lines.extend(["", f"说明：{note_items[0]}"])
    lines.extend(["", f"查看 demo：{feature_demo_help_command(node, feature)}"])
    return "\n".join(lines).strip()


def build_plugin_guide_copy_text(
    node: DocNode,
    *,
    hero_features: Sequence[FeatureDoc],
    advanced_features: Sequence[FeatureDoc],
    locale: LocaleCode,
) -> str:
    lines = [
        f"📖 {node.title}",
        "下面这些命令可以直接复制发送：",
        "",
    ]
    for feature in hero_features:
        lines.append(f"👉 {feature.title}")
        lines.extend(
            feature_command_sections(
                node.bundle,
                feature,
                node.title,
            )
        )
        lines.append(f"查看 demo：{feature_demo_help_command(node, feature)}")
        lines.append("")
    if advanced_features:
        lines.append("更多高级功能：")
        for feature in advanced_features:
            lines.append(f"- {feature.title}")
            lines.extend(
                f"  {section}"
                for section in feature_command_sections(
                    node.bundle,
                    feature,
                    node.title,
                )
            )
            lines.append(f"  查看 demo：{feature_demo_help_command(node, feature)}")
    return "\n".join(line for line in lines if line is not None).strip()


def build_simple_leaf_copy_text(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
) -> str:
    lines = [
        f"👉 {node.title}",
        *feature_command_sections(node.bundle, feature, node.title),
        "",
        f"查看 demo：{feature_demo_help_command(node, feature)}",
    ]
    note_items = _feature_notice_items(feature, locale=locale)
    if note_items:
        lines.extend(["", f"说明：{note_items[0]}"])
    return "\n".join(lines).strip()


_EMPTY_HEADING = Token("inline", "", 0)
_EMPTY_SECTION = MarkdownSection(title="", heading=_EMPTY_HEADING, tokens=())


def _extract_title(tokens: Sequence[Token]) -> str:
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open"
            and token.tag == "h1"
            and index + 1 < len(tokens)
        ):
            return _normalize_heading(
                _render_inline_markdown(tokens[index + 1].children or ())
            )
    return ""


def _extract_heading_sections(
    tokens: Sequence[Token],
    *,
    tag: str,
) -> tuple[MarkdownSection, ...]:
    sections: list[MarkdownSection] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type != "heading_open" or token.tag != tag or index + 1 >= len(tokens):
            index += 1
            continue
        heading = tokens[index + 1]
        body_start = min(index + 3, len(tokens))
        body_end = body_start
        while body_end < len(tokens):
            next_token = tokens[body_end]
            if next_token.type == "heading_open" and next_token.tag == tag:
                break
            body_end += 1
        sections.append(
            MarkdownSection(
                title=_normalize_heading(
                    _render_inline_markdown(heading.children or ())
                ),
                heading=heading,
                tokens=tuple(tokens[body_start:body_end]),
            )
        )
        index = body_end
    return tuple(sections)


def _normalize_heading(raw: str) -> str:
    return raw.strip().strip("`")


def _render_inline_markdown(tokens: Sequence[Token]) -> str:
    fragments: list[str] = []
    for token in tokens:
        if token.type == "inline":
            fragments.append(_render_inline_markdown(token.children or ()))
            continue
        if token.type == "text":
            fragments.append(token.content)
            continue
        if token.type == "code_inline":
            fragments.append(f"`{token.content}`")
            continue
        if token.type in {"softbreak", "hardbreak"}:
            fragments.append("\n")
            continue
        if token.type == "image":
            fragments.append(token.content)
    return "".join(fragments)


def _render_markdown_blocks(tokens: Sequence[Token]) -> str:
    blocks: list[str] = []
    for token in tokens:
        if token.type == "inline":
            text = _render_inline_markdown(token.children or ()).strip()
            if text:
                blocks.append(text)
            continue
        if token.type != "fence":
            continue
        info = token.info.strip()
        opening = f"```{info}" if info else "```"
        content = token.content.rstrip("\n")
        blocks.append(f"{opening}\n{content}\n```".strip())
    return "\n".join(blocks).strip()


def _extract_list_item_tokens(tokens: Sequence[Token]) -> tuple[tuple[Token, ...], ...]:
    items: list[tuple[Token, ...]] = []
    index = 0
    while index < len(tokens):
        if tokens[index].type != "list_item_open":
            index += 1
            continue
        depth = 1
        cursor = index + 1
        while cursor < len(tokens) and depth > 0:
            if tokens[cursor].type == "list_item_open":
                depth += 1
            elif tokens[cursor].type == "list_item_close":
                depth -= 1
            cursor += 1
        items.append(tuple(tokens[index + 1 : max(index + 1, cursor - 1)]))
        index = cursor
    return tuple(items)


def _parse_meta_block_tokens(tokens: Sequence[Token]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for item_tokens in _extract_list_item_tokens(tokens):
        payload = _render_markdown_blocks(item_tokens).replace("\n", " ").strip()
        key, value = _split_key_value(payload)
        if not key:
            continue
        meta[key] = _strip_wrapping_backticks(value)
    return meta


def _split_key_value(value: str) -> tuple[str, str]:
    for separator in (":", "："):
        if separator not in value:
            continue
        key, payload = value.split(separator, 1)
        return key.strip(), payload.strip()
    return "", ""


def _strip_wrapping_backticks(value: str) -> str:
    inline_tokens = _parse_inline_tokens(value)
    if len(inline_tokens) == 1 and inline_tokens[0].type == "code_inline":
        return inline_tokens[0].content.strip()
    return value


def _parse_permission(value: str) -> Permission:
    normalized = value.strip()
    if not normalized:
        return Permission.NORMAL
    try:
        return Permission[normalized]
    except KeyError:
        pass
    for permission in Permission:
        if normalized == permission.label:
            return permission
    try:
        return Permission(int(normalized))
    except ValueError:
        return Permission.NORMAL


def _parse_bool_meta(value: str, *, default: bool = False) -> bool:
    normalized = value.strip().strip("`").lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "y", "on", "是", "真"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否", "假"}:
        return False
    return default


def _parse_int_meta(value: str, *, default: int = 1000) -> int:
    normalized = value.strip().strip("`")
    if not normalized:
        return default
    try:
        return int(normalized)
    except ValueError:
        return default


def _parse_feature_index_tokens(tokens: Sequence[Token]) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for item_tokens in _extract_list_item_tokens(tokens):
        inline = next((token for token in item_tokens if token.type == "inline"), None)
        if inline is None:
            continue
        children = tuple(inline.children or ())
        if not children or children[0].type != "code_inline":
            continue
        slug = children[0].content.strip()
        title, summary = _split_key_value(_render_inline_markdown(children[1:]).strip())
        if not slug or not title:
            continue
        entries[slug] = (title, summary)
    return entries


def _parse_feature_details_tokens(
    tokens: Sequence[Token],
    source_path: Path,
) -> dict[str, FeatureDoc]:
    features: dict[str, FeatureDoc] = {}
    for section in _extract_heading_sections(tokens, tag="h3"):
        slug, title = _parse_feature_heading(section.heading)
        if not slug or not title:
            continue
        meta_tokens, body_tokens = _split_tokens_before_heading(
            section.tokens, tag="h4"
        )
        meta = _parse_meta_block_tokens(meta_tokens)
        subsections = {
            subsection.title: subsection.tokens
            for subsection in _extract_heading_sections(body_tokens, tag="h4")
        }
        flow_notes, demo_turns = _parse_flow_section_tokens(
            subsections.get("完整流程", ())
        )
        demo_filename = meta.get("Demo", f"{doc_asset_prefix(source_path)}-{slug}.png")
        demo_filename = demo_filename.strip("`")
        features[slug] = FeatureDoc(
            slug=slug,
            title=title.strip(),
            summary=meta.get("摘要", "").strip() or title.strip(),
            aliases=_split_csv(meta.get("别名", "")),
            trigger=meta.get("指令", "").strip() or meta.get("触发", "").strip(),
            permission=_parse_permission(meta.get("权限", "")),
            demo_filename=demo_filename,
            hero=_parse_bool_meta(
                meta.get("Hero", "").strip() or meta.get("主推", "").strip()
            ),
            priority=_parse_int_meta(
                meta.get("Priority", "").strip() or meta.get("优先级", "").strip()
            ),
            advanced=_parse_bool_meta(
                meta.get("Advanced", "").strip() or meta.get("高级", "").strip()
            ),
            overview=_render_markdown_blocks(subsections.get("说明", ())).strip(),
            preconditions=_render_markdown_blocks(
                subsections.get("前置条件", ())
            ).strip(),
            flow_notes=flow_notes.strip(),
            failures=_render_markdown_blocks(subsections.get("失败情况", ())).strip(),
            demo_turns=demo_turns,
        )
    return features


def _split_tokens_before_heading(
    tokens: Sequence[Token],
    *,
    tag: str,
) -> tuple[tuple[Token, ...], tuple[Token, ...]]:
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == tag:
            return tuple(tokens[:index]), tuple(tokens[index:])
    return tuple(tokens), ()


def _parse_feature_heading(heading: Token) -> tuple[str, str]:
    children = tuple(heading.children or ())
    if children and children[0].type == "code_inline":
        slug = children[0].content.strip()
        title = _render_inline_markdown(children[1:]).strip()
        if slug and title:
            return slug, title
    rendered = _normalize_heading(_render_inline_markdown(children))
    parts = rendered.split(maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip("`").strip(), parts[1].strip()


def _parse_flow_section_tokens(
    tokens: Sequence[Token],
) -> tuple[str, tuple[DocsDemoTurn, ...]]:
    demo_turns: list[DocsDemoTurn] = []
    cleaned: list[str] = []
    for token in tokens:
        if token.type == "fence" and token.info.strip() == "demo":
            demo_turns.extend(_parse_demo_turns(token.content))
            continue
        if token.type != "inline":
            continue
        text = _render_inline_markdown(token.children or ()).strip()
        if text:
            cleaned.append(text)
    return "\n".join(cleaned).strip(), tuple(demo_turns)


def _parse_demo_turns(content: str) -> list[DocsDemoTurn]:
    demo_turns: list[DocsDemoTurn] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped:
            speaker, text = stripped.split(":", 1)
            normalized = speaker.strip().upper()
            if normalized in {"USER", "BOT", "SYSTEM"}:
                demo_turns.append(
                    DocsDemoTurn(
                        cast(Literal["USER", "BOT", "SYSTEM"], normalized),
                        text.strip(),
                    )
                )
                continue
        if demo_turns:
            previous = demo_turns[-1]
            demo_turns[-1] = DocsDemoTurn(
                previous.speaker,
                f"{previous.text}\n{stripped}",
            )
    return demo_turns


def _merge_features(
    feature_index: dict[str, tuple[str, str]],
    details: dict[str, FeatureDoc],
) -> tuple[FeatureDoc, ...]:
    ordered: list[FeatureDoc] = []
    seen: set[str] = set()
    for slug, (title, summary) in feature_index.items():
        detail = details.get(slug)
        if detail is None:
            ordered.append(
                FeatureDoc(
                    slug=slug,
                    title=title,
                    summary=summary,
                    aliases=(),
                    trigger="",
                    permission=Permission.NORMAL,
                    demo_filename="",
                    hero=False,
                    priority=1000,
                    advanced=False,
                    overview="",
                    preconditions="",
                    flow_notes="",
                    failures="",
                    demo_turns=(),
                )
            )
        else:
            ordered.append(
                FeatureDoc(
                    slug=detail.slug,
                    title=detail.title or title,
                    summary=detail.summary or summary,
                    aliases=detail.aliases,
                    trigger=detail.trigger,
                    permission=detail.permission,
                    demo_filename=detail.demo_filename,
                    hero=detail.hero,
                    priority=detail.priority,
                    advanced=detail.advanced,
                    overview=detail.overview,
                    preconditions=detail.preconditions,
                    flow_notes=detail.flow_notes,
                    failures=detail.failures,
                    demo_turns=detail.demo_turns,
                )
            )
        seen.add(slug)
    for slug, feature in details.items():
        if slug not in seen:
            ordered.append(feature)
    return tuple(ordered)


def _split_csv(value: str) -> tuple[str, ...]:
    items = [part.strip().strip("`") for part in value.split(",")]
    return tuple(item for item in items if item)


def _resolve_doc_signature(source_path: Path) -> tuple[str, str]:
    module_path = _resolve_doc_owner_module_path(source_path)
    if module_path is None or not module_path.exists():
        return "Unknown", "0.0.0"

    raw_text = module_path.read_text(encoding="utf-8")
    author = _extract_metadata_field(raw_text, "author") or "Unknown"
    version = _extract_metadata_field(raw_text, "version") or "0.0.0"
    return author, version


def _resolve_doc_impression_color(source_path: Path) -> str:
    module_path = _resolve_doc_owner_module_path(source_path)
    if module_path is None or not module_path.exists():
        return DEFAULT_IMPRESSION_COLOR

    raw_text = module_path.read_text(encoding="utf-8")
    return normalize_hex_color(
        _extract_metadata_field(raw_text, "impression_color"),
        fallback=DEFAULT_IMPRESSION_COLOR,
    )


def _resolve_doc_owner_module_path(source_path: Path) -> Path | None:
    src_root = next(
        (parent for parent in source_path.parents if parent.name == "src"), None
    )
    if src_root is None:
        return None

    try:
        rel_path = source_path.relative_to(src_root)
    except ValueError:
        return None

    parts = rel_path.parts
    if len(parts) < 4 or parts[-1] != "README.MD":
        return None

    namespace = parts[0]
    if namespace not in {"plugins", "hooks"}:
        return None

    repo_root = src_root.parent
    if parts[1] == "docs":
        return repo_root / "src" / namespace / f"{parts[2]}.py"

    owner = parts[1]
    if len(parts) == 4 and parts[2] == "docs":
        return repo_root / "src" / namespace / owner / "__init__.py"
    if len(parts) == 5 and parts[2] == "docs":
        return repo_root / "src" / namespace / owner / f"{parts[3]}.py"
    return None


def _extract_metadata_field(raw_text: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}":\s*"([^"]+)"', raw_text)
    return match.group(1).strip() if match else ""


class LegacyDemoImageRenderer:
    """Render a compact plugin docs demo card."""

    WIDTH = 1280
    OUTER_MARGIN = 40
    SHELL_RADIUS = 32
    HEADER_HEIGHT = 248
    HEADER_LEFT = 126
    HEADER_TOP = 70
    HEADER_CHIP_TOP = 72
    HEADER_TITLE_TOP = 112
    HEADER_FEATURE_TOP = 178
    HEADER_TRIGGER_TOP = 218
    HEADER_RIGHT = 1138
    HEADER_STANDEE_SIZE = 150
    HEADER_STANDEE_X = 1030
    HEADER_STANDEE_Y = 104
    HEADER_STEPS_X = 302
    BODY_TOP_GAP = 20
    BODY_PADDING_X = 40
    BODY_PADDING_Y = 36
    TURN_GAP = 24
    FOOTER_HEIGHT = 52
    FOOTER_TOP_GAP = 24
    FOOTER_SIDE_PADDING = 28
    FOOTER_TEXT_GAP = 20
    CONVERSATION_SIDE_PADDING = 76
    AVATAR_SIZE = 48
    BUBBLE_RADIUS = 22
    BUBBLE_PADDING_X = 24
    BUBBLE_PADDING_Y = 20
    BUBBLE_LABEL_GAP = 12
    USER_CONTENT_WIDTH = 560
    BOT_CONTENT_WIDTH = 640
    SYSTEM_CONTENT_WIDTH = 860
    USER_MIN_BUBBLE_WIDTH = 270
    BOT_MIN_BUBBLE_WIDTH = 310
    SYSTEM_MIN_BUBBLE_WIDTH = 480
    CHIP_HEIGHT = 38
    FOOTER_RIGHT_TEXT = "help docs"
    FONT_FAMILIES: ClassVar[list[str]] = [MAPLE_FONT_NAME]

    def __init__(self) -> None:
        self.theme_name = SENRIN_V3_THEME.name
        self.theme = DEFAULT_DEMO_THEME
        try:
            # 移动端优化：增大字体以便手机聊天查看
            self.eyebrow_font = ImageFont.truetype(MAPLE_FONT_PATH, 22)  # 16 → 22
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 56)  # 42 → 56
            self.feature_font = ImageFont.truetype(MAPLE_FONT_PATH, 34)  # 26 → 34
            self.body_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)  # 24 → 32
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 22)  # 16 → 22
            self.footer_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)  # 15 → 20
        except OSError:
            self.eyebrow_font = ImageFont.load_default()
            self.title_font = ImageFont.load_default()
            self.feature_font = ImageFont.load_default()
            self.body_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.footer_font = ImageFont.load_default()
        self.senrin_avatar = self._load_asset(DEMO_AVATAR_PATH, self.AVATAR_SIZE)
        self.senrin_standee = self._load_asset(
            DEMO_STANDEE_PATH,
            self.HEADER_STANDEE_SIZE,
            alpha=168,
        )

    def render(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
    ) -> bytes:
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = self._conversation_height(turn_specs)
        panel_top = self.OUTER_MARGIN + self.HEADER_HEIGHT + self.BODY_TOP_GAP
        body_top = panel_top + self.BODY_PADDING_Y
        panel_bottom = body_top + conversation_height + self.BODY_PADDING_Y
        footer_top = panel_bottom + self.FOOTER_TOP_GAP
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        image = Image.new("RGB", (self.WIDTH, height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN,
                self.OUTER_MARGIN,
                self.WIDTH - self.OUTER_MARGIN,
                height - self.OUTER_MARGIN,
            ),
            radius=self.theme.shell_radius,
            fill=self.theme.shell_bg,
            outline=self.theme.shell_border,
            width=2,
        )
        self._draw_header(
            image,
            draw,
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_trigger=feature_trigger,
            turn_count=len(turns),
            locale=locale,
        )
        self._draw_conversation_panel(
            draw,
            top=panel_top,
            bottom=panel_bottom,
        )

        y = body_top
        for spec in turn_specs:
            self._draw_turn(image, draw, spec, y)
            y += spec.height + self.TURN_GAP

        self._draw_footer(
            draw,
            top=footer_top,
            plugin_title=plugin_title,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
        )

        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _paint_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, width, height), fill=self.theme.page_bg)
        draw.rectangle((0, 0, width, 10), fill=self.theme.accent)
        draw.rounded_rectangle(
            (74, 66, 112, height - 76),
            radius=19,
            fill=self.theme.showcase_accent_rail_bg,
        )
        draw.rounded_rectangle(
            (width - 142, 126, width - 86, height - 124),
            radius=28,
            fill=self.theme.showcase_support_rail_bg,
        )

    def _draw_header(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        turn_count: int,
        locale: LocaleCode,
    ) -> None:
        draw.rounded_rectangle((96, 94, 106, 190), radius=5, fill=self.theme.accent)
        self._draw_chip(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_CHIP_TOP,
            text="PLUGIN DEMO",
            fill=self.theme.muted_light,
            text_fill=self.theme.strong,
            font=self.eyebrow_font,
        )
        self._draw_chip(
            draw,
            x=self.HEADER_STEPS_X,
            y=self.HEADER_CHIP_TOP,
            text=f"{turn_count} STEP{'S' if turn_count != 1 else ''}",
            fill=self.theme.indigo_soft,
            text_fill=self.theme.indigo_text,
            font=self.eyebrow_font,
            min_width=154,
        )
        title_text = self._fit_text(
            draw,
            plugin_title,
            self.title_font,
            max_width=720,
        )
        self._draw_text(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_TITLE_TOP,
            text=title_text,
            font=self.title_font,
            fill=self.theme.deep,
        )
        feature_text = self._fit_text(
            draw,
            feature_title,
            self.feature_font,
            max_width=720,
        )
        self._draw_text(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_FEATURE_TOP,
            text=feature_text,
            font=self.feature_font,
            fill=self.theme.strong,
        )
        if feature_trigger.strip():
            trigger_example = tr(
                locale,
                "docs.feature.trigger_example",
                command=feature_trigger,
            )
            self._draw_inline_chip(
                draw,
                x=self.HEADER_LEFT,
                y=self.HEADER_TRIGGER_TOP,
                text=trigger_example,
                max_width=760,
                fill=self.theme.inline_code_bg,
                text_fill=self.theme.hint,
                font=self.meta_font,
                min_width=300,
            )
        self._draw_header_standee(image, draw)

    def _draw_conversation_panel(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        top: int,
        bottom: int,
    ) -> None:
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN + 28,
                top,
                self.WIDTH - self.OUTER_MARGIN - 28,
                bottom,
            ),
            radius=self.theme.panel_radius,
            fill=self.theme.panel_bg,
            outline=self.theme.line,
            width=2,
        )

    def _measure_turn(self, turn: DocsDemoTurn) -> "_TurnSpec":
        if turn.speaker == "SYSTEM":
            lines = self._wrap_inline_text(
                self._normalize_demo_text(turn.text),
                max_width=self.SYSTEM_CONTENT_WIDTH,
                font=self.body_font,
            )
            text_height = self._line_block_height(lines, self.body_font)
            width = (
                self._max_inline_line_width(lines, self.body_font)
                + self.BUBBLE_PADDING_X * 2
            )
            return _TurnSpec(
                turn=turn,
                lines=lines,
                width=max(width, self.SYSTEM_MIN_BUBBLE_WIDTH),
                height=text_height + self.BUBBLE_PADDING_Y * 2 + 18,
            )

        is_user = turn.speaker == "USER"
        lines = self._wrap_inline_text(
            self._normalize_demo_text(turn.text),
            max_width=self.USER_CONTENT_WIDTH if is_user else self.BOT_CONTENT_WIDTH,
            font=self.body_font,
        )
        text_height = self._line_block_height(lines, self.body_font)
        label_height = self._font_line_height(self.eyebrow_font)
        bubble_height = (
            text_height
            + label_height
            + self.BUBBLE_LABEL_GAP
            + self.BUBBLE_PADDING_Y * 2
        )
        bubble_width = (
            self._max_inline_line_width(lines, self.body_font)
            + self.BUBBLE_PADDING_X * 2
        )
        min_width = self.USER_MIN_BUBBLE_WIDTH if is_user else self.BOT_MIN_BUBBLE_WIDTH
        return _TurnSpec(
            turn=turn,
            lines=lines,
            width=min(
                max(bubble_width, min_width),
                self.USER_CONTENT_WIDTH + self.BUBBLE_PADDING_X * 2
                if is_user
                else self.BOT_CONTENT_WIDTH + self.BUBBLE_PADDING_X * 2,
            ),
            height=max(bubble_height, self.AVATAR_SIZE),
        )

    def _draw_turn(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        spec: "_TurnSpec",
        top: int,
    ) -> None:
        if spec.turn.speaker == "SYSTEM":
            left = (self.WIDTH - spec.width) // 2
            right = left + spec.width
            draw.rounded_rectangle(
                (left, top, right, top + spec.height),
                radius=20,
                fill=self.theme.system_bubble,
            )
            self._draw_multiline_text(
                draw,
                x=left + self.BUBBLE_PADDING_X,
                y=top + self.BUBBLE_PADDING_Y + 8,
                lines=spec.lines,
                font=self.body_font,
                fill=self.theme.system_text,
            )
            label = "SYSTEM"
            label_box = self._text_size(label, self.eyebrow_font)
            self._draw_text(
                draw,
                x=left + self.BUBBLE_PADDING_X,
                y=top + 12 - label_box[1],
                text=label,
                font=self.eyebrow_font,
                fill=self.theme.system_label,
            )
            return

        is_user = spec.turn.speaker == "USER"
        bubble_width = spec.width
        avatar_x = (
            self.WIDTH
            - self.OUTER_MARGIN
            - self.CONVERSATION_SIDE_PADDING
            - self.AVATAR_SIZE
            if is_user
            else self.OUTER_MARGIN + self.CONVERSATION_SIDE_PADDING
        )
        bubble_x = (
            avatar_x - 18 - bubble_width
            if is_user
            else avatar_x + self.AVATAR_SIZE + 18
        )
        bubble_y = top + max((self.AVATAR_SIZE - spec.height) // 2, 0)
        fill = self.theme.user_bubble if is_user else self.theme.bot_bubble
        text_fill = self.theme.deep if is_user else self.theme.indigo_text
        label = (
            tr("zh-CN", "docs.demo.avatar.user")
            if is_user
            else tr("zh-CN", "docs.demo.avatar.bot")
        )
        avatar_fill = self.theme.accent if is_user else self.theme.indigo

        if is_user:
            self._draw_avatar(draw, x=avatar_x, y=top, label=label, fill=avatar_fill)
        else:
            self._draw_bot_avatar(image, draw, x=avatar_x, y=top)
        draw.rounded_rectangle(
            (bubble_x, bubble_y, bubble_x + bubble_width, bubble_y + spec.height),
            radius=self.BUBBLE_RADIUS,
            fill=fill,
        )
        speaker = "USER" if is_user else "BOT"
        label_fill = self.theme.strong if is_user else self.theme.indigo
        label_box = self._text_size(speaker, self.eyebrow_font)
        self._draw_text(
            draw,
            x=bubble_x + self.BUBBLE_PADDING_X,
            y=bubble_y + self.BUBBLE_PADDING_Y - label_box[1],
            text=speaker,
            font=self.eyebrow_font,
            fill=label_fill,
        )
        self._draw_multiline_text(
            draw,
            x=bubble_x + self.BUBBLE_PADDING_X,
            y=(
                bubble_y
                + self.BUBBLE_PADDING_Y
                + self._font_line_height(self.eyebrow_font)
                + self.BUBBLE_LABEL_GAP
            ),
            lines=spec.lines,
            font=self.body_font,
            fill=text_fill,
        )

    def _draw_avatar(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        label: str,
        fill: str,
    ) -> None:
        draw.ellipse((x, y, x + self.AVATAR_SIZE, y + self.AVATAR_SIZE), fill=fill)
        bbox = self._text_size(label, self.meta_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        self._draw_text(
            draw,
            x=x + (self.AVATAR_SIZE - text_width) / 2,
            y=y + (self.AVATAR_SIZE - text_height) / 2 - 2,
            text=label,
            font=self.meta_font,
            fill=self.theme.avatar_text,
        )

    def _draw_bot_avatar(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
    ) -> None:
        if self.senrin_avatar is None:
            self._draw_avatar(
                draw,
                x=x,
                y=y,
                label=tr("zh-CN", "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        avatar = self.senrin_avatar
        mask = Image.new("L", avatar.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, avatar.width - 1, avatar.height - 1), fill=255)
        draw.ellipse(
            (x, y, x + self.AVATAR_SIZE, y + self.AVATAR_SIZE),
            fill=self.theme.bot_avatar_bg,
            outline=self.theme.bot_avatar_border,
            width=2,
        )
        image.paste(avatar, (x, y), mask)

    def _draw_header_standee(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        if self.senrin_standee is None:
            self._draw_avatar(
                draw,
                x=1088,
                y=128,
                label=tr("zh-CN", "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        image.paste(
            self.senrin_standee,
            (self.HEADER_STANDEE_X, self.HEADER_STANDEE_Y),
            self.senrin_standee,
        )

    def _load_asset(
        self,
        path: Path,
        size: int,
        *,
        alpha: int = 255,
    ) -> Image.Image | None:
        if not path.exists():
            return None
        try:
            image = Image.open(path).convert("RGBA")
        except OSError:
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        if alpha < 255:
            image = image.copy()
            alpha_channel = image.getchannel("A")
            alpha_channel = alpha_channel.point(
                [value * alpha // 255 for value in range(256)]
            )
            image.putalpha(alpha_channel)
        return image

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        top: int,
        plugin_title: str,
        plugin_version: str,
        plugin_author: str,
    ) -> None:
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN + 28,
                top,
                self.WIDTH - self.OUTER_MARGIN - 28,
                top + self.FOOTER_HEIGHT,
            ),
            radius=18,
            fill=self.theme.footer_bg,
        )
        footer_rect = (
            self.OUTER_MARGIN + 28,
            top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            top + self.FOOTER_HEIGHT,
        )
        right_text = self.FOOTER_RIGHT_TEXT
        right_bbox = self._text_size(right_text, self.footer_font)
        right_width = int(right_bbox[2] - right_bbox[0])
        right_rect = (
            footer_rect[2] - self.FOOTER_SIDE_PADDING - right_width,
            footer_rect[1],
            footer_rect[2] - self.FOOTER_SIDE_PADDING,
            footer_rect[3],
        )
        left_text = self._fit_text(
            draw,
            f"{plugin_title} · v{plugin_version.lstrip('v')} · {plugin_author}",
            self.footer_font,
            max_width=right_rect[0]
            - footer_rect[0]
            - self.FOOTER_SIDE_PADDING
            - self.FOOTER_TEXT_GAP,
        )
        self._draw_text_centered(
            draw,
            footer_rect,
            left_text,
            font=self.footer_font,
            fill=self.theme.hint,
            align="left",
            padding_x=self.FOOTER_SIDE_PADDING,
        )
        self._draw_text_centered(
            draw,
            right_rect,
            right_text,
            font=self.footer_font,
            fill=self.theme.hint,
            align="right",
        )

    def _draw_chip(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        fill: str,
        text_fill: str,
        font: Any,
        min_width: int = 0,
    ) -> None:
        rect = self._chip_rect(
            draw, x=x, y=y, text=text, font=font, min_width=min_width
        )
        draw.rounded_rectangle(rect, radius=self.theme.chip_radius, fill=fill)
        self._draw_text_centered(
            draw,
            rect,
            text,
            font=font,
            fill=text_fill,
        )

    def _draw_inline_chip(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        max_width: int,
        fill: str,
        text_fill: str,
        font: Any,
        min_width: int = 0,
    ) -> None:
        line = self._fit_inline_spans(split_inline_text_spans(text), font, max_width)
        rect = self._inline_chip_rect(
            x=x,
            y=y,
            line=line,
            font=font,
            min_width=min_width,
        )
        draw.rounded_rectangle(rect, radius=self.theme.chip_radius, fill=fill)
        line_width = self._inline_line_width(line, font)
        line_height = self._font_line_height(font)
        draw_x = rect[0] + (rect[2] - rect[0] - line_width) / 2
        draw_y = rect[1] + (rect[3] - rect[1] - line_height) / 2
        self._draw_inline_text_line(
            draw,
            x=draw_x,
            y=draw_y,
            line=line,
            font=font,
            fill=text_fill,
        )

    def audit(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
    ) -> tuple[str, ...]:
        errors: list[str] = []
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = self._conversation_height(turn_specs)
        panel_top = self.OUTER_MARGIN + self.HEADER_HEIGHT + self.BODY_TOP_GAP
        body_top = panel_top + self.BODY_PADDING_Y
        panel_bottom = body_top + conversation_height + self.BODY_PADDING_Y
        footer_top = panel_bottom + self.FOOTER_TOP_GAP
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        draw = ImageDraw.Draw(
            Image.new("RGB", (self.WIDTH, height), self.theme.panel_bg)
        )

        hero_rect = (
            self.OUTER_MARGIN,
            self.OUTER_MARGIN,
            self.WIDTH - self.OUTER_MARGIN - 28,
            self.OUTER_MARGIN + self.HEADER_HEIGHT,
        )
        panel_rect = (
            self.OUTER_MARGIN + 28,
            panel_top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            panel_bottom,
        )
        shell_rect = (
            self.OUTER_MARGIN,
            self.OUTER_MARGIN,
            self.WIDTH - self.OUTER_MARGIN,
            height - self.OUTER_MARGIN,
        )

        title_rect = self._text_rect(
            draw,
            self.HEADER_LEFT,
            self.HEADER_TITLE_TOP,
            self._fit_text(draw, plugin_title, self.title_font, max_width=720),
            self.title_font,
        )
        feature_rect = self._text_rect(
            draw,
            self.HEADER_LEFT,
            self.HEADER_FEATURE_TOP,
            self._fit_text(draw, feature_title, self.feature_font, max_width=720),
            self.feature_font,
        )
        plugin_chip_rect = self._chip_rect(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_CHIP_TOP,
            text="PLUGIN DEMO",
            font=self.eyebrow_font,
        )
        steps_chip_rect = self._chip_rect(
            draw,
            x=self.HEADER_STEPS_X,
            y=self.HEADER_CHIP_TOP,
            text=f"{len(turns)} STEP{'S' if len(turns) != 1 else ''}",
            font=self.eyebrow_font,
            min_width=154,
        )
        accent_rect = (96, 94, 106, 190)
        header_standee_rect = (
            self.HEADER_STANDEE_X,
            self.HEADER_STANDEE_Y,
            self.HEADER_STANDEE_X + self.HEADER_STANDEE_SIZE,
            self.HEADER_STANDEE_Y + self.HEADER_STANDEE_SIZE,
        )
        trigger_rect: tuple[int, int, int, int] | None = None
        if feature_trigger.strip():
            trigger_example = tr(
                locale,
                "docs.feature.trigger_example",
                command=feature_trigger,
            )
            trigger_rect = self._inline_chip_rect(
                x=self.HEADER_LEFT,
                y=self.HEADER_TRIGGER_TOP,
                line=self._fit_inline_spans(
                    split_inline_text_spans(trigger_example),
                    self.meta_font,
                    760,
                ),
                font=self.meta_font,
                min_width=300,
            )

        self._ensure_inside(hero_rect, plugin_chip_rect, "plugin chip", errors)
        self._ensure_inside(hero_rect, title_rect, "plugin title", errors)
        self._ensure_inside(hero_rect, feature_rect, "feature title", errors)
        self._ensure_inside(hero_rect, steps_chip_rect, "steps chip", errors)
        self._ensure_inside(hero_rect, header_standee_rect, "header standee", errors)
        if trigger_rect is not None:
            self._ensure_inside(hero_rect, trigger_rect, "trigger chip", errors)

        self._ensure_no_overlap(
            accent_rect, title_rect, "accent bar", "plugin title", errors
        )
        self._ensure_no_overlap(
            accent_rect, feature_rect, "accent bar", "feature title", errors
        )
        self._ensure_no_overlap(
            plugin_chip_rect, title_rect, "plugin chip", "plugin title", errors
        )
        self._ensure_no_overlap(
            title_rect, feature_rect, "plugin title", "feature title", errors
        )
        self._ensure_no_overlap(
            steps_chip_rect, header_standee_rect, "steps chip", "header standee", errors
        )
        self._ensure_no_overlap(
            title_rect, steps_chip_rect, "plugin title", "steps chip", errors
        )
        self._ensure_no_overlap(
            feature_rect, header_standee_rect, "feature title", "header standee", errors
        )
        if trigger_rect is not None:
            self._ensure_no_overlap(
                feature_rect, trigger_rect, "feature title", "trigger chip", errors
            )
            self._ensure_no_overlap(
                trigger_rect,
                header_standee_rect,
                "trigger chip",
                "header standee",
                errors,
            )

        y = body_top
        prior_rects: list[tuple[str, tuple[int, int, int, int]]] = []
        for index, spec in enumerate(turn_specs, start=1):
            for name, rect in self._turn_rects(spec, y):
                self._ensure_inside(panel_rect, rect, f"turn {index} {name}", errors)
                for prior_name, prior_rect in prior_rects:
                    self._ensure_no_overlap(
                        prior_rect,
                        rect,
                        prior_name,
                        f"turn {index} {name}",
                        errors,
                        padding=4,
                    )
                prior_rects.append((f"turn {index} {name}", rect))
            y += spec.height + self.TURN_GAP

        footer_rect = (
            self.OUTER_MARGIN + 28,
            footer_top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            footer_top + self.FOOTER_HEIGHT,
        )
        self._ensure_inside(shell_rect, footer_rect, "footer bar", errors)
        footer_right_bbox = self._text_size(self.FOOTER_RIGHT_TEXT, self.footer_font)
        footer_right_width = int(footer_right_bbox[2] - footer_right_bbox[0])
        footer_right_rect = (
            footer_rect[2] - self.FOOTER_SIDE_PADDING - footer_right_width,
            footer_rect[1],
            footer_rect[2] - self.FOOTER_SIDE_PADDING,
            footer_rect[3],
        )
        self._ensure_inside(footer_rect, footer_right_rect, "footer right text", errors)
        _ = plugin_version, plugin_author
        return tuple(errors)

    def _turn_rects(
        self,
        spec: "_TurnSpec",
        top: int,
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        if spec.turn.speaker == "SYSTEM":
            left = (self.WIDTH - spec.width) // 2
            right = left + spec.width
            return [("system bubble", (left, top, right, top + spec.height))]

        is_user = spec.turn.speaker == "USER"
        bubble_width = spec.width
        avatar_x = (
            self.WIDTH
            - self.OUTER_MARGIN
            - self.CONVERSATION_SIDE_PADDING
            - self.AVATAR_SIZE
            if is_user
            else self.OUTER_MARGIN + self.CONVERSATION_SIDE_PADDING
        )
        bubble_x = (
            avatar_x - 18 - bubble_width
            if is_user
            else avatar_x + self.AVATAR_SIZE + 18
        )
        bubble_y = top + max((self.AVATAR_SIZE - spec.height) // 2, 0)
        return [
            (
                "avatar",
                (avatar_x, top, avatar_x + self.AVATAR_SIZE, top + self.AVATAR_SIZE),
            ),
            (
                "bubble",
                (bubble_x, bubble_y, bubble_x + bubble_width, bubble_y + spec.height),
            ),
        ]

    def _text_rect(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        font: Any,
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = self._text_size(text, font)
        return (
            int(x + left),
            int(y + top),
            int(x + right),
            int(y + bottom),
        )

    def _draw_text_centered(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
        text: str,
        *,
        font: Any,
        fill: str,
        align: Literal["center", "left", "right"] = "center",
        padding_x: int = 0,
    ) -> None:
        bbox = self._text_size(text, font)
        text_width = int(bbox[2] - bbox[0])
        text_height = int(bbox[3] - bbox[1])
        if align == "left":
            x = rect[0] + padding_x
        elif align == "right":
            x = rect[2] - text_width
        else:
            x = rect[0] + (rect[2] - rect[0] - text_width) / 2
        y = rect[1] + (rect[3] - rect[1] - text_height) / 2 - bbox[1]
        self._draw_text(draw, x=x, y=y, text=text, font=font, fill=fill)

    def _chip_rect(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        font: Any,
        min_width: int = 0,
    ) -> tuple[int, int, int, int]:
        bbox = self._text_size(text, font)
        width = max(int(bbox[2] - bbox[0] + 28), min_width)
        height = max(int(bbox[3] - bbox[1] + 18), self.CHIP_HEIGHT)
        return (x, y, x + width, y + height)

    def _conversation_height(self, turn_specs: Sequence["_TurnSpec"]) -> int:
        return sum(spec.height for spec in turn_specs) + self.TURN_GAP * max(
            len(turn_specs) - 1,
            0,
        )

    def _ensure_inside(
        self,
        outer: tuple[int, int, int, int],
        inner: tuple[int, int, int, int],
        label: str,
        errors: list[str],
    ) -> None:
        if (
            inner[0] < outer[0]
            or inner[1] < outer[1]
            or inner[2] > outer[2]
            or inner[3] > outer[3]
        ):
            errors.append(f"{label} exceeds its container bounds")

    def _ensure_no_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        a_label: str,
        b_label: str,
        errors: list[str],
        *,
        padding: int = 0,
    ) -> None:
        if self._boxes_overlap(a, b, padding=padding):
            errors.append(f"{a_label} overlaps {b_label}")

    def _boxes_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        *,
        padding: int = 0,
    ) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return not (
            ax2 + padding <= bx1
            or bx2 + padding <= ax1
            or ay2 + padding <= by1
            or by2 + padding <= ay1
        )

    def _fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        *,
        max_width: int,
    ) -> str:
        if self._text_width(text, font) <= max_width:
            return text

        ellipsis = "..."
        current = text
        while current:
            current = current[:-1]
            candidate = current.rstrip() + ellipsis
            if self._text_width(candidate, font) <= max_width:
                return candidate
        return ellipsis

    def _normalize_demo_text(self, text: str) -> str:
        return text

    def _wrap_inline_text(
        self,
        text: str,
        *,
        max_width: int,
        font: Any,
    ) -> list[tuple[InlineTextSpan, ...]]:
        lines: list[tuple[InlineTextSpan, ...]] = []
        for paragraph in text.splitlines():
            current: list[InlineTextSpan] = []
            for span in split_inline_text_spans(paragraph):
                for char in span.text:
                    candidate = self._append_inline_char(
                        current,
                        char,
                        code=span.code,
                    )
                    if (
                        not current
                        or self._inline_line_width(candidate, font) <= max_width
                    ):
                        current = candidate
                        continue
                    lines.append(tuple(current))
                    current = [InlineTextSpan(char, code=span.code)]
            lines.append(tuple(current))
        return lines or [split_inline_text_spans(text)]

    def _line_block_height(
        self,
        lines: Iterable[tuple[InlineTextSpan, ...]],
        font: Any,
    ) -> int:
        count = 0
        for _ in lines:
            count += 1
        if count == 0:
            return 0
        return count * self._font_line_height(font) - 10

    def _max_inline_line_width(
        self,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
    ) -> int:
        return int(
            max(
                (self._inline_line_width(line, font) for line in lines),
                default=0,
            )
        )

    def _draw_multiline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
        fill: str,
    ) -> None:
        line_height = self._font_line_height(font)
        for index, line in enumerate(lines):
            self._draw_inline_text_line(
                draw,
                x=x,
                y=y + index * line_height,
                line=line,
                font=font,
                fill=fill,
            )

    def _draw_inline_text_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        line: Sequence[InlineTextSpan],
        font: Any,
        fill: str,
    ) -> None:
        cursor_x = x
        line_height = self._font_line_height(font)
        for span in line:
            if not span.text:
                continue
            if not span.code:
                self._draw_text(
                    draw,
                    x=cursor_x,
                    y=y,
                    text=span.text,
                    font=font,
                    fill=fill,
                )
                cursor_x += self._text_width(span.text, font)
                continue
            text_bbox = self._text_size(span.text, font)
            text_width = int(text_bbox[2] - text_bbox[0])
            text_height = int(text_bbox[3] - text_bbox[1])
            chip_height = max(text_height + self.theme.inline_code_pad_y * 2, 22)
            chip_y = y + max((line_height - chip_height) / 2, 0)
            chip_width = text_width + self.theme.inline_code_pad_x * 2
            draw.rounded_rectangle(
                (
                    cursor_x,
                    chip_y,
                    cursor_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=self.theme.inline_code_radius,
                fill=self.theme.inline_code_bg,
            )
            text_y = chip_y + (chip_height - text_height) / 2 - text_bbox[1]
            self._draw_text(
                draw,
                x=cursor_x + self.theme.inline_code_pad_x,
                y=text_y,
                text=span.text,
                font=font,
                fill=self.theme.inline_code_text,
            )
            cursor_x += chip_width

    def _font_line_height(self, font: Any) -> int:
        bbox = self._text_size("Ag", font)
        return int(bbox[3] - bbox[1] + 10)

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        text: str,
        font: Any,
        fill: str,
    ) -> None:
        if not text:
            return
        if not self._contains_emoji(text):
            draw.text((x, y), text, font=font, fill=fill)
            return
        text_box = self._text_size(text, font)
        text_width = max(text_box[2] - text_box[0] + 8, 1)
        text_height = max(text_box[3] - text_box[1] + 8, 1)
        text_layer = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
        BuildImage(text_layer).draw_text(
            (0, 0),
            text,
            font_size=self._font_size(font),
            fill=fill,
            font_families=self.FONT_FAMILIES,
            stroke_ratio=0,
        )
        draw._image.paste(text_layer, (int(x), int(y)), text_layer)

    def _text_size(self, text: str, font: Any) -> tuple[int, int, int, int]:
        if not text:
            return (0, 0, 0, self._font_line_height(font))
        if not self._contains_emoji(text):
            draw = ImageDraw.Draw(Image.new("RGB", (10, 10), self.theme.panel_bg))
            bbox = draw.textbbox((0, 0), text, font=font)
            return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        text_image = Text2Image.from_text(
            text,
            self._font_size(font),
            fill=self.theme.deep,
            stroke_width=0,
            font_families=self.FONT_FAMILIES,
        )
        return (0, 0, ceil(text_image.longest_line), ceil(text_image.height))

    def _text_width(self, text: str, font: Any) -> int:
        return self._text_size(text, font)[2]

    def _inline_line_width(
        self,
        line: Sequence[InlineTextSpan],
        font: Any,
    ) -> int:
        width = 0
        for span in line:
            if not span.text:
                continue
            width += self._text_width(span.text, font)
            if span.code:
                width += self.theme.inline_code_pad_x * 2
        return width

    def _append_inline_char(
        self,
        spans: Sequence[InlineTextSpan],
        char: str,
        *,
        code: bool,
    ) -> list[InlineTextSpan]:
        updated = list(spans)
        if updated and updated[-1].code is code:
            updated[-1] = InlineTextSpan(updated[-1].text + char, code=code)
        else:
            updated.append(InlineTextSpan(char, code=code))
        return updated

    def _inline_chip_rect(
        self,
        *,
        x: int,
        y: int,
        line: Sequence[InlineTextSpan],
        font: Any,
        min_width: int = 0,
    ) -> tuple[int, int, int, int]:
        width = max(self._inline_line_width(line, font) + 28, min_width)
        height = max(self._font_line_height(font) + 8, self.CHIP_HEIGHT)
        return (x, y, x + int(width), y + int(height))

    def _fit_inline_spans(
        self,
        spans: Sequence[InlineTextSpan],
        font: Any,
        max_width: int,
    ) -> tuple[InlineTextSpan, ...]:
        if self._inline_line_width(spans, font) <= max_width:
            return tuple(spans)

        ellipsis = InlineTextSpan("...", code=False)
        current: list[InlineTextSpan] = list(spans)
        while current:
            last = current[-1]
            if len(last.text) > 1:
                current[-1] = InlineTextSpan(last.text[:-1], code=last.code)
                if not current[-1].text:
                    current.pop()
            else:
                current.pop()
            candidate = [*current, ellipsis]
            if self._inline_line_width(candidate, font) <= max_width:
                return tuple(candidate)
        return (ellipsis,)

    def _font_size(self, font: Any) -> int:
        return int(getattr(font, "size", 16))

    def _contains_emoji(self, text: str) -> bool:
        return any(
            "\U0001f000" <= char <= "\U0001faff" or char == "\ufe0f" for char in text
        )


@dataclass(slots=True, frozen=True)
class _TurnSpec:
    turn: DocsDemoTurn
    lines: list[tuple[InlineTextSpan, ...]]
    width: int
    height: int


@dataclass(slots=True, frozen=True)
class _ShowcaseNoteItem:
    rect: tuple[int, int, int, int]
    lines: tuple[tuple[InlineTextSpan, ...], ...]
    line_height: int
    dot_color: str


@dataclass(slots=True, frozen=True)
class _ShowcaseTurnSpec:
    turn: DocsDemoTurn
    lines: tuple[tuple[InlineTextSpan, ...], ...]
    width: int
    height: int
    line_height: int


@dataclass(slots=True, frozen=True)
class _ShowcaseTurnPlacement:
    spec: _ShowcaseTurnSpec
    rect: tuple[int, int, int, int]
    avatar_rect: tuple[int, int, int, int] | None
    bubble_rect: tuple[int, int, int, int] | None
    text_rect: tuple[int, int, int, int]


@dataclass(slots=True, frozen=True)
class _ShowcaseLayout:
    plugin_title: str
    feature_title: str
    feature_summary: str
    plugin_version: str
    plugin_author: str
    pills: tuple[tuple[str, str, str], ...]
    pill_rects: tuple[tuple[int, int, int, int], ...]
    plugin_rect: tuple[int, int, int, int]
    plugin_lines: tuple[tuple[InlineTextSpan, ...], ...]
    title_rect: tuple[int, int, int, int]
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_rect: tuple[int, int, int, int]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    hero_rect: tuple[int, int, int, int]
    standee_rect: tuple[int, int, int, int]
    instruction_rect: tuple[int, int, int, int]
    trigger_rect: tuple[int, int, int, int]
    trigger_layout: CommandLayout
    overview_rect: tuple[int, int, int, int]
    overview_lines: tuple[tuple[InlineTextSpan, ...], ...]
    note_items: tuple[_ShowcaseNoteItem, ...]
    instruction_content_rects: tuple[tuple[int, int, int, int], ...]
    demo_heading_rect: tuple[int, int, int, int] | None
    demo_rect: tuple[int, int, int, int]
    turn_placements: tuple[_ShowcaseTurnPlacement, ...]
    footer_rect: tuple[int, int, int, int]
    footer_left_text: str
    footer_right_text: str
    total_height: int


class DemoImageRenderer:
    """Render plugin docs feature demos as a single-canvas showcase infographic."""

    WIDTH = DEFAULT_DEMO_THEME.canvas_width
    OUTER_MARGIN = DEFAULT_DEMO_THEME.outer_margin
    FONT_FAMILIES: ClassVar[list[str]] = [MAPLE_FONT_NAME]
    COMMAND_INDENT_PX = 48

    def __init__(self, *, impression_color: str | None = None) -> None:
        self.theme_name = SENRIN_V3_THEME.name
        self.impression_color = normalize_hex_color(impression_color)
        self.theme = get_demo_theme(
            theme_name=self.theme_name,
            impression_color=self.impression_color,
        )
        try:
            self.kicker_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.eyebrow_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 64)
            self.summary_font = ImageFont.truetype(MAPLE_FONT_PATH, 36)
            self.instruction_font = ImageFont.truetype(MAPLE_FONT_PATH, 28)
            self.body_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)
            self.note_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.footer_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
            self.system_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
        except OSError:
            self.kicker_font = ImageFont.load_default()
            self.eyebrow_font = ImageFont.load_default()
            self.title_font = ImageFont.load_default()
            self.summary_font = ImageFont.load_default()
            self.instruction_font = ImageFont.load_default()
            self.body_font = ImageFont.load_default()
            self.note_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.footer_font = ImageFont.load_default()
            self.system_font = ImageFont.load_default()
        self.senrin_avatar = self._load_asset(DEMO_AVATAR_PATH, self.theme.avatar_size)
        self.senrin_standee = self._load_asset(
            DEMO_STANDEE_PATH,
            self.theme.hero_standee_size,
            alpha=255,
        )

    def render(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_summary: str,
        feature_trigger: str,
        feature_overview: str,
        feature_preconditions: str,
        feature_failures: str,
        feature_flow_notes: str,
        plugin_trigger: str,
        feature_permission: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
        generated_at: datetime | None = None,
    ) -> bytes:
        layout = self._measure_layout(
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_summary=feature_summary,
            feature_trigger=feature_trigger,
            feature_overview=feature_overview,
            feature_preconditions=feature_preconditions,
            feature_failures=feature_failures,
            feature_flow_notes=feature_flow_notes,
            plugin_trigger=plugin_trigger,
            feature_permission=feature_permission,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
            turns=turns,
            locale=locale,
            generated_at=generated_at,
        )
        image = Image.new("RGBA", (self.WIDTH, layout.total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_hero(image, draw, layout)
        self._draw_instruction_card(image, draw, layout)
        self._draw_demo(image, draw, layout, locale=locale)
        self._draw_footer(draw, layout)

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def audit(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_summary: str,
        feature_trigger: str,
        feature_overview: str,
        feature_preconditions: str,
        feature_failures: str,
        feature_flow_notes: str,
        plugin_trigger: str,
        feature_permission: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
        generated_at: datetime | None = None,
    ) -> tuple[str, ...]:
        layout = self._measure_layout(
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_summary=feature_summary,
            feature_trigger=feature_trigger,
            feature_overview=feature_overview,
            feature_preconditions=feature_preconditions,
            feature_failures=feature_failures,
            feature_flow_notes=feature_flow_notes,
            plugin_trigger=plugin_trigger,
            feature_permission=feature_permission,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
            turns=turns,
            locale=locale,
            generated_at=generated_at,
        )
        errors: list[str] = []
        canvas = (0, 0, self.WIDTH, layout.total_height)
        hero_safe = (
            self.theme.hero_side_padding,
            self.theme.hero_top,
            self.WIDTH - self.theme.hero_side_padding,
            layout.hero_rect[3],
        )
        instruction_inner = (
            layout.instruction_rect[0] + self.theme.instruction_padding_x,
            layout.instruction_rect[1] + self.theme.instruction_padding_y,
            layout.instruction_rect[2] - self.theme.instruction_padding_x,
            layout.instruction_rect[3] - self.theme.instruction_padding_y,
        )
        self._ensure_inside(canvas, layout.hero_rect, "hero section", errors)
        self._ensure_inside(
            canvas, layout.instruction_rect, "instruction section", errors
        )
        self._ensure_inside(canvas, layout.footer_rect, "footer section", errors)
        for index, rect in enumerate(layout.pill_rects, start=1):
            self._ensure_inside(hero_safe, rect, f"hero pill {index}", errors)
        self._ensure_inside(hero_safe, layout.plugin_rect, "plugin kicker", errors)
        self._ensure_inside(hero_safe, layout.title_rect, "hero title", errors)
        self._ensure_inside(hero_safe, layout.summary_rect, "hero summary", errors)
        self._ensure_inside(canvas, layout.standee_rect, "hero standee", errors)
        self._ensure_inside(
            instruction_inner, layout.trigger_rect, "trigger block", errors
        )
        if self._boxes_overlap(layout.title_rect, layout.standee_rect, padding=12):
            errors.append("hero title overlaps standee")
        if self._boxes_overlap(layout.summary_rect, layout.standee_rect, padding=12):
            errors.append("hero summary overlaps standee")
        for index, rect in enumerate(layout.instruction_content_rects, start=1):
            self._ensure_inside(
                instruction_inner, rect, f"instruction block {index}", errors
            )
        prior_rects: list[tuple[str, tuple[int, int, int, int]]] = []
        for index, placement in enumerate(layout.turn_placements, start=1):
            for name, rect in self._turn_rects(placement):
                self._ensure_inside(
                    layout.demo_rect, rect, f"turn {index} {name}", errors
                )
                for prior_name, prior_rect in prior_rects:
                    self._ensure_no_overlap(
                        prior_rect,
                        rect,
                        prior_name,
                        f"turn {index} {name}",
                        errors,
                        padding=4,
                    )
                prior_rects.append((f"turn {index} {name}", rect))
        return tuple(errors)

    def preview_crop_box(
        self, image_size: tuple[int, int]
    ) -> tuple[int, int, int, int]:
        width, height = image_size
        side = max(0, self.theme.hero_side_padding - 16)
        if height >= 1600:
            top = int(height * 0.34)
            bottom = int(height * 0.84)
        elif height >= 1200:
            top = int(height * 0.24)
            bottom = int(height * 0.82)
        else:
            top = int(height * 0.14)
            bottom = int(height * 0.84)
        bottom = max(top + 1, bottom)
        return (side, top, max(side + 1, width - side), min(height, bottom))

    def _measure_layout(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_summary: str,
        feature_trigger: str,
        feature_overview: str,
        feature_preconditions: str,
        feature_failures: str,
        feature_flow_notes: str,
        plugin_trigger: str,
        feature_permission: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode,
        generated_at: datetime | None,
    ) -> _ShowcaseLayout:
        _ = locale
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        standee_size = self.theme.hero_standee_size
        text_max_width = (
            self.WIDTH - side * 2 - standee_size - self.theme.hero_content_gap
        )
        pills_list = [
            ("PLUGIN DOCS", self.theme.pill_blue_bg, self.theme.pill_blue_text),
            (
                plugin_trigger or "文档指引",
                self.theme.pill_blue_bg,
                self.theme.pill_blue_text,
            ),
            (plugin_author, self.theme.pill_pink_bg, self.theme.pill_pink_text),
        ]
        if feature_permission.strip() and feature_permission != "普通用户":
            pills_list.insert(
                1,
                (
                    feature_permission,
                    self.theme.pill_pink_bg,
                    self.theme.pill_pink_text,
                ),
            )
        pills = tuple(pills_list)
        pill_rects: list[tuple[int, int, int, int]] = []
        x = side
        y = self.theme.hero_top
        row_bottom = y + self.theme.pill_height
        for text, _, _ in pills:
            pill_width = self._pill_width(text, self.eyebrow_font)
            if x > side and x + pill_width > side + text_max_width:
                x = side
                y += self.theme.pill_height + self.theme.pill_gap
            rect = (x, y, x + pill_width, y + self.theme.pill_height)
            pill_rects.append(rect)
            x += pill_width + self.theme.pill_gap
            row_bottom = max(row_bottom, rect[3])

        plugin_lines = tuple(
            self._wrap_inline_text(
                plugin_title.strip() or "插件文档",
                max_width=text_max_width,
                font=self.kicker_font,
            )[:1]
        )
        plugin_y = row_bottom + 24
        plugin_rect = (
            side,
            plugin_y,
            side + self._max_inline_line_width(plugin_lines, self.kicker_font),
            plugin_y
            + self._line_block_height(
                plugin_lines,
                self._line_height_for_font(self.kicker_font),
            ),
        )

        title_lines = tuple(
            self._wrap_inline_text(
                feature_title.strip() or plugin_title.strip() or "功能说明",
                max_width=text_max_width,
                font=self.title_font,
            )[:2]
        )
        title_y = plugin_rect[3] + 24
        title_rect = (
            side,
            title_y,
            side + self._max_inline_line_width(title_lines, self.title_font),
            title_y
            + self._line_block_height(
                title_lines,
                self._line_height_for_font(self.title_font),
            ),
        )

        summary_source = (
            feature_summary.strip()
            or feature_overview.strip()
            or "查看触发方式、前置条件与实机演示。"
        )
        summary_lines = tuple(
            self._wrap_inline_text(
                summary_source,
                max_width=text_max_width,
                font=self.summary_font,
            )[:4]
        )
        summary_y = title_rect[3] + self.theme.hero_text_gap
        summary_rect = (
            side,
            summary_y,
            side + self._max_inline_line_width(summary_lines, self.summary_font),
            summary_y
            + self._line_block_height(
                summary_lines,
                self._line_height_for_font(
                    self.summary_font,
                    minimum=self.theme.hero_summary_line_height,
                ),
            ),
        )

        instruction_top = summary_rect[3] + self.theme.section_gap
        standee_y = instruction_top - standee_size + self.theme.hero_standee_overlap
        min_standee_top = self.theme.hero_top + 16
        if standee_y < min_standee_top:
            instruction_top += min_standee_top - standee_y
            standee_y = min_standee_top
        standee_rect = (
            self.WIDTH - side - standee_size,
            standee_y,
            self.WIDTH - side,
            standee_y + standee_size,
        )
        hero_rect = (
            0,
            0,
            self.WIDTH,
            max(
                instruction_top,
                summary_rect[3] + self.theme.hero_bottom_padding,
            ),
        )

        instruction_left = side
        instruction_right = self.WIDTH - side
        content_left = instruction_left + self.theme.instruction_padding_x
        content_right = instruction_right - self.theme.instruction_padding_x
        content_width = content_right - content_left
        instruction_y = instruction_top + self.theme.instruction_padding_y

        trigger_layout = build_command_layout(
            feature_trigger.strip() or f"#help {feature_title}",
            max_width=content_width - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.body_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.body_font),
            palette=self._command_palette(),
        )
        trigger_height = self.theme.trigger_padding_y * 2 + trigger_layout.total_height
        trigger_rect = (
            content_left,
            instruction_y,
            content_right,
            instruction_y + trigger_height,
        )
        instruction_y = trigger_rect[3] + self.theme.trigger_gap

        instruction_content_rects: list[tuple[int, int, int, int]] = [trigger_rect]
        overview_lines = tuple(
            self._wrap_inline_text(
                feature_overview.strip() or summary_source,
                max_width=content_width,
                font=self.instruction_font,
            )
        )
        overview_rect = (
            content_left,
            instruction_y,
            content_left
            + self._max_inline_line_width(overview_lines, self.instruction_font),
            instruction_y
            + self._line_block_height(
                overview_lines,
                self._line_height_for_font(self.instruction_font),
            ),
        )
        instruction_content_rects.append(overview_rect)
        instruction_y = overview_rect[3]

        if feature_flow_notes.strip():
            flow_lines = tuple(
                self._wrap_inline_text(
                    feature_flow_notes.strip(),
                    max_width=content_width,
                    font=self.note_font,
                )
            )
            flow_rect = (
                content_left,
                instruction_y + self.theme.note_gap,
                content_left + self._max_inline_line_width(flow_lines, self.note_font),
                instruction_y
                + self.theme.note_gap
                + self._line_block_height(
                    flow_lines,
                    self._line_height_for_font(self.note_font),
                ),
            )
            instruction_content_rects.append(flow_rect)
            instruction_y = flow_rect[3]

        note_items = self._measure_note_items(
            feature_preconditions=feature_preconditions,
            feature_failures=feature_failures,
            feature_permission=feature_permission,
            width=content_width,
            start_y=instruction_y + self.theme.note_gap,
            x=content_left,
        )
        instruction_content_rects.extend(item.rect for item in note_items)
        instruction_bottom = (
            max((rect[3] for rect in instruction_content_rects), default=instruction_y)
            + self.theme.instruction_padding_y
        )
        instruction_rect = (
            instruction_left,
            instruction_top,
            instruction_right,
            instruction_bottom,
        )

        demo_heading_rect: tuple[int, int, int, int] | None = None
        demo_rect: tuple[int, int, int, int] | None = None
        turn_placements: list[_ShowcaseTurnPlacement] = []
        current_bottom = instruction_bottom
        if turns:
            heading_text = "看看它是怎么工作的"
            heading_top = instruction_bottom + self.theme.demo_heading_gap_top
            heading_box = self._text_size(heading_text, self.note_font)
            demo_heading_rect = (
                side,
                heading_top,
                side + heading_box[2] - heading_box[0],
                heading_top + (heading_box[3] - heading_box[1]),
            )
            demo_top = demo_heading_rect[3] + self.theme.demo_heading_gap_bottom
            demo_left = side
            demo_right = self.WIDTH - side
            y_cursor = demo_top
            for turn in turns:
                spec = self._measure_turn(turn, demo_right - demo_left)
                placement = self._place_turn(
                    spec,
                    top=y_cursor,
                    left=demo_left,
                    right=demo_right,
                )
                turn_placements.append(placement)
                y_cursor = placement.rect[3] + self.theme.bubble_gap
            demo_bottom = (
                y_cursor - self.theme.bubble_gap if turn_placements else demo_top
            )
            demo_rect = (demo_left, demo_top, demo_right, demo_bottom)
            current_bottom = demo_bottom

        footer_top = current_bottom + self.theme.footer_gap_top
        footer_rect = (
            side,
            footer_top,
            self.WIDTH - side,
            footer_top + self.theme.footer_height,
        )
        footer_left_text = (
            f"{plugin_title} · {feature_title} · "
            f"v{plugin_version.lstrip('v')} · By {plugin_author}"
        )
        footer_right_text = (
            f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin"
        )
        return _ShowcaseLayout(
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_summary=summary_source,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
            pills=pills,
            pill_rects=tuple(pill_rects),
            plugin_rect=plugin_rect,
            plugin_lines=plugin_lines,
            title_rect=title_rect,
            title_lines=title_lines,
            summary_rect=summary_rect,
            summary_lines=summary_lines,
            hero_rect=hero_rect,
            standee_rect=standee_rect,
            instruction_rect=instruction_rect,
            trigger_rect=trigger_rect,
            trigger_layout=trigger_layout,
            overview_rect=overview_rect,
            overview_lines=overview_lines,
            note_items=tuple(note_items),
            instruction_content_rects=tuple(instruction_content_rects),
            demo_heading_rect=demo_heading_rect,
            demo_rect=demo_rect
            or (
                side,
                instruction_bottom,
                self.WIDTH - side,
                instruction_bottom,
            ),
            turn_placements=tuple(turn_placements),
            footer_rect=footer_rect,
            footer_left_text=footer_left_text,
            footer_right_text=footer_right_text,
            total_height=footer_rect[3] + self.theme.outer_margin,
        )

    def _paint_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, width, height), fill=self.theme.page_bg)
        self._draw_dot_matrix(draw, width=width, height=height)
        self._draw_floating_decor(draw, width=width, height=height)

    def _draw_hero(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
    ) -> None:
        for rect, pill in zip(layout.pill_rects, layout.pills, strict=True):
            self._draw_pill(
                draw,
                rect=rect,
                text=pill[0],
                fill=pill[1],
                text_fill=pill[2],
            )
        self._draw_multiline_text(
            draw,
            x=layout.plugin_rect[0],
            y=layout.plugin_rect[1],
            lines=layout.plugin_lines,
            font=self.kicker_font,
            fill=self.theme.strong,
            line_height=self._line_height_for_font(self.kicker_font),
        )
        self._draw_multiline_text(
            draw,
            x=layout.title_rect[0],
            y=layout.title_rect[1] + self.theme.hero_title_shadow_offset_y,
            lines=layout.title_lines,
            font=self.title_font,
            fill=self.theme.hero_title_shadow,
            line_height=self._line_height_for_font(self.title_font),
        )
        self._draw_multiline_text(
            draw,
            x=layout.title_rect[0],
            y=layout.title_rect[1],
            lines=layout.title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        self._draw_multiline_text(
            draw,
            x=layout.summary_rect[0],
            y=layout.summary_rect[1],
            lines=layout.summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        self._draw_standee(image, draw, layout.standee_rect)

    def _draw_instruction_card(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
    ) -> None:
        self._draw_shadowed_rect(
            image,
            rect=layout.instruction_rect,
            radius=self.theme.instruction_radius,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        draw.rounded_rectangle(
            layout.trigger_rect,
            radius=self.theme.trigger_radius,
            fill=self.theme.terminal_bg,
        )
        self._draw_command_layout(
            draw,
            x=layout.trigger_rect[0] + self.theme.trigger_padding_x,
            y=layout.trigger_rect[1] + self.theme.trigger_padding_y,
            layout=layout.trigger_layout,
            font=self.body_font,
            default_fill=self.theme.terminal_text,
            guide_fill=self.theme.line,
        )
        self._draw_multiline_text(
            draw,
            x=layout.overview_rect[0],
            y=layout.overview_rect[1],
            lines=layout.overview_lines,
            font=self.instruction_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.instruction_font),
            render_code_chip=False,
        )
        for item in layout.note_items:
            dot_y = item.rect[1] + max(
                0,
                (item.line_height - self.theme.note_dot_size) // 2,
            )
            draw.ellipse(
                (
                    item.rect[0],
                    dot_y,
                    item.rect[0] + self.theme.note_dot_size,
                    dot_y + self.theme.note_dot_size,
                ),
                fill=item.dot_color,
            )
            self._draw_multiline_text(
                draw,
                x=item.rect[0] + 24,
                y=item.rect[1],
                lines=item.lines,
                font=self.note_font,
                fill=self.theme.note_text,
                line_height=item.line_height,
            )

    def _draw_demo(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
        *,
        locale: LocaleCode,
    ) -> None:
        if layout.demo_heading_rect is None:
            return
        self._draw_text(
            draw,
            x=layout.demo_heading_rect[0],
            y=layout.demo_heading_rect[1],
            text="看看它是怎么工作的",
            font=self.note_font,
            fill=self.theme.demo_heading,
        )
        for placement in layout.turn_placements:
            self._draw_turn(image, draw, placement, locale=locale)

    def _draw_turn(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        placement: _ShowcaseTurnPlacement,
        *,
        locale: LocaleCode,
    ) -> None:
        spec = placement.spec
        if spec.turn.speaker == "SYSTEM":
            line_y = placement.text_rect[1] + 16
            draw.line(
                (
                    placement.rect[0],
                    line_y,
                    placement.text_rect[0] - self.theme.system_line_gap,
                    line_y,
                ),
                fill=self.theme.system_line,
                width=2,
            )
            draw.line(
                (
                    placement.text_rect[2] + self.theme.system_line_gap,
                    line_y,
                    placement.rect[2],
                    line_y,
                ),
                fill=self.theme.system_line,
                width=2,
            )
            self._draw_multiline_text(
                draw,
                x=placement.text_rect[0],
                y=placement.text_rect[1],
                lines=spec.lines,
                font=self.system_font,
                fill=self.theme.system_text,
                line_height=self._line_height_for_font(self.system_font),
                align="center",
                area_width=placement.text_rect[2] - placement.text_rect[0],
                render_code_chip=False,
            )
            return

        if placement.avatar_rect is None or placement.bubble_rect is None:
            return
        if spec.turn.speaker == "USER":
            self._draw_avatar(
                draw,
                rect=placement.avatar_rect,
                label=tr(locale, "docs.demo.avatar.user"),
                fill=self.theme.accent,
            )
            self._draw_shadowed_rect(
                image,
                rect=placement.bubble_rect,
                radius=self.theme.bubble_radius,
                shadow_color=self.theme.bubble_shadow,
                shadow_offset_y=self.theme.instruction_shadow_offset_y,
                shadow_blur=18,
                fill=self.theme.user_bubble,
            )
            bubble_fill = self.theme.deep
        else:
            self._draw_bot_avatar(
                image,
                draw,
                rect=placement.avatar_rect,
                locale=locale,
            )
            draw.rounded_rectangle(
                placement.bubble_rect,
                radius=self.theme.bubble_radius,
                fill=self.theme.bot_bubble,
            )
            bubble_fill = self.theme.bot_text

        self._draw_multiline_text(
            draw,
            x=placement.text_rect[0],
            y=placement.text_rect[1],
            lines=spec.lines,
            font=self.body_font,
            fill=bubble_fill,
            line_height=self._line_height_for_font(
                self.body_font,
                minimum=self.theme.bubble_line_height,
            ),
        )

    def _draw_standee(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
    ) -> None:
        self._draw_standee_anchor(image, rect)
        if self.senrin_standee is None:
            self._draw_avatar(
                draw,
                rect=rect,
                label=tr("zh-CN", "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        image.alpha_composite(self.senrin_standee, (rect[0], rect[1]))

    def _draw_avatar(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        label: str,
        fill: str,
    ) -> None:
        draw.ellipse(rect, fill=fill)
        bbox = self._text_size(label, self.meta_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        self._draw_text(
            draw,
            x=rect[0] + ((rect[2] - rect[0]) - text_width) / 2,
            y=rect[1] + ((rect[3] - rect[1]) - text_height) / 2 - 2,
            text=label,
            font=self.meta_font,
            fill=self.theme.avatar_text,
        )

    def _draw_bot_avatar(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        locale: LocaleCode,
    ) -> None:
        if self.senrin_avatar is None:
            self._draw_avatar(
                draw,
                rect=rect,
                label=tr(locale, "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        draw.ellipse(rect, fill=self.theme.panel_bg, outline=self.theme.line, width=2)
        mask = Image.new("L", self.senrin_avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
        image.paste(self.senrin_avatar, (rect[0], rect[1]), mask)

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        layout: _ShowcaseLayout,
    ) -> None:
        divider_y = layout.footer_rect[1] + 8
        self._draw_dashed_line(
            draw,
            start=(layout.footer_rect[0], divider_y),
            end=(layout.footer_rect[2], divider_y),
            fill=self.theme.footer_divider,
            dash=10,
            gap=10,
        )
        right_bbox = self._text_size(layout.footer_right_text, self.footer_font)
        right_width = int(right_bbox[2] - right_bbox[0])
        footer_y = layout.footer_rect[1] + 28
        right_x = layout.footer_rect[2] - right_width
        left_max_width = max(120, right_x - layout.footer_rect[0] - 32)
        left_text = self._fit_text(
            ImageDraw.Draw(Image.new("RGB", (1, 1), self.theme.panel_bg)),
            layout.footer_left_text,
            self.footer_font,
            max_width=left_max_width,
        )
        self._draw_text(
            draw,
            x=layout.footer_rect[0],
            y=footer_y,
            text=left_text,
            font=self.footer_font,
            fill=self.theme.system_text,
        )
        self._draw_text(
            draw,
            x=right_x,
            y=footer_y,
            text=layout.footer_right_text,
            font=self.footer_font,
            fill=self.theme.system_text,
        )

    def _draw_shadowed_rect(
        self,
        image: Image.Image,
        *,
        rect: tuple[int, int, int, int],
        radius: int,
        shadow_color: tuple[int, int, int, int],
        shadow_offset_y: int,
        shadow_blur: int,
        fill: str,
    ) -> None:
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_rect = (
            rect[0],
            rect[1] + shadow_offset_y,
            rect[2],
            rect[3] + shadow_offset_y,
        )
        shadow_draw.rounded_rectangle(shadow_rect, radius=radius, fill=shadow_color)
        shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        image.alpha_composite(shadow)
        ImageDraw.Draw(image).rounded_rectangle(rect, radius=radius, fill=fill)

    def _draw_standee_anchor(
        self,
        image: Image.Image,
        rect: tuple[int, int, int, int],
    ) -> None:
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        anchor_size = int(min(width, height) * 0.82)
        anchor_left = rect[0] + (width - anchor_size) // 2
        anchor_top = rect[1] + int(height * 0.1)
        anchor_rect = (
            anchor_left,
            anchor_top,
            anchor_left + anchor_size,
            anchor_top + anchor_size,
        )
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_rect = (
            anchor_rect[0],
            anchor_rect[1] + self.theme.instruction_shadow_offset_y,
            anchor_rect[2],
            anchor_rect[3] + self.theme.instruction_shadow_offset_y,
        )
        shadow_draw.ellipse(shadow_rect, fill=self.theme.standee_anchor_shadow)
        shadow = shadow.filter(ImageFilter.GaussianBlur(20))
        image.alpha_composite(shadow)
        ImageDraw.Draw(image).ellipse(
            anchor_rect,
            fill=self.theme.standee_anchor_fill,
            outline=self.theme.line,
            width=1,
        )

    def _draw_dot_matrix(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        width: int,
        height: int,
    ) -> None:
        dot_fill = self._rgba(self.theme.grid_color, 38)
        radius = 1
        max_height = min(height, max(height // 2 + 160, 480))
        for y in range(self.theme.hero_top // 2, max_height, self.theme.grid_spacing):
            for x in range(
                self.theme.hero_side_padding // 2,
                width - self.theme.hero_side_padding // 2,
                self.theme.grid_spacing,
            ):
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=dot_fill,
                )

    def _draw_floating_decor(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        width: int,
        height: int,
    ) -> None:
        decor_fill = self._rgba(self.theme.decor_color, 72)
        plus_origins = (
            (self.theme.hero_side_padding - 8, self.theme.hero_top + 24),
            (width - self.theme.hero_side_padding - 184, self.theme.hero_top + 120),
            (width - self.theme.hero_side_padding - 56, height - 168),
        )
        for origin_x, origin_y in plus_origins:
            self._draw_plus_cluster(
                draw, origin_x=origin_x, origin_y=origin_y, fill=decor_fill
            )
        self._draw_hollow_circle(
            draw,
            center=(
                width - self.theme.hero_side_padding - 132,
                self.theme.hero_top + 184,
            ),
            radius=28,
            outline=decor_fill,
        )
        self._draw_zigzag(
            draw,
            start=(width - self.theme.hero_side_padding - 224, height // 2 + 88),
            fill=decor_fill,
        )

    def _draw_plus_cluster(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        origin_x: int,
        origin_y: int,
        fill: tuple[int, int, int, int],
    ) -> None:
        offsets = ((0, 0), (28, 12), (12, 32))
        for offset_x, offset_y in offsets:
            cx = origin_x + offset_x
            cy = origin_y + offset_y
            draw.line((cx - 6, cy, cx + 6, cy), fill=fill, width=2)
            draw.line((cx, cy - 6, cx, cy + 6), fill=fill, width=2)

    def _draw_hollow_circle(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        center: tuple[int, int],
        radius: int,
        outline: tuple[int, int, int, int],
    ) -> None:
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            outline=outline,
            width=3,
        )

    def _draw_zigzag(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        start: tuple[int, int],
        fill: tuple[int, int, int, int],
    ) -> None:
        x, y = start
        points = [
            (x, y),
            (x + 16, y - 8),
            (x + 32, y),
            (x + 48, y - 8),
            (x + 64, y),
        ]
        draw.line(points, fill=fill, width=3)

    def _draw_dashed_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        start: tuple[int, int],
        end: tuple[int, int],
        fill: str,
        dash: int,
        gap: int,
    ) -> None:
        x1, y = start
        x2, _ = end
        cursor = x1
        while cursor < x2:
            segment_end = min(cursor + dash, x2)
            draw.line((cursor, y, segment_end, y), fill=fill, width=1)
            cursor = segment_end + gap

    def _draw_pill(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        rect: tuple[int, int, int, int],
        text: str,
        fill: str,
        text_fill: str,
    ) -> None:
        draw.rounded_rectangle(rect, radius=(rect[3] - rect[1]) // 2, fill=fill)
        self._draw_text_centered(
            draw,
            rect,
            text,
            font=self.eyebrow_font,
            fill=text_fill,
        )

    def _command_palette(self) -> CommandPalette:
        return CommandPalette(
            root=self.theme.indigo_text,
            text=self.theme.terminal_text,
            param=self.theme.terminal_param,
            flag=self.theme.terminal_flag,
        )

    def _draw_command_layout(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        layout: CommandLayout,
        font: Any,
        default_fill: str,
        guide_fill: str,
    ) -> None:
        if layout.has_guide:
            top = y + layout.line_height
            bottom = y + layout.total_height - max(layout.line_height // 4, 4)
            if bottom > top:
                guide_x = x + layout.indent_px - 18
                draw.line((guide_x, top, guide_x, bottom), fill=guide_fill, width=2)
        for index, line in enumerate(layout.lines):
            self._draw_inline_text_line(
                draw,
                x=x + line.indent_level * layout.indent_px,
                y=y + index * layout.line_height,
                line=line.segments,
                font=font,
                fill=default_fill,
                render_code_chip=False,
            )

    def _measure_note_items(
        self,
        *,
        feature_preconditions: str,
        feature_failures: str,
        feature_permission: str,
        width: int,
        start_y: int,
        x: int,
    ) -> list[_ShowcaseNoteItem]:
        items: list[_ShowcaseNoteItem] = []
        cursor_y = start_y
        source_items: list[tuple[str, str]] = []
        if feature_permission.strip() and feature_permission != "普通用户":
            source_items.append((feature_permission.strip(), self.theme.note_danger))
        source_items.extend(
            (text, self.theme.note_success)
            for text in self._split_note_lines(feature_preconditions)
        )
        source_items.extend(
            (text, self.theme.note_danger)
            for text in self._split_note_lines(feature_failures)
        )
        for text, color in source_items:
            lines = tuple(
                self._wrap_inline_text(
                    text,
                    max_width=width - 24,
                    font=self.note_font,
                )
            )
            line_height = self._line_height_for_font(self.note_font)
            height = self._line_block_height(lines, line_height)
            rect = (x, cursor_y, x + width, cursor_y + height)
            items.append(
                _ShowcaseNoteItem(
                    rect=rect,
                    lines=lines,
                    line_height=line_height,
                    dot_color=color,
                )
            )
            cursor_y = rect[3] + self.theme.note_gap
        return items

    def _split_note_lines(self, text: str) -> tuple[str, ...]:
        raw_lines = [line.strip(" -") for line in text.splitlines()]
        return tuple(line for line in raw_lines if line)

    def _place_turn(
        self,
        spec: _ShowcaseTurnSpec,
        *,
        top: int,
        left: int,
        right: int,
    ) -> _ShowcaseTurnPlacement:
        if spec.turn.speaker == "SYSTEM":
            text_width = self._max_inline_line_width(
                spec.lines,
                self.system_font,
                code_padding=False,
            )
            text_left = (left + right - text_width) // 2
            text_bottom = top + self._line_block_height(spec.lines, spec.line_height)
            return _ShowcaseTurnPlacement(
                spec=spec,
                rect=(left + 24, top, right - 24, text_bottom + 8),
                avatar_rect=None,
                bubble_rect=None,
                text_rect=(text_left, top, text_left + text_width, text_bottom),
            )

        avatar_size = self.theme.avatar_size
        bubble_gap = self.theme.avatar_gap
        if spec.turn.speaker == "USER":
            avatar_rect = (right - avatar_size, top, right, top + avatar_size)
            bubble_right = avatar_rect[0] - bubble_gap
            bubble_left = bubble_right - spec.width
        else:
            avatar_rect = (left, top, left + avatar_size, top + avatar_size)
            bubble_left = avatar_rect[2] + bubble_gap
            bubble_right = bubble_left + spec.width
        bubble_top = top + max(0, (avatar_size - spec.height) // 2)
        bubble_rect = (bubble_left, bubble_top, bubble_right, bubble_top + spec.height)
        text_rect = (
            bubble_left + self.theme.bubble_padding_x,
            bubble_top + self.theme.bubble_padding_y,
            bubble_right - self.theme.bubble_padding_x,
            bubble_top
            + self.theme.bubble_padding_y
            + self._line_block_height(spec.lines, spec.line_height),
        )
        rect = (
            min(avatar_rect[0], bubble_left),
            top,
            max(avatar_rect[2], bubble_right),
            max(avatar_rect[3], bubble_rect[3]),
        )
        return _ShowcaseTurnPlacement(
            spec=spec,
            rect=rect,
            avatar_rect=avatar_rect,
            bubble_rect=bubble_rect,
            text_rect=text_rect,
        )

    def _measure_turn(
        self,
        turn: DocsDemoTurn,
        content_width: int,
    ) -> _ShowcaseTurnSpec:
        if turn.speaker == "SYSTEM":
            lines = tuple(
                self._wrap_inline_text(
                    self._normalize_demo_text(turn.text),
                    max_width=min(content_width - 160, 720),
                    font=self.system_font,
                )
            )
            line_height = self._line_height_for_font(self.system_font)
            return _ShowcaseTurnSpec(
                turn=turn,
                lines=lines,
                width=0,
                height=self._line_block_height(lines, line_height),
                line_height=line_height,
            )

        bubble_max = min(
            760,
            content_width - self.theme.avatar_size - self.theme.avatar_gap - 80,
        )
        lines = tuple(
            self._wrap_inline_text(
                self._normalize_demo_text(turn.text),
                max_width=bubble_max - self.theme.bubble_padding_x * 2,
                font=self.body_font,
            )
        )
        line_height = self._line_height_for_font(
            self.body_font,
            minimum=self.theme.bubble_line_height,
        )
        text_height = self._line_block_height(lines, line_height)
        bubble_height = text_height + self.theme.bubble_padding_y * 2
        bubble_width = (
            self._max_inline_line_width(lines, self.body_font)
            + self.theme.bubble_padding_x * 2
        )
        return _ShowcaseTurnSpec(
            turn=turn,
            lines=lines,
            width=max(280, min(bubble_width, bubble_max)),
            height=max(bubble_height, self.theme.avatar_size),
            line_height=line_height,
        )

    def _turn_rects(
        self,
        placement: _ShowcaseTurnPlacement,
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        if placement.avatar_rect is None and placement.bubble_rect is None:
            return [("system text", placement.text_rect)]
        rects: list[tuple[str, tuple[int, int, int, int]]] = []
        if placement.avatar_rect is not None:
            rects.append(("avatar", placement.avatar_rect))
        if placement.bubble_rect is not None:
            rects.append(("bubble", placement.bubble_rect))
        return rects

    def _load_asset(
        self,
        path: Path,
        size: int,
        *,
        alpha: int = 255,
    ) -> Image.Image | None:
        if not path.exists():
            return None
        try:
            image = Image.open(path).convert("RGBA")
        except OSError:
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        if alpha < 255:
            image = image.copy()
            alpha_channel = image.getchannel("A")
            alpha_channel = alpha_channel.point(
                [value * alpha // 255 for value in range(256)]
            )
            image.putalpha(alpha_channel)
        return image

    def _draw_text_centered(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
        text: str,
        *,
        font: Any,
        fill: str,
        align: Literal["center", "left", "right"] = "center",
        padding_x: int = 0,
    ) -> None:
        bbox = self._text_size(text, font)
        text_width = int(bbox[2] - bbox[0])
        text_height = int(bbox[3] - bbox[1])
        if align == "left":
            x = rect[0] + padding_x
        elif align == "right":
            x = rect[2] - text_width
        else:
            x = rect[0] + (rect[2] - rect[0] - text_width) / 2
        y = rect[1] + (rect[3] - rect[1] - text_height) / 2 - bbox[1]
        self._draw_text(draw, x=x, y=y, text=text, font=font, fill=fill)

    def _ensure_inside(
        self,
        outer: tuple[int, int, int, int],
        inner: tuple[int, int, int, int],
        label: str,
        errors: list[str],
    ) -> None:
        if (
            inner[0] < outer[0]
            or inner[1] < outer[1]
            or inner[2] > outer[2]
            or inner[3] > outer[3]
        ):
            errors.append(f"{label} exceeds its container bounds")

    def _ensure_no_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        a_label: str,
        b_label: str,
        errors: list[str],
        *,
        padding: int = 0,
    ) -> None:
        if self._boxes_overlap(a, b, padding=padding):
            errors.append(f"{a_label} overlaps {b_label}")

    def _boxes_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        *,
        padding: int = 0,
    ) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return not (
            ax2 + padding <= bx1
            or bx2 + padding <= ax1
            or ay2 + padding <= by1
            or by2 + padding <= ay1
        )

    def _fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        *,
        max_width: int,
    ) -> str:
        _ = draw
        if self._text_width(text, font) <= max_width:
            return text
        ellipsis = "..."
        current = text
        while current:
            current = current[:-1]
            candidate = current.rstrip() + ellipsis
            if self._text_width(candidate, font) <= max_width:
                return candidate
        return ellipsis

    def _normalize_demo_text(self, text: str) -> str:
        return text

    def _wrap_inline_text(
        self,
        text: str,
        *,
        max_width: int,
        font: Any,
    ) -> list[tuple[InlineTextSpan, ...]]:
        if not text:
            return [()]
        lines: list[tuple[InlineTextSpan, ...]] = []
        for paragraph in text.splitlines():
            lines.extend(
                self._wrap_inline_spans(
                    split_inline_text_spans(paragraph),
                    max_width=max_width,
                    font=font,
                )
            )
        return lines or [()]

    def _wrap_inline_spans(
        self,
        spans: Sequence[InlineTextSpan],
        *,
        max_width: int,
        font: Any,
        code_padding: bool = True,
    ) -> list[tuple[InlineTextSpan, ...]]:
        lines: list[tuple[InlineTextSpan, ...]] = []
        current: list[InlineTextSpan] = []
        for span in spans:
            for char in span.text:
                candidate = self._append_inline_char(
                    current,
                    char,
                    code=span.code,
                    fill=span.fill,
                )
                if (
                    not current
                    or self._inline_line_width(
                        candidate,
                        font,
                        code_padding=code_padding,
                    )
                    <= max_width
                ):
                    current = candidate
                    continue
                lines.append(tuple(current))
                current = [InlineTextSpan(char, code=span.code, fill=span.fill)]
        if current or not lines:
            lines.append(tuple(current))
        return lines

    def _line_block_height(
        self,
        lines: Iterable[tuple[InlineTextSpan, ...]],
        line_height: int,
    ) -> int:
        count = sum(1 for _ in lines)
        return 0 if count == 0 else count * line_height

    def _max_inline_line_width(
        self,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
        *,
        code_padding: bool = True,
    ) -> int:
        return int(
            max(
                (
                    self._inline_line_width(
                        line,
                        font,
                        code_padding=code_padding,
                    )
                    for line in lines
                ),
                default=0,
            )
        )

    def _draw_multiline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
        fill: str,
        line_height: int,
        align: Literal["left", "center"] = "left",
        area_width: int | None = None,
        render_code_chip: bool = True,
    ) -> None:
        for index, line in enumerate(lines):
            line_x = x
            if align == "center" and area_width is not None:
                line_width = self._inline_line_width(
                    line,
                    font,
                    code_padding=render_code_chip,
                )
                line_x = x + max(0, (area_width - line_width) // 2)
            self._draw_inline_text_line(
                draw,
                x=line_x,
                y=y + index * line_height,
                line=line,
                font=font,
                fill=fill,
                render_code_chip=render_code_chip,
            )

    def _draw_inline_text_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        line: Sequence[InlineTextSpan],
        font: Any,
        fill: str,
        render_code_chip: bool = True,
    ) -> None:
        cursor_x = x
        line_height = self._font_line_height(font)
        for span in line:
            if not span.text:
                continue
            span_fill = span.fill or fill
            if not span.code or not render_code_chip:
                self._draw_text(
                    draw,
                    x=cursor_x,
                    y=y,
                    text=span.text,
                    font=font,
                    fill=span_fill,
                )
                cursor_x += self._text_width(span.text, font)
                continue
            text_bbox = self._text_size(span.text, font)
            text_width = int(text_bbox[2] - text_bbox[0])
            text_height = int(text_bbox[3] - text_bbox[1])
            chip_height = max(text_height + self.theme.inline_code_pad_y * 2, 22)
            chip_y = y + max((line_height - chip_height) / 2, 0)
            chip_width = text_width + self.theme.inline_code_pad_x * 2
            draw.rounded_rectangle(
                (
                    cursor_x,
                    chip_y,
                    cursor_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=self.theme.inline_code_radius,
                fill=self.theme.inline_code_bg,
            )
            text_y = chip_y + (chip_height - text_height) / 2 - text_bbox[1]
            self._draw_text(
                draw,
                x=cursor_x + self.theme.inline_code_pad_x,
                y=text_y,
                text=span.text,
                font=font,
                fill=span.fill or self.theme.inline_code_text,
            )
            cursor_x += chip_width

    def _font_line_height(self, font: Any) -> int:
        bbox = self._text_size("Ag", font)
        return int(bbox[3] - bbox[1] + 10)

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        text: str,
        font: Any,
        fill: str,
    ) -> None:
        if not text:
            return
        if not self._contains_emoji(text):
            draw.text((x, y), text, font=font, fill=fill)
            return
        text_box = self._text_size(text, font)
        text_width = max(text_box[2] - text_box[0] + 8, 1)
        text_height = max(text_box[3] - text_box[1] + 8, 1)
        text_layer = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
        BuildImage(text_layer).draw_text(
            (0, 0),
            text,
            font_size=self._font_size(font),
            fill=fill,
            font_families=self.FONT_FAMILIES,
            stroke_ratio=0,
        )
        draw._image.paste(text_layer, (int(x), int(y)), text_layer)

    def _text_size(self, text: str, font: Any) -> tuple[int, int, int, int]:
        if not text:
            return (0, 0, 0, self._font_line_height(font))
        if not self._contains_emoji(text):
            draw = ImageDraw.Draw(Image.new("RGB", (10, 10), self.theme.panel_bg))
            bbox = draw.textbbox((0, 0), text, font=font)
            return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        text_image = Text2Image.from_text(
            text,
            self._font_size(font),
            fill=self.theme.deep,
            stroke_width=0,
            font_families=self.FONT_FAMILIES,
        )
        return (0, 0, ceil(text_image.longest_line), ceil(text_image.height))

    def _text_width(self, text: str, font: Any) -> int:
        return self._text_size(text, font)[2]

    def _inline_line_width(
        self,
        line: Sequence[InlineTextSpan],
        font: Any,
        *,
        code_padding: bool = True,
    ) -> int:
        width = 0
        for span in line:
            if not span.text:
                continue
            width += self._text_width(span.text, font)
            if span.code and code_padding:
                width += self.theme.inline_code_pad_x * 2
        return width

    def _append_inline_char(
        self,
        spans: Sequence[InlineTextSpan],
        char: str,
        *,
        code: bool,
        fill: str | None = None,
    ) -> list[InlineTextSpan]:
        updated = list(spans)
        if updated and updated[-1].code is code and updated[-1].fill == fill:
            updated[-1] = InlineTextSpan(
                updated[-1].text + char,
                code=code,
                fill=fill,
            )
        else:
            updated.append(InlineTextSpan(char, code=code, fill=fill))
        return updated

    def _fit_inline_spans(
        self,
        spans: Sequence[InlineTextSpan],
        font: Any,
        max_width: int,
    ) -> tuple[InlineTextSpan, ...]:
        if self._inline_line_width(spans, font) <= max_width:
            return tuple(spans)
        ellipsis = InlineTextSpan("...", code=False)
        current: list[InlineTextSpan] = list(spans)
        while current:
            last = current[-1]
            if len(last.text) > 1:
                current[-1] = InlineTextSpan(
                    last.text[:-1],
                    code=last.code,
                    fill=last.fill,
                )
                if not current[-1].text:
                    current.pop()
            else:
                current.pop()
            candidate = [*current, ellipsis]
            if self._inline_line_width(candidate, font) <= max_width:
                return tuple(candidate)
        return (ellipsis,)

    def _trigger_spans(self, text: str) -> tuple[InlineTextSpan, ...]:
        spans: list[InlineTextSpan] = []
        for span in split_inline_text_spans(text):
            if not span.code:
                spans.append(
                    InlineTextSpan(
                        span.text,
                        code=False,
                        fill=self.theme.terminal_text,
                    )
                )
                continue
            for piece in re.split(r"(\[[^\]]+\]|<[^>]+>)", span.text):
                if not piece:
                    continue
                fill = (
                    self.theme.terminal_param
                    if re.fullmatch(r"(\[[^\]]+\]|<[^>]+>)", piece)
                    else self.theme.terminal_text
                )
                spans.append(InlineTextSpan(piece, code=True, fill=fill))
        return tuple(spans)

    def _pill_width(self, text: str, font: Any) -> int:
        return max(88, self._text_width(text, font) + 32)

    def _font_size(self, font: Any) -> int:
        return int(getattr(font, "size", 16))

    def _line_height_for_font(self, font: Any, *, minimum: int = 0) -> int:
        natural = ceil(self._font_size(font) * 1.4)
        return max(minimum, ceil(natural / 8) * 8)

    def _contains_emoji(self, text: str) -> bool:
        return any(
            "\U0001f000" <= char <= "\U0001faff" or char == "\ufe0f" for char in text
        )

    def _rgba(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        color = color.lstrip("#")
        return (
            int(color[0:2], 16),
            int(color[2:4], 16),
            int(color[4:6], 16),
            alpha,
        )


@dataclass(slots=True, frozen=True)
class _DashboardCardLayout:
    node: DocNode
    theme: Any
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    command_layout: CommandLayout
    content_x: int
    content_width: int
    category_rect: tuple[int, int, int, int]
    title_top: int
    title_line_height: int
    title_block_height: int
    summary_top: int
    summary_line_height: int
    summary_block_height: int
    command_rect: tuple[int, int, int, int]
    height: int


@dataclass(slots=True, frozen=True)
class _GuideSectionLayout:
    feature: FeatureDoc
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    trigger_layout: CommandLayout
    demo_layout: CommandLayout
    overview_lines: tuple[tuple[InlineTextSpan, ...], ...]
    note_items: tuple[_ShowcaseNoteItem, ...]
    turn_placements: tuple[_ShowcaseTurnPlacement, ...]
    height: int


@dataclass(slots=True, frozen=True)
class _GuideAdvancedItemLayout:
    feature: FeatureDoc
    title_lines: tuple[tuple[InlineTextSpan, ...], ...]
    summary_lines: tuple[tuple[InlineTextSpan, ...], ...]
    trigger_layout: CommandLayout
    demo_layout: CommandLayout
    height: int


class ProgressiveDisclosureRenderer(DemoImageRenderer):
    DASHBOARD_CARD_GAP_X = 32
    DASHBOARD_CARD_GAP_Y = 32
    DASHBOARD_CARD_RADIUS = 32
    DASHBOARD_CARD_PADDING_X = 40
    DASHBOARD_CARD_PADDING_Y = 40
    DASHBOARD_CARD_TEXT_SPACING = 8
    DASHBOARD_CARD_TITLE_GAP = 24
    DASHBOARD_CARD_SUMMARY_GAP = 16
    DASHBOARD_CARD_BOTTOM_SAFE_GAP = 24
    DASHBOARD_CARD_COMMAND_PADDING_X = 24
    DASHBOARD_CARD_COMMAND_PADDING_Y = 16
    DASHBOARD_CARD_SUMMARY_VISIBLE_LINES = 4
    GUIDE_SECTION_GAP = 48
    GUIDE_SECTION_PADDING_X = 48
    GUIDE_SECTION_PADDING_Y = 40
    GUIDE_SECTION_RADIUS = 32

    def render_dashboard(
        self,
        *,
        nodes: Sequence[DocNode],
        locale: LocaleCode,
        generated_at: datetime | None = None,
    ) -> bytes:
        _ = locale
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        display_nodes = tuple(nodes[:4])
        side = self.theme.hero_side_padding
        header_title = "Bot Dashboard"
        header_summary = "发送 #help 插件名，即可进入分层指引与完整演示。"
        header_title_lines = tuple(
            self._wrap_inline_text(
                header_title,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.title_font,
            )
        )
        header_summary_lines = tuple(
            self._wrap_inline_text(
                header_summary,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.summary_font,
            )
        )
        header_height = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
            + len(header_summary_lines)
            * self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            )
            + self.theme.hero_bottom_padding
        )

        card_width = (self.WIDTH - side * 2 - self.DASHBOARD_CARD_GAP_X) // 2
        cards = tuple(
            self._measure_dashboard_card(node, card_width) for node in display_nodes
        )
        placements: list[tuple[_DashboardCardLayout, int, int]] = []
        cursor_y = header_height
        for row_start in range(0, len(cards), 2):
            row = cards[row_start : row_start + 2]
            row_height = max((card.height for card in row), default=0)
            for column, card in enumerate(row):
                x = side + column * (card_width + self.DASHBOARD_CARD_GAP_X)
                placements.append((card, x, cursor_y))
            cursor_y += row_height + self.DASHBOARD_CARD_GAP_Y
        content_bottom = (
            cursor_y - self.DASHBOARD_CARD_GAP_Y if placements else header_height
        )
        footer_rect = (
            side,
            content_bottom + self.theme.footer_gap_top,
            self.WIDTH - side,
            content_bottom + self.theme.footer_gap_top + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin

        image = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        self._draw_multiline_text(
            draw,
            x=side,
            y=self.theme.hero_top,
            lines=header_title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        summary_top = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
        )
        self._draw_multiline_text(
            draw,
            x=side,
            y=summary_top,
            lines=header_summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        standee_rect = (
            self.WIDTH - side - self.theme.hero_standee_size,
            self.theme.hero_top + 8,
            self.WIDTH - side,
            self.theme.hero_top + 8 + self.theme.hero_standee_size,
        )
        self._draw_standee(image, draw, standee_rect)

        for card, x, y in placements:
            self._draw_dashboard_card(
                image, draw, card=card, x=x, y=y, width=card_width
            )

        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=(
                f"Help Center · Global Dashboard · {len(nodes)} plugins "
                "· By SakuraiSenrin"
            ),
            right_text=f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin",
        )
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def render_plugin_guide(
        self,
        *,
        node: DocNode,
        hero_features: Sequence[FeatureDoc],
        advanced_features: Sequence[FeatureDoc],
        locale: LocaleCode,
        generated_at: datetime | None = None,
    ) -> bytes:
        generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
            microsecond=0
        )
        side = self.theme.hero_side_padding
        header_title_lines = tuple(
            self._wrap_inline_text(
                node.title,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.title_font,
            )
        )
        header_summary_lines = tuple(
            self._wrap_inline_text(
                node.summary or node.bundle.summary,
                max_width=self.WIDTH - side * 2 - self.theme.hero_standee_size,
                font=self.summary_font,
            )
        )
        hero_bottom = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
            + len(header_summary_lines)
            * self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            )
            + self.theme.hero_bottom_padding
        )

        content_width = self.WIDTH - side * 2
        section_width = content_width
        hero_layouts = tuple(
            self._measure_plugin_guide_section(
                node=node,
                feature=feature,
                section_width=section_width,
            )
            for feature in hero_features
        )
        advanced_layouts = tuple(
            self._measure_advanced_item(
                node=node,
                feature=feature,
                width=section_width - self.GUIDE_SECTION_PADDING_X * 2,
            )
            for feature in advanced_features
        )

        cursor_y = hero_bottom
        section_positions: list[tuple[_GuideSectionLayout, int]] = []
        for layout in hero_layouts:
            section_positions.append((layout, cursor_y))
            cursor_y += layout.height + self.GUIDE_SECTION_GAP

        advanced_top = cursor_y if advanced_layouts else None
        advanced_height = 0
        if advanced_layouts:
            advanced_height = self.GUIDE_SECTION_PADDING_Y * 2 + 72
            advanced_height += sum(item.height for item in advanced_layouts)
            advanced_height += max(0, len(advanced_layouts) - 1) * 24
            cursor_y += advanced_height + self.GUIDE_SECTION_GAP

        footer_rect = (
            side,
            cursor_y + self.theme.footer_gap_top,
            self.WIDTH - side,
            cursor_y + self.theme.footer_gap_top + self.theme.footer_height,
        )
        total_height = footer_rect[3] + self.theme.outer_margin
        image = Image.new("RGBA", (self.WIDTH, total_height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)

        self._draw_multiline_text(
            draw,
            x=side,
            y=self.theme.hero_top,
            lines=header_title_lines,
            font=self.title_font,
            fill=self.theme.hero_title,
            line_height=self._line_height_for_font(self.title_font),
        )
        summary_top = (
            self.theme.hero_top
            + len(header_title_lines) * self._line_height_for_font(self.title_font)
            + self.theme.hero_text_gap
        )
        self._draw_multiline_text(
            draw,
            x=side,
            y=summary_top,
            lines=header_summary_lines,
            font=self.summary_font,
            fill=self.theme.hero_summary,
            line_height=self._line_height_for_font(
                self.summary_font,
                minimum=self.theme.hero_summary_line_height,
            ),
        )
        standee_rect = (
            self.WIDTH - side - self.theme.hero_standee_size,
            self.theme.hero_top + 8,
            self.WIDTH - side,
            self.theme.hero_top + 8 + self.theme.hero_standee_size,
        )
        self._draw_standee(image, draw, standee_rect)

        for layout, top in section_positions:
            self._draw_plugin_guide_section(
                image,
                draw,
                node=node,
                layout=layout,
                top=top,
                left=side,
                locale=locale,
            )

        if advanced_top is not None:
            self._draw_advanced_options(
                image,
                draw,
                node=node,
                layouts=advanced_layouts,
                top=advanced_top,
                left=side,
                width=section_width,
            )

        self._draw_trace_footer(
            draw,
            footer_rect=footer_rect,
            left_text=(
                f"{node.title} · Guide · "
                f"v{node.bundle.version.lstrip('v')} · By {node.bundle.author}"
            ),
            right_text=f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin",
        )
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _measure_dashboard_card(
        self,
        node: DocNode,
        width: int,
    ) -> _DashboardCardLayout:
        theme = get_demo_theme(
            theme_name=SENRIN_V3_THEME.name,
            impression_color=node.bundle.impression_color,
        )
        content_width = width - self.DASHBOARD_CARD_PADDING_X * 2
        title_lines = tuple(
            self._wrap_inline_text(
                node.title,
                max_width=content_width,
                font=self.summary_font,
            )
        )[:2]
        summary_lines = tuple(
            self._wrap_inline_text(
                node.summary or node.bundle.summary,
                max_width=content_width,
                font=self.note_font,
            )
        )[:3]
        palette = CommandPalette(
            root=theme.indigo_text,
            text=theme.deep,
            param=theme.pill_pink_text,
            flag=theme.note_success,
        )
        content_x = self.DASHBOARD_CARD_PADDING_X + 24
        content_width = width - content_x - self.DASHBOARD_CARD_PADDING_X
        category_top = self.DASHBOARD_CARD_PADDING_Y
        pill_height = 40
        pill_width = max(
            108,
            self._pixel_text_width(node.category.upper(), self.eyebrow_font) + 28,
        )
        category_rect = (
            content_x,
            category_top,
            content_x + pill_width,
            category_top + pill_height,
        )
        title_line_height = self._line_height_for_font(
            self.summary_font,
            minimum=self._font_pixel_height(self.summary_font)
            + self.DASHBOARD_CARD_TEXT_SPACING,
        )
        title_max_height = self._line_block_height_with_spacing(
            (
                "M",
                "M",
            ),
            self.summary_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        title_lines = self._wrap_plain_text_for_height(
            node.title,
            font=self.summary_font,
            max_width=content_width,
            max_height=title_max_height,
            line_spacing=self.DASHBOARD_CARD_TEXT_SPACING,
            ellipsis="...",
        )
        title_top = category_rect[3] + self.DASHBOARD_CARD_TITLE_GAP
        title_block_height = self._line_block_height_with_spacing(
            tuple(self._plain_text_from_line(line) for line in title_lines),
            self.summary_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        summary_top = title_top + title_block_height + self.DASHBOARD_CARD_SUMMARY_GAP
        command_layout = build_command_layout(
            f"#help {node.title}",
            max_width=content_width - self.DASHBOARD_CARD_COMMAND_PADDING_X * 2,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=palette,
        )
        command_height = (
            command_layout.total_height + self.DASHBOARD_CARD_COMMAND_PADDING_Y * 2
        )
        summary_max_height = self._line_block_height_with_spacing(
            tuple("M" for _ in range(self.DASHBOARD_CARD_SUMMARY_VISIBLE_LINES)),
            self.note_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        height = (
            summary_top
            + summary_max_height
            + self.DASHBOARD_CARD_BOTTOM_SAFE_GAP
            + command_height
            + self.DASHBOARD_CARD_PADDING_Y
        )
        command_rect = (
            content_x,
            height - self.DASHBOARD_CARD_PADDING_Y - command_height,
            width - self.DASHBOARD_CARD_PADDING_X,
            height - self.DASHBOARD_CARD_PADDING_Y,
        )
        summary_available_height = max(
            0,
            command_rect[1] - self.DASHBOARD_CARD_BOTTOM_SAFE_GAP - summary_top,
        )
        summary_lines = self._wrap_plain_text_for_height(
            node.summary or node.bundle.summary,
            font=self.note_font,
            max_width=content_width,
            max_height=summary_available_height,
            line_spacing=self.DASHBOARD_CARD_TEXT_SPACING,
            ellipsis="...",
        )
        summary_line_height = self._line_height_for_font(
            self.note_font,
            minimum=self._font_pixel_height(self.note_font)
            + self.DASHBOARD_CARD_TEXT_SPACING,
        )
        summary_block_height = self._line_block_height_with_spacing(
            tuple(self._plain_text_from_line(line) for line in summary_lines),
            self.note_font,
            self.DASHBOARD_CARD_TEXT_SPACING,
        )
        return _DashboardCardLayout(
            node=node,
            theme=theme,
            title_lines=title_lines,
            summary_lines=summary_lines,
            command_layout=command_layout,
            content_x=content_x,
            content_width=content_width,
            category_rect=category_rect,
            title_top=title_top,
            title_line_height=title_line_height,
            title_block_height=title_block_height,
            summary_top=summary_top,
            summary_line_height=summary_line_height,
            summary_block_height=summary_block_height,
            command_rect=command_rect,
            height=height,
        )

    def _draw_dashboard_card(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        card: _DashboardCardLayout,
        x: int,
        y: int,
        width: int,
    ) -> None:
        rect = (x, y, x + width, y + card.height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.DASHBOARD_CARD_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        accent_rect = (x + 24, y + 24, x + 40, y + card.height - 24)
        draw.rounded_rectangle(accent_rect, radius=8, fill=card.theme.accent)
        content_x = x + card.content_x
        category_rect = (
            x + card.category_rect[0],
            y + card.category_rect[1],
            x + card.category_rect[2],
            y + card.category_rect[3],
        )
        draw.rounded_rectangle(
            category_rect,
            radius=(category_rect[3] - category_rect[1]) // 2,
            fill=card.theme.indigo_soft,
        )
        self._draw_text_centered(
            draw,
            category_rect,
            card.node.category.upper(),
            font=self.eyebrow_font,
            fill=card.theme.indigo_text,
        )
        self._draw_multiline_text(
            draw,
            x=content_x,
            y=y + card.title_top,
            lines=card.title_lines,
            font=self.summary_font,
            fill=card.theme.deep,
            line_height=card.title_line_height,
            render_code_chip=False,
        )
        self._draw_multiline_text(
            draw,
            x=content_x,
            y=y + card.summary_top,
            lines=card.summary_lines,
            font=self.note_font,
            fill=card.theme.hint,
            line_height=card.summary_line_height,
            render_code_chip=False,
        )
        command_rect = (
            x + card.command_rect[0],
            y + card.command_rect[1],
            x + card.command_rect[2],
            y + card.command_rect[3],
        )
        draw.rounded_rectangle(command_rect, radius=20, fill=card.theme.panel_soft_bg)
        self._draw_command_layout(
            draw,
            x=command_rect[0] + self.DASHBOARD_CARD_COMMAND_PADDING_X,
            y=command_rect[1] + self.DASHBOARD_CARD_COMMAND_PADDING_Y,
            layout=card.command_layout,
            font=self.note_font,
            default_fill=card.theme.deep,
            guide_fill=card.theme.line,
        )

    def _measure_plugin_guide_section(
        self,
        *,
        node: DocNode,
        feature: FeatureDoc,
        section_width: int,
    ) -> _GuideSectionLayout:
        content_width = section_width - self.GUIDE_SECTION_PADDING_X * 2
        title_lines = tuple(
            self._wrap_inline_text(
                feature.title,
                max_width=content_width,
                font=self.summary_font,
            )
        )
        summary_lines = tuple(
            self._wrap_inline_text(
                feature.summary,
                max_width=content_width,
                font=self.instruction_font,
            )
        )[:2]
        trigger_layout = build_command_layout(
            _feature_command_for_display(node.bundle, feature, node.title),
            max_width=content_width - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.body_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.body_font),
            palette=self._command_palette(),
        )
        demo_layout = build_command_layout(
            feature_demo_help_command(node, feature),
            max_width=content_width - self.theme.trigger_padding_x * 2,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=self._command_palette(),
        )
        overview_lines = tuple(
            self._wrap_inline_text(
                feature.overview or feature.summary,
                max_width=content_width,
                font=self.instruction_font,
            )
        )[:5]
        note_items = tuple(
            self._measure_note_items(
                feature_preconditions=feature.preconditions,
                feature_failures=feature.failures,
                feature_permission=_permission_label(feature.permission),
                width=content_width,
                start_y=0,
                x=0,
            )
        )
        demo_left = self.theme.hero_side_padding
        demo_right = self.WIDTH - self.theme.hero_side_padding
        turn_placements: list[_ShowcaseTurnPlacement] = []
        y_cursor = 0
        for turn in feature.demo_turns:
            spec = self._measure_turn(turn, demo_right - demo_left)
            placement = self._place_turn(
                spec,
                top=y_cursor,
                left=demo_left,
                right=demo_right,
            )
            turn_placements.append(placement)
            y_cursor = placement.rect[3] + self.theme.bubble_gap
        demo_height = max(0, y_cursor - self.theme.bubble_gap)
        note_height = 0
        if note_items:
            note_height = note_items[-1].rect[3] - note_items[0].rect[1]
        height = (
            self.GUIDE_SECTION_PADDING_Y * 2
            + len(title_lines) * self._line_height_for_font(self.summary_font)
            + len(summary_lines) * self._line_height_for_font(self.instruction_font)
            + trigger_layout.total_height
            + demo_layout.total_height
            + len(overview_lines) * self._line_height_for_font(self.instruction_font)
            + note_height
            + demo_height
            + 220
        )
        return _GuideSectionLayout(
            feature=feature,
            title_lines=title_lines,
            summary_lines=summary_lines,
            trigger_layout=trigger_layout,
            demo_layout=demo_layout,
            overview_lines=overview_lines,
            note_items=note_items,
            turn_placements=tuple(turn_placements),
            height=height,
        )

    def _draw_plugin_guide_section(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        node: DocNode,
        layout: _GuideSectionLayout,
        top: int,
        left: int,
        locale: LocaleCode,
    ) -> None:
        width = self.WIDTH - left * 2
        rect = (left, top, left + width, top + layout.height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.GUIDE_SECTION_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        content_left = rect[0] + self.GUIDE_SECTION_PADDING_X
        cursor_y = rect[1] + self.GUIDE_SECTION_PADDING_Y
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=layout.title_lines,
            font=self.summary_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
        )
        cursor_y += (
            len(layout.title_lines) * self._line_height_for_font(self.summary_font) + 12
        )
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=layout.summary_lines,
            font=self.instruction_font,
            fill=self.theme.hint,
            line_height=self._line_height_for_font(self.instruction_font),
            render_code_chip=False,
        )
        cursor_y += (
            len(layout.summary_lines)
            * self._line_height_for_font(self.instruction_font)
            + 24
        )
        trigger_rect = (
            content_left,
            cursor_y,
            rect[2] - self.GUIDE_SECTION_PADDING_X,
            cursor_y
            + layout.trigger_layout.total_height
            + self.theme.trigger_padding_y * 2,
        )
        draw.rounded_rectangle(
            trigger_rect,
            radius=self.theme.trigger_radius,
            fill=self.theme.terminal_bg,
        )
        self._draw_command_layout(
            draw,
            x=trigger_rect[0] + self.theme.trigger_padding_x,
            y=trigger_rect[1] + self.theme.trigger_padding_y,
            layout=layout.trigger_layout,
            font=self.body_font,
            default_fill=self.theme.terminal_text,
            guide_fill=self.theme.line,
        )
        cursor_y = trigger_rect[3] + 12
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=(split_inline_text_spans("查看 demo"),),
            font=self.note_font,
            fill=self.theme.hint,
            line_height=self._line_height_for_font(self.note_font),
            render_code_chip=False,
        )
        cursor_y += self._line_height_for_font(self.note_font) + 8
        demo_rect = (
            content_left,
            cursor_y,
            rect[2] - self.GUIDE_SECTION_PADDING_X,
            cursor_y
            + layout.demo_layout.total_height
            + self.theme.trigger_padding_y * 2,
        )
        draw.rounded_rectangle(
            demo_rect,
            radius=self.theme.trigger_radius,
            fill=self.theme.panel_soft_bg,
        )
        self._draw_command_layout(
            draw,
            x=demo_rect[0] + self.theme.trigger_padding_x,
            y=demo_rect[1] + self.theme.trigger_padding_y,
            layout=layout.demo_layout,
            font=self.note_font,
            default_fill=self.theme.deep,
            guide_fill=self.theme.line,
        )
        cursor_y = demo_rect[3] + 24
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=layout.overview_lines,
            font=self.instruction_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.instruction_font),
            render_code_chip=False,
        )
        cursor_y += (
            len(layout.overview_lines)
            * self._line_height_for_font(self.instruction_font)
            + 24
        )

        if layout.note_items:
            for item in layout.note_items:
                actual_rect = (
                    content_left,
                    cursor_y,
                    rect[2] - self.GUIDE_SECTION_PADDING_X,
                    cursor_y + (item.rect[3] - item.rect[1]),
                )
                dot_y = actual_rect[1] + max(
                    0, (item.line_height - self.theme.note_dot_size) // 2
                )
                draw.ellipse(
                    (
                        actual_rect[0],
                        dot_y,
                        actual_rect[0] + self.theme.note_dot_size,
                        dot_y + self.theme.note_dot_size,
                    ),
                    fill=item.dot_color,
                )
                self._draw_multiline_text(
                    draw,
                    x=actual_rect[0] + 24,
                    y=actual_rect[1],
                    lines=item.lines,
                    font=self.note_font,
                    fill=self.theme.note_text,
                    line_height=item.line_height,
                    render_code_chip=False,
                )
                cursor_y = actual_rect[3] + self.theme.note_gap
            cursor_y += 8

        for placement in layout.turn_placements:
            shifted = _ShowcaseTurnPlacement(
                spec=placement.spec,
                rect=(
                    placement.rect[0],
                    placement.rect[1] + cursor_y,
                    placement.rect[2],
                    placement.rect[3] + cursor_y,
                ),
                avatar_rect=(
                    None
                    if placement.avatar_rect is None
                    else (
                        placement.avatar_rect[0],
                        placement.avatar_rect[1] + cursor_y,
                        placement.avatar_rect[2],
                        placement.avatar_rect[3] + cursor_y,
                    )
                ),
                bubble_rect=(
                    None
                    if placement.bubble_rect is None
                    else (
                        placement.bubble_rect[0],
                        placement.bubble_rect[1] + cursor_y,
                        placement.bubble_rect[2],
                        placement.bubble_rect[3] + cursor_y,
                    )
                ),
                text_rect=(
                    placement.text_rect[0],
                    placement.text_rect[1] + cursor_y,
                    placement.text_rect[2],
                    placement.text_rect[3] + cursor_y,
                ),
            )
            self._draw_turn(image, draw, shifted, locale=locale)

    def _measure_advanced_item(
        self,
        *,
        node: DocNode,
        feature: FeatureDoc,
        width: int,
    ) -> _GuideAdvancedItemLayout:
        title_lines = tuple(
            self._wrap_inline_text(
                feature.title,
                max_width=width,
                font=self.instruction_font,
            )
        )[:2]
        summary_lines = tuple(
            self._wrap_inline_text(
                feature.summary,
                max_width=width,
                font=self.note_font,
            )
        )[:2]
        trigger_layout = build_command_layout(
            _feature_command_for_display(node.bundle, feature, node.title),
            max_width=width,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=self._command_palette(),
        )
        demo_layout = build_command_layout(
            feature_demo_help_command(node, feature),
            max_width=width,
            line_height=self._line_height_for_font(self.note_font),
            indent_px=self.COMMAND_INDENT_PX,
            measure_text=lambda value: self._text_width(value, self.note_font),
            palette=self._command_palette(),
        )
        height = (
            len(title_lines) * self._line_height_for_font(self.instruction_font)
            + len(summary_lines) * self._line_height_for_font(self.note_font)
            + trigger_layout.total_height
            + demo_layout.total_height
            + 48
        )
        return _GuideAdvancedItemLayout(
            feature=feature,
            title_lines=title_lines,
            summary_lines=summary_lines,
            trigger_layout=trigger_layout,
            demo_layout=demo_layout,
            height=height,
        )

    def _draw_advanced_options(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        node: DocNode,
        layouts: Sequence[_GuideAdvancedItemLayout],
        top: int,
        left: int,
        width: int,
    ) -> None:
        total_height = self.GUIDE_SECTION_PADDING_Y * 2 + 72
        total_height += sum(layout.height for layout in layouts)
        total_height += max(0, len(layouts) - 1) * 24
        rect = (left, top, left + width, top + total_height)
        self._draw_shadowed_rect(
            image,
            rect=rect,
            radius=self.GUIDE_SECTION_RADIUS,
            shadow_color=self.theme.card_shadow,
            shadow_offset_y=self.theme.instruction_shadow_offset_y,
            shadow_blur=self.theme.instruction_shadow_blur,
            fill=self.theme.panel_bg,
        )
        content_left = rect[0] + self.GUIDE_SECTION_PADDING_X
        cursor_y = rect[1] + self.GUIDE_SECTION_PADDING_Y
        title_lines = tuple(
            self._wrap_inline_text(
                "Advanced Options",
                max_width=width - self.GUIDE_SECTION_PADDING_X * 2,
                font=self.summary_font,
            )
        )
        self._draw_multiline_text(
            draw,
            x=content_left,
            y=cursor_y,
            lines=title_lines,
            font=self.summary_font,
            fill=self.theme.deep,
            line_height=self._line_height_for_font(self.summary_font),
        )
        cursor_y += (
            len(title_lines) * self._line_height_for_font(self.summary_font) + 24
        )
        for layout in layouts:
            self._draw_multiline_text(
                draw,
                x=content_left,
                y=cursor_y,
                lines=layout.title_lines,
                font=self.instruction_font,
                fill=self.theme.deep,
                line_height=self._line_height_for_font(self.instruction_font),
            )
            cursor_y += (
                len(layout.title_lines)
                * self._line_height_for_font(self.instruction_font)
                + 8
            )
            self._draw_multiline_text(
                draw,
                x=content_left,
                y=cursor_y,
                lines=layout.summary_lines,
                font=self.note_font,
                fill=self.theme.hint,
                line_height=self._line_height_for_font(self.note_font),
                render_code_chip=False,
            )
            cursor_y += (
                len(layout.summary_lines) * self._line_height_for_font(self.note_font)
                + 12
            )
            trigger_rect = (
                content_left,
                cursor_y,
                rect[2] - self.GUIDE_SECTION_PADDING_X,
                cursor_y
                + layout.trigger_layout.total_height
                + self.theme.trigger_padding_y * 2,
            )
            draw.rounded_rectangle(
                trigger_rect,
                radius=self.theme.trigger_radius,
                fill=self.theme.terminal_bg,
            )
            self._draw_command_layout(
                draw,
                x=trigger_rect[0] + self.theme.trigger_padding_x,
                y=trigger_rect[1] + self.theme.trigger_padding_y,
                layout=layout.trigger_layout,
                font=self.note_font,
                default_fill=self.theme.terminal_text,
                guide_fill=self.theme.line,
            )
            cursor_y = trigger_rect[3] + 10
            demo_rect = (
                content_left,
                cursor_y,
                rect[2] - self.GUIDE_SECTION_PADDING_X,
                cursor_y
                + layout.demo_layout.total_height
                + self.theme.trigger_padding_y * 2,
            )
            draw.rounded_rectangle(
                demo_rect,
                radius=self.theme.trigger_radius,
                fill=self.theme.panel_soft_bg,
            )
            self._draw_command_layout(
                draw,
                x=demo_rect[0] + self.theme.trigger_padding_x,
                y=demo_rect[1] + self.theme.trigger_padding_y,
                layout=layout.demo_layout,
                font=self.note_font,
                default_fill=self.theme.accent,
                guide_fill=self.theme.line,
            )
            cursor_y = demo_rect[3] + 24

    def _draw_trace_footer(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        footer_rect: tuple[int, int, int, int],
        left_text: str,
        right_text: str,
    ) -> None:
        divider_y = footer_rect[1] + 8
        self._draw_dashed_line(
            draw,
            start=(footer_rect[0], divider_y),
            end=(footer_rect[2], divider_y),
            fill=self.theme.footer_divider,
            dash=10,
            gap=10,
        )
        footer_y = footer_rect[1] + 28
        right_bbox = self._text_size(right_text, self.footer_font)
        right_width = int(right_bbox[2] - right_bbox[0])
        right_x = footer_rect[2] - right_width
        left_max_width = max(120, right_x - footer_rect[0] - 32)
        left_fitted = self._truncate_text_to_width_pixels(
            left_text,
            self.footer_font,
            max_width=left_max_width,
            ellipsis="...",
        )
        self._draw_text(
            draw,
            x=footer_rect[0],
            y=footer_y,
            text=left_fitted,
            font=self.footer_font,
            fill=self.theme.system_text,
        )
        self._draw_text(
            draw,
            x=right_x,
            y=footer_y,
            text=right_text,
            font=self.footer_font,
            fill=self.theme.system_text,
        )

    def _wrap_plain_text_for_height(
        self,
        text: str,
        *,
        font: Any,
        max_width: int,
        max_height: int,
        line_spacing: int,
        ellipsis: str = "...",
    ) -> tuple[tuple[InlineTextSpan, ...], ...]:
        normalized = text.strip()
        if not normalized:
            return ((),)

        raw_lines = self._wrap_plain_text_pixels(
            normalized,
            font=font,
            max_width=max_width,
            max_height=max_height,
            line_spacing=line_spacing,
            ellipsis=ellipsis,
        )
        return tuple(
            ((InlineTextSpan(line, code=False),) if line else ())
            for line in raw_lines
        )

    def _wrap_plain_text_pixels(
        self,
        text: str,
        *,
        font: Any,
        max_width: int,
        max_height: int,
        line_spacing: int,
        ellipsis: str = "...",
    ) -> tuple[str, ...]:
        if max_width <= 0 or max_height <= 0:
            return (self._truncate_text_to_width_pixels(text, font, max_width=0),)

        lines: list[str] = []
        used_height = 0
        paragraphs = [part.strip() for part in text.splitlines()] or [text]

        for paragraph in paragraphs:
            remaining = paragraph
            while remaining:
                line, rest = self._fit_plain_text_line_pixels(
                    remaining,
                    font=font,
                    max_width=max_width,
                )
                line_height = self._pixel_text_height(line or "Ag", font)
                projected_height = used_height + (
                    line_height if not lines else line_spacing + line_height
                )
                if projected_height > max_height:
                    if lines:
                        lines[-1] = self._truncate_text_to_width_pixels(
                            lines[-1],
                            font,
                            max_width=max_width,
                            ellipsis=ellipsis,
                        )
                        return tuple(lines)
                    return (
                        self._truncate_text_to_width_pixels(
                            remaining,
                            font,
                            max_width=max_width,
                            ellipsis=ellipsis,
                        ),
                    )

                if rest:
                    next_height = (
                        projected_height
                        + line_spacing
                        + self._pixel_text_height("Ag", font)
                    )
                    if next_height > max_height:
                        lines.append(
                            self._truncate_text_to_width_pixels(
                                line,
                                font,
                                max_width=max_width,
                                ellipsis=ellipsis,
                            )
                        )
                        return tuple(lines)

                lines.append(line)
                used_height = projected_height
                remaining = rest

        return tuple(lines or [""])

    def _fit_plain_text_line_pixels(
        self,
        text: str,
        *,
        font: Any,
        max_width: int,
    ) -> tuple[str, str]:
        if not text:
            return "", ""
        if max_width <= 0:
            return "", text

        current_chars: list[str] = []
        current_width = 0
        last_break_after = -1
        allow_mid_word_break = self._looks_like_url(text)

        for index, char in enumerate(text):
            char_width = self._pixel_text_width(char, font)
            if current_chars and current_width + char_width > max_width:
                split_at = len(current_chars)
                if not allow_mid_word_break and last_break_after > 0:
                    split_at = last_break_after
                line = "".join(current_chars[:split_at]).rstrip()
                carry = "".join(current_chars[split_at:]) + text[index:]
                return line or "".join(current_chars), carry.lstrip()

            current_chars.append(char)
            current_width += char_width
            if self._is_wrap_boundary(char):
                last_break_after = len(current_chars)

        return "".join(current_chars).rstrip(), ""

    def _truncate_text_to_width_pixels(
        self,
        text: str,
        font: Any,
        *,
        max_width: int,
        ellipsis: str = "...",
    ) -> str:
        if max_width <= 0:
            return ellipsis
        if self._pixel_text_width(text, font) <= max_width:
            return text

        ellipsis_width = self._pixel_text_width(ellipsis, font)
        if ellipsis_width >= max_width:
            return ellipsis

        current_chars: list[str] = []
        current_width = 0
        for char in text:
            char_width = self._pixel_text_width(char, font)
            if (
                current_chars
                and current_width + char_width + ellipsis_width > max_width
            ):
                break
            if not current_chars and char_width + ellipsis_width > max_width:
                break
            current_chars.append(char)
            current_width += char_width

        candidate = "".join(current_chars).rstrip()
        while (
            candidate
            and self._pixel_text_width(candidate + ellipsis, font) > max_width
        ):
            candidate = candidate[:-1].rstrip()
        return f"{candidate}{ellipsis}" if candidate else ellipsis

    def _pixel_text_width(self, text: str, font: Any) -> int:
        if not text:
            return 0
        if not self._contains_emoji(text) and hasattr(font, "getlength"):
            return ceil(float(font.getlength(text)))
        return self._text_width(text, font)

    def _pixel_text_height(self, text: str, font: Any) -> int:
        sample = text or "Ag"
        if not self._contains_emoji(sample) and hasattr(font, "getbbox"):
            bbox = font.getbbox(sample)
            return int(bbox[3] - bbox[1])
        bbox = self._text_size(sample, font)
        return int(bbox[3] - bbox[1])

    def _font_pixel_height(self, font: Any) -> int:
        return self._pixel_text_height("Ag", font)

    def _line_block_height_with_spacing(
        self,
        lines: Sequence[str],
        font: Any,
        line_spacing: int,
    ) -> int:
        if not lines:
            return 0
        total = 0
        for index, line in enumerate(lines):
            total += self._pixel_text_height(line or "Ag", font)
            if index < len(lines) - 1:
                total += line_spacing
        return total

    def _plain_text_from_line(self, line: Sequence[InlineTextSpan]) -> str:
        return "".join(span.text for span in line)

    def _is_wrap_boundary(self, char: str) -> bool:
        return char.isspace() or char in "-/_,.;:|)]}>"

    def _looks_like_url(self, text: str) -> bool:
        lowered = text.lower()
        return "://" in lowered or lowered.startswith("www.") or "www." in lowered
