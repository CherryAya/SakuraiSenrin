from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.bot import Bot

from src.plugins.wordbank.handlers.passive import (
    handle_passive_message,
    is_revoke_signal,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from tests.plugins.water.helpers import build_group_message_event


def test_is_revoke_signal_detects_event_fields_and_segments() -> None:
    assert is_revoke_signal(SimpleNamespace(notice_type="group_recall"))
    assert is_revoke_signal(SimpleNamespace(sub_type="message_revoke"))
    assert is_revoke_signal(
        SimpleNamespace(
            message=[
                SimpleNamespace(type="text", data={"action": "recall_message"}),
            ]
        )
    )
    assert not is_revoke_signal(SimpleNamespace(notice_type="group_increase"))


async def test_handle_passive_message_aborts_on_revoke_signal() -> None:
    event = build_group_message_event("recall 晚安啦")
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    match_text = AsyncMock()
    match_images = AsyncMock()
    service = cast(
        WordbankService,
        SimpleNamespace(match_text=match_text, match_images=match_images),
    )
    media_service = cast(WordbankMediaService, SimpleNamespace())

    response = await handle_passive_message(bot, event, service, media_service)

    assert response is None
    match_text.assert_not_awaited()
    match_images.assert_not_awaited()
