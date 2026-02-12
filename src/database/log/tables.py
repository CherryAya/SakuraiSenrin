from sqlalchemy import (
    JSON,
    Index,
    String,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.orm import TimeMixin


class LogBase(DeclarativeBase):
    pass


class AuditLog(LogBase, TimeMixin):
    __tablename__ = "sys_audit_log"
    __table_args__ = (
        Index("idx_audit_target", "target_id", "category", "created_at"),
        Index("idx_audit_operator", "operator_id", "created_at"),
        Index("idx_audit_action", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    operator_id: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(255))
    meta_data: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        comment="结构化参数/新值快照",
    )


class PluginUsageLog(LogBase, TimeMixin):
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
