import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import nonebot
from nonebot.plugin import PluginMetadata
import pytest

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.demo_theme import DEFAULT_IMPRESSION_COLOR
from src.lib.message_assets import message_asset_repo
from src.lib.messages import text_message
from src.lib.plugin_docs import (
    DocNode,
    DocsMeta,
    build_doc_tree,
    build_help_home_sections,
    build_help_home_text,
    create_docs_meta,
    load_doc_node,
    node_help_command,
)

nonebot.init()
nonebot.require("nonebot_plugin_apscheduler")
if nonebot.get_plugin("help") is None:
    nonebot.load_plugin("src.plugins.help")
if nonebot.get_plugin("water") is None:
    sys.modules.pop("src.plugins.water", None)
    nonebot.load_plugin("src.plugins.water")
if nonebot.get_plugin("wordbank") is None:
    sys.modules.pop("src.plugins.wordbank", None)
    nonebot.load_plugin("src.plugins.wordbank")
if nonebot.get_plugin("community_miaomiao") is None:
    sys.modules.pop("src.plugins.community_miaomiao", None)
    nonebot.load_plugin("src.plugins.community_miaomiao")
if nonebot.get_plugin("study") is None:
    sys.modules.pop("src.plugins.study", None)
    nonebot.load_plugin("src.plugins.study")

from src.plugins.help import (
    HELP_FORWARD_FALLBACK_NICKNAME,
    HELP_FORWARD_WAIT_PROMPT,
    DocsEntry,
    HelpDeliveryPlan,
    _build_index_message,
    _build_permission_denied_message,
    _can_view_entry,
    _deliver_help_plan,
    _filter_authorized_entries,
    _iter_docs_entries,
    _match_entry,
    _read_plugin_permission,
    _resolve_docs_delivery_plan,
    _resolve_docs_message,
    _resolve_forward_sender,
    _send_help_forward,
    _split_query,
)
from tests.plugins.water.helpers import (
    build_group_message_event,
    build_private_message_event,
)


def _first_image_file(message: Any) -> str | None:
    for segment in message:
        if segment.type == "image":
            return cast(str | None, segment.data.get("file"))
    return None


def _make_entry(
    *,
    display_name: str,
    plugin_name: str,
    module_name: str,
    summary: str = "summary",
    category: str = "general",
    order: int = 100,
    permission: Permission = Permission.NORMAL,
    slug: str | None = None,
    parent_slug: str | None = None,
    visible: bool = True,
) -> DocsEntry:
    plugin = cast(
        Any,
        SimpleNamespace(
            name=plugin_name,
            module_name=module_name,
        ),
    )
    metadata = PluginMetadata(
        name=display_name,
        description=summary,
        usage="",
        config=None,
        extra={"permission": permission},
    )
    docs: DocsMeta = create_docs_meta(
        visible=visible,
        category=category,
        order=order,
        source="src/plugins/help/docs/README.MD",
        slug=slug or plugin_name,
        parent_slug=parent_slug,
        aliases=(display_name,),
    )
    docs["permission"] = permission
    node = load_doc_node(
        source=docs["source"]["readme_path"],
        default_name=display_name,
        default_description=summary,
        trigger=TriggerType.COMMAND,
        permission=permission,
        docs_meta=docs,
        module_name=module_name,
        plugin_name=plugin_name,
    )
    node = DocNode(
        kind=node.kind,
        slug=node.slug,
        parent_slug=node.parent_slug,
        category=node.category,
        order=node.order,
        visible=node.visible,
        hidden=node.hidden,
        internal=node.internal,
        permission=permission,
        title=display_name,
        summary=summary,
        description=summary,
        help_query="",
        aliases=(display_name,),
        source_path=node.source_path,
        bundle=node.bundle,
        module_name=module_name,
        plugin_name=plugin_name,
    )
    return DocsEntry(
        plugin=plugin,
        metadata=metadata,
        docs=docs,
        display_name=display_name,
        summary=summary,
        permission=permission,
        node=node,
    )


