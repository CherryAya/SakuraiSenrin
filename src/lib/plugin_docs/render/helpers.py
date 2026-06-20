"""Shared helpers for plugin docs renderers."""

from __future__ import annotations

from src.database.core.consts import Permission
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs.command_layout import normalize_inline_text
from src.lib.plugin_docs.copy import (
    build_static_entry_copy_text,
    feature_command_for_display,
    feature_demo_help_command,
    node_help_command,
)
from src.lib.plugin_docs.meta import support_bundle, support_note, support_text_block
from src.lib.plugin_docs.models import (
    DocNode,
    FeatureDoc,
    HelpSupportBundle,
    PluginDocBundle,
)


def permission_label(permission: Permission) -> str:
    labels = {
        Permission.NONE: "权限开放",
        Permission.NORMAL: "普通用户",
        Permission.GROUP_ADMIN: "群管理",
        Permission.GROUP_OWNER: "群主",
        Permission.SUPERUSER: "超级用户",
    }
    return labels.get(permission, "普通用户")


def feature_command_for_display_text(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
) -> str:
    return feature_command_for_display(
        bundle,
        feature,
        node_title,
        normalize_inline_text=normalize_inline_text,
    )


def build_static_entry_copy(node: DocNode, *, locale: LocaleCode) -> str:
    return build_static_entry_copy_text(
        node,
        locale=locale,
        support_note=support_note,
        support_text_block=support_text_block,
    )


def build_help_support_bundle(*, locale: LocaleCode) -> HelpSupportBundle:
    return support_bundle(locale)


__all__ = [
    "build_help_support_bundle",
    "build_static_entry_copy",
    "feature_command_for_display_text",
    "feature_demo_help_command",
    "node_help_command",
    "permission_label",
]
