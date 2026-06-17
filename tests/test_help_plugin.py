import sys
from types import SimpleNamespace
from typing import Any, cast

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebot.plugin import PluginMetadata
from nonebug import App
import pytest

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.demo_theme import DEFAULT_IMPRESSION_COLOR
from src.lib.plugin_docs import (
    DocNode,
    DocsMeta,
    build_doc_tree,
    create_docs_meta,
    load_doc_node,
)
from tests.plugins.water.helpers import build_group_message_event

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

from src.plugins.help import (
    DocsEntry,
    _build_index_message,
    _build_permission_denied_message,
    _can_view_entry,
    _filter_authorized_entries,
    _iter_docs_entries,
    _match_entry,
    _read_plugin_permission,
    _resolve_actor_permission,
    _resolve_docs_message,
    _split_query,
    help_matcher,
)


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
        plugin_name="wordbank",
        module_name="src.plugins.wordbank",
        slug="derived.wordbank.miaomiao-toolkit",
        visible=True,
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
    assert "欢迎来到 SakuraiSenrin 帮助中心" in rendered
    assert "以下是当前可用帮助入口" in rendered
    assert "【系统预置】" in rendered
    assert "【开发者插件】" in rendered
    assert "【社区创作】" in rendered
    assert "- #help 帮助中心" in rendered
    assert "- #help 吹水记录" in rendered
    assert "- #help 凛凛的妙妙小工具目录" in rendered
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
    assert "#help 吹水记录" in rendered
    assert "#help 凛凛的妙妙小工具目录" in rendered
    assert "查看 demo：" not in rendered


def test_iter_docs_entries_includes_wordbank_derived_root_entry() -> None:
    entries = _iter_docs_entries("zh-CN")

    derived = next(
        entry
        for entry in entries
        if entry.node.slug == "derived.wordbank.miaomiao-toolkit"
    )

    assert derived.display_name == "凛凛的妙妙小工具目录"
    assert derived.node.plugin_name == "wordbank"
    assert not derived.node.bundle.index


def test_iter_docs_entries_no_longer_exposes_wordbank_derived_feature_nodes() -> None:
    entries = _iter_docs_entries("zh-CN")

    slugs = {entry.node.slug for entry in entries}

    assert "derived.wordbank.miaomiao-toolkit" in slugs
    assert all(
        not slug.startswith("derived.wordbank.miaomiao-toolkit.") for slug in slugs
    )


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
    assert "📖 群组管理模块" in str(child_message)


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
    assert "👉 查看周期榜单" in rendered
    assert "#水王" in rendered


async def test_resolve_docs_message_supports_wordbank_derived_static_entry() -> None:
    entries = _iter_docs_entries("zh-CN")
    derived_entry = next(
        entry
        for entry in entries
        if entry.node.slug == "derived.wordbank.miaomiao-toolkit"
    )

    root_message = await _resolve_docs_message(
        derived_entry,
        "zh-CN",
        actor_permission=Permission.NORMAL,
        all_entries=entries,
    )

    assert any(segment.type == "image" for segment in root_message)
    assert "📖 凛凛的妙妙小工具目录" in str(root_message)
    assert "不提供子功能级 help" in str(root_message)


async def test_derived_static_entry_feature_query_is_not_found() -> None:
    entries = _iter_docs_entries("zh-CN")
    derived_entry = next(
        entry
        for entry in entries
        if entry.node.slug == "derived.wordbank.miaomiao-toolkit"
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
    assert "👉 图片搜索" in rendered
    assert "搜图" in rendered
    assert "查看 demo：" not in rendered
    assert "下面这些命令可以直接复制发送" not in rendered


@pytest.mark.asyncio
async def test_help_matcher_formats_water_overview_shortcuts(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = build_group_message_event("#help 吹水记录")
    entries = _iter_docs_entries("zh-CN")
    water_entry = next(entry for entry in entries if entry.display_name == "吹水记录")
    monkeypatch.setattr(
        "src.plugins.help.render_plugin_guide", lambda *args, **kwargs: b"guide-demo"
    )

    async with app.test_matcher(help_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        actor_permission = await _resolve_actor_permission(bot, event)
        expected = await _resolve_docs_message(
            water_entry,
            "zh-CN",
            actor_permission=actor_permission,
            all_entries=entries,
        )
        rendered = str(expected)

        assert any(segment.type == "image" for segment in expected)
        assert "下面这些命令可以直接复制发送" in rendered
        assert "👉 查看个人画像" in rendered
        assert "#我有多水" in rendered
        assert "👉 查看周期榜单" in rendered
        assert "#水王" in rendered
        assert "更多高级功能" not in rendered

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, bot=bot)
        ctx.should_finished(help_matcher)


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