def test_split_query_supports_optional_feature_name() -> None:
    assert _split_query("") == ("", None)
    assert _split_query("吹水记录") == ("吹水记录", None)
    assert _split_query("吹水记录 ranking") == ("吹水记录", "ranking")
    assert _split_query("  吹水记录   ranking detail  ") == (
        "吹水记录",
        "ranking detail",
    )


def test_node_help_command_prefers_readme_defined_help_query() -> None:
    node = load_doc_node(
        source="src/plugins/study/docs/README.MD",
        default_name="词库模块（传统版）",
        default_description="desc",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )

    assert node.help_query == "study"
    assert node_help_command(node) == "#help study"


def test_match_entry_supports_exact_fuzzy_and_ambiguous() -> None:
    water = _make_entry(
        display_name="吹水记录",
        plugin_name="water",
        module_name="src.plugins.water",
        slug="water",
    )
    water_admin = _make_entry(
        display_name="吹水管理",
        plugin_name="water-admin",
        module_name="src.plugins.water_admin",
        slug="water.admin",
    )
    help_entry = _make_entry(
        display_name="帮助中心",
        plugin_name="help",
        module_name="src.plugins.help",
        slug="help",
    )
    entries = [water, water_admin, help_entry]

    exact = _match_entry(entries, "吹水记录")
    by_module = _match_entry(entries, "water")
    ambiguous = _match_entry(entries, "吹水")
    not_found = _match_entry(entries, "missing")

    assert exact.status == "matched"
    assert exact.entry is water

    assert by_module.status == "matched"
    assert by_module.entry is water

    assert ambiguous.status == "ambiguous"
    assert ambiguous.candidates is not None
    assert [entry.display_name for entry in ambiguous.candidates] == [
        "吹水记录",
        "吹水管理",
    ]

    assert not_found.status == "not_found"


def test_read_plugin_permission_supports_enum_name_and_raw_value() -> None:
    metadata_by_enum = PluginMetadata(
        name="enum",
        description="",
        usage="",
        config=None,
        extra={"permission": Permission.SUPERUSER},
    )
    metadata_by_name = PluginMetadata(
        name="name",
        description="",
        usage="",
        config=None,
        extra={"permission": "GROUP_ADMIN"},
    )
    metadata_by_value = PluginMetadata(
        name="value",
        description="",
        usage="",
        config=None,
        extra={"permission": int(Permission.GROUP_OWNER)},
    )
    metadata_invalid = PluginMetadata(
        name="invalid",
        description="",
        usage="",
        config=None,
        extra={"permission": "missing"},
    )

    assert _read_plugin_permission(metadata_by_enum) == Permission.SUPERUSER
    assert _read_plugin_permission(metadata_by_name) == Permission.GROUP_ADMIN
    assert _read_plugin_permission(metadata_by_value) == Permission.GROUP_OWNER
    assert _read_plugin_permission(metadata_invalid) == Permission.NORMAL


def test_filter_authorized_entries_matches_permission_visibility() -> None:
    normal = _make_entry(
        display_name="吹水记录",
        plugin_name="water",
        module_name="src.plugins.water",
        permission=Permission.NORMAL,
    )
    group_admin = _make_entry(
        display_name="退群说明",
        plugin_name="remove",
        module_name="src.plugins.remove",
        permission=Permission.GROUP_ADMIN,
    )
    superuser = _make_entry(
        display_name="好友管理模块",
        plugin_name="user",
        module_name="src.plugins.admin.user",
        permission=Permission.SUPERUSER,
    )
    entries = [normal, group_admin, superuser]

    normal_entries = _filter_authorized_entries(entries, Permission.NORMAL)
    group_admin_entries = _filter_authorized_entries(
        entries,
        Permission.NORMAL | Permission.GROUP_ADMIN,
    )
    superuser_entries = _filter_authorized_entries(
        entries,
        Permission.NORMAL
        | Permission.GROUP_ADMIN
        | Permission.GROUP_OWNER
        | Permission.SUPERUSER,
    )

    assert [entry.display_name for entry in normal_entries] == ["吹水记录"]
    assert [entry.display_name for entry in group_admin_entries] == [
        "吹水记录",
        "退群说明",
    ]
    assert [entry.display_name for entry in superuser_entries] == [
        "吹水记录",
        "退群说明",
        "好友管理模块",
    ]
    assert not _can_view_entry(superuser, Permission.NORMAL)


