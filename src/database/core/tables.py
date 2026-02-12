from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.lib.consts import GLOBAL_SCOPE
from src.lib.db.orm import IntFlagType, TimeMixin

from .consts import GroupStatus, InvitationStatus, Permission, UserStatus


class CoreBase(DeclarativeBase):
    pass


class User(CoreBase, TimeMixin):
    __tablename__ = "biz_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
    )
    user_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permission: Mapped[Permission] = mapped_column(
        IntFlagType(Permission),
        default=Permission.NORMAL,
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        SQLAEnum(UserStatus),
        default=UserStatus.NORMAL,
        nullable=False,
    )
    is_self_ignore: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
        nullable=False,
        comment="用户忽略自身消息开关",
    )
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)

    joined_groups: Mapped[list["Member"]] = relationship(
        "Member",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    sent_invitations: Mapped[list["Invitation"]] = relationship(
        "Invitation",
        back_populates="inviter",
        cascade="all, delete-orphan",
    )
    blacklist_records: Mapped[list["Blacklist"]] = relationship(
        "Blacklist",
        foreign_keys="[Blacklist.target_user_id]",
        back_populates="target_user",
        cascade="all, delete-orphan",
    )
    operated_blacklist_records: Mapped[list["Blacklist"]] = relationship(
        "Blacklist",
        foreign_keys="[Blacklist.operator_id]",
        back_populates="operator",
    )
    operated_groups: Mapped[list["Group"]] = relationship(
        "Group",
        foreign_keys="[Group.last_operator_id]",
        back_populates="last_operator",
    )


class Group(CoreBase, TimeMixin):
    __tablename__ = "biz_group"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
    )
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[GroupStatus] = mapped_column(
        SQLAEnum(GroupStatus),
        default=GroupStatus.UNAUTHORIZED,
        nullable=False,
    )

    last_operator_id: Mapped[str | None] = mapped_column(
        ForeignKey("biz_user.user_id", ondelete="SET NULL"),
        nullable=True,
    )

    last_operator: Mapped["User"] = relationship(
        "User",
        foreign_keys=[last_operator_id],
        back_populates="operated_groups",
    )
    members: Mapped[list["Member"]] = relationship(
        "Member",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    plugin_settings: Mapped[list["GroupPluginSetting"]] = relationship(
        "GroupPluginSetting",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    active_invitation: Mapped["Invitation"] = relationship(
        "Invitation",
        back_populates="group",
        cascade="all, delete-orphan",
    )


class Member(CoreBase, TimeMixin):
    __tablename__ = "biz_group_member"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member"),
        Index("idx_member_group", "group_id"),
        Index("idx_member_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    group_id: Mapped[str] = mapped_column(
        ForeignKey("biz_group.group_id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("biz_user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    group_card: Mapped[str | None] = mapped_column(String(64))
    permission: Mapped[Permission] = mapped_column(
        IntFlagType(Permission),
        default=Permission.NORMAL,
        nullable=False,
    )
    group: Mapped["Group"] = relationship("Group", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="joined_groups")


class Invitation(CoreBase, TimeMixin):
    __tablename__ = "biz_invitation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("biz_group.group_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    inviter_id: Mapped[str] = mapped_column(
        ForeignKey("biz_user.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    flag: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        SQLAEnum(InvitationStatus),
        default=InvitationStatus.PENDING,
        nullable=False,
    )

    group: Mapped["Group"] = relationship("Group", back_populates="active_invitation")
    inviter: Mapped["User"] = relationship("User", back_populates="sent_invitations")


class Blacklist(CoreBase, TimeMixin):
    __tablename__ = "sys_blacklist"
    __table_args__ = (
        UniqueConstraint("target_user_id", "group_id", name="uq_blacklist_scope"),
        Index("idx_check_ban", "target_user_id", "group_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_user_id: Mapped[str] = mapped_column(
        ForeignKey("biz_user.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=GLOBAL_SCOPE,
        server_default=GLOBAL_SCOPE,
        comment=f"生效范围，{GLOBAL_SCOPE} 代表全局",
    )
    operator_id: Mapped[str] = mapped_column(
        ForeignKey("biz_user.user_id", ondelete="SET NULL"),
        nullable=False,
    )
    ban_expiry: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    target_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[target_user_id],
        back_populates="blacklist_records",
    )

    operator: Mapped["User"] = relationship(
        "User",
        foreign_keys=[operator_id],
        back_populates="operated_blacklist_records",
    )


class PluginConfig(CoreBase):
    __tablename__ = "sys_plugin"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    config_data: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )


class GroupPluginSetting(CoreBase, TimeMixin):
    __tablename__ = "biz_group_plugin_setting"
    __table_args__ = (
        UniqueConstraint("group_id", "plugin_name", name="uq_group_plugin_setting"),
        Index("idx_group_plugin_status", "group_id", "plugin_name", "is_enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    group_id: Mapped[str] = mapped_column(
        ForeignKey("biz_group.group_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plugin_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    group: Mapped["Group"] = relationship("Group", back_populates="plugin_settings")
