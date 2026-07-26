from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.orm import TimeMixin


class SelfUnbanBase(DeclarativeBase):
    """Self-unban plugin database base."""


class SelfUnbanAttempt(SelfUnbanBase, TimeMixin):
    __tablename__ = "self_unban_attempt"
    __table_args__ = (
        Index(
            "idx_self_unban_subject_quota",
            "subject_type",
            "subject_id",
            "consumes_quota",
        ),
        Index(
            "idx_self_unban_requester_created",
            "requester_user_id",
            "created_at",
        ),
        Index(
            "idx_self_unban_scope_created",
            "scope_group_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    requester_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    consumes_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = ["SelfUnbanAttempt", "SelfUnbanBase"]
