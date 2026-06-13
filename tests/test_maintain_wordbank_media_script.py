from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from scripts import maintain_wordbank_media as maintain_script
from src.plugins.wordbank.database.types import WordbankImageRecord


def _image_record(
    image_id: int,
    *,
    status: str = "pending",
    remote_storage_path: str = "",
) -> WordbankImageRecord:
    return WordbankImageRecord(
        id=image_id,
        canonical_image_id=image_id,
        md5=f"{image_id:032x}",
        dhash="0" * 16,
        phash="0" * 16,
        width=16,
        height=16,
        file_size=128,
        hash_version=2,
        storage_path=f"/tmp/{image_id}.webp",
        remote_storage_path=remote_storage_path,
        remote_sync_status=status,
    )


def _install_fake_components(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        maintain_script,
        "wordbank_repo",
        SimpleNamespace(
            init_all_tables=None,
            list_images_for_remote_sync=None,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        maintain_script,
        "wordbank_media_service",
        SimpleNamespace(
            rebuild_cache_metadata=None,
            list_remote_objects_by_key=None,
            reconcile_image_with_remote_inventory=None,
            remote_storage=None,
            sync_image_to_remote=None,
        ),
        raising=False,
    )
    monkeypatch.setattr(maintain_script, "_load_wordbank_components", lambda: None)


def test_maintain_wordbank_media_parse_args_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["maintain_wordbank_media.py"])

    args = maintain_script.parse_args()

    assert args.dry_run is False
    assert args.limit == 0
    assert args.batch_size == maintain_script.DEFAULT_BATCH_SIZE
    assert args.concurrency == maintain_script.DEFAULT_CONCURRENCY
    assert args.id_start == 0
    assert args.only_unsynced is False
    assert args.verify_remote is False
    assert args.rebuild_cache_metadata is False
    assert args.report == "./data/db/wordbank-media-maintenance-report.json"


@pytest.mark.asyncio
async def test_maintenance_script_uploads_all_unsynced_wordbank_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    rows_by_call = [
        [_image_record(1), _image_record(2, status="failed")],
        [],
    ]

    async def list_rows(**_: object) -> list[WordbankImageRecord]:
        return rows_by_call.pop(0)

    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(side_effect=list_rows),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "list_remote_objects_by_key",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "reconcile_image_with_remote_inventory",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(
            side_effect=lambda image, verify_remote: _image_record(
                image.id,
                status="synced",
                remote_storage_path=f"r2://bucket/wordbank/media/{image.id}.webp",
            )
        ),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=False,
        limit=100,
        batch_size=2,
        concurrency=2,
        id_start=0,
        only_unsynced=True,
        verify_remote=True,
        rebuild_cache_metadata=True,
    )

    assert report["scanned"] == 2
    assert report["synced"] == 2
    assert report["failed"] == 0
    assert report["skipped"] == 0
    assert (
        maintain_script.wordbank_repo.list_images_for_remote_sync.await_args_list
        == [
            call(limit=2, id_start=0, only_unsynced=True),
            call(limit=2, id_start=3, only_unsynced=True),
        ]
    )
    assert maintain_script.wordbank_media_service.sync_image_to_remote.await_count == 2


@pytest.mark.asyncio
async def test_maintenance_script_marks_failed_uploads_without_aborting_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    rows_by_call = [[_image_record(1), _image_record(2)], []]

    async def list_rows(**_: object) -> list[WordbankImageRecord]:
        return rows_by_call.pop(0)

    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(side_effect=list_rows),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "list_remote_objects_by_key",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "reconcile_image_with_remote_inventory",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(
            side_effect=lambda image, verify_remote: _image_record(
                image.id,
                status="failed" if image.id == 1 else "synced",
                remote_storage_path=(
                    "" if image.id == 1 else "r2://bucket/wordbank/media/2.webp"
                ),
            )
        ),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=False,
        limit=50,
        batch_size=10,
        concurrency=4,
        id_start=10,
        only_unsynced=True,
        verify_remote=False,
        rebuild_cache_metadata=False,
    )

    assert report["scanned"] == 2
    assert report["synced"] == 1
    assert report["failed"] == 1
    assert report["skipped"] == 0
    assert len(report["rows"]) == 2
    by_id = {row["id"]: row for row in report["rows"]}
    assert by_id[1]["remote_sync_status_after"] == "failed"
    assert by_id[2]["remote_sync_status_after"] == "synced"


