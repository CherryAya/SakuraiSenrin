from collections.abc import AsyncIterator
from types import SimpleNamespace, TracebackType
from typing import Self, cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.bot import Bot
import pytest

from src.lib.interaction import is_revoke_signal
from src.plugins.wordbank.handlers import passive
from src.plugins.wordbank.handlers.passive import (
    build_event_triggers,
    build_passive_response,
    fetch_image_bytes,
    handle_passive_message,
    handle_passive_notice,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.matching import SelectedMatch
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleContext
from tests.plugins.water.helpers import (
    build_group_increase_event,
    build_group_message_event,
    build_group_poke_event,
)


class _FakeStreamResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.chunks = chunks
        self.headers = headers or {}
        self.iterated = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        self.iterated = True
        for chunk in self.chunks:
            yield chunk


class _FakeAsyncClient:
    response: _FakeStreamResponse

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    def stream(self, method: str, url: str) -> _FakeStreamResponse:
        assert method == "GET"
        assert url == "https://example.test/image.png"
        return self.response


def _selected(text: str = "我在") -> SimpleNamespace:
    return SimpleNamespace(
        response=SimpleNamespace(id=300, text=text),
        candidate=SimpleNamespace(
            entry=SimpleNamespace(id=12),
            trigger=SimpleNamespace(id=120),
        ),
    )


def test_build_passive_response_preserves_image_response_metadata() -> None:
    selected = cast(
        SelectedMatch,
        SimpleNamespace(
            response=SimpleNamespace(
                id=300,
                text="配图如下",
                kind="image",
                canonical_image_id=7,
            ),
            candidate=SimpleNamespace(
                entry=SimpleNamespace(id=12),
                trigger=SimpleNamespace(id=120),
            ),
        ),
    )

    response = build_passive_response(
        selected,
        context=cast(RuleContext, SimpleNamespace(group_id="20001", user_id="10001")),
        message_type="text",
    )

    assert response.text == "配图如下"
    assert response.response_kind == "image"
    assert response.response_canonical_image_id == 7


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
    match_event = AsyncMock(return_value=_selected("我在"))
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

    assert response is not None
    assert response.text == "我在"
    assert response.entry_id == 12
    assert response.trigger_id == 120
    assert response.response_id == 300
    assert response.message_type == "event"
    match_text.assert_awaited_once()
    match_event.assert_awaited_once()
    assert match_event.await_args_list[0].args == (("event:at", "event:mention"),)
    match_images.assert_not_awaited()


async def test_handle_passive_notice_matches_poke_and_join_events() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))
    match_event = AsyncMock(
        side_effect=[
            _selected("别戳啦"),
            _selected("欢迎"),
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

    assert poke_response is not None
    assert join_response is not None
    assert poke_response.text == "别戳啦"
    assert join_response.text == "欢迎"
    assert match_event.await_args_list[0].args == (("event:poke",),)
    assert match_event.await_args_list[1].args == (
        ("event:join", "event:group_join", "event:group_increase"),
    )


def test_build_event_triggers_ignores_poke_to_other_user() -> None:
    bot = cast(Bot, SimpleNamespace(self_id="99999"))

    assert build_event_triggers(build_group_poke_event(target_id=10002), bot) == ()


async def test_fetch_image_bytes_skips_content_length_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeStreamResponse(
        [b"body"],
        headers={"content-length": "6"},
    )
    _FakeAsyncClient.response = response
    monkeypatch.setattr(passive.httpx, "AsyncClient", _FakeAsyncClient)

    data = await fetch_image_bytes(
        "https://example.test/image.png",
        max_bytes=5,
    )

    assert data is None
    assert not response.iterated


async def test_fetch_image_bytes_skips_stream_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeStreamResponse([b"123", b"456"])
    _FakeAsyncClient.response = response
    monkeypatch.setattr(passive.httpx, "AsyncClient", _FakeAsyncClient)

    data = await fetch_image_bytes(
        "https://example.test/image.png",
        max_bytes=5,
    )

    assert data is None
    assert response.iterated