def test_permission_denied_message_uses_required_permission_label() -> None:
    entry = _make_entry(
        display_name="好友管理模块",
        plugin_name="user",
        module_name="src.plugins.admin.user",
        permission=Permission.SUPERUSER,
    )

    message = _build_permission_denied_message(entry, "zh-CN")

    assert "无权限查看插件文档: 好友管理模块" in str(message)
    assert "需要权限: 超级管理员" in str(message)


def test_build_index_message_only_lists_root_nodes() -> None:
    system_root = _make_entry(
        display_name="帮助中心",
        plugin_name="help",
        module_name="src.plugins.help",
        slug="help",
        visible=True,
    )
    plugin_root = _make_entry(
        display_name="吹水记录",
        plugin_name="water",
        module_name="src.plugins.water",
        slug="water",
        visible=True,
    )
    community_root = _make_entry(
        display_name="凛凛的妙妙小工具目录",
        plugin_name="community_miaomiao",
        module_name="src.plugins.community_miaomiao",
        slug="community.miaomiao-toolkit",
        visible=True,
        category="community",
    )
    child = _make_entry(
        display_name="群组管理模块",
        plugin_name="admin-group",
        module_name="src.plugins.admin.group",
        slug="admin.group",
        parent_slug="admin",
        visible=True,
    )
    message = _build_index_message(
        [system_root, plugin_root, community_root, child],
        "zh-CN",
        Permission.NORMAL
        | Permission.GROUP_ADMIN
        | Permission.GROUP_OWNER
        | Permission.SUPERUSER,
    )

    rendered = str(message)
    assert "今天想要做些什么呢" in rendered
    assert "凛凛的系统" in rendered
    assert "有趣的功能" in rendered
    assert "爱来自社区" in rendered
    assert "#help 帮助中心" in rendered
    assert "#help 吹水记录" in rendered
    assert "#help 凛凛的妙妙小工具目录" in rendered
    assert "继续查看" not in rendered
    assert "群组管理模块" not in rendered
    assert any(segment.type == "image" for segment in message)


def test_build_index_message_lists_full_root_entries_without_demo_hint() -> None:
    entries = _iter_docs_entries("zh-CN")

    message = _build_index_message(
        entries,
        "zh-CN",
        Permission.NORMAL,
    )

    rendered = str(message)
    assert "#help 运行时处理器" not in rendered
    assert "#help water" in rendered
    assert "#help study" in rendered
    assert "#help miaomiao" in rendered
    assert "查看 demo：" not in rendered


def test_build_index_message_superuser_sees_system_hooks_and_admin() -> None:
    for module_name, plugin_name in (
        ("src.plugins.admin", "admin"),
        ("src.hooks.processor", "hook.processor"),
        ("src.hooks.plugin", "hook.plugin"),
        ("src.plugins.notice", "notice"),
    ):
        if nonebot.get_plugin(plugin_name) is None:
            sys.modules.pop(module_name, None)
            nonebot.load_plugin(module_name)

    entries = _iter_docs_entries("zh-CN")

    message = _build_index_message(
        entries,
        "zh-CN",
        Permission.NORMAL
        | Permission.GROUP_ADMIN
        | Permission.GROUP_OWNER
        | Permission.SUPERUSER,
    )

    rendered = str(message)
    assert "凛凛的系统" in rendered
    assert "#help admin" in rendered
    assert "#help processor" in rendered
    assert "#help plugin" in rendered


def test_build_index_message_normal_user_hides_admin_and_hooks() -> None:
    entries = _iter_docs_entries("zh-CN")

    message = _build_index_message(entries, "zh-CN", Permission.NORMAL)

    rendered = str(message)
    assert "#help admin" not in rendered
    assert "#help processor" not in rendered
    assert "#help plugin" not in rendered


