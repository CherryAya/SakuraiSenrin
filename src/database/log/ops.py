"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 16:18:08
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-03-01 14:14:56
Description: log db 操作类
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from sqlalchemy import CursorResult, Table, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps
from src.lib.types import UNSET, Unset, is_set
from src.lib.utils.common import get_current_time

from .base import BaseAuditEnum
from .tables import AuditLog, PluginUsageLog, TraceEventLog
from .types import AuditLogPayload, PluginUsageLogPayload, TraceEventLogPayload

if TYPE_CHECKING:
    from datetime import datetime


class AuditLogOps(BaseOps[AuditLog]):
    async def bulk_create_audit_logs(self, audit_logs: list[AuditLogPayload]) -> int:
        if not audit_logs:
            return 0
        stmt = sqlite_insert(AuditLog).values(audit_logs)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def create_audit_log(
        self,
        target_id: str,
        context_type: BaseAuditEnum,
        category: BaseAuditEnum,
        action: BaseAuditEnum,
        context_id: str | Unset = UNSET,
        operator_id: str | Unset = UNSET,
        summary: str | Unset = UNSET,
        meta_data: dict | Unset = UNSET,
    ) -> AuditLog:
        event_time = get_current_time()
        audit_log_payload: AuditLogPayload = {
            "target_id": target_id,
            "context_type": context_type.value,
            "category": category.value,
            "action": action.value,
            "created_at": event_time,
        }
        if is_set(meta_data):
            audit_log_payload["meta_data"] = meta_data
        if is_set(context_id):
            audit_log_payload["context_id"] = context_id
        if is_set(operator_id):
            audit_log_payload["operator_id"] = operator_id
        if is_set(summary):
            audit_log_payload["summary"] = summary

        stmt = sqlite_insert(AuditLog).values(audit_log_payload)
        stmt = stmt.returning(AuditLog)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def query_audit_logs(
        self,
        target_id: str,
        category: str,
        action: str,
        start_time: datetime | Unset = UNSET,
        end_time: datetime | Unset = UNSET,
        limit: int | Unset = UNSET,
        offset: int | Unset = UNSET,
    ) -> Sequence[AuditLog]:
        stmt = select(AuditLog).where(
            AuditLog.target_id == target_id,
            AuditLog.category == category,
            AuditLog.action == action,
        )
        if is_set(start_time):
            stmt = stmt.where(AuditLog.created_at >= start_time)
        if is_set(end_time):
            stmt = stmt.where(AuditLog.created_at <= end_time)
        if is_set(limit):
            stmt = stmt.limit(limit)
        if is_set(offset):
            stmt = stmt.offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class PluginUsageLogOps(BaseOps[PluginUsageLog]):
    async def bulk_create_plugin_usage_logs(
        self,
        plugin_usage_logs: list[PluginUsageLogPayload],
    ) -> int:
        if not plugin_usage_logs:
            return 0
        stmt = sqlite_insert(PluginUsageLog).values(plugin_usage_logs)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


class TraceEventLogOps(BaseOps[TraceEventLog]):
    async def bulk_create_trace_event_logs(
        self,
        trace_event_logs: list[TraceEventLogPayload],
    ) -> int:
        if not trace_event_logs:
            return 0
        table = cast(Table, TraceEventLog.__table__)
        stmt = sqlite_insert(table).values(trace_event_logs)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def query_trace_events(
        self,
        *,
        trace_id: str | None = None,
        component: str | None = None,
        source_kind: str | None = None,
        status: str | None = None,
        record_date: int | None = None,
        job_id: str | None = None,
        shard_key: str | None = None,
        limit: int = 100,
    ) -> Sequence[TraceEventLog]:
        stmt = select(TraceEventLog)
        if trace_id is not None:
            stmt = stmt.where(TraceEventLog.trace_id == trace_id)
        if component is not None:
            stmt = stmt.where(TraceEventLog.component == component)
        if source_kind is not None:
            stmt = stmt.where(TraceEventLog.source_kind == source_kind)
        if status is not None:
            stmt = stmt.where(TraceEventLog.status == status)
        if record_date is not None:
            stmt = stmt.where(TraceEventLog.record_date == record_date)
        if job_id is not None:
            stmt = stmt.where(TraceEventLog.job_id == job_id)
        if shard_key is not None:
            stmt = stmt.where(TraceEventLog.shard_key == shard_key)
        stmt = stmt.order_by(TraceEventLog.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()
