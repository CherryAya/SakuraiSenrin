"""Project-wide plugin docs engine, README parser, and demo rendering helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
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
from PIL import Image, ImageDraw, ImageFont
from pil_utils import BuildImage
from pil_utils.text2image import Text2Image

from src.database.core.consts import Permission
from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.demo_theme import BASE_THEME
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .consts import TriggerType

DEMO_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEMO_AVATAR_PATH = DEMO_ASSETS_DIR / "senrin-demo-avatar.png"
DEMO_STANDEE_PATH = DEMO_ASSETS_DIR / "senrin-demo-standee.png"
DEFAULT_HELP_CATEGORY = "general"

type DocsResult = Message | Awaitable[Message] | str | Awaitable[str]
type DocsProvider = Callable[..., DocsResult]
type DocNodeKind = Literal["plugin", "overview", "internal"]
type DocRenderView = Literal["text", "index", "plugin", "feature"]


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

    @property
    def search_tokens(self) -> set[str]:
        return {
            self.slug.lower(),
            self.title.lower(),
            *(alias.lower() for alias in self.aliases),
        }


@dataclass(slots=True, frozen=True)
class PluginDocBundle:
    title: str
    description: str
    summary: str
    trigger: str
    permission: str
    author: str
    version: str
    index: tuple[FeatureDoc, ...]
    source_path: Path


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
) -> PluginDocBundle:
    source_path = Path(source).resolve()
    return _load_plugin_doc_bundle_cached(
        source_path,
        default_name,
        default_description,
        trigger,
        permission,
    )


@lru_cache(maxsize=256)
def _load_plugin_doc_bundle_cached(
    source_path: Path,
    default_name: str,
    default_description: str,
    trigger: TriggerType,
    permission: Permission,
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
) -> bytes | None:
    """加载代表性 demo 图片，优先使用集合图片，否则使用第一个有权限的功能 demo"""
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


def render_demo_png(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes:
    return DemoImageRenderer().render(
        plugin_title=bundle.title,
        feature_title=feature.title,
        feature_trigger=feature.trigger,
        plugin_version=bundle.version,
        plugin_author=bundle.author,
        turns=feature.demo_turns,
        locale="zh-CN",
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


def audit_demo_layout(bundle: PluginDocBundle, feature: FeatureDoc) -> tuple[str, ...]:
    return DemoImageRenderer().audit(
        plugin_title=bundle.title,
        feature_title=feature.title,
        feature_trigger=feature.trigger,
        plugin_version=bundle.version,
        plugin_author=bundle.author,
        turns=feature.demo_turns,
        locale="zh-CN",
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


class DemoImageRenderer:
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
        self.theme = BASE_THEME
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
        draw.rounded_rectangle((74, 66, 112, height - 76), radius=19, fill="#FFF4F7")
        draw.rounded_rectangle(
            (width - 142, 126, width - 86, height - 124),
            radius=28,
            fill="#F1F4FF",
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
            fill="#FFFFFF",
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
            fill="#F7FAFF",
            outline="#D8E3FF",
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
        draw = ImageDraw.Draw(Image.new("RGB", (self.WIDTH, height), "#FFFFFF"))

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
            draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#FFFFFF"))
            bbox = draw.textbbox((0, 0), text, font=font)
            return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        text_image = Text2Image.from_text(
            text,
            self._font_size(font),
            fill="#000000",
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
