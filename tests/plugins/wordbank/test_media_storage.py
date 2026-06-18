from tests.plugins.wordbank.test_media_support import *


async def test_r2_media_storage_saves_and_loads_remote_image(tmp_path: Path) -> None:
    storage = _ObjectStorage()
    media_storage = R2WordbankMediaStorage(
        storage,
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _png((0, 0, 255))
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    loaded = await media_storage.load_bytes(storage_path)

    assert storage_path == f"r2://bucket/wordbank/media/{fingerprint.md5}.webp"
    assert loaded == storage.objects[f"wordbank/media/{fingerprint.md5}.webp"]


async def test_object_media_storage_saves_and_loads_github_uri(tmp_path: Path) -> None:
    storage = _ObjectStorage(provider="github")
    media_storage = ObjectStorageWordbankMediaStorage(
        storage,
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _png((128, 128, 255))
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    loaded = await media_storage.load_bytes(storage_path)

    assert storage_path == f"github://bucket/wordbank/media/{fingerprint.md5}.webp"
    assert loaded == storage.objects[f"wordbank/media/{fingerprint.md5}.webp"]


async def test_r2_media_storage_falls_back_to_local_on_upload_error(
    tmp_path: Path,
) -> None:
    media_storage = R2WordbankMediaStorage(
        _ObjectStorage(fail=True),
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _png((255, 255, 0))
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )

    assert storage_path == str(tmp_path / f"{fingerprint.md5}.webp")
    assert (tmp_path / f"{fingerprint.md5}.webp").is_file()


async def test_r2_media_storage_saves_gif_as_animated_media(tmp_path: Path) -> None:
    storage = _ObjectStorage()
    media_storage = R2WordbankMediaStorage(
        storage,
        fallback=LocalWordbankMediaStorage(tmp_path),
    )
    data = _gif([(0, 0, 255), (255, 255, 0)])
    fingerprint = fingerprint_image(data)

    storage_path = await media_storage.save_image(
        prepare_image_bytes(data),
        md5_hex=fingerprint.md5,
        keep_original=False,
    )
    key = storage_path.removeprefix("r2://bucket/")
    loaded = await media_storage.load_bytes(storage_path)

    assert loaded is not None
    assert loaded == storage.objects[key]
    assert Path(key).suffix == ".webp"
    assert storage.content_types[key] == "image/webp"
    with Image.open(BytesIO(loaded)) as stored_image:
        assert getattr(stored_image, "n_frames", 1) > 1


async def test_media_ingest_persists_remote_storage_path_for_new_images(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_sync_mode="sync",
    )

    image = await service.ingest_image_bytes(_png((1, 2, 3)))

    assert image.remote_storage_path == f"r2://bucket/wordbank/media/{image.md5}.webp"
    assert image.remote_sync_status == "synced"
    assert image.storage_path == image.remote_storage_path
    assert await asyncio.to_thread(Path(repo.images[0].local_cache_path).is_file)


async def test_media_ingest_defers_remote_sync_and_keeps_local_storage_available(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    storage = _ObjectStorage()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=ObjectStorageWordbankMediaStorage(storage),
        legacy_storage=LocalWordbankMediaStorage(tmp_path / "legacy"),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_sync_mode="deferred",
    )

    image = await service.ingest_image_bytes(_png((14, 15, 16)))
    await asyncio.sleep(0)

    assert image.remote_storage_path == ""
    assert image.remote_sync_status == "pending"
    assert await asyncio.to_thread(Path(image.storage_path).is_file)
    assert storage.objects[f"wordbank/media/{image.md5}.webp"]
    assert repo.images[0].remote_sync_status == "synced"


async def test_media_ingest_marks_failed_when_expected_remote_is_unavailable(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=None,
        legacy_storage=LocalWordbankMediaStorage(tmp_path / "legacy"),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_provider="r2",
    )

    image = await service.ingest_image_bytes(_png((9, 9, 9)))

    assert image.remote_sync_status == "failed"
    assert image.remote_storage_path == ""
    assert await asyncio.to_thread(Path(image.storage_path).is_file)


async def test_media_ingest_raises_when_remote_required_but_provider_is_unavailable(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(
        repo,
        media_root=tmp_path / "legacy",
        remote_storage=None,
        legacy_storage=LocalWordbankMediaStorage(tmp_path / "legacy"),
        cache_storage=LocalLruCacheWordbankMediaStorage(tmp_path / "cache"),
        remote_provider="github",
        remote_required=True,
    )

    with pytest.raises(media_module.MediaError):
        await service.ingest_image_bytes(_png((11, 11, 11)))
