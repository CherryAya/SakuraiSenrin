from types import SimpleNamespace
from typing import Any, cast

import nonebot
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.plugin import PluginMetadata

from src.database.core.consts import Permission
from src.lib.plugin_docs import DocsMeta

nonebot.init()

from src.plugins.help import (
    DocsEntry,
    _build_index_message,
    _build_permission_denied_message,
    _can_view_entry,
    _filter_authorized_entries,
    _match_entry,
    _read_plugin_permission,
    _resolve_docs_message,
    _split_query,
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
        extra={},
    )
    docs: DocsMeta = {
        "provider": lambda: Message("docs"),
        "visible": True,
        "category": category,
        "order": order,
        "source": "",
        "hidden": False,
    }
    return DocsEntry(
        plugin=plugin,
        metadata=metadata,
        docs=docs,
        display_name=display_name,
        summary=summary,
        permission=permission,
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
    )
    water_admin = _make_entry(
        display_name="吹水管理",
        plugin_name="water-admin",
        module_name="src.plugins.water_admin",
    )
    help_entry = _make_entry(
        display_name="帮助中心",
        plugin_name="help",
        module_name="src.plugins.help",
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


def test_filter_authorized_entries_matches_tutorials_permission_visibility() -> None:
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
    owner_entries = _filter_authorized_entries(
        entries,
        Permission.NORMAL | Permission.GROUP_ADMIN | Permission.GROUP_OWNER,
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
    assert [entry.display_name for entry in owner_entries] == [
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


def test_build_index_message_attaches_demo_image(monkeypatch: Any) -> None:
    entry = _make_entry(
        display_name="帮助中心",
        plugin_name="help",
        module_name="src.plugins.help",
        category="core",
    )
    monkeypatch.setattr(
        "src.plugins.help._load_help_index_demo",
        lambda: Message(MessageSegment.image(b"fake-demo")),
    )

    message = _build_index_message([entry], "zh-CN")

    assert "📖 ===== 帮助文档 =====" in str(message)
    assert "命令前缀: #help / #帮助" in str(message)
    assert "帮助中心" in str(message)
    assert "#help 帮助中心" in str(message)
    assert "反馈群「427842039」" in str(message)
    assert any(segment.type == "image" for segment in message)


async def test_resolve_docs_message_requests_plugin_or_feature_view() -> None:
    seen_views: list[str] = []

    def provider(ctx: Any) -> Message:
        seen_views.append(ctx.view)
        if ctx.view == "feature":
            assert ctx.feature_query == "ranking"
            assert ctx.actor_permission == Permission.SUPERUSER
        return Message(MessageSegment.image(b"fake-demo"))

    entry = _make_entry(
        display_name="吹水记录",
        plugin_name="water",
        module_name="src.plugins.water",
    )
    entry.docs["provider"] = provider

    plugin_message = await _resolve_docs_message(entry, "zh-CN")
    feature_message = await _resolve_docs_message(
        entry,
        "zh-CN",
        feature_query="ranking",
        actor_permission=Permission.SUPERUSER,
    )

    assert seen_views == ["plugin", "feature"]
    assert any(segment.type == "image" for segment in plugin_message)
    assert any(segment.type == "image" for segment in feature_message)
