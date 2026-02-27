"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-26 19:16:05
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 20:09:30
Description: water db 表定义
"""

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WaterBase(DeclarativeBase):
    pass


class WaterMessage(WaterBase):
    __tablename__ = "water_message"
    __table_args__ = (Index("idx_group_time", "group_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[int] = mapped_column()


class WaterDailySummary(WaterBase):
    __tablename__ = "water_daily_summary"
    __table_args__ = (Index("idx_summary_group_user", "group_id", "user_id"),)

    group_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    record_date: Mapped[int] = mapped_column(primary_key=True)

    msg_count: Mapped[int] = mapped_column(default=0)
    hourly_counts: Mapped[list[int]] = mapped_column(JSON, default=lambda: [0] * 24)
