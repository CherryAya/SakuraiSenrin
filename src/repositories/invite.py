"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-22 17:13:38
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-24 17:54:52
Description: 邀请相关实现
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text

from src.database.core.consts import InvitationStatus
from src.database.core.ops import InvitationOps
from src.database.core.tables import Invitation, InvitationMessage
from src.database.instances import core_db


@dataclass(slots=True, frozen=True)
class InvitationEventResult:
    invitation_id: int
    group_id: str
    inviter_id: str
    flag: str | None
    sub_type: str
    status: InvitationStatus
    should_notify: bool
    replayed: bool


class InviteRepository:
    async def reconcile_event(
        self,
        *,
        group_id: str,
        inviter_id: str,
        flag: str | None,
        joined: bool,
        event_time: int,
    ) -> InvitationEventResult:
        async with core_db.write_session() as core_session:
            # SQLite 的写锁必须先于查询，多个进程/实例同时接收也只创建一个待办。
            await core_session.execute(text("BEGIN IMMEDIATE"))
            invitation, should_notify, replayed = await InvitationOps(
                core_session,
            ).reconcile_event(
                group_id=group_id,
                inviter_id=inviter_id,
                flag=flag,
                joined=joined,
                event_time=event_time,
            )
            return InvitationEventResult(
                invitation_id=invitation.id,
                group_id=invitation.group_id,
                inviter_id=invitation.inviter_id,
                flag=invitation.flag,
                sub_type=invitation.sub_type,
                status=invitation.status,
                should_notify=should_notify,
                replayed=replayed,
            )

    async def mark_joined(self, invitation_id: int) -> None:
        async with core_db.write_session() as core_session:
            await InvitationOps(core_session).mark_joined(invitation_id)

    async def create_invitation(
        self,
        group_id: str,
        inviter_id: str,
        flag: str | None,
        sub_type: str = "invite",
    ) -> Invitation:
        async with core_db.session(commit=True) as core_session:
            return await InvitationOps(core_session).create_invitation(
                group_id=group_id,
                inviter_id=inviter_id,
                flag=flag,
                sub_type=sub_type,
            )

    async def add_message_record(
        self,
        invitation_id: int,
        message_id: str,
    ) -> InvitationMessage:
        async with core_db.session(commit=True) as core_session:
            return await InvitationOps(core_session).add_message_record(
                invitation_id=invitation_id,
                message_id=message_id,
            )

    async def update_status(
        self,
        invitation_id: int,
        status: InvitationStatus,
        operator_id: str | None = None,
    ) -> Invitation:
        async with core_db.session(commit=True) as core_session:
            return await InvitationOps(core_session).update_status(
                invitation_id=invitation_id,
                status=status,
                operator_id=operator_id,
            )

    async def get_by_message_id(self, message_id: str) -> Invitation | None:
        async with core_db.session(commit=False) as core_session:
            return await InvitationOps(core_session).get_by_message_id(
                message_id=message_id,
            )

    async def get_by_status(self, status: InvitationStatus) -> Sequence[Invitation]:
        async with core_db.session(commit=False) as core_session:
            return await InvitationOps(core_session).get_by_status(status)

    async def get_by_id(self, invitation_id: int) -> Invitation | None:
        async with core_db.session(commit=False) as core_session:
            return await InvitationOps(core_session).get_by_id(invitation_id)

    async def get_by_group_id(self, group_id: str) -> Invitation | None:
        async with core_db.session(commit=False) as core_session:
            return await InvitationOps(core_session).get_by_group_id(group_id)

    async def get_by_flag(self, flag: str) -> Invitation | None:
        async with core_db.session(commit=False) as core_session:
            return await InvitationOps(core_session).get_by_flag(flag)

    async def ignore_all_pending(
        self,
        operator_id: str | None = None,
    ) -> Sequence[Invitation]:
        async with core_db.session(commit=True) as core_session:
            return await InvitationOps(core_session).ignore_all_pending(
                operator_id=operator_id
            )

    async def reject_all_pending(
        self,
        operator_id: str | None = None,
    ) -> Sequence[Invitation]:
        async with core_db.session(commit=True) as core_session:
            return await InvitationOps(core_session).reject_all_pending(
                operator_id=operator_id
            )
