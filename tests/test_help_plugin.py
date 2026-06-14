import sys
from types import SimpleNamespace
from typing import Any, cast

import nonebot
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebug import App
import pytest

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
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

    message = _build_permission_denied_message(entry)

    assert "无权限查看插件文档: 好友管理模块" in str(message)
    assert "需要权限: 超级管理员" in str(message)


def test_build_index_message_only_lists_root_nodes(monkeypatch: Any) -> None:
    root = _make_entry(
        display_name="管理模块总览",
        plugin_name="admin",
        module_name="src.plugins.admin",
        slug="admin",
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
    monkeypatch.setattr(
        "src.plugins.help._load_help_index_demo",
        lambda: Message(MessageSegment.image(b"fake-demo")),
    )

    message = _build_index_message(
        [root, child],
        "zh-CN",
        Permission.NORMAL
        | Permission.GROUP_ADMIN
        | Permission.GROUP_OWNER
        | Permission.SUPERUSER,
    )

    assert "📖 ===== 帮助文档 =====" in str(message)
    assert "管理模块总览" in str(message)
    assert "群组管理模块" not in str(message)
    assert any(segment.type == "image" for segment in message)


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

    assert "子节点:" in str(root_message)
    assert "群组管理模块" in str(root_message)
    assert "📖 ===== 群组管理模块 =====" in str(child_message)


@pytest.mark.asyncio
async def test_help_matcher_formats_water_overview_shortcuts(app: App) -> None:
    event = build_group_message_event("#help 吹水记录")
    entries = _iter_docs_entries("zh-CN")
    water_entry = next(entry for entry in entries if entry.display_name == "吹水记录")

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

        assert "3. 查看周期榜单" in rendered
        assert "  #水王 / #水王 <主体> <范围> <时间>" in rendered
        assert "  快捷入口:" in rendered
        assert "    #今日矩阵群榜 / #今日矩阵群聊榜" in rendered

        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, bot=bot)
        ctx.should_finished(help_matcher)
