"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-19 00:30:24
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-04-04 15:09:39
Description: 插件入口
"""

from __future__ import annotations

from typing import cast

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput
from src.lib.plugin_meta import create_plugin_metadata

from . import bootstrap as _bootstrap  # noqa: F401  注册启动钩子与定时任务

# 导入命令入口模块即完成全部 matcher 注册（模块级 @matcher.handle()）
from . import entry_commands as entry_commands
from .docs_support import wordbank_docs_meta
from .entry_runtime import register_wordbank_runtime_handlers
from .errors import build_wordbank_error_message
from .flows import _cancel_guided_resources
from .guided_flow import (
    wordbank_guided_locale,
)
from .handlers import PassiveResponse
from .lifecycle import _initialize_wordbank_plugin
from .lifecycle import (
    initialize_wordbank_plugin as initialize_wordbank_plugin,
)
from .matchers import (
    wordbank_add_command,
    wordbank_approval_reply_command,
    wordbank_command,
    wordbank_notice,
    wordbank_passive,
    wordbank_reply_command,
    wordbank_view_reply_command,
)
from .matchers import (
    wordbank_approve_command as wordbank_approve_command,
)
from .matchers import (
    wordbank_delete_command as wordbank_delete_command,
)
from .matchers import (
    wordbank_pending_command as wordbank_pending_command,
)
from .matchers import (
    wordbank_rank_command as wordbank_rank_command,
)
from .matchers import (
    wordbank_reject_command as wordbank_reject_command,
)
from .matchers import (
    wordbank_restore_command as wordbank_restore_command,
)
from .matchers import (
    wordbank_search_command as wordbank_search_command,
)

name = tr("zh-CN", "plugin.wordbank.name")
description = tr("zh-CN", "plugin.wordbank.description")


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.1.0",
        "impression_color": "#74C0FC",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.NORMAL,
        "i18n": {
            "name_key": "plugin.wordbank.name",
            "description_key": "plugin.wordbank.description",
        },
        "docs": wordbank_docs_meta(),
    },
)

runtime_exports = register_wordbank_runtime_handlers(
    wordbank_reply_command=wordbank_reply_command,
    wordbank_approval_reply_command=wordbank_approval_reply_command,
    wordbank_view_reply_command=wordbank_view_reply_command,
    wordbank_passive=wordbank_passive,
    wordbank_notice=wordbank_notice,
    wordbank_add_command=wordbank_add_command,
    wordbank_command=wordbank_command,
    initialize_plugin=_initialize_wordbank_plugin,
    build_error_message=build_wordbank_error_message,
    cancel_guided_resources=_cancel_guided_resources,
    guided_locale=wordbank_guided_locale,
)


async def _build_passive_message(
    response: PassiveResponse,
    *,
    locale: LocaleCode,
) -> tuple[MessagePlanInput, dict[str, object]]:
    result = await runtime_exports["_build_passive_message"](response, locale=locale)
    if result is None:
        raise RuntimeError("wordbank passive message builder is not registered")
    return cast(tuple[MessagePlanInput, dict[str, object]], result)
