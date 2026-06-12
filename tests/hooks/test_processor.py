from __future__ import annotations

from collections.abc import Iterator
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from nonebot import message as message_module
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebug import App
import pytest

from tests.plugins.water.helpers import build_group_message_event


@pytest.fixture(autouse=True)
def _isolated_processor_module(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ModuleType]:
    original_run_preprocessors = message_module._run_preprocessors.copy()
    sys.modules.pop("src.hooks.processor", None)
    module = importlib.import_module("src.hooks.processor")

    try:
        yield module
    finally:
        message_module._run_preprocessors.clear()
        message_module._run_preprocessors.update(original_run_preprocessors)
        sys.modules.pop("src.hooks.processor", None)


@pytest.fixture(autouse=True)
def _stub_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_processor_module: ModuleType,
) -> None:
    processor_module = _isolated_processor_module
    monkeypatch.setattr(
        processor_module,
        "config",
        SimpleNamespace(
            DEBUG=True,
            DEV_TEST_GROUPS={"20001"},
            DEV_TEST_USERS={"10002"},
            IGNORED_USERS=set(),
            SUPERUSERS={"1"},
        ),
    )
    monkeypatch.setattr(processor_module, "sync_user_runtime", AsyncMock())
    monkeypatch.setattr(processor_module, "sync_group_runtime", AsyncMock())
    monkeypatch.setattr(processor_module, "sync_member_runtime", AsyncMock())
    monkeypatch.setattr(processor_module, "sync_members_from_api", AsyncMock())
    monkeypatch.setattr(
        processor_module.user_repo,
        "get_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        processor_module.group_repo,
        "get_group",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        processor_module.member_repo,
        "get_member",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        processor_module.blacklist_repo,
        "is_banned",
        AsyncMock(return_value=False),
    )


@pytest.mark.asyncio
async def test_debug_runtime_check_allows_dev_test_group(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = on_message(priority=1, block=True)
    handler_mock = AsyncMock()

    @matcher.handle()
    async def _() -> None:
        await handler_mock()

    event = build_group_message_event("#ping", user_id=10001, group_id=20001)

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    handler_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_debug_runtime_check_blocks_non_whitelisted_event(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = on_message(priority=1, block=True)
    handler_mock = AsyncMock()

    @matcher.handle()
    async def _() -> None:
        await handler_mock()

    event = build_group_message_event("#ping", user_id=10001, group_id=30001)

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    handler_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_runtime_check_allows_dev_test_user(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = on_message(priority=1, block=True)
    handler_mock = AsyncMock()

    @matcher.handle()
    async def _() -> None:
        await handler_mock()

    event = build_group_message_event("#ping", user_id=10002, group_id=30001)

    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)

    handler_mock.assert_awaited_once()
