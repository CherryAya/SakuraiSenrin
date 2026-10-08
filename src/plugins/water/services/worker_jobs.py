from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Literal, TypedDict, cast

from src.lib.types import (
    JsonValue,
    as_int,
    as_str,
    is_json_object,
    is_json_value,
    is_object_mapping,
    is_object_sequence,
)
from src.lib.utils.common import get_current_time

WaterWorkerJobName = Literal[
    "settlement",
    "message_archive",
    "summary_archive",
    "daily_report_prepare",
]
WaterWorkerStatus = Literal["success", "skipped", "failed", "partial"]
WaterPreparedReportMessageKind = Literal["image", "text"]

_WATER_WORKER_STATUSES: tuple[WaterWorkerStatus, ...] = (
    "success",
    "skipped",
    "failed",
    "partial",
)
_WATER_REPORT_MESSAGE_KINDS: tuple[WaterPreparedReportMessageKind, ...] = (
    "image",
    "text",
)
_WATER_WORKER_JOB_NAMES: tuple[WaterWorkerJobName, ...] = (
    "settlement",
    "message_archive",
    "summary_archive",
    "daily_report_prepare",
)


def _as_status(value: object) -> WaterWorkerStatus:
    """把跨进程读回的 status 收窄成字面量，非法值回落 ``failed``。

    manifest 由子进程写出、父进程回读，``.get`` 只能兜「缺 key」，兜不住
    「值非法」：大小写串（``Success``）在过去会被断言成合法字面量一路流出，
    下游按 ``message_kind`` 分派时把 PNG 当文本读。这里显式白名单回落。
    """
    if isinstance(value, str) and value in _WATER_WORKER_STATUSES:
        return value
    return "failed"


def _as_message_kind(value: object) -> WaterPreparedReportMessageKind:
    """同上：报告项的消息形态也只接受白名单值。"""
    if isinstance(value, str) and value in _WATER_REPORT_MESSAGE_KINDS:
        return value
    return "text"


def _as_job_name(value: object) -> WaterWorkerJobName:
    """作业名非法时回落 ``settlement``，避免空串流入 job_id 与文件名。"""
    if isinstance(value, str) and value in _WATER_WORKER_JOB_NAMES:
        return value
    return "settlement"


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


def _parse_report_item(item: object) -> WaterPreparedReportItem | None:
    """把单个报告项解析成 dataclass；非映射形态返回 None（整项丢弃）。"""
    if not is_object_mapping(item):
        return None
    return WaterPreparedReportItem(
        group_id=as_str(item.get("group_id")),
        record_date=as_int(item.get("record_date"), 0),
        message_kind=_as_message_kind(item.get("message_kind")),
        payload_name=as_str(item.get("payload_name")),
        activity_score=as_int(item.get("activity_score"), 0),
        total_msg_count=as_int(item.get("total_msg_count"), 0),
        active_user_count=as_int(item.get("active_user_count"), 0),
        error=as_str(item.get("error")),
    )


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
    metrics: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    artifacts: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    report_items: tuple[WaterPreparedReportItem, ...] = ()
    error: str = ""

    def to_dict(self) -> WaterWorkerManifestPayload:
        data = asdict(self)
        data["report_items"] = [asdict(item) for item in self.report_items]
        return cast(WaterWorkerManifestPayload, data)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "WaterWorkerManifest":
        """从「键为 str 的映射」构造。

        入参声明为 ``Mapping[str, object]`` 而非 ``WaterWorkerManifestPayload``：
        真实来源是跨进程回读的原始 JSON（未经校验），若声明成 TypedDict 就等于
        在签名上撒谎、把校验责任推给调用方——之前那处 ``cast`` 正是这么来的。
        所有字段在这里逐个收窄，非法值回落默认值而不是带病流出。
        """
        raw_items = payload.get("report_items")
        items = raw_items if is_object_sequence(raw_items) else ()
        report_items = tuple(
            parsed for item in items if (parsed := _parse_report_item(item)) is not None
        )
        raw_record_date = payload.get("record_date")
        raw_metrics = payload.get("metrics")
        raw_artifacts = payload.get("artifacts")
        return cls(
            job_name=_as_job_name(payload.get("job_name")),
            job_id=as_str(payload.get("job_id")),
            started_at=as_int(payload.get("started_at"), 0),
            finished_at=as_int(payload.get("finished_at"), 0),
            status=_as_status(payload.get("status")),
            record_date=(
                raw_record_date
                if isinstance(raw_record_date, int)
                and not isinstance(raw_record_date, bool)
                else None
            ),
            metrics=dict(raw_metrics) if is_json_object(raw_metrics) else {},
            artifacts=dict(raw_artifacts) if is_json_object(raw_artifacts) else {},
            report_items=report_items,
            error=as_str(payload.get("error")),
        )


def build_water_job_id(job_name: WaterWorkerJobName) -> str:
    return f"water-{job_name}-{get_current_time()}"


def load_water_worker_manifest(path: Path) -> WaterWorkerManifest:
    """读回子进程写出的 manifest。

    这里是跨进程 JSON 边界，不能用 ``cast`` 把任意解析结果断言成 payload：
    manifest 缺失/被截断/被外部改写时，``json.loads`` 可能返回 list、数字或
    字符串，直接断言会让下游在 ``.get`` 上抛 ``AttributeError``。显式校验成
    映射再逐字段收窄。
    """
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not is_json_value(parsed) or not isinstance(parsed, Mapping):
        raise ValueError(
            f"water worker manifest must be a JSON object, got {type(parsed).__name__}",
        )
    return WaterWorkerManifest.from_dict(parsed)


def write_water_worker_manifest(path: Path, manifest: WaterWorkerManifest) -> None:
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
