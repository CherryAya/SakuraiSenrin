"""Wordbank docs and demo helpers."""

from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.message import Message

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import build_doc_demo_message, create_docs_meta

DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


def wordbank_docs_meta() -> list[object]:
    main_docs = create_docs_meta(
        visible=True,
        category="fun",
        order=80,
        source=DOCS_SOURCE,
        slug="wordbank",
        aliases=("词库模块", "词库", "wordbank"),
    )
    main_docs["permission"] = Permission.NORMAL

    return [main_docs]


def wordbank_error_feature(exc: Exception, default_feature: str | None) -> str | None:
    key = str(getattr(exc, "key", "")).strip()
    if not key:
        return default_feature
    if key.startswith("wordbank.rank."):
        return "rank"
    if key.startswith("wordbank.error.guided_search_") or key.startswith(
        "wordbank.error.search_"
    ):
        return "search"
    if key == "wordbank.reply.group_command_invalid":
        return default_feature or "reply-shortcut"
    if (
        key.startswith("wordbank.error.scope_")
        or key == "wordbank.error.guided_scope_invalid"
    ):
        return "add-scope"
    if "probability" in key:
        return "add-prob"
    if "weight" in key:
        return "add-weight"
    if "role_" in key:
        return "add-role"
    if "call_" in key:
        return "add-call"
    if key.startswith("wordbank.error.study_"):
        return default_feature
    if key in {"wordbank.error.trigger_empty", "wordbank.error.response_empty"}:
        return default_feature
    return default_feature


def build_wordbank_error_demo(
    locale: LocaleCode,
    message: str,
    *,
    feature_query: str | None,
    source: Path = DOCS_SOURCE,
    actor_permission: Permission = Permission.NORMAL,
) -> Message:
    return build_doc_demo_message(
        source=source,
        name=tr("zh-CN", "plugin.wordbank.name"),
        description=tr("zh-CN", "plugin.wordbank.description"),
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        actor_permission=actor_permission,
        locale=locale,
        feature_query=feature_query,
        prefix_text=message,
    )


def wordbank_error_message(
    exc: Exception,
    locale: LocaleCode,
    *,
    default_feature: str | None = None,
    prefix_text: str = "",
    source: Path = DOCS_SOURCE,
    actor_permission: Permission = Permission.NORMAL,
) -> Message:
    return build_doc_demo_message(
        source=source,
        name=tr("zh-CN", "plugin.wordbank.name"),
        description=tr("zh-CN", "plugin.wordbank.description"),
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        actor_permission=actor_permission,
        locale=locale,
        feature_query=wordbank_error_feature(exc, default_feature),
        prefix_text=prefix_text or str(exc),
    )
