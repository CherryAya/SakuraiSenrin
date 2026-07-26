from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.typing import T_State

from src.database.core.consts import Permission
from src.lib.consts import GLOBAL_GROUP_FLAG, TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import (
    MessagePlanInput,
    finish_with_message,
    reject_with_message,
)
from src.lib.plugin_docs import DocsRenderContext, build_readme_docs_plan_entry
from src.plugins.self_unban.services import (
    ManagedBannedGroupOption,
    PreparedSelfUnbanRequest,
    SelfUnbanSelectionSession,
    self_unban_service,
)

DOCS_SOURCE = Path(__file__).resolve().parents[1] / "docs" / "README.MD"


def build_docs(locale: LocaleCode) -> MessagePlanInput:
    return build_readme_docs_plan_entry(
        source=DOCS_SOURCE,
        name=tr("zh-CN", "plugin.self_unban.name"),
        description=tr("zh-CN", "plugin.self_unban.description"),
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale=locale),
    )


def _is_cancelled(text: str) -> bool:
    return text.strip().lower() in {"取消", "cancel", "exit", "quit"}


def _build_kind_prompt(session: SelfUnbanSelectionSession) -> str:
    return tr(
        session.locale,
        "self_unban.prompt.kind",
        self_label=(
            tr(session.locale, "self_unban.prompt.kind.self")
            if session.user_candidate is not None
            else tr(session.locale, "self_unban.prompt.kind.self_unavailable")
        ),
        group_label=(
            tr(session.locale, "self_unban.prompt.kind.group")
            if session.group_candidates
            else tr(session.locale, "self_unban.prompt.kind.group_unavailable")
        ),
    )


def _build_group_prompt(session: SelfUnbanSelectionSession) -> str:
    lines = [tr(session.locale, "self_unban.prompt.group_select")]
    for option in session.group_candidates:
        lines.append(
            tr(
                session.locale,
                "self_unban.prompt.group_select.item",
                index=option.index,
                group_name=option.group_name,
                group_id=option.group_id,
                user_remaining=option.prepared.user_remaining_attempts_before,
                group_remaining=option.prepared.group_remaining_attempts_before,
            )
        )
    return "\n".join(lines)


def _build_reason_prompt(prepared: PreparedSelfUnbanRequest) -> str:
    if prepared.kind == "group":
        return tr(
            prepared.locale,
            "self_unban.prompt.reason.group",
            group_name=prepared.target_group_name or prepared.subject_id,
            group_id=prepared.subject_id,
            user_remaining=prepared.user_remaining_attempts_before,
            group_remaining=prepared.group_remaining_attempts_before,
        )
    if prepared.scope_group_id == GLOBAL_GROUP_FLAG:
        return tr(
            prepared.locale,
            "self_unban.prompt.reason.user_global",
            remaining=prepared.user_remaining_attempts_before,
        )
    return tr(
        prepared.locale,
        "self_unban.prompt.reason.user_group",
        group_id=prepared.scope_group_id,
        remaining=prepared.user_remaining_attempts_before,
    )


def _resolve_group_option(
    session: SelfUnbanSelectionSession,
    raw_text: str,
) -> ManagedBannedGroupOption | None:
    normalized = raw_text.strip()
    if not normalized:
        return None
    if normalized.isdigit():
        for option in session.group_candidates:
            if normalized in {str(option.index), option.group_id}:
                return option
    for option in session.group_candidates:
        if normalized == option.group_name:
            return option
    return None


def register_handlers(matcher: type[Matcher]) -> None:
    @matcher.handle()
    async def _start(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
        arg: Message = CommandArg(),
    ) -> None:
        if state.get("self_unban_stage"):
            return
        locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
        argv = arg.extract_plain_text().strip().split()
        if argv and argv[0].lower() in {"help", "帮助"}:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_docs(locale),
                source_kind="self_unban_command",
            )
            return
        if argv:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(locale, "self_unban.args_error"),
                source_kind="self_unban_command",
            )
            return

        session_or_error = await self_unban_service.prepare_selection_session(
            requester_user_id=str(event.user_id),
            locale=locale,
            current_group_id=(
                str(event.group_id) if isinstance(event, GroupMessageEvent) else None
            ),
        )
        if isinstance(session_or_error, str):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=session_or_error,
                source_kind="self_unban_command",
            )
            return

        session = session_or_error
        state["self_unban_session"] = session
        state["self_unban_stage"] = "target_kind"
        await reject_with_message(
            matcher,
            message=_build_kind_prompt(session),
        )

    @matcher.handle()
    async def _select_kind(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
    ) -> None:
        if state.get("self_unban_stage") != "target_kind":
            return
        session = state.get("self_unban_session")
        if not isinstance(session, SelfUnbanSelectionSession):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr("zh-CN", "self_unban.args_error"),
                source_kind="self_unban_command",
            )
            return
        text = event.message.extract_plain_text().strip()
        if _is_cancelled(text):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(session.locale, "interaction.cancelled"),
                source_kind="self_unban_command",
            )
            return
        normalized = text.lower()
        if normalized in {"1", "自己", "自助解封自己", "user", "me"}:
            if session.user_candidate is None:
                await reject_with_message(
                    matcher,
                    message=_build_kind_prompt(session),
                )
                return
            state["self_unban_stage"] = "reason"
            state["self_unban_prepared"] = session.user_candidate
            await reject_with_message(
                matcher,
                message=_build_reason_prompt(session.user_candidate),
            )
            return
        if normalized in {"2", "群", "群聊", "group"} and session.group_candidates:
            state["self_unban_stage"] = "group_choice"
            await reject_with_message(
                matcher,
                message=_build_group_prompt(session),
            )
            return
        await reject_with_message(
            matcher,
            message=tr(session.locale, "self_unban.invalid_kind_choice"),
        )

    @matcher.handle()
    async def _select_group(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
    ) -> None:
        if state.get("self_unban_stage") != "group_choice":
            return
        session = state.get("self_unban_session")
        if not isinstance(session, SelfUnbanSelectionSession):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr("zh-CN", "self_unban.args_error"),
                source_kind="self_unban_command",
            )
            return
        text = event.message.extract_plain_text().strip()
        if _is_cancelled(text):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(session.locale, "interaction.cancelled"),
                source_kind="self_unban_command",
            )
            return
        option = _resolve_group_option(session, text)
        if option is None:
            await reject_with_message(
                matcher,
                message=tr(session.locale, "self_unban.invalid_group_choice"),
            )
            return
        state["self_unban_stage"] = "reason"
        state["self_unban_prepared"] = option.prepared
        await reject_with_message(
            matcher,
            message=_build_reason_prompt(option.prepared),
        )

    @matcher.handle()
    async def _reason(
        bot: Bot,
        matcher: Matcher,
        event: MessageEvent,
        state: T_State,
    ) -> None:
        if state.get("self_unban_stage") != "reason":
            return
        prepared = state.get("self_unban_prepared")
        if not isinstance(prepared, PreparedSelfUnbanRequest):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr("zh-CN", "self_unban.args_error"),
                source_kind="self_unban_command",
            )
            return
        text = event.message.extract_plain_text()
        if _is_cancelled(text):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=tr(prepared.locale, "interaction.cancelled"),
                source_kind="self_unban_command",
            )
            return
        result = await self_unban_service.submit_request(
            bot,
            prepared=prepared,
            reason=text,
        )
        if result.should_retry:
            await reject_with_message(
                matcher,
                message=result.final_message,
            )
            return
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=result.final_message,
            source_kind="self_unban_command",
        )
