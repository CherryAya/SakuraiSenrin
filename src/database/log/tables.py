"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-01 14:11:33
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 20:47:46
Description: log db tabel 定义
"""

from sqlalchemy import (
    JSON,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class LogBase(DeclarativeBase):
    pass


class AuditLog(LogBase):
    __tablename__ = "sys_audit_log"
    __table_args__ = (
        Index("idx_audit_context", "context_type", "context_id", "created_at"),
        Index("idx_audit_operator", "operator_id", "created_at"),
        Index("idx_audit_target", "target_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[str] = mapped_column(String(32), nullable=False)
    context_type: Mapped[str] = mapped_column(String(16), nullable=False)
    context_id: Mapped[str | None] = mapped_column(String(32))
    operator_id: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(255))
    meta_data: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class PluginUsageLog(LogBase):
    __tablename__ = "sys_plugin_log"
    __table_args__ = (
        Index("idx_plugin_stat", "plugin_name", "created_at"),
        Index("idx_plugin_group", "group_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    group_id: Mapped[str] = mapped_column(String(32), nullable=False)
    plugin_name: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    cost_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