def test_iter_docs_entries_includes_wordbank_derived_root_entry() -> None:
    entries = _iter_docs_entries("zh-CN")

    derived = next(
        entry for entry in entries if entry.node.slug == "community.miaomiao-toolkit"
    )

    assert derived.display_name == "凛凛的妙妙小工具"
    assert derived.node.plugin_name == "community_miaomiao"
    assert [feature.slug for feature in derived.node.bundle.index] == ["main"]


def test_iter_docs_entries_no_longer_exposes_wordbank_derived_feature_nodes() -> None:
    entries = _iter_docs_entries("zh-CN")

    slugs = {entry.node.slug for entry in entries}

    assert "community.miaomiao-toolkit" in slugs
    assert all(not slug.startswith("community.miaomiao-toolkit.") for slug in slugs)


def test_build_index_message_filters_invisible_and_unauthorized_root_entries() -> None:
    visible_normal = _make_entry(
        display_name="可见普通插件",
        plugin_name="visible",
        module_name="src.plugins.visible",
        slug="visible",
        permission=Permission.NORMAL,
        visible=True,
    )
    hidden_visible_false = _make_entry(
        display_name="不可见插件",
        plugin_name="hidden",
        module_name="src.plugins.hidden",
        slug="hidden",
        permission=Permission.NORMAL,
        visible=False,
    )
    superuser_only = _make_entry(
        display_name="超管插件",
        plugin_name="super",
        module_name="src.plugins.super",
        slug="super",
        permission=Permission.SUPERUSER,
        visible=True,
    )

    message = _build_index_message(
        [visible_normal, hidden_visible_false, superuser_only],
        "zh-CN",
        Permission.NORMAL,
    )

    rendered = str(message)
    assert "#help 可见普通插件" in rendered
    assert "#help 不可见插件" not in rendered
    assert "#help 超管插件" not in rendered


def test_build_help_home_sections_applies_runtime_grouping_and_styles() -> None:
    nodes = [
        _make_entry(
            display_name="帮助中心",
            plugin_name="help",
            module_name="src.plugins.help",
            slug="help",
        ).node,
        _make_entry(
            display_name="运行时处理器",
            plugin_name="processor",
            module_name="src.hooks.processor",
            slug="hook.processor",
            permission=Permission.SUPERUSER,
        ).node,
        _make_entry(
            display_name="吹水记录",
            plugin_name="water",
            module_name="src.plugins.water",
            slug="water",
        ).node,
        _make_entry(
            display_name="妙妙小工具",
            plugin_name="miaomiao",
            module_name="src.plugins.community_miaomiao",
            slug="community.miaomiao-toolkit",
            category="community",
        ).node,
    ]

    sections = build_help_home_sections(
        nodes,
        locale="zh-CN",
        actor_permission=Permission.NORMAL,
    )

    assert [section.kind for section in sections] == [
        "system",
        "developer",
        "community",
    ]
    assert [section.title for section in sections] == [
        "凛凛的系统😇",
        "有趣的功能😮",
        "爱来自社区🥰",
    ]
    assert [node.slug for node in sections[0].nodes] == ["help"]
    assert [node.slug for node in sections[1].nodes] == ["water"]
    assert [node.slug for node in sections[2].nodes] == ["community.miaomiao-toolkit"]
    assert sections[0].accent == "#8AB4F8"
    assert sections[1].marker == "diamond"
    assert sections[2].command_text == "#9A4B68"


def test_build_help_home_text_uses_shared_sections() -> None:
    nodes = [
        _make_entry(
            display_name="帮助中心",
            plugin_name="help",
            module_name="src.plugins.help",
            slug="help",
        ).node,
        _make_entry(
            display_name="吹水记录",
            plugin_name="water",
            module_name="src.plugins.water",
            slug="water",
        ).node,
    ]

    text = build_help_home_text(
        build_help_home_sections(
            nodes,
            locale="zh-CN",
            actor_permission=Permission.NORMAL,
        ),
        locale="zh-CN",
    )

    assert "今天想要做些什么呢" in text
    assert "凛凛的系统" in text
    assert "有趣的功能" in text
    assert "#help 帮助中心" in text
    assert "#help 吹水记录" in text
    assert "反馈与交流群" in text
    assert "群号 1107576103" in text


