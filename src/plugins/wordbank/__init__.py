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
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import MessagePlanInput
from src.lib.plugin_meta import create_plugin_metadata

from .bootstrap import (
    _initialize_wordbank_plugin,
)
from .bootstrap import (
    initialize_wordbank_plugin as initialize_wordbank_plugin,
)
from .docs_support import wordbank_docs_meta
from .entry_commands import register_wordbank_command_handlers
from .entry_runtime import register_wordbank_runtime_handlers
from .errors import build_wordbank_error_message
from .flows import (
    _cancel_guided_resources,
    _collect_search_query_content,
    _finish_guided_add,
    _handle_search_session_event,
    _record_guided_forward_response_choice,
    _record_guided_response,
    _record_guided_trigger,
    _send_pending_entries_view,
    _start_guided_add,
    _start_guided_add_with_trigger_image,
    _wordbank_submission_lifecycle,
)
from .guided_flow import (
    copy_guided_state,
    guided_search_stage,
    register_guided_checkpoint,
    reject_guided_error,
    wordbank_guided_locale,
)
from .handlers import PassiveResponse
from .matchers import (
    wordbank_add_command,
    wordbank_approval_reply_command,
    wordbank_approve_command,
    wordbank_command,
    wordbank_delete_command,
    wordbank_notice,
    wordbank_passive,
    wordbank_pending_command,
    wordbank_rank_command,
    wordbank_reject_command,
    wordbank_reply_command,
    wordbank_restore_command,
    wordbank_search_command,
    wordbank_view_reply_command,
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


register_wordbank_command_handlers(
    wordbank_command=wordbank_command,
    wordbank_add_command=wordbank_add_command,
    wordbank_search_command=wordbank_search_command,
    wordbank_pending_command=wordbank_pending_command,
    wordbank_rank_command=wordbank_rank_command,
    wordbank_approve_command=wordbank_approve_command,
    wordbank_reject_command=wordbank_reject_command,
    wordbank_delete_command=wordbank_delete_command,
    wordbank_restore_command=wordbank_restore_command,
    initialize_plugin=_initialize_wordbank_plugin,
    build_error_message=build_wordbank_error_message,
    finalize_submission=_wordbank_submission_lifecycle.finalize,
    collect_search_query_content=_collect_search_query_content,
    start_guided_add=_start_guided_add,
    start_guided_add_with_trigger_image=_start_guided_add_with_trigger_image,
    finish_guided_add=_finish_guided_add,
    handle_search_session_event=_handle_search_session_event,
    record_guided_trigger=_record_guided_trigger,
    record_guided_response=_record_guided_response,
    guided_search_stage=guided_search_stage,
    reject_guided_error=reject_guided_error,
    register_guided_checkpoint=register_guided_checkpoint,
    guided_locale=wordbank_guided_locale,
    copy_guided_state=copy_guided_state,
    notify_creator_review_result=runtime_exports["notify_creator_review_result"],
    record_guided_forward_response_choice=_record_guided_forward_response_choice,
    send_pending_entries_view=_send_pending_entries_view,
    resolve_locale_fn=resolve_locale,
)
