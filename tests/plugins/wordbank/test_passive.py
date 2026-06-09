from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.bot import Bot

from src.plugins.wordbank.handlers.passive import (
    build_event_triggers,
    handle_passive_message,
    handle_passive_notice,
    is_revoke_signal,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from tests.plugins.water.helpers import (
    build_group_increase_event,
    build_group_message_event,
    build_group_poke_event,
)


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
    match_event = AsyncMock()
    match_images = AsyncMock()
    service = cast(
        WordbankService,
        SimpleNamespace(
            match_text=match_text,
            match_event=match_event,
            match_images=match_images,
        ),
    )
    media_service = cast(WordbankMediaService, SimpleNamespace())

    response = await handle_passive_message(bot, event, service, media_service)

    assert response is None
    match_text.assert_not_awaited()
    match_event.assert_not_awaited()
    match_images.assert_not_awaited()


async def test_handle_passive_message_matches_at_event_after_text_miss() -> None:
    event = build_group_message_event("[CQ:at,qq=99999] 在吗")
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    match_text = AsyncMock(return_value=None)
    match_event = AsyncMock(
        return_value=SimpleNamespace(response=SimpleNamespace(text="我在"))
    )
    match_images = AsyncMock()
    service = cast(
        WordbankService,
        SimpleNamespace(
            match_text=match_text,
            match_event=match_event,
            match_images=match_images,
        ),
    )
    media_service = cast(WordbankMediaService, SimpleNamespace())

    response = await handle_passive_message(bot, event, service, media_service)

    assert response == "我在"
    match_text.assert_awaited_once()
    match_event.assert_awaited_once()
    assert match_event.await_args_list[0].args == (("event:at", "event:mention"),)
    match_images.assert_not_awaited()


async def test_handle_passive_notice_matches_poke_and_join_events() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    match_event = AsyncMock(
        side_effect=[
            SimpleNamespace(response=SimpleNamespace(text="别戳啦")),
            SimpleNamespace(response=SimpleNamespace(text="欢迎")),
        ]
    )
    service = cast(WordbankService, SimpleNamespace(match_event=match_event))

    poke_response = await handle_passive_notice(
        bot,
        build_group_poke_event(target_id=99999),
        service,
    )
    join_response = await handle_passive_notice(
        bot,
        build_group_increase_event(),
        service,
    )

    assert poke_response == "别戳啦"
    assert join_response == "欢迎"
    assert match_event.await_args_list[0].args == (("event:poke",),)
    assert match_event.await_args_list[1].args == (
        ("event:join", "event:group_join", "event:group_increase"),
    )


def test_build_event_triggers_ignores_poke_to_other_user() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))

    assert build_event_triggers(build_group_poke_event(target_id=10002), bot) == ()
