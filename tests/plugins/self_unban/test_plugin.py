from __future__ import annotations

from collections.abc import Generator
import importlib
from pathlib import Path
import sys
import types
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from nonebot import message as message_module
from nonebot.adapters.onebot.v11 import Bot
from nonebug import App
import pytest

from src.lib.consts import GLOBAL_GROUP_FLAG

_ROOT = Path(__file__).resolve().parents[3]
_SELF_UNBAN_PKG_PATH = _ROOT / "src" / "plugins" / "self_unban"
if "src.plugins.self_unban" not in sys.modules:
    pkg = types.ModuleType("src.plugins.self_unban")
    pkg.__path__ = [str(_SELF_UNBAN_PKG_PATH)]  # type: ignore[attr-defined]
    sys.modules["src.plugins.self_unban"] = pkg

from src.plugins.self_unban.services.core import (
    ManagedBannedGroupOption,
    PreparedSelfUnbanRequest,
    SelfUnbanSelectionSession,
    self_unban_service,
)
from tests.plugins.water.helpers import (
    build_private_message_event,
)


@pytest.fixture(autouse=True)
async def _reset_self_unban_service_state() -> None:
    await self_unban_service.reset_runtime_state()


@pytest.fixture
def processor_module(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[ModuleType, None, None]:
    original_run_preprocessors = message_module._run_preprocessors.copy()
    sys.modules.pop("src.hooks.processor", None)
    module = importlib.import_module("src.hooks.processor")
    monkeypatch.setattr(
        module,
        "config",
        SimpleNamespace(
            DEBUG=False,
            DEV_TEST_GROUPS=set(),
            DEV_TEST_USERS=set(),
            IGNORED_USERS=set(),
            SUPERUSERS={"1"},
        ),
    )
    monkeypatch.setattr(module, "sync_user_runtime", AsyncMock())
    monkeypatch.setattr(module, "sync_group_runtime", AsyncMock())
    monkeypatch.setattr(module, "sync_member_runtime", AsyncMock())
    monkeypatch.setattr(module, "sync_members_from_api", AsyncMock())
    monkeypatch.setattr(module.user_repo, "get_user", AsyncMock(return_value=None))
    monkeypatch.setattr(module.group_repo, "get_group", AsyncMock(return_value=None))
    monkeypatch.setattr(module.member_repo, "get_member", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "is_restore_in_progress", lambda: False)
    try:
        yield module
    finally:
        message_module._run_preprocessors.clear()
        message_module._run_preprocessors.update(original_run_preprocessors)
        sys.modules.pop("src.hooks.processor", None)


@pytest.mark.asyncio
async def test_self_unban_guided_group_flow_prompts_select_and_finishes(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("src.plugins.self_unban", None)
    from src.plugins.self_unban import self_unban_matcher

    command_module = importlib.import_module("src.plugins.self_unban.handlers.command")
    session = SelfUnbanSelectionSession(
        requester_user_id="10001",
        locale="zh-CN",
        user_candidate=PreparedSelfUnbanRequest(
            kind="user",
            subject_type="user",
            subject_id="10001",
            scope_group_id=GLOBAL_GROUP_FLAG,
            requester_user_id="10001",
            user_remaining_attempts_before=2,
            locale="zh-CN",
            source_hint="private_global",
        ),
        group_candidates=(
            ManagedBannedGroupOption(
                index=1,
                group_id="20001",
                group_name="测试群",
                prepared=PreparedSelfUnbanRequest(
                    kind="group",
                    subject_type="group",
                    subject_id="20001",
                    scope_group_id="20001",
                    requester_user_id="10001",
                    user_remaining_attempts_before=2,
                    group_remaining_attempts_before=1,
                    locale="zh-CN",
                    source_hint="managed_group",
                    target_group_name="测试群",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        command_module,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        command_module.self_unban_service,
        "prepare_selection_session",
        AsyncMock(return_value=session),
    )
    monkeypatch.setattr(
        command_module.self_unban_service,
        "submit_request",
        AsyncMock(
            return_value=SimpleNamespace(
                final_message=(
                    "已解除群 测试群（20001）封禁，当前状态恢复为已授权。"
                    "你的额度已用 1/2 次，剩余 1 次；本群额度已用 2/2 次，剩余 0 次。"
                ),
                should_retry=False,
            )
        ),
    )

    first = build_private_message_event("#自助解封", user_id=10001)
    second = build_private_message_event("2", user_id=10001, message_id=2)
    third = build_private_message_event("1", user_id=10001, message_id=3)
    fourth = build_private_message_event(
        "原先的风控问题已经处理完成，后续不会再出现禁言机器人行为",
        user_id=10001,
        message_id=4,
    )

    async with app.test_matcher(self_unban_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择要解封的对象类型：\n1. 解封自己\n2. 解封群聊",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            (
                "请选择要解封的群聊，回复序号、群号或群名：\n"
                "1. 测试群（20001）- 你的剩余额度 2 次 / 本群剩余额度 1 次"
            ),
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            (
                "请输入群 测试群（20001）的自助解封理由（至少 10 个字）。"
                "本次通过后会同时消耗你 1 次额度和本群 1 次额度。"
                "你当前剩余 2 次，本群剩余 1 次。"
            ),
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, fourth)
        ctx.should_call_send(
            fourth,
            (
                "已解除群 测试群（20001）封禁，当前状态恢复为已授权。"
                "你的额度已用 1/2 次，剩余 1 次；本群额度已用 2/2 次，剩余 0 次。"
            ),
            bot=bot,
        )
        ctx.should_finished(self_unban_matcher)


@pytest.mark.asyncio
async def test_self_unban_invalid_group_choice_rejects_again(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("src.plugins.self_unban", None)
    from src.plugins.self_unban import self_unban_matcher

    command_module = importlib.import_module("src.plugins.self_unban.handlers.command")
    session = SelfUnbanSelectionSession(
        requester_user_id="10001",
        locale="zh-CN",
        user_candidate=None,
        group_candidates=(
            ManagedBannedGroupOption(
                index=1,
                group_id="20001",
                group_name="测试群",
                prepared=PreparedSelfUnbanRequest(
                    kind="group",
                    subject_type="group",
                    subject_id="20001",
                    scope_group_id="20001",
                    requester_user_id="10001",
                    user_remaining_attempts_before=2,
                    group_remaining_attempts_before=2,
                    locale="zh-CN",
                    source_hint="managed_group",
                    target_group_name="测试群",
                ),
            ),
            ManagedBannedGroupOption(
                index=2,
                group_id="20002",
                group_name="备用群",
                prepared=PreparedSelfUnbanRequest(
                    kind="group",
                    subject_type="group",
                    subject_id="20002",
                    scope_group_id="20002",
                    requester_user_id="10001",
                    user_remaining_attempts_before=2,
                    group_remaining_attempts_before=1,
                    locale="zh-CN",
                    source_hint="managed_group",
                    target_group_name="备用群",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        command_module,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        command_module.self_unban_service,
        "prepare_selection_session",
        AsyncMock(return_value=session),
    )
    first = build_private_message_event("#self.unban", user_id=10001)
    second = build_private_message_event("2", user_id=10001, message_id=2)
    third = build_private_message_event("999", user_id=10001, message_id=3)

    async with app.test_matcher(self_unban_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, first)
        ctx.should_call_send(
            first,
            "请选择要解封的对象类型：\n1. 解封自己（当前不可用）\n2. 解封群聊",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            (
                "请选择要解封的群聊，回复序号、群号或群名：\n"
                "1. 测试群（20001）- 你的剩余额度 2 次 / 本群剩余额度 2 次\n"
                "2. 备用群（20002）- 你的剩余额度 2 次 / 本群剩余额度 1 次"
            ),
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, third)
        ctx.should_call_send(
            third,
            "群聊选择无效，请回复列表中的序号、群号或群名。",
            bot=bot,
        )
        ctx.should_rejected()


@pytest.mark.asyncio
async def test_self_unban_no_check_bypasses_runtime_blacklist_block(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
    processor_module: ModuleType,
) -> None:
    sys.modules.pop("src.plugins.self_unban", None)
    from src.plugins.self_unban import self_unban_matcher

    command_module = importlib.import_module("src.plugins.self_unban.handlers.command")
    monkeypatch.setattr(
        processor_module.blacklist_repo,
        "is_banned",
        AsyncMock(return_value=True),
    )
    cast(Any, self_unban_matcher).plugin = SimpleNamespace(
        metadata=SimpleNamespace(extra={"no_check": True})
    )
    session = SelfUnbanSelectionSession(
        requester_user_id="10001",
        locale="zh-CN",
        user_candidate=PreparedSelfUnbanRequest(
            kind="user",
            subject_type="user",
            subject_id="10001",
            scope_group_id=GLOBAL_GROUP_FLAG,
            requester_user_id="10001",
            user_remaining_attempts_before=2,
            locale="zh-CN",
            source_hint="private_global",
        ),
        group_candidates=(),
    )
    monkeypatch.setattr(
        command_module,
        "resolve_locale",
        AsyncMock(return_value="zh-CN"),
    )
    monkeypatch.setattr(
        command_module.self_unban_service,
        "prepare_selection_session",
        AsyncMock(return_value=session),
    )
    event = build_private_message_event("#自助解封", user_id=10001)
    second = build_private_message_event("1", user_id=10001, message_id=2)

    async with app.test_matcher(self_unban_matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            "请选择要解封的对象类型：\n1. 解封自己\n2. 解封群聊（当前不可用）",
            bot=bot,
        )
        ctx.should_rejected()

        ctx.receive_event(bot, second)
        ctx.should_call_send(
            second,
            (
                "请输入本次全局自助解封理由（至少 10 个字）。"
                "本次通过后会消耗 1 次额度，当前剩余 2 次。"
            ),
            bot=bot,
        )
        ctx.should_rejected()
