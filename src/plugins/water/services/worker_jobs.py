from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Literal, TypedDict, cast

from src.lib.types import JsonValue
from src.lib.utils.common import get_current_time

WaterWorkerJobName = Literal[
    "settlement",
    "message_archive",
    "summary_archive",
    "daily_report_prepare",
]
WaterWorkerStatus = Literal["success", "skipped", "failed", "partial"]
WaterPreparedReportMessageKind = Literal["image", "text"]


class WaterPreparedReportItemPayload(TypedDict):
    group_id: str
    record_date: int
    message_kind: str
    payload_name: str
    activity_score: int
    total_msg_count: int
    active_user_count: int
    error: str


class WaterWorkerManifestPayload(TypedDict):
    job_name: str
    job_id: str
    started_at: int
    finished_at: int
    status: str
    record_date: int | None
    metrics: dict[str, JsonValue]
    artifacts: dict[str, JsonValue]
    report_items: list[WaterPreparedReportItemPayload]
    error: str


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
    metrics: dict[str, JsonValue] = field(default_factory=dict)
    artifacts: dict[str, JsonValue] = field(default_factory=dict)
    report_items: tuple[WaterPreparedReportItem, ...] = ()
    error: str = ""

    def to_dict(self) -> WaterWorkerManifestPayload:
        data = asdict(self)
        data["report_items"] = [asdict(item) for item in self.report_items]
        return cast(WaterWorkerManifestPayload, data)

    @classmethod
    def from_dict(cls, payload: WaterWorkerManifestPayload) -> "WaterWorkerManifest":
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
            for item in payload.get("report_items", [])
        )
        raw_record_date = payload.get("record_date")
        return cls(
            job_name=cast(WaterWorkerJobName, str(payload.get("job_name", ""))),
            job_id=str(payload.get("job_id", "")),
            started_at=int(payload.get("started_at", 0)),
            finished_at=int(payload.get("finished_at", 0)),
            status=cast(WaterWorkerStatus, str(payload.get("status", "failed"))),
            record_date=(int(raw_record_date) if raw_record_date is not None else None),
            metrics=dict(payload.get("metrics", {})),
            artifacts=dict(payload.get("artifacts", {})),
            report_items=report_items,
            error=str(payload.get("error", "")),
        )


def build_water_job_id(job_name: WaterWorkerJobName) -> str:
    return f"water-{job_name}-{get_current_time()}"


def load_water_worker_manifest(path: Path) -> WaterWorkerManifest:
    return WaterWorkerManifest.from_dict(
        cast(
            WaterWorkerManifestPayload,
            json.loads(path.read_text(encoding="utf-8")),
        )
    )


def write_water_worker_manifest(path: Path, manifest: WaterWorkerManifest) -> None:
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
