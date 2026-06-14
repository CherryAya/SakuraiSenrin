from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.plugins.wordbank.handlers.passive import (
    PassiveImageRef,
    build_message_match_shapes,
    build_passive_response,
    build_rule_context,
    handle_passive_message,
    handle_passive_notice,
    resolve_message_image_ids,
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
                responses=(),
            ),
            trigger=RuntimeTrigger(
                id=21,
                trigger_group_id=12,
                trigger_text="晚安",
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
        SimpleNamespace(
            resolve_canonical_id_from_hints=lambda _hints: None,
            resolve_canonical_id=lambda _data, **_kwargs: None,
        ),
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
async def test_handle_passive_message_preserves_single_space_trigger() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    event = build_group_message_event(" ")
    match_message = AsyncMock(return_value=_selected(response_text="空格触发"))
    service = cast(
        WordbankService,
        SimpleNamespace(match_message=match_message),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            resolve_canonical_id_from_hints=lambda _hints: None,
            resolve_canonical_id=lambda _data, **_kwargs: None,
        ),
    )

    response = await handle_passive_message(
        bot,
        event,
        service,
        media_service,
    )

    assert response is not None
    assert response.text == "空格触发"
    first_shape = match_message.await_args_list[0].args[0]
    assert first_shape == shape_from_text(" ", preserve_blank_text=True)


@pytest.mark.asyncio
async def test_handle_passive_message_tries_original_message_after_to_me_strip() -> (
    None
):
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    event = build_group_message_event("的妙妙小工具")
    event.message = type(event.message)("的妙妙小工具")
    event.original_message = type(event.message)("凛凛的妙妙小工具")
    event.raw_message = "凛凛的妙妙小工具"
    event.to_me = True
    match_message = AsyncMock(side_effect=[None, _selected(response_text="完整命中")])
    service = cast(
        WordbankService,
        SimpleNamespace(match_message=match_message),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            resolve_canonical_id_from_hints=lambda _hints: None,
            resolve_canonical_id=lambda _data, **_kwargs: None,
        ),
    )

    response = await handle_passive_message(
        bot,
        event,
        service,
        media_service,
    )

    assert response is not None
    assert response.text == "完整命中"
    assert match_message.await_count == 2
    assert match_message.await_args_list[0].args[0] == shape_from_text("的妙妙小工具")
    assert match_message.await_args_list[1].args[0] == shape_from_text(
        "凛凛的妙妙小工具"
    )


def test_build_message_match_shapes_deduplicates_same_message_sources() -> None:
    event = build_group_message_event("晚安")
    event.original_message = type(event.message)("晚安")

    shapes = build_message_match_shapes(event, image_ids={})

    assert shapes == (("message", shape_from_text("晚安")),)


@pytest.mark.asyncio
async def test_handle_passive_message_uses_image_name_hints_before_download() -> None:
    from src.plugins.wordbank.handlers import passive as passive_module

    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    event = build_group_message_event(
        "[CQ:image,file=ABCDEF1234567890ABCDEF1234567890.PNG,url=https://example.test/static/abcdef1234567890abcdef1234567890.png?download=1]"
    )
    service = cast(
        WordbankService,
        SimpleNamespace(match_message=AsyncMock(return_value=None)),
    )
    resolve_canonical_id_from_hints = Mock(return_value=7)
    resolve_canonical_id = Mock(return_value=None)
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            resolve_canonical_id_from_hints=resolve_canonical_id_from_hints,
            resolve_canonical_id=resolve_canonical_id,
        ),
    )
    monkeypatch = pytest.MonkeyPatch()
    fetch_image_bytes = AsyncMock(return_value=b"image-bytes")
    monkeypatch.setattr(
        passive_module,
        "fetch_image_bytes",
        fetch_image_bytes,
    )

    try:
        await handle_passive_message(
            bot,
            event,
            service,
            media_service,
        )
    finally:
        monkeypatch.undo()

    assert resolve_canonical_id_from_hints.call_count == 1
    assert resolve_canonical_id_from_hints.call_args.args[0] == (
        "https://example.test/static/abcdef1234567890abcdef1234567890.png?download=1",
        "ABCDEF1234567890ABCDEF1234567890.PNG",
        "abcdef1234567890abcdef1234567890.png",
    )
    assert fetch_image_bytes.await_count == 0
    assert resolve_canonical_id.call_count == 0


@pytest.mark.asyncio
async def test_resolve_message_image_ids_reuses_shared_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import passive as passive_module

    created_clients: list[object] = []

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout
            self.closed = False
            created_clients.append(self)

        async def aclose(self) -> None:
            self.closed = True

    fetch_image_bytes = AsyncMock(side_effect=[b"first", b"second"])
    resolve_canonical_id = Mock(side_effect=[7, 8])
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            resolve_canonical_id_from_hints=Mock(return_value=None),
            resolve_canonical_id=resolve_canonical_id,
        ),
    )
    monkeypatch.setattr(passive_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(passive_module, "fetch_image_bytes", fetch_image_bytes)

    result = await resolve_message_image_ids(
        media_service,
        (
            PassiveImageRef(url="https://example.test/1.png"),
            PassiveImageRef(url="https://example.test/2.png"),
        ),
    )

    assert result == {0: 7, 1: 8}
    assert len(created_clients) == 1
    shared_client = created_clients[0]
    assert fetch_image_bytes.await_count == 2
    assert fetch_image_bytes.await_args_list[0].kwargs["client"] is shared_client
    assert fetch_image_bytes.await_args_list[1].kwargs["client"] is shared_client
    assert getattr(shared_client, "closed") is True


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
