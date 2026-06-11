from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.plugins.wordbank.handlers.passive import (
    build_passive_response,
    build_rule_context,
    handle_passive_message,
    handle_passive_notice,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    shape_from_event,
    shape_from_text,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.matching import (
    MatchCandidate,
    RuntimeEntry,
    RuntimeResponse,
    RuntimeTrigger,
    SelectedMatch,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from tests.plugins.water.helpers import (
    build_group_increase_event,
    build_group_message_event,
    build_group_poke_event,
)


def _selected(
    *,
    response_shape: MessageShape | None = None,
    response_text: str = "收到",
) -> SelectedMatch:
    return SelectedMatch(
        candidate=MatchCandidate(
            group=RuntimeEntry(
                id=12,
                status="approved",
                enabled=1,
                group_id="20001",
                created_by="10001",
                trigger_mode="strict",
                responses=(),
            ),
            trigger=RuntimeTrigger(
                id=21,
                trigger_group_id=12,
                trigger_text="晚安",
                trigger_mode="strict",
                message_shape=shape_from_text("晚安"),
                exact_md5="md5",
                structure_key="text",
            ),
        ),
        response=RuntimeResponse(
            id=22,
            trigger_group_id=12,
            text=response_text,
            message_shape=response_shape or shape_from_text(response_text),
            exact_md5="response-md5",
            status="approved",
            enabled=1,
            scope="current_group",
            priority=1,
            probability=1.0,
            weight=1,
            rule={},
            group_id="20001",
            created_by="10001",
        ),
    )


def test_build_passive_response_preserves_message_shape() -> None:
    event = build_group_message_event("晚安")
    context = build_rule_context(event)
    selected = _selected(response_shape=shape_from_text("做个好梦"))

    response = build_passive_response(
        selected,
        context=context,
        message_type="message",
    )

    assert response.trigger_group_id == 12
    assert response.response_item_id == 22
    assert response.response_shape == shape_from_text("做个好梦")


@pytest.mark.asyncio
async def test_handle_passive_message_falls_back_to_at_event() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    event = build_group_message_event("[CQ:at,qq=99999]")
    match_message = AsyncMock(side_effect=[None, _selected(response_text="收到@我了")])
    service = cast(
        WordbankService,
        SimpleNamespace(match_message=match_message),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(resolve_canonical_id=lambda _data: None),
    )

    response = await handle_passive_message(
        bot,
        event,
        service,
        media_service,
    )

    assert response is not None
    assert response.text == "收到@我了"
    assert match_message.await_count == 2
    second_shape = match_message.await_args_list[1].args[0]
    assert second_shape == shape_from_event("event:at")


@pytest.mark.asyncio
async def test_handle_passive_notice_matches_poke_event() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    event = build_group_poke_event(target_id=99999)
    service = cast(
        WordbankService,
        SimpleNamespace(match_message=AsyncMock(return_value=_selected())),
    )

    response = await handle_passive_notice(bot, event, service)

    assert response is not None
    assert response.message_type == "event"


@pytest.mark.asyncio
async def test_handle_passive_notice_matches_group_join_event() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    event = build_group_increase_event()
    service = cast(
        WordbankService,
        SimpleNamespace(match_message=AsyncMock(return_value=_selected())),
    )

    response = await handle_passive_notice(bot, event, service)

    assert response is not None
    assert response.message_type == "event"
