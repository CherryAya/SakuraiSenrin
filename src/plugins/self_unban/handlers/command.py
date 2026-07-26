from __future__ import annotations

from dataclasses import dataclass
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
    PreparedSelfUnbanRequest,
    self_unban_service,
)

DOCS_SOURCE = Path(__file__).resolve().parents[1] / "docs" / "README.MD"


@dataclass(slots=True, frozen=True)
class ParsedSelfUnbanCommand:
    action: str
    group_id: str | None = None


def build_docs(locale: LocaleCode) -> MessagePlanInput:
    return build_readme_docs_plan_entry(
        source=DOCS_SOURCE,
        name=tr("zh-CN", "plugin.self_unban.name"),
        description=tr("zh-CN", "plugin.self_unban.description"),
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
        ctx=DocsRenderContext(locale=locale),
    )


def _parse_args(
    argv: list[str],
    event: MessageEvent,
    locale: LocaleCode,
) -> ParsedSelfUnbanCommand | str | None:
    if not argv or argv[0].lower() in {"help", "帮助"}:
        return None

    action = argv[0].lower()
    if action in {"group", "群", "群聊"}:
        if len(argv) != 1:
            return tr(locale, "self_unban.args_error")
        return ParsedSelfUnbanCommand(action="group")

    if action not in {"user", "me", "用户", "自己"}:
        return tr(locale, "self_unban.args_error")

    group_id: str | None = None
    index = 1
    while index < len(argv):
        token = argv[index].lower()
        if token not in {"-g", "--group"}:
            return tr(locale, "self_unban.args_error")
        if group_id is not None or index + 1 >= len(argv):
            return tr(locale, "self_unban.args_error")
        raw_group_id = argv[index + 1].strip()
        if not raw_group_id.isdigit():
            return tr(locale, "self_unban.args_error")
        group_id = raw_group_id
        index += 2

    if group_id is None and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
    if group_id is None:
        group_id = GLOBAL_GROUP_FLAG
    return ParsedSelfUnbanCommand(action="user", group_id=group_id)


def _build_reason_prompt(prepared: PreparedSelfUnbanRequest) -> str:
    if prepared.kind == "group":
        return tr(
            prepared.locale,
            "self_unban.prompt.reason.group",
            remaining=prepared.remaining_attempts_before,
        )
    if prepared.scope_group_id == GLOBAL_GROUP_FLAG:
        return tr(
            prepared.locale,
            "self_unban.prompt.reason.user_global",
            remaining=prepared.remaining_attempts_before,
        )
    return tr(
        prepared.locale,
        "self_unban.prompt.reason.user_group",
        group_id=prepared.scope_group_id,
        remaining=prepared.remaining_attempts_before,
    )


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
        parsed = _parse_args(argv, event, locale)
        if parsed is None:
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=build_docs(locale),
                source_kind="self_unban_command",
            )
            return
        if isinstance(parsed, str):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=parsed,
                source_kind="self_unban_command",
            )
            return

        if parsed.action == "group":
            if not isinstance(event, GroupMessageEvent):
                await finish_with_message(
                    bot,
                    matcher,
                    event=event,
                    message=tr(locale, "self_unban.group.only_in_group"),
                    source_kind="self_unban_command",
                )
                return
            prepared_or_error = await self_unban_service.prepare_group_request(
                event=event,
                locale=locale,
            )
        else:
            source_hint = (
                "group_scope"
                if parsed.group_id != GLOBAL_GROUP_FLAG
                else "private_global"
            )
            prepared_or_error = await self_unban_service.prepare_user_request(
                requester_user_id=str(event.user_id),
                scope_group_id=parsed.group_id or GLOBAL_GROUP_FLAG,
                locale=locale,
                source_hint=source_hint,
            )

        if isinstance(prepared_or_error, str):
            await finish_with_message(
                bot,
                matcher,
                event=event,
                message=prepared_or_error,
                source_kind="self_unban_command",
            )
            return

        state["self_unban_stage"] = "reason"
        state["self_unban_prepared"] = prepared_or_error
        await reject_with_message(
            matcher,
            message=_build_reason_prompt(prepared_or_error),
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
        result = await self_unban_service.submit_request(
            bot,
            prepared=prepared,
            reason=event.message.extract_plain_text(),
        )
        if result.should_retry:
            await reject_with_message(
                matcher,
                message=result.final_message,
            )
        await finish_with_message(
            bot,
            matcher,
            event=event,
            message=result.final_message,
            source_kind="self_unban_command",
        )
