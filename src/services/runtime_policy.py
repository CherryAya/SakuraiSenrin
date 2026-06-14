"""Pure runtime policy helpers shared by hooks and plugins."""

from __future__ import annotations

from src.database.core.consts import GroupStatus, InvitationStatus
from src.lib.i18n.runtime import tr


def get_group_block_reason(status: GroupStatus) -> str | None:
    if status.is_banned:
        return tr("zh-CN", "hook.processor.group_banned")
    if status.is_unauthorized:
        return tr("zh-CN", "hook.processor.group_unauthorized")
    return None


def resolve_invitation_transition(
    *,
    approve: bool,
    group_status: GroupStatus,
) -> tuple[InvitationStatus, GroupStatus]:
    if approve and group_status.is_banned:
        raise ValueError("group is banned")
    if approve:
        return InvitationStatus.APPROVED, GroupStatus.AUTHORIZED
    return InvitationStatus.REJECTED, GroupStatus.UNAUTHORIZED


__all__ = ["get_group_block_reason", "resolve_invitation_transition"]
