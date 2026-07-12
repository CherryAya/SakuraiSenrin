from tests.plugins.wordbank.test_media_support import *


def test_fingerprint_and_hamming_distance() -> None:
    first = fingerprint_image(_png((255, 0, 0)))
    second = fingerprint_image(_png((255, 0, 0)))

    assert first.md5 == second.md5
    assert first.width == 16
    assert first.height == 16
    assert hamming_distance(first.dhash, second.dhash) == 0


async def test_media_ingest_dedupes_md5_and_uses_cache(tmp_path: Path) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _png((0, 255, 0))

    first = await service.ingest_image_bytes(data)
    second = await service.ingest_image_bytes(data)

    assert first.id == second.id
    assert len(repo.images) == 1
    assert service.resolve_canonical_id(data) == first.canonical_id
    assert (tmp_path / f"{first.md5}.webp").is_file()


async def test_media_search_similar_images_returns_ranked_matches(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    source = _png((64, 128, 255))

    first = await service.ingest_image_bytes(source)
    await service.rebuild_cache()
    matches = service.search_similar_images(source)

    assert matches
    assert matches[0].canonical_id == first.canonical_id
    assert matches[0].score == 1.0


class _TestObjectStorage:
    available = True

    def __init__(self, *, provider: str = "r2", fail: bool = False) -> None:
        self.provider = provider
        self.bucket = "bucket"
        self.fail = fail
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str | None] = {}

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StorageObject:
        _ = content_type
        if self.fail:
            raise RuntimeError("upload failed")
        self.objects[key] = data
        self.content_types[key] = content_type
        return StorageObject(
            provider=self.provider,
            bucket="bucket",
            key=key,
            uri=f"{self.provider}://bucket/{key}",
            public_url=None,
            etag="etag",
            size=len(data),
        )

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def list_objects(self, prefix: str) -> list[StorageObject]:
        normalized_prefix = prefix.strip("/")
        return [
            StorageObject(
                provider=self.provider,
                bucket=self.bucket,
                key=key,
                uri=f"{self.provider}://{self.bucket}/{key}",
                public_url=None,
                etag="etag",
                size=len(data),
            )
            for key, data in self.objects.items()
            if key.startswith(normalized_prefix)
        ]

    async def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        _ = expires_in
        return f"https://example.test/{key}"


def test_fingerprint_uses_representative_gif_frame() -> None:
    first_frame = _png((255, 0, 0))
    animated = _gif([(255, 0, 0), (0, 0, 255)])

    still_fingerprint = fingerprint_image(first_frame)
    animated_fingerprint = fingerprint_image(animated)

    assert animated_fingerprint.md5 != still_fingerprint.md5
    assert animated_fingerprint.dhash == still_fingerprint.dhash
    assert animated_fingerprint.phash == still_fingerprint.phash
    assert animated_fingerprint.width == 16
    assert animated_fingerprint.height == 16


