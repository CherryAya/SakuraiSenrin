"""wordbank 错误消息构造。"""

from __future__ import annotations

from pathlib import Path

from src.database.core.consts import Permission
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput

from .docs_support import DOCS_SOURCE, wordbank_error_message
from .handlers import localize_command_error


def build_wordbank_error_message(
    exc: Exception,
    locale: LocaleCode,
    *,
    default_feature: str | None = None,
    source: Path = DOCS_SOURCE,
    actor_permission: Permission = Permission.NORMAL,
) -> MessagePlanInput:
    return wordbank_error_message(
        exc,
        locale,
        default_feature=default_feature,
        prefix_text=localize_command_error(exc, locale),
        source=source,
        actor_permission=actor_permission,
    )
