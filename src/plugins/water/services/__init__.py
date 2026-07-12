"""Water services package."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .achievement import ACHIEVEMENT_RULES as ACHIEVEMENT_RULES
    from .report import (
        TODAY_REPORT_COOLDOWN_SECONDS as TODAY_REPORT_COOLDOWN_SECONDS,
    )
    from .report import WaterDailyReportBatchResult as WaterDailyReportBatchResult
    from .report import water_report_service as water_report_service

__all__ = [
    "ACHIEVEMENT_RULES",
    "TODAY_REPORT_COOLDOWN_SECONDS",
    "WaterDailyReportBatchResult",
    "water_report_service",
]


def __getattr__(name: str) -> Any:
    if name == "ACHIEVEMENT_RULES":
        from .achievement import ACHIEVEMENT_RULES

        return ACHIEVEMENT_RULES
    if name in {
        "TODAY_REPORT_COOLDOWN_SECONDS",
        "WaterDailyReportBatchResult",
        "water_report_service",
    }:
        report_module = import_module(".report", __name__)
        return getattr(report_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
