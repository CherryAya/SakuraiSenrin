from __future__ import annotations

from types import SimpleNamespace

from src.lib.interactive_recall import (
    RecallCheckpoint,
    build_interaction_session_key,
    find_recall_session,
)
from tests.plugins.water.helpers import (
    build_friend_recall_event,
    build_group_message_event,
    build_group_recall_event,
    build_private_message_event,
)


def test_build_interaction_session_key_supports_group_and_private() -> None:
    group_event = build_group_message_event("test", self_id=99999, user_id=10001)
    private_event = build_private_message_event("test", self_id=99999, user_id=10001)

    assert build_interaction_session_key(group_event) == "99999:group:20001:10001"
    assert build_interaction_session_key(private_event) == "99999:private:10001"


def test_find_recall_session_matches_root_and_checkpoint() -> None:
    source = SimpleNamespace(module_name="src.plugins.study", _source=object())
    temp_matcher = SimpleNamespace(
        temp=True,
        module_name="src.plugins.study",
        _source=source._source,
        _default_state={
            "__interaction_session_key__": "99999:group:20001:10001",
            "__interaction_root_message_id__": "10",
            "__interaction_recall_checkpoint__": RecallCheckpoint(
                message_id="11",
                step_index=3,
                prompt="请输入触发词",
                state_snapshot={"foo": "bar"},
                cleanup_keys=("study_response_image_pending",),
            ),
        },
    )

    import src.lib.interactive_recall as module

    original_matchers = module.matchers
    module.matchers = {0: [temp_matcher]}  # type: ignore[assignment]
    try:
        root_match = find_recall_session(
            source,  # type: ignore[arg-type]
            build_group_recall_event(message_id=10),
        )
        checkpoint_match = find_recall_session(
            source,  # type: ignore[arg-type]
            build_group_recall_event(message_id=11),
        )
        private_miss = find_recall_session(
            source,  # type: ignore[arg-type]
            build_friend_recall_event(message_id=11),
        )
    finally:
        module.matchers = original_matchers  # type: ignore[assignment]

    assert root_match is not None
    assert root_match.is_root_message is True
    assert checkpoint_match is not None
    assert checkpoint_match.is_root_message is False
    assert checkpoint_match.checkpoint is not None
    assert checkpoint_match.checkpoint.step_index == 3
    assert private_miss is None
