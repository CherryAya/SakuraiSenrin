"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-22 17:13:38
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-22 17:37:27
Description: 邀请相关实现
"""

from src.database.core.consts import InvitationStatus
from src.database.core.ops import InvitationOps
from src.database.core.tables import Invitation, InvitationMessage
from src.database.instances import core_db


class InviteRepository:
    async def create_invitation(
        self,
        group_id: str,
        inviter_id: str,
        flag: str | None,
    ) -> Invitation:
        async with core_db.session() as core_session:
            return await InvitationOps(core_session).create_invitation(
                group_id=group_id,
                inviter_id=inviter_id,
                flag=flag,
            )

    async def add_message_record(
        self,
        invitation_id: int,
        message_id: str,
    ) -> InvitationMessage:
        async with core_db.session() as core_session:
            return await InvitationOps(core_session).add_message_record(
                invitation_id=invitation_id,
                message_id=message_id,
            )

    async def update_status(
        self,
        invitation_id: int,
        status: InvitationStatus,
    ) -> Invitation:
        async with core_db.session() as core_session:
            return await InvitationOps(core_session).update_status(
                invitation_id=invitation_id,
                status=status,
            )
