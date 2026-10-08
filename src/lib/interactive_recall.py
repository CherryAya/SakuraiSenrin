"""Helpers for rewinding interactive matchers by recalled message id."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from nonebot.adapters.onebot.v11.event import (
    FriendRecallNoticeEvent,
    GroupRecallNoticeEvent,
    MessageEvent,
    NoticeEvent,
)
from nonebot.dependencies import Dependent
from nonebot.matcher import Matcher, MatcherSource, matchers
from nonebot.permission import Permission
from nonebot.rule import Rule
from nonebot.typing import T_PermissionUpdater, T_State, T_TypeUpdater

from src.lib.message_plan import MessagePlanInput

RecallCleanup = Callable[[], Awaitable[None] | None]

INTERACTION_SESSION_KEY = "__interaction_session_key__"
INTERACTION_ROOT_MESSAGE_ID = "__interaction_root_message_id__"
INTERACTION_RECALL_CHECKPOINT = "__interaction_recall_checkpoint__"


@dataclass(slots=True, frozen=True)
class RecallCheckpoint:
    message_id: str
    step_index: int
    prompt: MessagePlanInput
    state_snapshot: dict[str, object]
    cleanup_keys: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class RecallSessionMatch:
    matcher_cls: type[Matcher]
    checkpoint: RecallCheckpoint | None
    is_root_message: bool


def build_interaction_session_key(event: MessageEvent | NoticeEvent) -> str:
    self_id = str(getattr(event, "self_id", "") or "")
    user_id = str(getattr(event, "user_id", "") or "")
    group_id = str(getattr(event, "group_id", "") or "")
    channel = f"group:{group_id}:{user_id}" if group_id else f"private:{user_id}"
    return f"{self_id}:{channel}"


def set_interaction_session_key(state: T_State, event: MessageEvent) -> str:
    session_key = build_interaction_session_key(event)
    state[INTERACTION_SESSION_KEY] = session_key
    return session_key


def get_interaction_session_key(state: Mapping[str, object]) -> str | None:
    value = state.get(INTERACTION_SESSION_KEY)
    return str(value) if value is not None else None


def register_root_message(state: T_State, event: MessageEvent) -> None:
    state[INTERACTION_ROOT_MESSAGE_ID] = str(getattr(event, "message_id", "") or "")
    set_interaction_session_key(state, event)


def register_recall_checkpoint(
    state: T_State,
    *,
    message_id: str | int,
    step_index: int,
    prompt: MessagePlanInput,
    state_snapshot: Mapping[str, object],
    cleanup_keys: Iterable[str] = (),
) -> None:
    state[INTERACTION_RECALL_CHECKPOINT] = RecallCheckpoint(
        message_id=str(message_id),
        step_index=step_index,
        prompt=prompt,
        state_snapshot=dict(state_snapshot),
        cleanup_keys=tuple(cleanup_keys),
    )


def get_recall_checkpoint(state: Mapping[str, object]) -> RecallCheckpoint | None:
    checkpoint = state.get(INTERACTION_RECALL_CHECKPOINT)
    return checkpoint if isinstance(checkpoint, RecallCheckpoint) else None


def find_recall_session(
    matcher_source: type[Matcher],
    event: GroupRecallNoticeEvent | FriendRecallNoticeEvent,
) -> RecallSessionMatch | None:
    recalled_message_id = str(getattr(event, "message_id", "") or "")
    if not recalled_message_id:
        return None

    session_key = build_interaction_session_key(event)
    for matcher_cls in list(matchers.get(0, [])):
        if not matcher_cls.temp:
            continue
        if matcher_cls.module_name != matcher_source.module_name:
            continue
        # _source / _default_state 是 nonebot 内部字段（直接访问触发
        # reportPrivateUsage）；沿用仓库既有做法用 getattr 字符串取原始值再收窄，
        # 不对类型做欺骗。
        cls_source = cast("MatcherSource | None", getattr(matcher_cls, "_source", None))
        source = cast("MatcherSource | None", getattr(matcher_source, "_source", None))
        if cls_source != source:
            continue

        raw_state: object = getattr(matcher_cls, "_default_state", None)
        if not isinstance(raw_state, dict):
            continue
        state: dict[str, object] = {}
        for key, value in cast("dict[object, object]", raw_state).items():
            if isinstance(key, str):
                state[key] = value
        if get_interaction_session_key(state) != session_key:
            continue

        root_message_id = str(state.get(INTERACTION_ROOT_MESSAGE_ID, "") or "")
        checkpoint = get_recall_checkpoint(state)
        if recalled_message_id == root_message_id:
            return RecallSessionMatch(
                matcher_cls=matcher_cls,
                checkpoint=checkpoint,
                is_root_message=True,
            )
        if checkpoint is not None and checkpoint.message_id == recalled_message_id:
            return RecallSessionMatch(
                matcher_cls=matcher_cls,
                checkpoint=checkpoint,
                is_root_message=False,
            )
    return None


async def cancel_state_resources(
    state: Mapping[str, object],
    cleanup_keys: Iterable[str],
    *,
    cleaners: Mapping[str, RecallCleanup],
) -> None:
    for key in cleanup_keys:
        cleaner = cleaners.get(key)
        if cleaner is None:
            continue
        result = cleaner()
        if result is not None:
            await result


def rebuild_temp_matcher(
    matcher_template: type[Matcher],
    matcher_source: type[Matcher],
    *,
    step_index: int,
    state: Mapping[str, object],
) -> type[Matcher]:
    # _source / _default_*_updater 是 nonebot 内部字段（直接访问触发
    # reportPrivateUsage）；getattr 取原始值后按 Matcher.new 的形参类型收窄。
    source = cast("MatcherSource | None", getattr(matcher_source, "_source", None))
    type_updater = cast(
        "T_TypeUpdater | Dependent[str] | None",
        getattr(matcher_source, "_default_type_updater", None),
    )
    permission_updater = cast(
        "T_PermissionUpdater | Dependent[Permission] | None",
        getattr(matcher_source, "_default_permission_updater", None),
    )
    return matcher_source.new(
        type_=matcher_template.type,
        rule=Rule(),
        permission=matcher_template.permission,
        handlers=matcher_source.handlers[step_index:],
        temp=True,
        priority=0,
        block=True,
        source=source,
        default_state=dict(state),
        default_type_updater=type_updater,
        default_permission_updater=permission_updater,
    )


def is_supported_recall_notice(
    event: NoticeEvent,
) -> bool:
    return isinstance(event, (GroupRecallNoticeEvent, FriendRecallNoticeEvent))
