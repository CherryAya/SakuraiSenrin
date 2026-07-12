from unittest.mock import AsyncMock

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.matcher import Matcher
from nonebug import App
import pytest

from src.plugins.water.handlers.achievement import handle_my_achievements
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_handle_my_achievements_returns_message(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.water.handlers import achievement as achievement_handler_module

    monkeypatch.setattr(
        achievement_handler_module.water_repo,
        "get_or_create_group_matrix_id",
        AsyncMock(return_value="abcd1234"),
    )
    monkeypatch.setattr(
        achievement_handler_module.achievement_service,
        "build_user_achievement_message",
        AsyncMock(return_value="ACHIEVEMENT_MESSAGE"),
    )

    matcher = on_message(priority=1, block=True)

    @matcher.handle()
    async def _(
        matcher: Matcher,
        event: GroupMessageEvent,
    ) -> None:
        await handle_my_achievements(matcher, event, "zh-CN")

    event = build_group_message_event("我的水王成就")

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "ACHIEVEMENT_MESSAGE", bot=bot)
        ctx.should_finished(matcher)
