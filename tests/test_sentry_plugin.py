# 测试/脚本需直接验证内部不变量，故关闭跨模块私有符号告警
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from collections.abc import Callable
from textwrap import dedent
from typing import Any, cast

from nonebot.adapters.onebot.v11 import ActionFailed
import pytest
from sentry_sdk.types import Event

from src.lib.error_context import (
    bind_error_context,
    reset_error_context,
    snapshot_error_context,
)
from src.lib.i18n.keys import MessageKey
from src.plugins.sentry import (
    _should_downgrade_level,
    _should_drop_event,
    before_send_handler,
    notify_admin,
)
from src.plugins.wordbank.services.errors import WordbankUserError


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


def _event_dict(event: Event) -> dict[str, Any]:
    """TypedDict 下访问非必填键会报错，统一走 Any 读取。"""
    return cast(dict[str, Any], event)


def _event_contexts(event: Event) -> dict[str, Any]:
    return cast(dict[str, Any], _event_dict(event).get("contexts") or {})


def _event_tags(event: Event) -> list[dict[str, str]]:
    return cast(list[dict[str, str]], _event_dict(event).get("tags") or [])


def _run_before_send(
    monkeypatch: pytest.MonkeyPatch,
    event: Event,
    exc: BaseException,
) -> list[str]:
    """驱动 before_send_handler 并回收内部调度出去的告警任务。"""
    recorded_messages: list[str] = []

    async def _fake_notify(error_message: str) -> None:
        recorded_messages.append(error_message)

    loop = asyncio.new_event_loop()
    monkeypatch.setattr("src.plugins.sentry.notify_admin", _fake_notify)
    monkeypatch.setattr("src.plugins.sentry.background_tasks", set())
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    try:
        before_send_handler(event, {"exc_info": (type(exc), exc, None)})
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        loop.run_until_complete(asyncio.gather(*pending))
    finally:
        loop.close()
    return recorded_messages


def test_before_send_handler_attaches_error_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_error_context()
    bind_error_context(
        group_id="10001",
        group_name="测试群",
        user_id="20002",
        user_name=" tester ",
        message_id="30003",
        message_type="group",
        event_type="GroupMessageEvent",
        matcher_module="src.plugins.study",
    )
    event = cast(Event, {"message": "ctx"})
    try:
        recorded = _run_before_send(monkeypatch, event, RuntimeError("boom"))
    finally:
        reset_error_context()

    assert len(recorded) == 1
    assert "Type: RuntimeError" in recorded[0]
    assert "Value: boom" in recorded[0]
    assert "来源群: 10001(测试群)" in recorded[0]
    assert "触发者: 20002(tester)" in recorded[0]
    assert "消息 ID: 30003 [group]" in recorded[0]
    assert "事件类型: GroupMessageEvent" in recorded[0]
    assert "处理器: src.plugins.study" in recorded[0]

    contexts = _event_contexts(event)
    assert contexts["senrin"]["group_id"] == "10001"
    assert contexts["senrin"]["user_id"] == "20002"
    assert {"key": "ctx_group_id", "value": "10001"} in _event_tags(event)


def test_before_send_handler_skips_context_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_error_context()
    event = cast(Event, {"message": "no-ctx"})
    try:
        recorded = _run_before_send(monkeypatch, event, RuntimeError("boom"))
    finally:
        reset_error_context()

    assert recorded == ["Type: RuntimeError\nValue: boom"]
    assert "contexts" not in event


def test_error_context_is_isolated_between_events() -> None:
    reset_error_context()
    try:
        bind_error_context(group_id="111", user_id="222")
        first = snapshot_error_context()
        assert first.group_id == "111"

        reset_error_context()
        bind_error_context(group_id="999")
        second = snapshot_error_context()
        assert second.group_id == "999"
        assert second.user_id == ""
    finally:
        reset_error_context()


_FORWARD_TOO_MANY_KEY: MessageKey = "wordbank.error.forward_message_too_many"


def _build_action_failed(wording: str) -> ActionFailed:
    """按 nonebot ActionFailed 的真实契约构造异常。"""

    def _init(self: object, *, wording: str = "") -> None:
        self.wording = wording  # type: ignore[attr-defined]
        self.info = {  # type: ignore[attr-defined]
            "status": "failed",
            "retcode": 100,
            "data": None,
            "wording": wording,
            "echo": None,
        }

    fake_type = type("FakeActionFailed", (ActionFailed,), {"__init__": _init})
    return cast(ActionFailed, fake_type(wording=wording))


@pytest.mark.parametrize(
    "wording",
    [
        "send group message rejected: result=299 err=本群每分钟只能发10条消息",
        "OIDB error 120162002 on 0x10c8_1: already agree msg",
        "send private message rejected: result=16 err=发送失败，请先添加对方为好友",
        # 未知文案也必须降级：判定只看异常类型，不依赖文案
        "some brand new platform rejection nobody has seen before",
    ],
)
def test_any_action_failed_is_downgraded_regardless_of_wording(wording: str) -> None:
    assert _should_downgrade_level(_build_action_failed(wording))


def test_non_action_failed_is_not_downgraded() -> None:
    assert not _should_downgrade_level(RuntimeError("boom"))
    assert not _should_downgrade_level(
        WordbankUserError("too many nodes", key=_FORWARD_TOO_MANY_KEY)
    )
    assert not _should_downgrade_level(None)


def test_before_send_handler_downgrades_action_failed_but_still_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ActionFailed 只降级 Sentry level，管理员通报必须照常发出。"""
    reset_error_context()
    bind_error_context(group_id="10001", user_id="20002", message_id="30003")
    exc = _build_action_failed(
        "send group message rejected: result=299 err=本群每分钟只能发10条消息"
    )
    event = cast(Event, {"message": "rate-limited", "level": "error"})
    try:
        recorded = _run_before_send(monkeypatch, event, exc)
    finally:
        reset_error_context()

    assert _event_dict(event)["level"] == "warning"
    assert _event_contexts(event)["default"]["level"] == "warning"
    assert {"key": "action_failed", "value": "true"} in _event_tags(event)

    # 关键断言：业务异常不能被静默，必须通报管理员，且带完整上下文
    assert len(recorded) == 1
    assert "Type: FakeActionFailed" in recorded[0]
    assert "本群每分钟只能发10条消息" in recorded[0]
    assert "来源群: 10001" in recorded[0]
    assert "触发者: 20002" in recorded[0]
    assert "消息 ID: 30003" in recorded[0]


def test_before_send_handler_never_drops_business_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """除心跳噪声外，任何异常都不得被 before_send 丢弃。"""
    reset_error_context()
    for exc in (
        _build_action_failed("already agree msg"),
        WordbankUserError("合并转发节点过多", key=_FORWARD_TOO_MANY_KEY),
        RuntimeError("boom"),
        ValueError("bad"),
    ):
        event = cast(Event, {"message": "x"})
        recorded = _run_before_send(monkeypatch, event, exc)
        assert event is not None, f"{type(exc).__name__} 不应被丢弃"
        assert len(recorded) == 1, f"{type(exc).__name__} 应通报管理员"


def test_notify_admin_swallows_missing_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> None:
        raise ValueError("There are no bots to get.")

    monkeypatch.setattr("src.plugins.sentry.get_bot", _raise)

    # 不应抛出，避免告警失败再次触发 Sentry 形成自激循环。
    asyncio.run(notify_admin("boom"))
