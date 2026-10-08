"""worker manifest 跨进程 JSON 边界的类型诚实性测试。

manifest 由子进程写出、父进程回读，属于「外部数据」；历史上的实现用 cast
把原始 JSON 直接断言成 TypedDict，非法值会带病流出到下游分派逻辑。
这里锁定新的白名单/形态校验行为。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.water.services.worker_jobs import (
    WaterWorkerManifest,
    load_water_worker_manifest,
    write_water_worker_manifest,
)


def _write_raw_manifest(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_manifest_roundtrip_preserves_valid_values(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    original = WaterWorkerManifest(
        job_name="daily_report_prepare",
        job_id="job-1",
        started_at=1,
        finished_at=2,
        status="partial",
        record_date=20260718,
        metrics={"candidate_groups": 2},
        artifacts={"report": "a.png"},
        error="",
    )
    write_water_worker_manifest(path, original)

    assert load_water_worker_manifest(path) == original


def test_load_manifest_rejects_non_object_json(tmp_path: Path) -> None:
    """非对象 JSON（list/标量）必须显式报错，而不是断言后在下游 .get 崩掉。"""
    path = tmp_path / "manifest.json"
    _write_raw_manifest(path, [1, 2, 3])

    with pytest.raises(ValueError, match="JSON object"):
        load_water_worker_manifest(path)


@pytest.mark.parametrize(
    "bad_status", ["Success", "SUCCESS", "", "done", "  success  "]
)
def test_load_manifest_falls_back_on_invalid_status(
    tmp_path: Path, bad_status: str
) -> None:
    """非白名单 status 必须回落 failed，不得以 Literal 之名流出。

    历史故障：大小写串被 cast 成合法字面量，下游按 message_kind 分派时
    把 PNG 当文本读，UnicodeDecodeError 被 per-item except 吞掉，单群静默丢失。
    """
    path = tmp_path / "manifest.json"
    _write_raw_manifest(path, {"status": bad_status, "job_name": "settlement"})

    assert load_water_worker_manifest(path).status == "failed"


def test_load_manifest_keeps_whitelisted_status(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write_raw_manifest(path, {"status": "skipped", "job_name": "settlement"})

    assert load_water_worker_manifest(path).status == "skipped"


@pytest.mark.parametrize("bad_kind", ["Image", "png", 7, None, ""])
def test_load_manifest_normalizes_report_item_message_kind(
    tmp_path: Path, bad_kind: object
) -> None:
    """报告项 message_kind 非法时回落 text，避免把二进制当文本读。"""
    path = tmp_path / "manifest.json"
    _write_raw_manifest(
        path,
        {
            "status": "success",
            "job_name": "daily_report_prepare",
            "report_items": [{"group_id": "10001", "message_kind": bad_kind}],
        },
    )

    items = load_water_worker_manifest(path).report_items
    assert len(items) == 1
    assert items[0].message_kind == "text"


def test_load_manifest_drops_non_mapping_report_items(tmp_path: Path) -> None:
    """report_items 混入非映射元素时整项丢弃，不得抛异常拖垮整份 manifest。"""
    path = tmp_path / "manifest.json"
    _write_raw_manifest(
        path,
        {
            "status": "success",
            "job_name": "daily_report_prepare",
            "report_items": [
                "not-a-mapping",
                42,
                {"group_id": "10001", "message_kind": "image"},
            ],
        },
    )

    items = load_water_worker_manifest(path).report_items
    assert len(items) == 1
    assert items[0].group_id == "10001"
    assert items[0].message_kind == "image"


def test_load_manifest_tolerates_wrong_typed_scalar_fields(tmp_path: Path) -> None:
    """标量字段类型不符时走默认值，不得 int(None) / int(True) 式静默错误。

    旧实现用 int(x)：``{"started_at": null}`` 的默认值不生效会抛 TypeError，
    ``true`` 会变成 1 绕过校验；as_int 显式排除 bool 与非整数。
    """
    path = tmp_path / "manifest.json"
    _write_raw_manifest(
        path,
        {
            "status": "success",
            "job_name": "settlement",
            "started_at": None,
            "finished_at": True,
            "record_date": "20260718",
            "report_items": [{"group_id": "10001", "activity_score": True}],
        },
    )

    manifest = load_water_worker_manifest(path)
    assert manifest.started_at == 0
    assert manifest.finished_at == 0  # bool 不被当成 1
    assert manifest.record_date is None
    assert manifest.report_items[0].activity_score == 0


def test_load_manifest_ignores_non_mapping_metrics(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write_raw_manifest(
        path,
        {"status": "success", "job_name": "settlement", "metrics": [1, 2]},
    )

    assert load_water_worker_manifest(path).metrics == {}