async def test_media_ingest_preserves_animation_bytes_for_gif(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    image = await service.ingest_image_bytes(data)
    stored_path = Path(image.storage_path)
    stored_bytes = await asyncio.to_thread(stored_path.read_bytes)

    assert stored_path.suffix == ".gif"
    assert stored_bytes == data
    with Image.open(BytesIO(stored_bytes)) as stored_image:
        assert str(getattr(stored_image, "format", "")).upper() == "GIF"
        assert getattr(stored_image, "n_frames", 1) > 1


async def test_media_ingest_detects_gif_by_header_even_when_suffix_is_jpg(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    fake_jpg_path = tmp_path / "fake-animation.jpg"
    fake_jpg_path.write_bytes(_gif([(255, 0, 0), (0, 255, 0)]))

    image = await service.ingest_image_bytes(fake_jpg_path.read_bytes())
    stored_path = Path(image.storage_path)
    stored_bytes = await asyncio.to_thread(stored_path.read_bytes)

    assert stored_path.suffix == ".gif"
    assert stored_bytes == fake_jpg_path.read_bytes()
    with Image.open(BytesIO(stored_bytes)) as stored_image:
        assert str(getattr(stored_image, "format", "")).upper() == "GIF"
        assert getattr(stored_image, "n_frames", 1) > 1


async def test_media_ingest_rewrites_animated_webp_to_gif(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _animated_webp([(255, 0, 0), (0, 255, 0)])

    image = await service.ingest_image_bytes(data)
    stored_path = Path(image.storage_path)
    stored_bytes = await asyncio.to_thread(stored_path.read_bytes)

    assert stored_path.suffix == ".gif"
    assert stored_bytes != data
    with Image.open(BytesIO(stored_bytes)) as stored_image:
        assert str(getattr(stored_image, "format", "")).upper() == "GIF"
        assert getattr(stored_image, "n_frames", 1) > 1


def test_prepare_image_bytes_rewrites_apng_to_gif() -> None:
    data = _apng([(255, 0, 0), (0, 0, 255)])

    prepared = prepare_image_bytes(data)

    assert prepared.stored_media.extension == ".gif"
    assert prepared.stored_media.content_type == "image/gif"
    with Image.open(BytesIO(prepared.stored_media.data)) as stored_image:
        assert str(getattr(stored_image, "format", "")).upper() == "GIF"
        assert getattr(stored_image, "n_frames", 1) > 1


async def test_media_ingest_dedupes_gif_by_md5_before_similarity(
    tmp_path: Path,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    first = await service.ingest_image_bytes(data)
    second = await service.ingest_image_bytes(data)

    assert first.id == second.id
    assert len(repo.images) == 1


async def test_media_ingest_short_circuits_on_md5_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    first = await service.ingest_image_bytes(data)

    def _fail_prepare(_data: bytes) -> Any:
        raise AssertionError("prepare_image_bytes should not run after md5 hit")

    monkeypatch.setattr(media_module, "prepare_image_bytes", _fail_prepare)

    second = await service.ingest_image_bytes(data)

    assert second.id == first.id


async def test_resolve_canonical_id_short_circuits_on_name_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _png((10, 20, 30))

    first = await service.ingest_image_bytes(data)

    def _fail_fingerprint(_data: bytes) -> Any:
        raise AssertionError("fingerprint_image should not run after md5 hint hit")

    monkeypatch.setattr(media_module, "fingerprint_image", _fail_fingerprint)

    resolved = service.resolve_canonical_id(
        b"not-an-image",
        name_hints=[f"https://example.test/path/{first.md5.upper()}.PNG?download=1"],
    )

    assert resolved == first.canonical_id


async def test_resolve_canonical_id_short_circuits_on_raw_bytes_md5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _ImageRepo()
    service = WordbankMediaService(repo, media_root=tmp_path)
    data = _gif([(255, 0, 0), (0, 255, 0)])

    first = await service.ingest_image_bytes(data)

    def _fail_fingerprint(_data: bytes) -> Any:
        raise AssertionError("fingerprint_image should not run after raw md5 hit")

    monkeypatch.setattr(media_module, "fingerprint_image", _fail_fingerprint)

    resolved = service.resolve_canonical_id(data)

    assert resolved == first.canonical_id


def test_prepare_image_bytes_falls_back_to_original_animated_webp_when_gif_encode_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _animated_webp([(255, 0, 255), (0, 255, 255)])

    monkeypatch.setattr(
        media_models_module,
        "_encode_animated_gif",
        lambda _image, resize_to_limit=False: None,
    )

    prepared = prepare_image_bytes(data)

    assert prepared.stored_media.extension == ".webp"
    assert prepared.stored_media.content_type == "image/webp"
    assert prepared.stored_media.data == data


def test_prepare_image_bytes_falls_back_to_original_jpeg_when_static_webp_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _jpeg((12, 34, 56))

    monkeypatch.setattr(media_models_module, "_encode_static_webp", lambda _image: None)

    prepared = prepare_image_bytes(data)

    assert prepared.stored_media.extension == ".jpg"
    assert prepared.stored_media.content_type == "image/jpeg"
    assert prepared.stored_media.data == data
