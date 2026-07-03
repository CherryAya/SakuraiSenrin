"""Project-wide plugin docs engine, README parser, and demo rendering helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token
from nonebot.adapters.onebot.v11.message import Message
from nonebot.plugin import PluginMetadata

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.demo_theme import (
    DEFAULT_IMPRESSION_COLOR,
    normalize_hex_color,
)
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import (
    ImageBytesBlock,
    MessagePlanEntry,
    TextBlock,
    render_message_plan_entry,
)
from src.lib.utils.common import get_current_time

from .command_layout import (
    CommandLayout,
    CommandPalette,
    InlineTextSpan,
)
from .command_layout import (
    build_command_layout as build_command_layout_impl,
)
from .command_layout import (
    normalize_inline_text as normalize_inline_text_impl,
)
from .command_layout import (
    split_inline_text_spans as split_inline_text_spans_impl,
)

DEMO_ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
DEMO_AVATAR_PATH = DEMO_ASSETS_DIR / "senrin-demo-avatar.png"
DEMO_STANDEE_PATH = DEMO_ASSETS_DIR / "senrin-demo-standee.png"
DEFAULT_HELP_CATEGORY = "general"

DocsResult = Message | Awaitable[Message] | str | Awaitable[str]
DocsProvider = Callable[..., DocsResult]
HelpEntryShape = Literal[
    "simple_leaf",
    "plugin_guide",
    "overview_group",
    "static_entry",
]

from .copy import (
    build_feature_copy_text as build_feature_copy_text_impl,
)
from .copy import (
    build_plugin_guide_copy_text as build_plugin_guide_copy_text_impl,
)
from .copy import (
    build_plugin_summary_copy_text as build_plugin_summary_copy_text_impl,
)
from .copy import (
    build_simple_leaf_copy_text as build_simple_leaf_copy_text_impl,
)
from .copy import (
    build_static_entry_copy_text as build_static_entry_copy_text_impl,
)
from .copy import (
    feature_command_for_display as feature_command_for_display_impl,
)
from .copy import (
    feature_command_sections as feature_command_sections_impl,
)
from .copy import (
    feature_notice_items as feature_notice_items_impl,
)
from .copy import (
    format_feature_command_lines as format_feature_command_lines_impl,
)
from .copy import (
    node_help_command as node_help_command_impl,
)
from .meta import (
    coerce_permission as coerce_permission_impl,
)
from .meta import (
    create_docs_meta as create_docs_meta_impl,
)
from .meta import (
    derive_tree_identity_from_source as derive_tree_identity_from_source_impl,
)
from .meta import (
    extract_metadata_field as extract_metadata_field_impl,
)
from .meta import (
    normalize_docs_meta as normalize_docs_meta_impl,
)
from .meta import (
    read_docs_meta as read_docs_meta_impl,
)
from .meta import (
    read_docs_metas as read_docs_metas_impl,
)
from .meta import (
    resolve_doc_impression_color as resolve_doc_impression_color_impl,
)
from .meta import (
    resolve_doc_owner_module_path as resolve_doc_owner_module_path_impl,
)
from .meta import (
    resolve_doc_signature as resolve_doc_signature_impl,
)
from .meta import (
    resolve_main_group_id as resolve_main_group_id_impl,
)
from .meta import (
    support_note as support_note_impl,
)
from .meta import (
    support_text_block as support_text_block_impl,
)
from .models import (
    DocNode,
    DocNodeKind,
    DocsDemoTurn,
    DocsMeta,
    DocsRenderContext,
    DocTree,
    FeatureDoc,
    FeatureMatchResult,
    HelpDashboardSection,
    MarkdownSection,
    NodeMatchResult,
    PluginDocBundle,
    VirtualPluginDocSpec,
)
from .query import (
    build_doc_tree as build_doc_tree_impl,
)
from .query import (
    build_virtual_plugin_doc_bundle as build_virtual_plugin_doc_bundle_impl,
)
from .query import (
    can_view_node as can_view_node_impl,
)
from .query import (
    filter_features_by_permission as filter_features_by_permission_impl,
)
from .query import (
    load_virtual_doc_node as load_virtual_doc_node_impl,
)
from .query import (
    match_doc_node as match_doc_node_impl,
)
from .query import (
    match_feature as match_feature_impl,
)
from .query import (
    permission_allows as permission_allows_impl,
)
from .query import (
    rank_features_for_disclosure as rank_features_for_disclosure_impl,
)
from .query import (
    split_features_for_disclosure as split_features_for_disclosure_impl,
)
from .readme import (
    extract_heading_sections as extract_heading_sections_impl,
)
from .readme import (
    extract_title as extract_title_impl,
)
from .readme import (
    merge_features as merge_features_impl,
)
from .readme import (
    normalize_heading as normalize_heading_impl,
)
from .readme import (
    parse_bool_meta as parse_bool_meta_impl,
)
from .readme import (
    parse_demo_turns as parse_demo_turns_impl,
)
from .readme import (
    parse_feature_details_tokens as parse_feature_details_tokens_impl,
)
from .readme import (
    parse_feature_heading as parse_feature_heading_impl,
)
from .readme import (
    parse_feature_index_tokens as parse_feature_index_tokens_impl,
)
from .readme import (
    parse_flow_section_tokens as parse_flow_section_tokens_impl,
)
from .readme import (
    parse_int_meta as parse_int_meta_impl,
)
from .readme import (
    parse_meta_block_tokens as parse_meta_block_tokens_impl,
)
from .readme import (
    parse_permission as parse_permission_impl,
)
from .readme import (
    render_inline_markdown as render_inline_markdown_impl,
)
from .readme import (
    render_markdown_blocks as render_markdown_blocks_impl,
)
from .readme import (
    split_csv as split_csv_impl,
)
from .readme import (
    split_tokens_before_heading as split_tokens_before_heading_impl,
)
from .render.demo import DemoImageRenderer, build_trace_footer_left_text
from .render.progressive import ProgressiveDisclosureRenderer
from .static_assets import (
    dashboard_target_key,
    feature_target_key,
    guide_target_key,
    load_static_asset_bytes,
    static_target_key,
    summary_target_key,
)


def _support_note(locale: LocaleCode) -> str:
    return support_note_impl(locale)


def _support_text_block(locale: LocaleCode) -> str:
    return support_text_block_impl(locale)


type HelpHomeSectionKind = Literal["system", "developer", "community"]

HELP_HOME_SECTION_TITLE_KEYS: dict[HelpHomeSectionKind, MessageKey] = {
    "system": "help.dashboard.section.system",
    "developer": "help.dashboard.section.developer",
    "community": "help.dashboard.section.community",
}


def _help_home_section_title(
    section: HelpHomeSectionKind,
    locale: LocaleCode,
) -> str:
    key = HELP_HOME_SECTION_TITLE_KEYS[section]
    return tr(locale, key)


def _help_home_section_style(section: HelpHomeSectionKind) -> dict[str, str]:
    return {
        "system": {
            "accent": "#8AB4F8",
            "panel_bg": "#F4F8FF",
            "panel_soft_bg": "#EAF2FF",
            "text": "#2F5E9E",
            "hint": "#5F7EA8",
            "command_bg": "#EAF2FF",
            "command_text": "#315F9D",
            "marker": "square",
        },
        "developer": {
            "accent": "#FFB067",
            "panel_bg": "#FFF9F2",
            "panel_soft_bg": "#FFF1DE",
            "text": "#9A5B1D",
            "hint": "#8C7254",
            "command_bg": "#FFF0DE",
            "command_text": "#8A531F",
            "marker": "diamond",
        },
        "community": {
            "accent": "#FF9EBB",
            "panel_bg": "#FFF5F8",
            "panel_soft_bg": "#FFE8EF",
            "text": "#A24E6A",
            "hint": "#946476",
            "command_bg": "#FFE7EF",
            "command_text": "#9A4B68",
            "marker": "ring",
        },
    }[section]


def _resolve_main_group_id() -> str:
    return resolve_main_group_id_impl()


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
    return create_docs_meta_impl(
        visible=visible,
        category=category or DEFAULT_HELP_CATEGORY,
        order=order,
        source=source,
        hidden=hidden,
        slug=slug,
        parent_slug=parent_slug,
        kind=kind,
        internal=internal,
        aliases=aliases,
    )


def read_docs_meta(metadata: PluginMetadata) -> DocsMeta | None:
    return read_docs_meta_impl(metadata)


def read_docs_metas(metadata: PluginMetadata) -> tuple[DocsMeta, ...]:
    return read_docs_metas_impl(metadata)


def _normalize_docs_meta(
    raw: dict[str, Any],
    *,
    default_permission: Permission | int | str,
) -> DocsMeta | None:
    return normalize_docs_meta_impl(raw, default_permission=default_permission)


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
    return render_message_plan_entry(
        build_static_docs_plan_entry(
            name=name,
            description=description,
            content=content,
            name_key=name_key,
            description_key=description_key,
            content_key=content_key,
            trigger=trigger,
            permission=permission,
            locale=locale,
        )
    )


def build_static_docs_plan_entry(
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
) -> MessagePlanEntry:
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
    return _build_text_plan_entry(
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
    return render_message_plan_entry(
        build_readme_docs_plan_entry(
            source=source,
            name=name,
            description=description,
            trigger=trigger,
            permission=permission,
            ctx=ctx,
        )
    )


def build_readme_docs_plan_entry(
    *,
    source: str | Path,
    name: str,
    description: str,
    trigger: TriggerType,
    permission: Permission,
    ctx: DocsRenderContext | None = None,
) -> MessagePlanEntry:
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
            return render_doc_feature_plan_entry(
                node,
                match.feature,
                locale=locale,
                include_demo=ctx.include_demo,
            )
        if match.status == "ambiguous":
            return _build_text_plan_entry(
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
        return _build_text_plan_entry(
            tr(
                locale,
                "docs.feature.not_found",
                query=ctx.feature_query,
            ).strip()
        )
    include_demo = (
        ctx.include_demo if ctx is not None and ctx.view == "plugin" else False
    )
    return render_doc_node_overview_plan_entry(
        node,
        locale=locale,
        include_demo=include_demo,
        actor_permission=actor_permission,
    )


def build_doc_tree(nodes: Sequence[DocNode]) -> DocTree:
    return build_doc_tree_impl(nodes)


def match_doc_node(nodes: Sequence[DocNode], query: str) -> NodeMatchResult:
    return match_doc_node_impl(nodes, query)


def build_help_home_sections(
    nodes: Sequence[DocNode],
    *,
    locale: LocaleCode,
    actor_permission: Permission = Permission.NORMAL,
) -> tuple[HelpDashboardSection, ...]:
    roots = [
        node
        for node in build_doc_tree(nodes).roots()
        if node.visible
        and not node.hidden
        and not node.internal
        and can_view_node(node, actor_permission)
    ]
    buckets: dict[HelpHomeSectionKind, list[DocNode]] = {
        "system": [],
        "developer": [],
        "community": [],
    }
    for node in roots:
        if node.category == "community" or node.slug.startswith("derived."):
            buckets["community"].append(node)
        elif node.module_name.startswith("src.hooks.") or node.slug.startswith("hook."):
            buckets["system"].append(node)
        elif node.slug in {"help", "notice", "admin"}:
            buckets["system"].append(node)
        else:
            buckets["developer"].append(node)

    sections: list[HelpDashboardSection] = []
    for kind in ("system", "developer", "community"):
        section_nodes = buckets[kind]
        if not section_nodes:
            continue
        style = _help_home_section_style(kind)
        sections.append(
            HelpDashboardSection(
                kind=kind,
                title=_help_home_section_title(kind, locale),
                nodes=tuple(section_nodes),
                accent=style["accent"],
                panel_bg=style["panel_bg"],
                panel_soft_bg=style["panel_soft_bg"],
                text=style["text"],
                hint=style["hint"],
                command_bg=style["command_bg"],
                command_text=style["command_text"],
                marker=style["marker"],
            )
        )
    return tuple(sections)


def build_help_home_text(
    sections: Sequence[HelpDashboardSection],
    *,
    locale: LocaleCode,
) -> str:
    if not sections:
        return tr(locale, "help.index.empty")

    lines = [
        tr(locale, "help.dashboard.title"),
        "----------",
        tr(locale, "help.dashboard.lead.line1"),
        tr(locale, "help.dashboard.lead.line2"),
        "----------",
    ]
    for section in sections:
        lines.append(section.title)
        for node in section.nodes:
            lines.append(node_help_command(node))
        lines.append("----------")
    lines.extend(["", _support_text_block(locale)])
    return "\n".join(lines).strip()


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
    if meta["kind"] == "static":
        bundle = type(bundle)(
            title=bundle.title,
            description=bundle.description,
            summary=bundle.summary,
            trigger=bundle.trigger,
            help_query=bundle.help_query,
            permission=bundle.permission,
            author=bundle.author,
            version=bundle.version,
            impression_color=bundle.impression_color,
            index=(),
            source_path=bundle.source_path,
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
        permission=_coerce_permission(meta["permission"])
        if meta.get("permission") not in (None, Permission.NORMAL)
        else permission,
        title=bundle.title or default_name,
        summary=bundle.summary or default_description,
        description=default_description,
        help_query=bundle.help_query,
        aliases=meta["aliases"],
        source_path=source_path,
        bundle=bundle,
        module_name=module_name,
        plugin_name=plugin_name,
    )


def build_virtual_plugin_doc_bundle(spec: VirtualPluginDocSpec) -> PluginDocBundle:
    return build_virtual_plugin_doc_bundle_impl(spec)


def load_virtual_doc_node(spec: VirtualPluginDocSpec) -> DocNode:
    return load_virtual_doc_node_impl(spec)


def render_doc_node_overview(
    node: DocNode,
    *,
    locale: LocaleCode,
    include_demo: bool = False,
    actor_permission: Permission = Permission.NORMAL,
    children: Sequence[DocNode] = (),
) -> Message:
    return render_message_plan_entry(
        render_doc_node_overview_plan_entry(
            node,
            locale=locale,
            include_demo=include_demo,
            actor_permission=actor_permission,
            children=children,
        )
    )


def render_doc_node_overview_plan_entry(
    node: DocNode,
    *,
    locale: LocaleCode,
    include_demo: bool = False,
    actor_permission: Permission = Permission.NORMAL,
    children: Sequence[DocNode] = (),
) -> MessagePlanEntry:
    lines = [node.title, ""]
    if node.summary:
        lines.extend([node.summary, ""])

    visible_children = tuple(
        child for child in children if can_view_node(child, actor_permission)
    )
    visible_features = filter_features_by_permission(node.features, actor_permission)

    if visible_children:
        lines.append("可用子模块：")
        for child in visible_children:
            lines.append(child.title)
            lines.append(f"#help {child.title}")
        lines.append("")
    elif visible_features:
        lines.append("可用功能：")
        for feature in visible_features:
            lines.append(feature.title)
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
            "",
            _support_text_block(locale),
        ]
    )
    message = _build_text_plan_entry("\n".join(lines).strip())
    if not include_demo:
        return message
    demo_bytes = load_representative_demo_bytes(
        bundle=node.bundle,
        actor_permission=actor_permission,
        prefer_collection=should_prefer_collection_demo(
            node,
            actor_permission=actor_permission,
            children=children,
        ),
    )
    if demo_bytes is None:
        return message
    return _append_image_plan_entry(message, demo_bytes)


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
    if node.kind == "static":
        return "static_entry"

    if not node.features:
        return "static_entry"

    visible_features = filter_features_by_permission(node.features, actor_permission)
    if len(visible_features) <= 1:
        return "simple_leaf"

    if len(visible_features) <= 2 and all(
        not feature.advanced for feature in visible_features
    ):
        return "simple_leaf"

    return "plugin_guide"


def should_prefer_collection_demo(
    node: DocNode,
    *,
    actor_permission: Permission = Permission.NORMAL,
    children: Sequence[DocNode] = (),
) -> bool:
    return (
        resolve_help_entry_shape(
            node,
            actor_permission=actor_permission,
            children=children,
        )
        != "simple_leaf"
    )


def render_doc_feature(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    include_demo: bool = True,
) -> Message:
    return render_message_plan_entry(
        render_doc_feature_plan_entry(
            node,
            feature,
            locale=locale,
            include_demo=include_demo,
        )
    )


def render_doc_feature_plan_entry(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    include_demo: bool = True,
) -> MessagePlanEntry:
    lines = [
        node.title,
        feature.title,
        "",
        "命令：",
        *_format_feature_command_lines(
            node.bundle,
            feature,
            node.title,
            locale=locale,
        ),
        "",
        "说明：",
    ]
    for index, note in enumerate(
        _feature_notice_items(feature, locale=locale),
        start=1,
    ):
        lines.append(f"{index}. {note}")
    message = _build_text_plan_entry("\n".join(lines).strip())
    if not include_demo:
        return message
    demo_bytes = load_demo_bytes(node.bundle, feature)
    if demo_bytes is None:
        return message
    return _append_image_plan_entry(message, demo_bytes)


def can_view_node(node: DocNode, actor_permission: Permission) -> bool:
    return can_view_node_impl(node, actor_permission)


def filter_features_by_permission(
    features: Sequence[FeatureDoc],
    actor_permission: Permission,
) -> tuple[FeatureDoc, ...]:
    return filter_features_by_permission_impl(features, actor_permission)


def rank_features_for_disclosure(
    features: Sequence[FeatureDoc],
) -> tuple[FeatureDoc, ...]:
    return rank_features_for_disclosure_impl(features)


def split_features_for_disclosure(
    features: Sequence[FeatureDoc],
    *,
    actor_permission: Permission,
    hero_limit: int = 3,
) -> tuple[tuple[FeatureDoc, ...], tuple[FeatureDoc, ...]]:
    return split_features_for_disclosure_impl(
        features,
        actor_permission=actor_permission,
        hero_limit=hero_limit,
    )


def _permission_allows(
    actor_permission: Permission,
    required_permission: Permission,
) -> bool:
    return permission_allows_impl(actor_permission, required_permission)


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
    simple_meta = _parse_meta_block_tokens(sections.get("用法", _EMPTY_SECTION).tokens)
    if not features:
        features = _parse_single_feature_bundle(
            source_path=source_path,
            title=title,
            summary=summary or default_description,
            trigger_label=meta.get("触发方式", trigger.label),
            default_permission=permission,
            sections=sections,
        )
    author, version = _resolve_doc_signature(source_path)
    return PluginDocBundle(
        title=title,
        description=default_description,
        summary=summary or default_description,
        trigger=meta.get("触发方式", trigger.label),
        help_query=_normalize_inline_text(
            simple_meta.get("Help", "").strip()
            or simple_meta.get("帮助命令", "").strip()
        ),
        permission=meta.get("权限", permission.label),
        author=author,
        version=version,
        impression_color=impression_color,
        index=features,
        source_path=source_path,
    )


def _parse_single_feature_bundle(
    *,
    source_path: Path,
    title: str,
    summary: str,
    trigger_label: str,
    default_permission: Permission,
    sections: dict[str, MarkdownSection],
) -> tuple[FeatureDoc, ...]:
    feature_slug = _derive_single_feature_slug(source_path)
    meta = _parse_meta_block_tokens(sections.get("用法", _EMPTY_SECTION).tokens)
    raw_permission = meta.get("权限", "").strip()
    flow_notes, demo_turns = _parse_flow_section_tokens(
        sections.get("完整流程", _EMPTY_SECTION).tokens
    )
    demo_filename = meta.get(
        "Demo",
        f"{doc_asset_prefix(source_path)}-{feature_slug}.webp",
    )
    demo_filename = demo_filename.strip("`")
    feature = FeatureDoc(
        slug=feature_slug,
        title=title,
        summary=summary or title,
        aliases=_split_csv(meta.get("别名", "")),
        trigger=meta.get("指令", "").strip() or trigger_label,
        permission=(
            _parse_permission(raw_permission) if raw_permission else default_permission
        ),
        demo_filename=demo_filename,
        hero=True,
        priority=10,
        advanced=False,
        overview=_render_markdown_blocks(
            sections.get("说明", _EMPTY_SECTION).tokens
        ).strip(),
        preconditions=_render_markdown_blocks(
            sections.get("前置条件", _EMPTY_SECTION).tokens
        ).strip(),
        flow_notes=flow_notes.strip(),
        failures=_render_markdown_blocks(
            sections.get("失败情况", _EMPTY_SECTION).tokens
        ).strip(),
        demo_turns=demo_turns,
    )
    return (feature,)


def _derive_single_feature_slug(source_path: Path) -> str:
    if source_path.name == "README.MD" and source_path.parent.name != "docs":
        return "main"
    if source_path.parent.name == "docs":
        return "main"
    return source_path.parent.name.lower()


def match_feature(
    features: Sequence[FeatureDoc],
    query: str,
) -> FeatureMatchResult:
    return match_feature_impl(features, query)


def _unique_nodes(nodes: Sequence[DocNode]) -> tuple[DocNode, ...]:
    unique: dict[str, DocNode] = {}
    for node in nodes:
        unique.setdefault(node.slug, node)
    return tuple(unique.values())


def _coerce_permission(value: Permission | int | str) -> Permission:
    return coerce_permission_impl(value)


def _derive_tree_identity_from_source(source_path: Path) -> tuple[str, str | None]:
    return derive_tree_identity_from_source_impl(source_path)


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
    return normalize_inline_text_impl(value, parse_inline_tokens=_parse_inline_tokens)


def split_inline_text_spans(text: str) -> tuple[InlineTextSpan, ...]:
    return split_inline_text_spans_impl(text, parse_inline_tokens=_parse_inline_tokens)


def build_command_layout(
    text: str,
    *,
    max_width: int,
    line_height: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> CommandLayout:
    return build_command_layout_impl(
        text,
        max_width=max_width,
        line_height=line_height,
        indent_px=indent_px,
        measure_text=measure_text,
        palette=palette,
        parse_inline_tokens=_parse_inline_tokens,
    )


def _parse_inline_tokens(text: str) -> tuple[Token, ...]:
    tokens = _markdown_parser().parseInline(text)
    if not tokens:
        return ()
    return tuple(tokens[0].children or ())


def load_demo_bytes(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes | None:
    if feature.demo_filename:
        demo_path = bundle.source_path.parent / "demos" / feature.demo_filename
        if demo_path.is_file():
            return demo_path.read_bytes()
        legacy_path = demo_path.with_suffix(".png")
        if legacy_path.is_file():
            return legacy_path.read_bytes()
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
    legacy_path = demo_path.with_suffix(".png")
    if legacy_path.is_file():
        return legacy_path.read_bytes()
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


def render_demo_png_with_audit(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    *,
    generated_at: datetime | None = None,
) -> tuple[bytes, tuple[str, ...]]:
    result = DemoImageRenderer(
        impression_color=bundle.impression_color
    ).render_with_audit(
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
    return result.data, result.errors


def render_feature_deep_dive(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
    actor_permission: Permission = Permission.NORMAL,
    prefer_static: bool = True,
) -> bytes:
    generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
        microsecond=0
    )
    if prefer_static:
        static_bytes = load_static_asset_bytes(
            node.source_path,
            target_key=feature_target_key(node, feature),
            actor_permission=actor_permission,
        )
        if static_bytes is not None:
            return static_bytes
    demo_result = DemoImageRenderer(
        impression_color=node.bundle.impression_color
    ).render_with_audit(
        plugin_title=node.bundle.title,
        feature_title=feature.title,
        feature_summary=feature.summary,
        feature_trigger=feature.trigger,
        feature_overview=feature.overview,
        feature_preconditions=feature.preconditions,
        feature_failures=feature.failures,
        feature_flow_notes=feature.flow_notes,
        plugin_trigger=node.bundle.trigger,
        feature_permission=_permission_label(feature.permission),
        plugin_version=node.bundle.version,
        plugin_author=node.bundle.author,
        turns=feature.demo_turns,
        locale=locale,
        generated_at=generated_at,
    )
    return ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    ).compose_with_support_strip(
        demo_result.image,
        locale=locale,
        footer_left_text=build_trace_footer_left_text(
            plugin_title=node.title,
            feature_title=feature.title,
            plugin_version=node.bundle.version,
            plugin_author=node.bundle.author,
        ),
        footer_right_text=(
            f"Generated at {generated:%Y-%m-%d %H:%M:%S} | © SakuraiSenrin"
        ),
        trim_source_footer=True,
    )


def render_plugin_guide(
    node: DocNode,
    *,
    actor_permission: Permission,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
    prefer_static: bool = True,
) -> bytes:
    generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
        microsecond=0
    )
    if prefer_static:
        static_bytes = load_static_asset_bytes(
            node.source_path,
            target_key=guide_target_key(node),
            actor_permission=actor_permission,
        )
        if static_bytes is not None:
            return static_bytes
    visible_features = filter_features_by_permission(node.features, actor_permission)
    return ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    ).render_plugin_guide(
        node=node,
        features=visible_features,
        locale=locale,
        generated_at=generated,
    )


def render_static_entry(
    node: DocNode,
    *,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
    actor_permission: Permission = Permission.NORMAL,
    prefer_static: bool = True,
) -> bytes:
    generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
        microsecond=0
    )
    if prefer_static:
        static_bytes = load_static_asset_bytes(
            node.source_path,
            target_key=static_target_key(node),
            actor_permission=actor_permission,
        )
        if static_bytes is not None:
            return static_bytes
    return ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    ).render_static_entry(
        node=node,
        locale=locale,
        generated_at=generated,
    )


def render_plugin_summary(
    node: DocNode,
    *,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
    actor_permission: Permission = Permission.NORMAL,
    prefer_static: bool = True,
) -> bytes:
    if prefer_static:
        static_bytes = load_static_asset_bytes(
            node.source_path,
            target_key=summary_target_key(node),
            actor_permission=actor_permission,
        )
        if static_bytes is not None:
            return static_bytes
    return ProgressiveDisclosureRenderer(
        impression_color=node.bundle.impression_color
    ).render_plugin_summary(
        node=node,
        locale=locale,
        generated_at=generated_at,
    )


def render_help_dashboard(
    sections: Sequence[HelpDashboardSection],
    *,
    locale: LocaleCode = "zh-CN",
    generated_at: datetime | None = None,
    actor_permission: Permission = Permission.NORMAL,
    prefer_static: bool = True,
    source_path: str | Path | None = None,
) -> bytes:
    generated = generated_at or datetime.fromtimestamp(get_current_time()).replace(
        microsecond=0
    )
    first_node = next(
        (node for section in sections for node in section.nodes),
        None,
    )
    dashboard_source = Path(source_path).resolve() if source_path is not None else None
    if dashboard_source is None and first_node is not None:
        dashboard_source = first_node.source_path
    if prefer_static and dashboard_source is not None:
        static_bytes = load_static_asset_bytes(
            dashboard_source,
            target_key=dashboard_target_key(),
            actor_permission=actor_permission,
        )
        if static_bytes is not None:
            return static_bytes
    theme_color = (
        first_node.bundle.impression_color
        if first_node is not None
        else DEFAULT_IMPRESSION_COLOR
    )
    return ProgressiveDisclosureRenderer(impression_color=theme_color).render_dashboard(
        sections=sections,
        locale=locale,
        generated_at=generated,
    )


def collection_demo_filename(source_path: Path) -> str:
    return f"{doc_asset_prefix(source_path)}-collection.webp"


def node_help_command(node: DocNode) -> str:
    return node_help_command_impl(node)


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
    _, errors = render_demo_png_with_audit(
        bundle,
        feature,
        generated_at=generated_at,
    )
    return errors


def _feature_command_for_display(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
) -> str:
    return feature_command_for_display_impl(
        bundle,
        feature,
        node_title,
        normalize_inline_text=_normalize_inline_text,
    )


def _format_feature_command_lines(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
    *,
    locale: LocaleCode = "zh-CN",
) -> list[str]:
    return format_feature_command_lines_impl(
        bundle,
        feature,
        node_title,
        locale=locale,
        normalize_inline_text=_normalize_inline_text,
    )


def _feature_notice_items(feature: FeatureDoc, *, locale: LocaleCode) -> list[str]:
    return feature_notice_items_impl(
        feature,
        locale=locale,
        normalize_inline_text=_normalize_inline_text,
        support_note=_support_note,
    )


def feature_command_sections(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
) -> tuple[str, ...]:
    return feature_command_sections_impl(
        bundle,
        feature,
        node_title,
        normalize_inline_text=_normalize_inline_text,
    )


def build_doc_demo_plan_entry(
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
) -> MessagePlanEntry:
    from src.lib import plugin_docs as plugin_docs_module

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
            image_bytes = plugin_docs_module.render_feature_deep_dive(
                node,
                match.feature,
                locale=locale,
            )
    if image_bytes is None:
        image_bytes = plugin_docs_module.render_plugin_guide(
            node,
            actor_permission=viewer_permission,
            locale=locale,
        )

    text = (prefix_text or "").strip()
    if not text:
        return MessagePlanEntry(blocks=(ImageBytesBlock(image_bytes),))
    return MessagePlanEntry(
        blocks=(
            TextBlock(f"{text}\n参考示例如下：\n"),
            ImageBytesBlock(image_bytes),
        )
    )


def _build_text_plan_entry(text: str) -> MessagePlanEntry:
    return MessagePlanEntry(blocks=(TextBlock(text),))


def _append_image_plan_entry(
    entry: MessagePlanEntry,
    image_bytes: bytes,
) -> MessagePlanEntry:
    return MessagePlanEntry(
        blocks=(*entry.blocks, ImageBytesBlock(image_bytes))
    )


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
    return render_message_plan_entry(
        build_doc_demo_plan_entry(
            source=source,
            name=name,
            description=description,
            trigger=trigger,
            permission=permission,
            locale=locale,
            actor_permission=actor_permission,
            feature_query=feature_query,
            prefix_text=prefix_text,
        )
    )


def build_feature_copy_text(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
) -> str:
    return build_feature_copy_text_impl(
        node,
        feature,
        locale=locale,
        normalize_inline_text=_normalize_inline_text,
        support_note=_support_note,
        support_text_block=_support_text_block,
    )


def build_plugin_guide_copy_text(
    node: DocNode,
    *,
    features: Sequence[FeatureDoc],
    child_nodes: Sequence[DocNode] = (),
    locale: LocaleCode,
) -> str:
    return build_plugin_guide_copy_text_impl(
        node,
        features=features,
        child_nodes=child_nodes,
        normalize_inline_text=_normalize_inline_text,
        support_text_block=_support_text_block,
        locale=locale,
    )


def build_plugin_summary_copy_text(
    node: DocNode,
) -> str:
    return build_plugin_summary_copy_text_impl(
        node,
        normalize_inline_text=_normalize_inline_text,
    )


def build_simple_leaf_copy_text(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
) -> str:
    return build_simple_leaf_copy_text_impl(
        node,
        feature,
        locale=locale,
        normalize_inline_text=_normalize_inline_text,
        support_note=_support_note,
        support_text_block=_support_text_block,
    )


def build_static_entry_copy_text(
    node: DocNode,
    *,
    locale: LocaleCode,
) -> str:
    return build_static_entry_copy_text_impl(
        node,
        locale=locale,
        support_note=_support_note,
        support_text_block=_support_text_block,
    )


_EMPTY_HEADING = Token("inline", "", 0)
_EMPTY_SECTION = MarkdownSection(title="", heading=_EMPTY_HEADING, tokens=())


def _extract_title(tokens: Sequence[Token]) -> str:
    return extract_title_impl(
        tokens,
        normalize_heading=_normalize_heading,
        render_inline_markdown=_render_inline_markdown,
    )


def _extract_heading_sections(
    tokens: Sequence[Token],
    *,
    tag: str,
) -> tuple[MarkdownSection, ...]:
    return extract_heading_sections_impl(
        tokens,
        tag=tag,
        normalize_heading=_normalize_heading,
        render_inline_markdown=_render_inline_markdown,
    )


def _normalize_heading(raw: str) -> str:
    return normalize_heading_impl(raw)


def _render_inline_markdown(tokens: Sequence[Token]) -> str:
    return render_inline_markdown_impl(tokens)


def _render_markdown_blocks(tokens: Sequence[Token]) -> str:
    return render_markdown_blocks_impl(tokens)


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
    return parse_meta_block_tokens_impl(
        tokens, parse_inline_tokens=_parse_inline_tokens
    )


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
    return parse_permission_impl(value)


def _parse_bool_meta(value: str, *, default: bool = False) -> bool:
    return parse_bool_meta_impl(value, default=default)


def _parse_int_meta(value: str, *, default: int = 1000) -> int:
    return parse_int_meta_impl(value, default=default)


def _parse_feature_index_tokens(tokens: Sequence[Token]) -> dict[str, tuple[str, str]]:
    return parse_feature_index_tokens_impl(tokens)


def _parse_feature_details_tokens(
    tokens: Sequence[Token],
    source_path: Path,
) -> dict[str, FeatureDoc]:
    return parse_feature_details_tokens_impl(
        tokens,
        source_path,
        doc_asset_prefix=doc_asset_prefix,
        parse_inline_tokens=_parse_inline_tokens,
    )


def _split_tokens_before_heading(
    tokens: Sequence[Token],
    *,
    tag: str,
) -> tuple[tuple[Token, ...], tuple[Token, ...]]:
    return split_tokens_before_heading_impl(tokens, tag=tag)


def _parse_feature_heading(heading: Token) -> tuple[str, str]:
    return parse_feature_heading_impl(heading)


def _parse_flow_section_tokens(
    tokens: Sequence[Token],
) -> tuple[str, tuple[DocsDemoTurn, ...]]:
    return parse_flow_section_tokens_impl(tokens)


def _parse_demo_turns(content: str) -> list[DocsDemoTurn]:
    return parse_demo_turns_impl(content)


def _merge_features(
    feature_index: dict[str, tuple[str, str]],
    details: dict[str, FeatureDoc],
) -> tuple[FeatureDoc, ...]:
    return merge_features_impl(feature_index, details)


def _split_csv(value: str) -> tuple[str, ...]:
    return split_csv_impl(value)


def _resolve_doc_signature(source_path: Path) -> tuple[str, str]:
    return resolve_doc_signature_impl(source_path)


def _resolve_doc_impression_color(source_path: Path) -> str:
    return resolve_doc_impression_color_impl(source_path)


def _resolve_doc_owner_module_path(source_path: Path) -> Path | None:
    return resolve_doc_owner_module_path_impl(source_path)


def _extract_metadata_field(raw_text: str, field: str) -> str:
    return extract_metadata_field_impl(raw_text, field)
