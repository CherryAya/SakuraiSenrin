from typing import cast

from sqlalchemy import CursorResult
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.database.log.types import AuditLogPayload, PluginUsageLogPayload
from src.lib.db.ops import BaseOps

from .tables import AuditLog, PluginUsageLog


class AuditLogOps(BaseOps[AuditLog]):
    async def bulk_create_audit_logs(self, audit_logs: list[AuditLogPayload]) -> int:
        if not audit_logs:
            return 0
        stmt = sqlite_insert(AuditLog).values(audit_logs)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount


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
