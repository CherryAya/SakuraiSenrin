"""Water 数据表定义 (v2.0)."""

from sqlalchemy import JSON, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.orm import TimeMixin


class WaterMessageBase(DeclarativeBase):
    """水王流水分库表基类。"""


class WaterCoreBase(DeclarativeBase):
    """水王核心资产主库表基类。"""


class WaterMessage(WaterMessageBase):
    __tablename__ = "water_message"
    __table_args__ = (
        Index(
            "idx_water_message_group_user_time",
            "group_id",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class WaterDailySummary(WaterCoreBase, TimeMixin):
    __tablename__ = "water_daily_summary"
    __table_args__ = (
        Index(
            "idx_water_summary_group_date",
            "group_id",
            "record_date",
        ),
        Index(
            "idx_water_summary_user_date",
            "user_id",
            "record_date",
        ),
    )

    group_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_date: Mapped[int] = mapped_column(Integer, primary_key=True)

    msg_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hourly_counts: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)


class WaterGroupMatrixMap(WaterCoreBase, TimeMixin):
    __tablename__ = "water_group_matrix_map"

    group_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    matrix_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class WaterMatrixLevel(WaterCoreBase, TimeMixin):
    __tablename__ = "water_matrix_level"
    __table_args__ = (UniqueConstraint("matrix_id", "user_id", name="uq_matrix_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    matrix_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    season_exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WaterGlobalLevel(WaterCoreBase, TimeMixin):
    __tablename__ = "water_global_level"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    season_exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WaterMatrixTotalLevel(WaterCoreBase, TimeMixin):
    __tablename__ = "water_matrix_total_level"

    matrix_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    season_exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WaterPenaltyLog(WaterCoreBase, TimeMixin):
    __tablename__ = "water_penalty_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    matrix_id: Mapped[str] = mapped_column(String(64), nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    record_date: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    delta_exp: Mapped[int] = mapped_column(Integer, nullable=False)
    is_revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WaterSettlementJob(WaterCoreBase, TimeMixin):
    __tablename__ = "water_settlement_job"

    record_date: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    started_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WaterMatrixMergeState(WaterCoreBase, TimeMixin):
    __tablename__ = "water_matrix_merge_state"
    __table_args__ = (Index("idx_water_merge_ignored", "is_ignored"),)

    group_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_ignored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    target_matrix_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class WaterUserAchievement(WaterCoreBase):
    __tablename__ = "water_user_achievement"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "achievement_id",
            "season_id",
            name="uq_user_achievement",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    achievement_id: Mapped[str] = mapped_column(String(64), nullable=False)
    track_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="permanent",
    )
    season_id: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    unlocked_at: Mapped[int] = mapped_column(Integer, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
