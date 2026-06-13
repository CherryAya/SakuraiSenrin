from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    assert args.limit == 200
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
    rows = [_image_record(1), _image_record(2, status="failed")]
    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(return_value=rows),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(
            side_effect=[
                _image_record(
                    1,
                    status="synced",
                    remote_storage_path="r2://bucket/wordbank/media/1.webp",
                ),
                _image_record(
                    2,
                    status="synced",
                    remote_storage_path="r2://bucket/wordbank/media/2.webp",
                ),
            ]
        ),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=False,
        limit=100,
        id_start=0,
        only_unsynced=True,
        verify_remote=True,
        rebuild_cache_metadata=True,
    )

    assert report["scanned"] == 2
    assert report["synced"] == 2
    assert report["failed"] == 0
    maintain_script.wordbank_repo.list_images_for_remote_sync.assert_awaited_once_with(
        limit=100,
        id_start=0,
        only_unsynced=True,
    )
    assert maintain_script.wordbank_media_service.sync_image_to_remote.await_count == 2


@pytest.mark.asyncio
async def test_maintenance_script_marks_failed_uploads_without_aborting_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    rows = [_image_record(1), _image_record(2)]
    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(return_value=rows),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(
            side_effect=[
                _image_record(
                    1,
                    status="failed",
                    remote_storage_path="",
                ),
                _image_record(
                    2,
                    status="synced",
                    remote_storage_path="r2://bucket/wordbank/media/2.webp",
                ),
            ]
        ),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=False,
        limit=50,
        id_start=10,
        only_unsynced=True,
        verify_remote=False,
        rebuild_cache_metadata=False,
    )

    assert report["scanned"] == 2
    assert report["synced"] == 1
    assert report["failed"] == 1
    assert len(report["rows"]) == 2
    assert report["rows"][0]["remote_sync_status_after"] == "failed"
    assert report["rows"][1]["remote_sync_status_after"] == "synced"


@pytest.mark.asyncio
async def test_maintenance_script_dry_run_skips_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    monkeypatch.setattr(
        maintain_script.wordbank_repo,
        "list_images_for_remote_sync",
        AsyncMock(return_value=[_image_record(3)]),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "rebuild_cache_metadata",
        AsyncMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(
        maintain_script.wordbank_media_service,
        "sync_image_to_remote",
        AsyncMock(),
    )

    report = await maintain_script.maintain_wordbank_media(
        dry_run=True,
        limit=1,
        id_start=0,
        only_unsynced=False,
        verify_remote=False,
        rebuild_cache_metadata=True,
    )

    assert report["rows"][0]["action"] == "inspect"
    maintain_script.wordbank_media_service.sync_image_to_remote.assert_not_awaited()
