from __future__ import annotations

import asyncio
from collections.abc import Callable
from textwrap import dedent
from typing import cast

import pytest
from sentry_sdk.types import Event

from src.plugins.sentry import _should_drop_event, before_send_handler


def _build_websocket_keepalive_assertion() -> AssertionError:
    namespace: dict[str, object] = {}
    exec(
        dedent(
            """
            def _drain_helper():
                raise AssertionError

            def keepalive_ping():
                _drain_helper()
            """
        ),
        namespace,
    )
    namespace["__name__"] = "websockets.legacy.protocol"
    keepalive_ping = namespace["keepalive_ping"]
    assert callable(keepalive_ping)
    try:
        cast(Callable[[], None], keepalive_ping)()
    except AssertionError as exc:
        return exc
    raise AssertionError("expected keepalive assertion")


def test_should_drop_event_for_websocket_keepalive_assertion() -> None:
    exc = _build_websocket_keepalive_assertion()

    assert _should_drop_event({"exc_info": (AssertionError, exc, exc.__traceback__)})


def test_should_not_drop_other_assertions() -> None:
    exc = AssertionError("boom")

    assert not _should_drop_event({"exc_info": (AssertionError, exc, None)})


def test_before_send_handler_drops_websocket_keepalive_noise() -> None:
    exc = _build_websocket_keepalive_assertion()

    result = before_send_handler(
        cast(Event, {"message": "ignored"}),
        {"exc_info": (AssertionError, exc, exc.__traceback__)},
    )

    assert result is None


def test_before_send_handler_keeps_real_errors_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = cast(Event, {"message": "keep"})
    exc = RuntimeError("boom")
    recorded_messages: list[str] = []

    async def _fake_notify(error_message: str) -> None:
        recorded_messages.append(error_message)

    loop = asyncio.new_event_loop()
    monkeypatch.setattr("src.plugins.sentry.notify_admin", _fake_notify)
    monkeypatch.setattr("src.plugins.sentry.background_tasks", set())
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    try:
        result = before_send_handler(
            event,
            {"exc_info": (RuntimeError, exc, None)},
        )
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        loop.run_until_complete(asyncio.gather(*pending))
    finally:
        loop.close()

    assert result is event
    assert recorded_messages == ["Type: RuntimeError\nValue: boom"]