async def test_resolve_docs_message_supports_tree_and_feature_queries() -> None:
    root = _make_entry(
        display_name="管理模块总览",
        plugin_name="admin",
        module_name="src.plugins.admin",
        slug="admin",
        permission=Permission.SUPERUSER,
    )
    child = _make_entry(
        display_name="群组管理模块",
        plugin_name="admin-group",
        module_name="src.plugins.admin.group",
        slug="admin.group",
        parent_slug="admin",
        permission=Permission.SUPERUSER,
        visible=True,
    )
    entries = [root, child]
    tree = build_doc_tree([entry.node for entry in entries])

    assert [node.slug for node in tree.children_of("admin")] == ["admin.group"]

    root_message = await _resolve_docs_message(
        root,
        "zh-CN",
        actor_permission=Permission.SUPERUSER,
        all_entries=entries,
    )
    child_message = await _resolve_docs_message(
        root,
        "zh-CN",
        feature_query="群组管理模块",
        actor_permission=Permission.SUPERUSER,
        all_entries=entries,
    )

    assert any(segment.type == "image" for segment in root_message)
    assert "群组管理模块" in str(root_message)
    assert any(segment.type == "image" for segment in child_message)
    assert "群组管理模块" in str(child_message)


async def test_resolve_docs_message_feature_query_returns_deep_dive_reply() -> None:
    entries = _iter_docs_entries("zh-CN")
    water_entry = next(entry for entry in entries if entry.display_name == "吹水记录")

    message = await _resolve_docs_message(
        water_entry,
        "zh-CN",
        feature_query="ranking",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert any(segment.type == "image" for segment in message)
    rendered = str(message)
    assert "查看周期榜单" in rendered
    assert "#水王" in rendered


async def test_resolve_docs_message_supports_wordbank_derived_static_entry() -> None:
    entries = _iter_docs_entries("zh-CN")
    derived_entry = next(
        entry for entry in entries if entry.node.slug == "community.miaomiao-toolkit"
    )

    root_message = await _resolve_docs_message(
        derived_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert any(segment.type == "image" for segment in root_message)
    assert "凛凛的妙妙小工具" in str(root_message)
    assert "词条触发" in str(root_message)


async def test_derived_static_entry_feature_query_is_not_found() -> None:
    entries = _iter_docs_entries("zh-CN")
    derived_entry = next(
        entry for entry in entries if entry.node.slug == "community.miaomiao-toolkit"
    )

    message = await _resolve_docs_message(
        derived_entry,
        "zh-CN",
        feature_query="运势",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert "未找到" in str(message)


async def test_resolve_docs_message_simple_leaf_returns_direct_demo_reply() -> None:
    if nonebot.get_plugin("picsearch") is None:
        nonebot.load_plugin("src.plugins.picsearch")
    entries = _iter_docs_entries("zh-CN")
    picsearch_entry = next(
        entry for entry in entries if entry.display_name == "图片搜索"
    )

    message = await _resolve_docs_message(
        picsearch_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    rendered = str(message)
    assert "图片搜索" in rendered
    assert "搜图" in rendered
    assert "查看 demo：" not in rendered
    assert "下面这些命令可以直接复制发送" not in rendered


def test_match_entry_supports_manual_aliases() -> None:
    entries = _iter_docs_entries("zh-CN")

    picsearch = _match_entry(entries, "搜索图片")
    picsearch_short = _match_entry(entries, "搜图")
    picsearch_slug = _match_entry(entries, "picsearch")

    assert picsearch.status == "matched"
    assert picsearch.entry is not None
    assert picsearch.entry.node.slug == "picsearch"
    assert picsearch_short.status == "matched"
    assert picsearch_short.entry is not None
    assert picsearch_short.entry.node.slug == "picsearch"
    assert picsearch_slug.status == "matched"
    assert picsearch_slug.entry is not None
    assert picsearch_slug.entry.node.slug == "picsearch"


def test_match_entry_prefers_direct_root_slug_over_other_fuzzy_match() -> None:
    root = _make_entry(
        display_name="词库模块",
        plugin_name="wordbank",
        module_name="src.plugins.wordbank",
        slug="wordbank",
    )
    other = _make_entry(
        display_name="词库模块扩展",
        plugin_name="wordbank-extra",
        module_name="src.plugins.wordbank_extra",
        slug="wordbank.extra",
    )

    result = _match_entry([root, other], "wordbank")

    assert result.status == "matched"
    assert result.entry is root


@pytest.mark.asyncio
async def test_resolve_docs_message_formats_water_overview_shortcuts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _iter_docs_entries("zh-CN")
    water_entry = next(entry for entry in entries if entry.display_name == "吹水记录")
    monkeypatch.setattr(
        "src.plugins.help.render_plugin_summary",
        lambda *args, **kwargs: b"summary-demo",
    )
    message = await _resolve_docs_message(
        water_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )
    rendered = str(message)

    assert any(segment.type == "image" for segment in message)
    assert "查看个人画像" in rendered
    assert "#我有多水" in rendered
    assert "查看周期榜单" in rendered
    assert "#水王" in rendered
    assert "👉 查看个人画像" in rendered
    assert "👉 查看周期榜单" in rendered
    assert "更多高级功能" not in rendered
    assert "📖" not in rendered
    assert _first_image_file(message) == "base64://c3VtbWFyeS1kZW1v"
    assert sum(1 for segment in message if segment.type == "image") >= 3


@pytest.mark.asyncio
async def test_resolve_docs_message_wordbank_guide_hides_admin_features_for_normal_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _iter_docs_entries("zh-CN")
    wordbank_entry = next(entry for entry in entries if entry.node.slug == "wordbank")
    monkeypatch.setattr(
        "src.plugins.help.render_plugin_summary",
        lambda *args, **kwargs: b"summary-demo",
    )

    message = await _resolve_docs_message(
        wordbank_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    rendered = str(message)
    assert "主功能" not in rendered
    assert "高级功能" not in rendered
    assert "👉 基础添加" in rendered
    assert "#添加词条 触发词 => 响应词" in rendered
    assert "待审核词条" not in rendered
    assert "通过审核" not in rendered
    assert "拒绝审核" not in rendered
    assert _first_image_file(message) == "base64://c3VtbWFyeS1kZW1v"


@pytest.mark.asyncio
async def test_resolve_docs_message_wordbank_guide_shows_admin_features_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _iter_docs_entries("zh-CN")
    wordbank_entry = next(entry for entry in entries if entry.node.slug == "wordbank")
    monkeypatch.setattr(
        "src.plugins.help.render_plugin_summary",
        lambda *args, **kwargs: b"summary-demo",
    )

    message = await _resolve_docs_message(
        wordbank_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL | Permission.GROUP_ADMIN,
        all_entries=entries,
    )

    rendered = str(message)
    assert "👉 基础添加" in rendered
    assert "👉 待审核词条" in rendered
    assert "#待审核词条" in rendered
    assert "👉 通过审核" in rendered
    assert "👉 拒绝审核" in rendered
    assert "👉 审批回复快捷方式" in rendered
    assert _first_image_file(message) == "base64://c3VtbWFyeS1kZW1v"


@pytest.mark.asyncio
async def test_resolve_docs_message_wordbank_supports_admin_feature_query() -> None:
    entries = _iter_docs_entries("zh-CN")
    wordbank_entry = next(entry for entry in entries if entry.node.slug == "wordbank")
    permission = Permission.NORMAL | Permission.GROUP_ADMIN

    message = await _resolve_docs_message(
        wordbank_entry,
        "zh-CN",
        feature_query="pending",
        actor_permission=permission,
        all_entries=entries,
    )

    rendered = str(message)
    assert "待审核词条" in rendered
    assert "待审核词条 &#91;关键词&#93;" in rendered


@pytest.mark.asyncio
async def test_wordbank_admin_feature_query_denied_for_normal_user() -> None:
    entries = _iter_docs_entries("zh-CN")
    wordbank_entry = next(entry for entry in entries if entry.node.slug == "wordbank")

    message = await _resolve_docs_message(
        wordbank_entry,
        "zh-CN",
        feature_query="pending",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert "未找到" in str(message)


@pytest.mark.asyncio
async def test_resolve_docs_delivery_plan_wordbank_guide_splits_into_forward_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _iter_docs_entries("zh-CN")
    wordbank_entry = next(entry for entry in entries if entry.node.slug == "wordbank")
    monkeypatch.setattr(
        "src.plugins.help.render_plugin_summary",
        lambda *args, **kwargs: b"summary-demo",
    )

    plan = await _resolve_docs_delivery_plan(
        wordbank_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert plan.should_forward is True
    assert len(plan.messages) > 1
    assert plan.messages[0][-1].data["file"] == "base64://c3VtbWFyeS1kZW1v"
    assert "👉 基础添加" in str(plan.messages[1])


@pytest.mark.asyncio
async def test_resolve_docs_delivery_plan_simple_leaf_stays_single_message() -> None:
    if nonebot.get_plugin("picsearch") is None:
        nonebot.load_plugin("src.plugins.picsearch")
    entries = _iter_docs_entries("zh-CN")
    picsearch_entry = next(
        entry for entry in entries if entry.display_name == "图片搜索"
    )

    plan = await _resolve_docs_delivery_plan(
        picsearch_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert plan.should_forward is False
    assert len(plan.messages) == 1
    assert "图片搜索" in str(plan.messages[0])


@pytest.mark.asyncio
async def test_resolve_forward_sender_prefers_login_nickname() -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(return_value={"nickname": "测试机器人"}),
        ),
    )

    sender = await _resolve_forward_sender(bot)

    assert sender == (99999, "测试机器人")


@pytest.mark.asyncio
async def test_send_help_forward_uses_group_forward_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                side_effect=[
                    {"message_id": 101},
                    {"message_id": 102},
                    {"nickname": "测试机器人"},
                    {"message_id": 9001},
                ]
            ),
        ),
    )
    event = build_group_message_event("#help wordbank")
    plan = HelpDeliveryPlan(messages=(text_message("A"), text_message("B")))
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        AsyncMock(return_value=None),
    )

    await _send_help_forward(bot, event, plan)

    assert [call.args[0] for call in bot.call_api.await_args_list] == [
        "send_private_msg",
        "send_private_msg",
        "get_login_info",
        "send_group_forward_msg",
    ]
    assert bot.call_api.await_args_list[-1].args[0] == "send_group_forward_msg"
    assert bot.call_api.await_args_list[-1].kwargs["group_id"] == 20001
    nodes = bot.call_api.await_args_list[-1].kwargs["messages"]
    assert len(nodes) == 2
    assert all(node.type == "node" for node in nodes)
    assert nodes[0].data["nickname"] == "测试机器人"
    assert str(nodes[0].data["content"]) == "A"


@pytest.mark.asyncio
async def test_send_help_forward_uses_private_forward_api_with_fallback_nickname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_api(api: str, **kwargs: Any) -> Any:
        if api == "get_login_info":
            raise RuntimeError("boom")
        if api == "send_private_forward_msg":
            return {"message_id": 9002}
        return {"message_id": 101}

    bot = cast(
        Any,
        SimpleNamespace(self_id="99999", call_api=AsyncMock(side_effect=fake_call_api)),
    )
    event = build_private_message_event("#help wordbank")
    plan = HelpDeliveryPlan(messages=(text_message("A"), text_message("B")))
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        AsyncMock(return_value=None),
    )

    await _send_help_forward(bot, event, plan)

    assert [call.args[0] for call in bot.call_api.await_args_list] == [
        "send_private_msg",
        "send_private_msg",
        "get_login_info",
        "send_private_forward_msg",
    ]
    assert bot.call_api.await_args_list[-1].args[0] == "send_private_forward_msg"
    assert bot.call_api.await_args_list[-1].kwargs["user_id"] == 10001
    nodes = bot.call_api.await_args_list[-1].kwargs["messages"]
    assert nodes[0].data["nickname"] == HELP_FORWARD_FALLBACK_NICKNAME
    assert str(nodes[0].data["content"]) == "A"


@pytest.mark.asyncio
async def test_deliver_help_plan_sends_wait_prompt_before_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="99999",
            call_api=AsyncMock(
                side_effect=[
                    {"message_id": 101},
                    {"message_id": 102},
                    {"message_id": 103},
                    {"nickname": "SakuraiSenrin"},
                    {"message_id": 9003},
                ]
            ),
        ),
    )
    matcher = cast(Any, SimpleNamespace(send=AsyncMock(), finish=AsyncMock()))
    event = build_group_message_event("#help wordbank")
    plan = HelpDeliveryPlan(messages=(text_message("A"), text_message("B")))
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_bundle_asset",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        message_asset_repo,
        "get_forward_node_asset",
        AsyncMock(return_value=None),
    )

    await _deliver_help_plan(bot, matcher, event, plan)

    first_call = bot.call_api.await_args_list[0]
    assert first_call.args[0] == "send_group_msg"
    assert first_call.kwargs["group_id"] == 20001
    assert str(first_call.kwargs["message"]) == HELP_FORWARD_WAIT_PROMPT
    assert [call.args[0] for call in bot.call_api.await_args_list] == [
        "send_group_msg",
        "send_private_msg",
        "send_private_msg",
        "get_login_info",
        "send_group_forward_msg",
    ]
    matcher.finish.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_deliver_help_plan_finishes_directly_for_single_message() -> None:
    bot = cast(Any, SimpleNamespace(self_id="99999", call_api=AsyncMock()))
    matcher = cast(Any, SimpleNamespace(send=AsyncMock(), finish=AsyncMock()))
    event = build_group_message_event("#help picsearch")
    message = text_message("single")

    await _deliver_help_plan(
        bot,
        matcher,
        event,
        HelpDeliveryPlan(messages=(message,)),
    )

    matcher.send.assert_not_awaited()
    matcher.finish.assert_awaited_once_with(message)
    bot.call_api.assert_not_awaited()


