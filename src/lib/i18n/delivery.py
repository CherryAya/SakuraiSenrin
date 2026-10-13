"""i18n 事件绑定：语言解析与发送。

依赖 nonebot matcher、message_delivery 与 repositories，与纯查表的 catalog 分离。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import Event
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher

from src.lib.message_delivery import (
    DeliveryResult,
    DeliveryTarget,
    deliver_single_message,
)
from src.lib.message_plan import finish_with_message, send_with_message
from src.lib.messages import text_message
from src.repositories import i18n_repo

from .catalog import tr
from .keys import MessageKey
from .types import LocaleCode


def msg(locale_code: LocaleCode, key: MessageKey, **params: object) -> Message:
    return text_message(tr(locale_code, key, **params))


def get_group_locale(event: Event | None) -> str | None:
    if event is None:
        return None
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None
    return str(group_id)


async def resolve_locale(group_id: str | None = None) -> LocaleCode:
    return await i18n_repo.resolve_locale(group_id)


def _resolve_event_delivery_target(event: Event | None) -> DeliveryTarget | None:
    if event is None:
        return None
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return DeliveryTarget(kind="group", target_id=str(group_id))
    user_id = getattr(event, "user_id", None)
    if user_id is not None:
        return DeliveryTarget(kind="private", target_id=str(user_id))
    return None


async def send_i18n(
    matcher: Matcher,
    event: Event | None,
    key: MessageKey,
    **params: object,
) -> DeliveryResult | None:
    locale = await resolve_locale(get_group_locale(event))
    bot = getattr(matcher, "bot", None)
    target = _resolve_event_delivery_target(event)
    message = msg(locale, key, **params)
    if (
        bot is not None
        and hasattr(bot, "call_api")
        and hasattr(bot, "self_id")
        and target is not None
    ):
        return await deliver_single_message(
            bot,
            target=target,
            message=message,
            source_kind="i18n_matcher_send",
        )
    return await send_with_message(matcher, message=message)


async def finish_i18n(
    matcher: Matcher,
    event: Event | None,
    key: MessageKey,
    **params: object,
) -> None:
    locale = await resolve_locale(get_group_locale(event))
    bot = getattr(matcher, "bot", None)
    target = _resolve_event_delivery_target(event)
    message = msg(locale, key, **params)
    if (
        bot is not None
        and hasattr(bot, "call_api")
        and hasattr(bot, "self_id")
        and target is not None
    ):
        await deliver_single_message(
            bot,
            target=target,
            message=message,
            source_kind="i18n_matcher_finish",
        )
        # getattr 取 finish：绕开 pyright 泛型
        finish = getattr(matcher, "finish")
        await finish()
        return
    await finish_with_message(
        None,
        matcher,
        message=message,
        source_kind="i18n_matcher_finish",
    )


async def send_private_i18n(
    bot: Bot,
    target_user_id: int,
    key: MessageKey,
    *,
    locale_group_id: str | None = None,
    **params: object,
) -> dict[str, str]:
    locale = await resolve_locale(locale_group_id)
    result = await deliver_single_message(
        bot,
        target=DeliveryTarget(kind="private", target_id=str(target_user_id)),
        message=msg(locale, key, **params),
        source_kind="i18n_private",
    )
    return {"message_id": result.message_id}


async def send_group_i18n(
    bot: Bot,
    group_id: int,
    key: MessageKey,
    **params: object,
) -> dict[str, str]:
    locale = await resolve_locale(str(group_id))
    result = await deliver_single_message(
        bot,
        target=DeliveryTarget(kind="group", target_id=str(group_id)),
        message=msg(locale, key, **params),
        source_kind="i18n_group",
    )
    return {"message_id": result.message_id}