@pytest.mark.asyncio
async def test_maintenance_script_dry_run_skips_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(side_effect=[[_image_record(3)], []]),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "list_remote_objects_by_key",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "reconcile_image_with_remote_inventory",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=True,
        limit=1,
        batch_size=1,
        concurrency=1,
        id_start=0,
        only_unsynced=False,
        verify_remote=False,
        rebuild_cache_metadata=True,
    )

    assert report["rows"][0]["action"] == "inspect"
    assert report["skipped"] == 0
    maintain_script.wordbank_media_service.sync_image_to_remote.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_script_paginates_until_total_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    rows_by_call = [
        [_image_record(11), _image_record(12)],
        [_image_record(13)],
    ]

    async def list_rows(**_: object) -> list[WordbankImageRecord]:
        if rows_by_call:
            return rows_by_call.pop(0)
        return []

    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(side_effect=list_rows),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "list_remote_objects_by_key",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "reconcile_image_with_remote_inventory",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(
            side_effect=lambda image, verify_remote: _image_record(
                image.id,
                status="synced",
                remote_storage_path=f"r2://bucket/wordbank/media/{image.id}.webp",
            )
        ),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=False,
        limit=3,
        batch_size=2,
        concurrency=2,
        id_start=11,
        only_unsynced=True,
        verify_remote=False,
        rebuild_cache_metadata=False,
    )

    assert report["scanned"] == 3
    assert report["synced"] == 3
    assert (
        maintain_script.wordbank_repo.list_images_for_remote_sync.await_args_list
        == [
            call(limit=2, id_start=11, only_unsynced=True),
            call(limit=1, id_start=13, only_unsynced=True),
        ]
    )


@pytest.mark.asyncio
async def test_maintenance_script_verify_remote_uses_inventory_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    rows_by_call = [[_image_record(21), _image_record(22)], []]

    async def list_rows(**_: object) -> list[WordbankImageRecord]:
        return rows_by_call.pop(0)

    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(side_effect=list_rows),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "remote_storage",
        object(),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "list_remote_objects_by_key",
        AsyncMock(
            return_value={
                "wordbank/media/00000000000000000000000000000015.webp": object()
            }
        ),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "reconcile_image_with_remote_inventory",
        AsyncMock(
            side_effect=lambda image, remote_inventory: (
                _image_record(
                    21,
                    status="synced",
                    remote_storage_path="r2://bucket/wordbank/media/00000000000000000000000000000015.webp",
                )
                if image.id == 21
                else None
            )
        ),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(
            side_effect=lambda image, verify_remote: _image_record(
                image.id,
                status="synced",
                remote_storage_path=f"r2://bucket/wordbank/media/{image.md5}.webp",
            )
        ),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=False,
        limit=0,
        batch_size=10,
        concurrency=2,
        id_start=21,
        only_unsynced=True,
        verify_remote=True,
        rebuild_cache_metadata=False,
    )

    assert report["scanned"] == 2
    assert report["synced"] == 2
    assert report["rows"][0]["action"] == "verify"
    assert report["rows"][1]["action"] == "sync"
    maintain_script.wordbank_media_service.list_remote_objects_by_key.assert_awaited_once()
    maintain_script.wordbank_media_service.sync_image_to_remote.assert_awaited_once_with(
        _image_record(22),
        verify_remote=False,
    )
