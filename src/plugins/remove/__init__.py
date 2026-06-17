"""真实退群插件。"""

from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.exception import ActionFailed
from nonebot.matcher import Matcher
from nonebot.plugin import on_command
from nonebot.typing import T_State

from src.config import config
from src.database.core.consts import GroupStatus, Permission
from src.lib.consts import TriggerType
from src.lib.i18n.runtime import resolve_locale, tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs import create_docs_meta
from src.lib.plugin_meta import create_plugin_metadata
from src.logger import logger
from src.repositories import group_repo, invite_repo
from src.services.info import resolve_group_name

name = tr("zh-CN", "plugin.remove.name")
description = tr("zh-CN", "plugin.remove.description")
DOCS_SOURCE = Path(__file__).parent / "docs" / "README.MD"


__plugin_meta__ = create_plugin_metadata(
    name=name,
    description=description,
    extra={
        "author": "SakuraiCora",
        "version": "0.2.0",
        "impression_color": "#FA5252",
        "trigger": TriggerType.COMMAND,
        "permission": Permission.GROUP_ADMIN,
        "no_check": True,
        "i18n": {
            "name_key": "plugin.remove.name",
            "description_key": "plugin.remove.description",
        },
        "docs": create_docs_meta(
            visible=True,
            category="system",
            order=90,
            source=DOCS_SOURCE,
        ),
    },
)

remove_matcher = on_command("remove", aliases={"退群"}, priority=5, block=True)


def is_group_admin(event: GroupMessageEvent) -> bool:
    return getattr(event.sender, "role", "") in {"admin", "owner"}


def is_remove_confirmed(text: str) -> bool:
    return text.strip().lower() in {"y", "yes"}


async def is_group_inviter(event: GroupMessageEvent) -> bool:
    invitation = await invite_repo.get_by_group_id(str(event.group_id))
    if invitation is None:
        return False
    return invitation.inviter_id == event.get_user_id()


async def has_remove_permission(event: GroupMessageEvent) -> bool:
    return is_group_admin(event) or await is_group_inviter(event)


async def notify_superusers(
    bot: Bot,
    *,
    locale: LocaleCode,
    group_id: str,
    group_name: str,
    operator_id: str,
    reason: str,
) -> None:
    message = tr(
        locale,
        "remove.report",
        group_id=group_id,
        group_name=group_name,
        operator_id=operator_id,
        reason=reason,
    )
    for superuser in config.SUPERUSERS:
        await bot.send_private_msg(user_id=int(superuser), message=message)


async def perform_remove(
    bot: Bot,
    matcher: Matcher,
    event: GroupMessageEvent,
    *,
    locale: LocaleCode,
    reason: str,
) -> None:
    reason_text = reason.strip()
    if not reason_text:
        await matcher.finish(tr(locale, "remove.reason.empty"))

    group_id = str(event.group_id)
    group_name = await resolve_group_name(bot, group_id)

    await matcher.send(tr(locale, "remove.farewell", reason=reason_text))
    try:
        await bot.set_group_leave(group_id=event.group_id)
    except ActionFailed as exc:
        logger.warning(f"[Remove] leave failed for group {group_id}: {exc}")
        await matcher.finish(tr(locale, "remove.leave_failed"))

    await group_repo.update_status(group_id, GroupStatus.LEFT)
    await notify_superusers(
        bot,
        locale=locale,
        group_id=group_id,
        group_name=group_name,
        operator_id=event.get_user_id(),
        reason=reason_text,
    )
    await matcher.finish(tr(locale, "remove.leave_success", group_name=group_name))


@remove_matcher.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    state: T_State,
) -> None:
    if state.get("remove_stage"):
        return
    locale = await resolve_locale(str(getattr(event, "group_id", "")) or None)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish(tr(locale, "remove.group_only"))
    if not await has_remove_permission(event):
        await matcher.finish(tr(locale, "remove.permission_denied"))
    state["remove_stage"] = "confirm"
    await matcher.reject(tr(locale, "remove.confirm.prompt"))


@remove_matcher.handle()
async def _(
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
) -> None:
    if state.get("remove_stage") != "confirm":
        return
    locale = await resolve_locale(str(event.group_id))
    confirm_text = event.message.extract_plain_text().strip()
    if not confirm_text:
        await matcher.reject(tr(locale, "remove.confirm.prompt"))
    if not is_remove_confirmed(confirm_text):
        await matcher.finish(tr(locale, "remove.cancelled"))
    state["remove_stage"] = "reason"
    await matcher.reject(tr(locale, "remove.reason.prompt"))


@remove_matcher.handle()
async def _(
    bot: Bot,
    matcher: Matcher,
    event: GroupMessageEvent,
    state: T_State,
) -> None:
    if state.get("remove_stage") != "reason":
        return
    locale = await resolve_locale(str(event.group_id))
    reason = event.message.extract_plain_text()
    if not reason.strip():
        await matcher.reject(tr(locale, "remove.reason.prompt"))
    await perform_remove(
        bot,
        matcher,
        event,
        locale=locale,
        reason=reason,
    )
