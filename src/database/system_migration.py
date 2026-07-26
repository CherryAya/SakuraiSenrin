"""Legacy senrin_system migration helpers."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.core.consts import GroupStatus, InvitationStatus, Permission
from src.database.core.tables import (
    Blacklist,
    Group,
    GroupLocaleSetting,
    GroupPluginSetting,
    Invitation,
    InvitationMessage,
    Member,
    PluginConfig,
    User,
)
from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG
from src.services.db import init_db
from src.services.runtime_policy import resolve_invitation_transition

_LEGACY_TZ = ZoneInfo("Asia/Shanghai")
_PLACEHOLDER_USER_REMARK = "[migration placeholder from invitation]"


@dataclass(slots=True, frozen=True)
class LegacyPgConfig:
    host: str
    port: int
    user: str
    password: str
    database: str = "senrin_system"


@dataclass(slots=True, frozen=True)
class LegacyUserRecord:
    user_id: str
    user_name: str | None
    status: str
    operator_id: str
    effective_time: object
    create_time: object
    update_time: object
    remark: str | None


@dataclass(slots=True, frozen=True)
class LegacyGroupRecord:
    group_id: str
    group_name: str | None
    status: str
    operator_id: str
    effective_time: object
    create_time: object
    update_time: object
    remark: str | None


@dataclass(slots=True, frozen=True)
class LegacyInvitationRecord:
    id: int
    group_id: str
    group_name: str | None
    inviter_id: str
    flag: str | None
    sub_type: str
    status: str
    operator_id: str
    create_time: object
    update_time: object


@dataclass(slots=True, frozen=True)
class LegacyInvitationMessageRecord:
    report_message_id: str
    invitation_info_id: int


@dataclass(slots=True, frozen=True)
class LegacyPluginInfoRecord:
    plugin_raw_name: str | None
    plugin_metadata_name: str | None
    plugin_module_name: str | None
    plugin_description: str | None
    plugin_usage: str | None
    trigger_type: str | None
    plugin_permission: str | None


@dataclass(slots=True, frozen=True)
class LegacySystemData:
    users: list[LegacyUserRecord]
    groups: list[LegacyGroupRecord]
    invitations: list[LegacyInvitationRecord]
    invitation_messages: list[LegacyInvitationMessageRecord]
    plugin_infos: list[LegacyPluginInfoRecord]


@dataclass(slots=True)
class SystemMigrationReport:
    source_counts: dict[str, int] = field(default_factory=dict)
    inserted_counts: dict[str, int] = field(default_factory=dict)
    placeholder_users: int = 0
    placeholder_groups: int = 0
    status_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    plugin_info_summary: list[dict[str, str | None]] = field(default_factory=list)
    dropped_fields: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_legacy_env_file(path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value_text = raw_value.strip()
        if not key:
            continue
        if "#" in value_text and not value_text.startswith(("'", '"')):
            value_text = value_text.split("#", 1)[0].strip()
        if not value_text:
            continue
        try:
            values[key] = ast.literal_eval(value_text)
        except (SyntaxError, ValueError):
            values[key] = value_text.strip().strip('"').strip("'")
    return values


def load_legacy_pg_config(old_repo_root: Path) -> LegacyPgConfig:
    root_env = old_repo_root / ".env"
    environment = "dev"
    if root_env.is_file():
        parsed = parse_legacy_env_file(root_env)
        environment_value = parsed.get("ENVIRONMENT")
        if isinstance(environment_value, str) and environment_value.strip():
            environment = environment_value.strip()

    selected_env = old_repo_root / f".env.{environment}"
    if not selected_env.is_file():
        selected_env = old_repo_root / ".env.dev"
    values = parse_legacy_env_file(selected_env)
    return LegacyPgConfig(
        host=str(values["pg_host"]),
        port=_coerce_int(values["pg_port"], field="pg_port"),
        user=str(values["pg_username"]),
        password=str(values["pg_password"]),
    )


def map_legacy_group_status(value: str) -> GroupStatus:
    status_map = {
        "ENABLE": GroupStatus.AUTHORIZED,
        "UNAUTH": GroupStatus.UNAUTHORIZED,
        "DISABLE": GroupStatus.UNAUTHORIZED,
        "BAN": GroupStatus.BANNED,
        "LEAVE": GroupStatus.LEFT,
        "REMOVE": GroupStatus.LEFT,
    }
    return status_map[value]


def map_legacy_invitation_status(value: str) -> InvitationStatus:
    status_map = {
        "PENDING": InvitationStatus.PENDING,
        "ACCEPT": InvitationStatus.APPROVED,
        "REJECT": InvitationStatus.REJECTED,
    }
    return status_map[value]


def should_blacklist_legacy_user(value: str) -> bool:
    return value in {"BAN", "DISABLE", "DELETE"}


def normalize_legacy_timestamp(value: object) -> int:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        raise TypeError(f"unsupported timestamp value: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LEGACY_TZ)
    return int(dt.timestamp())


def resolve_user_name(value: str | None, user_id: str) -> str:
    name = (value or "").strip()
    return name or user_id


def resolve_group_name(
    group_id: str,
    group_name: str | None,
    invitation_group_names: Mapping[str, str],
) -> str:
    name = (group_name or "").strip()
    if name:
        return name
    fallback = invitation_group_names.get(group_id, "").strip()
    return fallback or group_id


def build_legacy_system_data(
    *,
    users: Sequence[Mapping[str, object]],
    groups: Sequence[Mapping[str, object]],
    invitations: Sequence[Mapping[str, object]],
    invitation_messages: Sequence[Mapping[str, object]],
    plugin_infos: Sequence[Mapping[str, object]],
) -> LegacySystemData:
    return LegacySystemData(
        users=[
            LegacyUserRecord(
                user_id=str(row["user_id"]),
                user_name=_optional_str(row.get("user_name")),
                status=str(row["status"]),
                operator_id=str(row["operator_id"]),
                effective_time=row["effective_time"],
                create_time=row["create_time"],
                update_time=row["update_time"],
                remark=_optional_str(row.get("remark")),
            )
            for row in users
        ],
        groups=[
            LegacyGroupRecord(
                group_id=str(row["group_id"]),
                group_name=_optional_str(row.get("group_name")),
                status=str(row["status"]),
                operator_id=str(row["operator_id"]),
                effective_time=row["effective_time"],
                create_time=row["create_time"],
                update_time=row["update_time"],
                remark=_optional_str(row.get("remark")),
            )
            for row in groups
        ],
        invitations=[
            LegacyInvitationRecord(
                id=_coerce_int(row["id"], field="invitation.id"),
                group_id=str(row["group_id"]),
                group_name=_optional_str(row.get("group_name")),
                inviter_id=str(row["inviter_id"]),
                flag=_optional_str(row.get("flag")),
                sub_type=str(row["sub_type"]),
                status=str(row["status"]),
                operator_id=str(row["operator_id"]),
                create_time=row["create_time"],
                update_time=row["update_time"],
            )
            for row in invitations
        ],
        invitation_messages=[
            LegacyInvitationMessageRecord(
                report_message_id=str(row["report_message_id"]),
                invitation_info_id=_coerce_int(
                    row["invitation_info_id"],
                    field="invitation_message.invitation_info_id",
                ),
            )
            for row in invitation_messages
        ],
        plugin_infos=[
            LegacyPluginInfoRecord(
                plugin_raw_name=_optional_str(row.get("plugin_raw_name")),
                plugin_metadata_name=_optional_str(row.get("plugin_metadata_name")),
                plugin_module_name=_optional_str(row.get("plugin_module_name")),
                plugin_description=_optional_str(row.get("plugin_description")),
                plugin_usage=_optional_str(row.get("plugin_usage")),
                trigger_type=_optional_str(row.get("trigger_type")),
                plugin_permission=_optional_str(row.get("plugin_permission")),
            )
            for row in plugin_infos
        ],
    )


async def migrate_legacy_system_data(
    data: LegacySystemData,
    session: AsyncSession,
    *,
    reset_target: bool = True,
) -> SystemMigrationReport:
    if reset_target:
        await reset_core_runtime_tables(session)

    report = SystemMigrationReport(
        source_counts={
            "users": len(data.users),
            "groups": len(data.groups),
            "invitations": len(data.invitations),
            "invitation_messages": len(data.invitation_messages),
            "plugin_infos": len(data.plugin_infos),
        },
        status_counts={
            "legacy_user": dict(Counter(row.status for row in data.users)),
            "legacy_group": dict(Counter(row.status for row in data.groups)),
            "legacy_invitation": dict(Counter(row.status for row in data.invitations)),
        },
        plugin_info_summary=[
            {
                "plugin_raw_name": row.plugin_raw_name,
                "plugin_metadata_name": row.plugin_metadata_name,
                "plugin_module_name": row.plugin_module_name,
                "trigger_type": row.trigger_type,
                "plugin_permission": row.plugin_permission,
            }
            for row in data.plugin_infos
        ],
        dropped_fields={
            "group_info": ["remark", "effective_time"],
            "plugin_info": [
                "plugin_raw_name",
                "plugin_metadata_name",
                "plugin_module_name",
                "plugin_description",
                "plugin_usage",
                "trigger_type",
                "plugin_permission",
            ],
        },
    )

    invitation_name_by_group = _build_invitation_name_index(data.invitations)

    user_payloads: dict[str, dict[str, object]] = {}
    blacklist_payloads: list[dict[str, object]] = []
    for row in data.users:
        user_payloads[row.user_id] = {
            "user_id": row.user_id,
            "user_name": resolve_user_name(row.user_name, row.user_id),
            "permission": Permission.NORMAL,
            "remark": row.remark,
            "created_at": normalize_legacy_timestamp(row.create_time),
            "updated_at": normalize_legacy_timestamp(row.update_time),
        }
        if should_blacklist_legacy_user(row.status):
            blacklist_payloads.append(
                {
                    "target_user_id": row.user_id,
                    "group_id": GLOBAL_GROUP_FLAG,
                    "operator_id": row.operator_id,
                    "ban_expiry": PERMANENT_BAN_FLAG,
                    "reason": row.remark,
                    "created_at": normalize_legacy_timestamp(row.effective_time),
                    "updated_at": normalize_legacy_timestamp(row.update_time),
                }
            )

    group_payloads: dict[str, dict[str, object]] = {}
    for row in data.groups:
        group_payloads[row.group_id] = {
            "group_id": row.group_id,
            "group_name": resolve_group_name(
                row.group_id,
                row.group_name,
                invitation_name_by_group,
            ),
            "status": map_legacy_group_status(row.status),
            "last_operator_id": row.operator_id,
            "created_at": normalize_legacy_timestamp(row.create_time),
            "updated_at": normalize_legacy_timestamp(row.update_time),
        }

    placeholder_users = _build_placeholder_users(data.invitations, user_payloads)
    placeholder_groups = _build_placeholder_groups(
        data.invitations,
        invitation_name_by_group,
        group_payloads,
    )
    user_payloads.update(placeholder_users)
    group_payloads.update(placeholder_groups)
    report.placeholder_users = len(placeholder_users)
    report.placeholder_groups = len(placeholder_groups)

    session.add_all(User(**payload) for payload in user_payloads.values())
    await session.flush()
    session.add_all(Group(**payload) for payload in group_payloads.values())
    await session.flush()
    session.add_all(Blacklist(**payload) for payload in blacklist_payloads)
    await session.flush()

    invitation_id_map: dict[int, tuple[int, int]] = {}
    for row in data.invitations:
        invitation = Invitation(
            group_id=row.group_id,
            inviter_id=row.inviter_id,
            operator_id=row.operator_id,
            flag=row.flag,
            sub_type=row.sub_type,
            status=map_legacy_invitation_status(row.status),
            created_at=normalize_legacy_timestamp(row.create_time),
            updated_at=normalize_legacy_timestamp(row.update_time),
        )
        session.add(invitation)
        await session.flush()
        invitation_id_map[row.id] = (invitation.id, invitation.created_at)

    message_records: list[InvitationMessage] = []
    for row in data.invitation_messages:
        invitation_id, invitation_created_at = invitation_id_map[row.invitation_info_id]
        message_records.append(
            InvitationMessage(
                invitation_id=invitation_id,
                message_id=row.report_message_id,
                created_at=invitation_created_at,
                updated_at=invitation_created_at,
            )
        )
    session.add_all(message_records)
    await session.flush()

    report.inserted_counts = {
        "biz_user": len(user_payloads),
        "biz_group": len(group_payloads),
        "sys_blacklist": len(blacklist_payloads),
        "biz_invitation": len(invitation_id_map),
        "biz_invitation_message": len(message_records),
    }
    report.status_counts["new_group"] = dict(
        Counter(
            cast(GroupStatus, payload["status"]).value
            for payload in group_payloads.values()
        )
    )
    report.status_counts["new_invitation"] = dict(
        Counter(
            map_legacy_invitation_status(row.status).value for row in data.invitations
        )
    )
    return report


async def reset_core_runtime_tables(session: AsyncSession) -> None:
    for model in (
        InvitationMessage,
        Invitation,
        Blacklist,
        Member,
        GroupLocaleSetting,
        GroupPluginSetting,
        Group,
        User,
    ):
        await session.execute(delete(model))


async def build_runtime_table_counts(session: AsyncSession) -> dict[str, int]:
    table_counts: dict[str, int] = {}
    for label, model in (
        ("biz_user", User),
        ("biz_group", Group),
        ("sys_blacklist", Blacklist),
        ("biz_invitation", Invitation),
        ("biz_invitation_message", InvitationMessage),
        ("sys_plugin", PluginConfig),
    ):
        result = await session.execute(select(func.count()).select_from(model))
        table_counts[label] = int(result.scalar_one())
    return table_counts


async def initialize_sqlite_runtime() -> None:
    await init_db()


def write_report(path: Path, report: SystemMigrationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_invitation_name_index(
    invitations: Sequence[LegacyInvitationRecord],
) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in invitations:
        if row.group_id in names:
            continue
        group_name = (row.group_name or "").strip()
        if group_name:
            names[row.group_id] = group_name
    return names


def _build_placeholder_users(
    invitations: Sequence[LegacyInvitationRecord],
    existing_users: Mapping[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    placeholders: dict[str, dict[str, object]] = {}
    for row in invitations:
        if row.inviter_id in existing_users or row.inviter_id in placeholders:
            continue
        created_at = normalize_legacy_timestamp(row.create_time)
        updated_at = normalize_legacy_timestamp(row.update_time)
        placeholders[row.inviter_id] = {
            "user_id": row.inviter_id,
            "user_name": row.inviter_id,
            "permission": Permission.NORMAL,
            "remark": _PLACEHOLDER_USER_REMARK,
            "created_at": created_at,
            "updated_at": updated_at,
        }
    return placeholders


def _build_placeholder_groups(
    invitations: Sequence[LegacyInvitationRecord],
    invitation_name_by_group: Mapping[str, str],
    existing_groups: Mapping[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    placeholders: dict[str, dict[str, object]] = {}
    for row in invitations:
        if row.group_id in existing_groups or row.group_id in placeholders:
            continue
        created_at = normalize_legacy_timestamp(row.create_time)
        updated_at = normalize_legacy_timestamp(row.update_time)
        placeholders[row.group_id] = {
            "group_id": row.group_id,
            "group_name": resolve_group_name(
                row.group_id,
                row.group_name,
                invitation_name_by_group,
            ),
            "status": GroupStatus.UNAUTHORIZED,
            "last_operator_id": row.operator_id,
            "created_at": created_at,
            "updated_at": updated_at,
        }
    return placeholders


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text


def _coerce_int(value: object, *, field: str) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid int for {field}: {value!r}") from exc


__all__ = [
    "LegacyPgConfig",
    "LegacySystemData",
    "SystemMigrationReport",
    "build_legacy_system_data",
    "build_runtime_table_counts",
    "initialize_sqlite_runtime",
    "load_legacy_pg_config",
    "map_legacy_group_status",
    "map_legacy_invitation_status",
    "migrate_legacy_system_data",
    "normalize_legacy_timestamp",
    "parse_legacy_env_file",
    "resolve_invitation_transition",
    "should_blacklist_legacy_user",
    "write_report",
]
