from __future__ import annotations

from datetime import datetime

import pytest

from src.database.core.consts import GroupStatus, InvitationStatus
from src.database.system_migration import (
    build_legacy_system_data,
    map_legacy_group_status,
    map_legacy_invitation_status,
    migrate_legacy_system_data,
    normalize_legacy_timestamp,
    should_blacklist_legacy_user,
)
from src.services.runtime_policy import (
    get_group_block_reason,
    resolve_invitation_transition,
)


def test_map_legacy_group_status_preserves_banned_semantics() -> None:
    assert map_legacy_group_status("BAN") == GroupStatus.BANNED
    assert map_legacy_group_status("UNAUTH") == GroupStatus.UNAUTHORIZED
    assert map_legacy_group_status("ENABLE") == GroupStatus.AUTHORIZED


def test_map_legacy_invitation_status() -> None:
    assert map_legacy_invitation_status("PENDING") == InvitationStatus.PENDING
    assert map_legacy_invitation_status("ACCEPT") == InvitationStatus.APPROVED
    assert map_legacy_invitation_status("REJECT") == InvitationStatus.REJECTED


def test_should_blacklist_legacy_user() -> None:
    assert should_blacklist_legacy_user("BAN") is True
    assert should_blacklist_legacy_user("DISABLE") is True
    assert should_blacklist_legacy_user("DELETE") is True
    assert should_blacklist_legacy_user("ENABLE") is False


def test_normalize_legacy_timestamp_uses_shanghai_for_naive_values() -> None:
    timestamp = normalize_legacy_timestamp(datetime(2026, 6, 11, 12, 0, 0))
    assert timestamp == 1781150400


def test_resolve_invitation_transition_blocks_approve_for_banned_group() -> None:
    with pytest.raises(ValueError, match="banned"):
        resolve_invitation_transition(
            approve=True,
            group_status=GroupStatus.BANNED,
        )


def test_banned_group_helpers_match_runtime_and_admin_flow() -> None:
    assert get_group_block_reason(GroupStatus.BANNED) == "群聊已封禁"
    assert get_group_block_reason(GroupStatus.UNAUTHORIZED) == "群聊未授权"
    with pytest.raises(ValueError, match="banned"):
        resolve_invitation_transition(
            approve=True,
            group_status=GroupStatus.BANNED,
        )


async def test_migrate_legacy_system_data_creates_placeholders_and_banned_groups() -> (
    None
):
    data = build_legacy_system_data(
        users=[
            {
                "user_id": "1001",
                "user_name": "Alice",
                "status": "BAN",
                "operator_id": "1001",
                "effective_time": datetime(2026, 6, 11, 10, 0, 0),
                "create_time": datetime(2026, 6, 10, 10, 0, 0),
                "update_time": datetime(2026, 6, 11, 11, 0, 0),
                "remark": "legacy ban",
            }
        ],
        groups=[
            {
                "group_id": "2001",
                "group_name": None,
                "status": "BAN",
                "operator_id": "1001",
                "effective_time": datetime(2026, 6, 11, 10, 0, 0),
                "create_time": datetime(2026, 6, 10, 10, 0, 0),
                "update_time": datetime(2026, 6, 11, 11, 0, 0),
                "remark": None,
            }
        ],
        invitations=[
            {
                "id": 1,
                "group_id": "3001",
                "group_name": "Pending Group",
                "inviter_id": "4001",
                "flag": "flag-1",
                "sub_type": "invite",
                "status": "PENDING",
                "operator_id": "1001",
                "create_time": datetime(2026, 6, 11, 12, 0, 0),
                "update_time": datetime(2026, 6, 11, 12, 1, 0),
            }
        ],
        invitation_messages=[
            {
                "report_message_id": "5001",
                "invitation_info_id": 1,
            }
        ],
        plugin_infos=[],
    )

    from src.database.instances import core_db

    async with core_db.session() as session:
        report = await migrate_legacy_system_data(data, session, reset_target=True)

    assert report.inserted_counts["biz_user"] == 2
    assert report.inserted_counts["biz_group"] == 2
    assert report.inserted_counts["sys_blacklist"] == 1
    assert report.inserted_counts["biz_invitation"] == 1
    assert report.inserted_counts["biz_invitation_message"] == 1
    assert report.placeholder_users == 1
    assert report.placeholder_groups == 1
    assert report.status_counts["new_group"]["BANNED"] == 1
