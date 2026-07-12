"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-27 14:21:16
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-27 20:38:24
Description:
"""

from .repo import WaterRepository
from .repo_models import (
    DailyAggregateItem,
    GlobalPeriodOverview,
    GlobalPeriodRankItem,
    NaturalPeriodRankSnapshot,
    NaturalRankItem,
    RankItem,
    WaterActivitySeasonRecord,
    WaterDailyReportCandidate,
    WaterGroupDailyRankItem,
    WaterGroupDailyRankSnapshot,
    WaterGroupReportMember,
    WaterGroupReportSnapshot,
)

water_repo = WaterRepository()

__all__ = [
    "DailyAggregateItem",
    "GlobalPeriodOverview",
    "GlobalPeriodRankItem",
    "NaturalPeriodRankSnapshot",
    "NaturalRankItem",
    "RankItem",
    "WaterActivitySeasonRecord",
    "WaterDailyReportCandidate",
    "WaterGroupDailyRankItem",
    "WaterGroupDailyRankSnapshot",
    "WaterGroupReportMember",
    "WaterGroupReportSnapshot",
    "water_repo",
]
