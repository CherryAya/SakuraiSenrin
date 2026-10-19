from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11 import ActionFailed, Bot
from nonebug import App
import pytest

from src.lib.request_flags import is_valid_request_flag
from src.services.invitation import (
    InvitationStateError,
    invitation_event_names,
    invitation_lock,
    is_bot_in_group,
    read_invitation_group_state,
)
from tests.plugins.notice.helpers import build_bot_join_event, build_group_request_event


@pytest.mark.parametrize("flag", [None, "", " ", "\t\r\n", "\u3000", 1, False, b"flag"])
def test_request_flag_requires_nonblank_text(flag: object) -> None:
    assert is_valid_request_flag(flag) is False


@pytest.mark.parametrize("flag", ["flag-1", "slreq:opaque", " opaque token "])
def test_valid_request_flag_is_not_normalized(flag: str) -> None:
    assert is_valid_request_flag(flag) is True


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ([], False),
        ([{"group_id": 20001}], True),
        ([{"group_id": "20001"}, {"group_id": 20002}], True),
        ([{"group_id": 20002}], False),
    ],
)
async def test_membership_uses_live_group_list(
    app: App, result: object, expected: bool
) -> None:
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api("get_group_list", {}, result=result)
        assert await is_bot_in_group(bot, "20001") is expected


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        "20001",
        [None],
        [{}],
        [{"group_id": True}],
        [{"group_id": "invalid"}],
        [{"group_id": 0}],
        [{"group_id": 20001}, {"group_id": None}],
    ],
)
async def test_invalid_membership_response_never_proves_absence(
    app: App, result: object
) -> None:
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api("get_group_list", {}, result=result)
        with pytest.raises(InvitationStateError):
            await is_bot_in_group(bot, "20001")


async def test_membership_platform_failure_remains_unknown(app: App) -> None:
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api("get_group_list", {}, exception=ActionFailed(retcode=100))
        with pytest.raises(InvitationStateError) as raised:
            await is_bot_in_group(bot, "20001")
        assert isinstance(raised.value.__cause__, ActionFailed)


@pytest.mark.parametrize("name", [None, "", " \t", 1, {"name": "invalid"}, "新群名"])
async def test_membership_reuses_only_the_matching_groups_valid_name(
    app: App, name: object
) -> None:
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="99999")
        ctx.should_call_api(
            "get_group_list",
            {},
            result=[
                {"group_id": 20002, "group_name": "其他群"},
                {"group_id": 20001, "group_name": name},
            ],
        )
        state = await read_invitation_group_state(bot, "20001")
    assert state.joined is True
    assert state.group_name == ("新群名" if name == "新群名" else None)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"group_name": "事件群", "nickname": "邀请者"}, ("事件群", "邀请者")),
        ({"group_name": [], "nickname": " \t"}, (None, None)),
        ({"inviter_name": "邀请者"}, (None, "邀请者")),
        ({"sender": {"user_id": 10001, "nickname": "邀请者"}}, (None, "邀请者")),
        ({"sender": {"user_id": "10001", "nickname": "邀请者"}}, (None, "邀请者")),
        ({"sender": {"user_id": 99999, "nickname": "机器人"}}, (None, None)),
        ({"sender": {"nickname": "身份不明"}}, (None, None)),
    ],
)
def test_request_event_names_require_valid_text_and_matching_identity(
    extra: dict[str, object], expected: tuple[str | None, str | None]
) -> None:
    event = build_group_request_event(extra=extra)
    assert invitation_event_names(event, "10001") == expected
    assert invitation_event_names(event, "10002")[1] is None


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            {"nickname": "机器人", "sender": {"user_id": 99999, "nickname": "机器人"}},
            None,
        ),
        ({"operator_name": "邀请者"}, "邀请者"),
        ({"operator": {"user_id": 10001, "nickname": "邀请者"}}, "邀请者"),
        ({"operator": {"user_id": 10002, "nickname": "其他人"}}, None),
        ({"operator_name": " ", "operator": []}, None),
    ],
)
def test_join_notice_never_mistakes_the_bots_nickname_for_the_inviter(
    extra: dict[str, object], expected: str | None
) -> None:
    event = build_bot_join_event(extra=extra)
    assert invitation_event_names(event, "10001")[1] == expected


async def test_event_and_approval_lock_serializes_only_same_bot_and_group() -> None:
    entered = asyncio.Event()
    waiting = asyncio.Event()
    actions: list[str] = []

    async def approval() -> None:
        waiting.set()
        async with invitation_lock("99999", "20001"):
            actions.append("approval")
            entered.set()

    async with invitation_lock("99999", "20001"):
        actions.append("event")
        task = asyncio.create_task(approval())
        await waiting.wait()
        assert not entered.is_set()
        # 其他群和其他机器人无需等待当前群的处理。
        async with invitation_lock("99999", "20002"):
            actions.append("other_group")
        async with invitation_lock("88888", "20001"):
            actions.append("other_bot")
    await asyncio.wait_for(task, timeout=5)
    assert actions == ["event", "other_group", "other_bot", "approval"]
