from tests.plugins.wordbank.test_media_support import *


async def test_media_load_prefers_local_lru_cache(tmp_path: Path) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    cached_bytes = _png((4, 5, 6))
    cache_path = cache_root / "cached.png"
    cache_path.write_bytes(cached_bytes)
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(cache_root),
    )
    await repo.create_image(
        {
            "md5": "1" * 32,
            "dhash": "0" * 16,
            "phash": "0" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(cached_bytes),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_storage_path": "r2://bucket/wordbank/media/test.png",
            "local_cache_path": str(cache_path),
            "cache_file_size": len(cached_bytes),
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()
    storage.get_bytes = AsyncMock(side_effect=AssertionError("remote should not run"))

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == cached_bytes
    assert repo.images[0].cache_last_hit_at > 0


async def test_media_load_remote_miss_backfills_local_cache_and_updates_access_time(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    data = _png((7, 8, 9))
    storage.objects["wordbank/media/test.png"] = data
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "2" * 32,
            "dhash": "1" * 16,
            "phash": "1" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(data),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == data
    assert repo.images[0].local_cache_path
    assert await asyncio.to_thread(Path(repo.images[0].local_cache_path).is_file)
    assert repo.images[0].last_accessed_at > 0


async def test_media_cache_evicts_least_recently_used_when_size_exceeded(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    first_bytes = _png((10, 20, 30))
    second_bytes = _png((40, 50, 60))
    storage.objects["wordbank/media/first.png"] = first_bytes
    storage.objects["wordbank/media/second.png"] = second_bytes
    cache_storage = LocalLruCacheWordbankMediaStorage(
        tmp_path / "cache",
        max_bytes=len(first_bytes) + 8,
        trim_to_bytes=len(first_bytes),
        max_files=10,
    )
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=cache_storage,
    )
    await repo.create_image(
        {
            "md5": "3" * 32,
            "dhash": "2" * 16,
            "phash": "2" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(first_bytes),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/first.png",
            "remote_storage_path": "r2://bucket/wordbank/media/first.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await repo.create_image(
        {
            "md5": "4" * 32,
            "dhash": "3" * 16,
            "phash": "3" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(second_bytes),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/second.png",
            "remote_storage_path": "r2://bucket/wordbank/media/second.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    assert await service.load_canonical_storage_bytes(1) == first_bytes
    assert await service.load_canonical_storage_bytes(2) == second_bytes

    assert repo.images[0].local_cache_path == ""
    assert repo.images[1].local_cache_path != ""
    first_cache_exists = await asyncio.to_thread(
        Path(tmp_path / "cache" / f"{'3' * 32}.png").exists
    )
    assert not first_cache_exists


async def test_media_cache_singleflight_prevents_duplicate_remote_download(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    data = _png((70, 80, 90))

    class _SlowObjectStorage(_ObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.objects["wordbank/media/test.png"] = data

        async def get_bytes(self, key: str) -> bytes:
            self.calls += 1
            await asyncio.sleep(0.05)
            return await super().get_bytes(key)

    storage = _SlowObjectStorage()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "5" * 32,
            "dhash": "4" * 16,
            "phash": "4" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(data),
            "hash_version": 2,
            "storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_storage_path": "r2://bucket/wordbank/media/test.png",
            "remote_sync_status": "synced",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    first, second = await asyncio.gather(
        service.load_canonical_storage_bytes(1),
        service.load_canonical_storage_bytes(1),
    )

    assert first == data
    assert second == data
    assert storage.calls == 1


async def test_media_prefers_legacy_local_storage_before_remote(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    storage.get_bytes = AsyncMock(side_effect=AssertionError("remote should not run"))
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy.png"
    legacy_bytes = _png((100, 110, 120))
    legacy_path.write_bytes(legacy_bytes)
    service = WordbankMediaService(
        repo,
        media_root=legacy_root,
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "6" * 32,
            "dhash": "5" * 16,
            "phash": "5" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(legacy_bytes),
            "hash_version": 2,
            "storage_path": str(legacy_path),
            "remote_storage_path": "r2://bucket/wordbank/media/missing.png",
            "remote_sync_status": "failed",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == legacy_bytes
    assert repo.images[0].remote_sync_status == "failed"
    storage.get_bytes.assert_not_awaited()


async def test_media_logs_legacy_stages_when_local_file_is_used_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    storage.get_bytes = AsyncMock(side_effect=AssertionError("remote should not run"))
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy.png"
    legacy_bytes = _png((101, 102, 103))
    legacy_path.write_bytes(legacy_bytes)
    service = WordbankMediaService(
        repo,
        media_root=legacy_root,
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
    )
    await repo.create_image(
        {
            "md5": "9" * 32,
            "dhash": "7" * 16,
            "phash": "7" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(legacy_bytes),
            "hash_version": 2,
            "storage_path": str(legacy_path),
            "remote_storage_path": "r2://bucket/wordbank/media/missing.png",
            "remote_sync_status": "failed",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    events: list[str] = []

    def _capture(stage: str, *, start: float | None = None, **fields: Any) -> None:
        _ = start, fields
        events.append(stage)

    monkeypatch.setattr(media_runtime_module, "log_perf", _capture)

    loaded = await service.load_canonical_storage_bytes(1)

    assert loaded == legacy_bytes
    assert "media.load_canonical_storage_bytes.legacy_fetch" in events
    assert "media.load_canonical_storage_bytes.end" in events
    assert "media.load_canonical_storage_bytes.remote_fetch" not in events


async def test_media_backfills_local_cache_metadata_from_existing_cache_files(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    image_bytes = _png((12, 34, 56))
    cache_path = cache_root / f"{'a' * 32}.webp"
    cache_path.write_bytes(image_bytes)
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        cache_storage=LocalLruCacheWordbankMediaStorage(cache_root),
    )
    await repo.create_image(
        {
            "md5": "a" * 32,
            "dhash": "1" * 16,
            "phash": "1" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(image_bytes),
            "hash_version": 2,
            "storage_path": str(tmp_path / "legacy" / "a.webp"),
            "remote_storage_path": "r2://bucket/wordbank/media/test.webp",
            "local_cache_path": "",
            "cache_file_size": 0,
            "created_at": 1,
            "updated_at": 1,
        }
    )

    report = await service.backfill_local_cache_metadata()

    assert report["scanned"] == 1
    assert report["updated"] == 1
    assert report["missing_files"] == 0
    assert repo.images[0].local_cache_path == str(cache_path)
    assert repo.images[0].cache_file_size == len(image_bytes)


async def test_media_backfill_dry_run_reports_updates_without_writing(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    image_bytes = _png((65, 43, 21))
    cache_path = cache_root / f"{'b' * 32}.webp"
    cache_path.write_bytes(image_bytes)
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        cache_storage=LocalLruCacheWordbankMediaStorage(cache_root),
    )
    await repo.create_image(
        {
            "md5": "b" * 32,
            "dhash": "2" * 16,
            "phash": "2" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(image_bytes),
            "hash_version": 2,
            "storage_path": str(tmp_path / "legacy" / "b.webp"),
            "remote_storage_path": "r2://bucket/wordbank/media/test2.webp",
            "local_cache_path": "",
            "cache_file_size": 0,
            "created_at": 1,
            "updated_at": 1,
        }
    )

    report = await service.backfill_local_cache_metadata(dry_run=True)

    assert report["updated"] == 1
    assert report["rows"][0]["action"] == "would_update"
    assert repo.images[0].local_cache_path == ""
    assert repo.images[0].cache_file_size == 0


async def test_media_syncs_legacy_local_image_to_remote_and_keeps_it_readable(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_path = legacy_root / "legacy.webp"
    legacy_bytes = _png((123, 45, 67))
    legacy_path.write_bytes(legacy_bytes)
    service = WordbankMediaService(
        repo,
        media_root=legacy_root,
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        legacy_storage=LocalWordbankMediaStorage(legacy_root),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_provider="r2",
    )
    await repo.create_image(
        {
            "md5": "7" * 32,
            "dhash": "6" * 16,
            "phash": "6" * 16,
            "width": 16,
            "height": 16,
            "file_size": len(legacy_bytes),
            "hash_version": 2,
            "storage_path": str(legacy_path),
            "remote_storage_path": "",
            "remote_sync_status": "pending",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    await service.rebuild_cache()

    updated = await service.sync_image_to_remote(repo.images[0], verify_remote=True)
    assert updated is not None
    assert updated.remote_sync_status == "synced"
    assert updated.remote_storage_path == f"r2://bucket/wordbank/media/{'7' * 32}.webp"

    legacy_path.unlink()
    reloaded = await service.load_canonical_storage_bytes(1)

    assert reloaded == legacy_bytes
