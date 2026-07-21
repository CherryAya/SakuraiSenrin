from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Literal, cast

from src.lib.utils.common import get_current_time

WaterWorkerJobName = Literal[
    "settlement",
    "message_archive",
    "summary_archive",
    "daily_report_prepare",
]
WaterWorkerStatus = Literal["success", "skipped", "failed", "partial"]
WaterPreparedReportMessageKind = Literal["image", "text"]


@dataclass(slots=True, frozen=True)
class WaterPreparedReportItem:
    group_id: str
    record_date: int
    message_kind: WaterPreparedReportMessageKind
    payload_name: str
    activity_score: int
    total_msg_count: int
    active_user_count: int
    error: str = ""


@dataclass(slots=True, frozen=True)
class WaterWorkerManifest:
    job_name: WaterWorkerJobName
    job_id: str
    started_at: int
    finished_at: int
    status: WaterWorkerStatus
    record_date: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    report_items: tuple[WaterPreparedReportItem, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["report_items"] = [asdict(item) for item in self.report_items]
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WaterWorkerManifest":
        report_items = tuple(
            WaterPreparedReportItem(
                group_id=str(item.get("group_id", "")),
                record_date=int(item.get("record_date", 0)),
                message_kind=cast(
                    WaterPreparedReportMessageKind,
                    str(item.get("message_kind", "text")),
                ),
                payload_name=str(item.get("payload_name", "")),
                activity_score=int(item.get("activity_score", 0)),
                total_msg_count=int(item.get("total_msg_count", 0)),
                active_user_count=int(item.get("active_user_count", 0)),
                error=str(item.get("error", "")),
            )
            for item in cast(list[dict[str, Any]], payload.get("report_items", []))
        )
        return cls(
            job_name=cast(WaterWorkerJobName, str(payload.get("job_name", ""))),
            job_id=str(payload.get("job_id", "")),
            started_at=int(payload.get("started_at", 0)),
            finished_at=int(payload.get("finished_at", 0)),
            status=cast(WaterWorkerStatus, str(payload.get("status", "failed"))),
            record_date=(
                int(payload["record_date"])
                if payload.get("record_date") is not None
                else None
            ),
            metrics=dict(cast(dict[str, Any], payload.get("metrics", {}))),
            artifacts=dict(cast(dict[str, Any], payload.get("artifacts", {}))),
            report_items=report_items,
            error=str(payload.get("error", "")),
        )


def build_water_job_id(job_name: WaterWorkerJobName) -> str:
    return f"water-{job_name}-{get_current_time()}"


def load_water_worker_manifest(path: Path) -> WaterWorkerManifest:
    return WaterWorkerManifest.from_dict(
        cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    )


def write_water_worker_manifest(path: Path, manifest: WaterWorkerManifest) -> None:
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