def test_iter_docs_entries_resolves_explicit_or_inherited_impression_colors() -> None:
    for module_name in (
        "src.hooks.processor",
        "src.plugins.admin",
        "src.plugins.notice",
        "src.plugins.picsearch",
        "src.plugins.remove",
    ):
        if nonebot.get_plugin(module_name.split(".")[-1]) is None:
            nonebot.load_plugin(module_name)

    entries = _iter_docs_entries("zh-CN")
    color_by_slug = {
        entry.node.slug: entry.node.bundle.impression_color for entry in entries
    }

    assert color_by_slug["admin"] == "#FF922B"
    assert color_by_slug["admin.group"] == "#FF922B"
    assert color_by_slug["notice"] == "#845EF7"
    assert color_by_slug["notice.group"] == "#845EF7"
    assert color_by_slug["picsearch"] == "#748FFC"
    assert color_by_slug["remove"] == "#FA5252"
    assert color_by_slug["hook.processor"] == "#12B886"
    direct_hook_plugin = load_doc_node(
        source="src/hooks/docs/plugin/README.MD",
        default_name="插件钩子扩展点",
        default_description="desc",
        trigger=TriggerType.PASSIVE,
        permission=Permission.SUPERUSER,
    )
    assert direct_hook_plugin.bundle.impression_color == "#15AABF"
    assert all(color != DEFAULT_IMPRESSION_COLOR for color in color_by_slug.values())
