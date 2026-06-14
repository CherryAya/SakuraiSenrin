from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import backfill_wordbank_media_cache as backfill_script


def _install_fake_components(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backfill_script,
        "wordbank_repo",
        SimpleNamespace(init_all_tables=None),
        raising=False,
    )
    monkeypatch.setattr(
        backfill_script,
        "wordbank_media_service",
        SimpleNamespace(backfill_local_cache_metadata=None),
        raising=False,
    )
    monkeypatch.setattr(backfill_script, "_load_wordbank_components", lambda: None)


def test_backfill_script_parse_args_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["backfill_wordbank_media_cache.py"])

    args = backfill_script.parse_args()

    assert args.dry_run is False
    assert args.limit == 0
    assert args.id_start == 0
    assert args.include_existing is False
    assert args.report == backfill_script.DEFAULT_REPORT_PATH


@pytest.mark.asyncio
async def test_backfill_script_calls_service_with_expected_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_components(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_backfill(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "dry_run": False,
            "limit": 50,
            "id_start": 100,
            "only_missing": False,
            "scanned": 3,
            "updated": 2,
            "unchanged": 0,
            "skipped_existing": 1,
            "missing_files": 0,
            "failed": 0,
            "rows": [],
        }

    monkeypatch.setattr(
        backfill_script.wordbank_media_service,
        "backfill_local_cache_metadata",
        fake_backfill,
    )

    report = await backfill_script.backfill_wordbank_media_cache(
        dry_run=False,
        limit=50,
        id_start=100,
        include_existing=True,
    )

    assert captured == {
        "dry_run": False,
        "limit": 50,
        "id_start": 100,
        "only_missing": False,
    }
    assert report["scanned"] == 3
    assert report["updated"] == 2
    assert report["include_existing"] is True
