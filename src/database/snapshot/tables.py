from sqlalchemy import Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.orm import TimeMixin


class SnapshotBase(DeclarativeBase):
    pass


class UserSnapshot(SnapshotBase, TimeMixin):
    """用户快照流水，记录昵称变更"""

    __tablename__ = "obs_user_snapshot"
    __table_args__ = (Index("idx_snap_user_name", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(String(255))


class GroupSnapshot(SnapshotBase, TimeMixin):
    """群组信息快照流水，记录群名变更"""

    __tablename__ = "obs_group_snapshot"
    __table_args__ = (Index("idx_snap_group_name", "group_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(String(255))


class MemberSnapshot(SnapshotBase, TimeMixin):
    """群成员信息快照流水，记录群名片变更"""

    __tablename__ = "obs_member_snapshot"
    __table_args__ = (
        Index("idx_snap_member_card", "user_id", "group_id", "created_at"),
        Index("idx_snap_member_time", "group_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    group_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(String(255))
