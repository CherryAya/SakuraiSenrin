from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import tarfile

import pytest
import zstandard

from src.lib import docs_assets
from src.lib.docs_assets import DocsAssetsError
from src.lib.object_storage.types import StorageObject


class MemoryStorage:
    provider = "r2"
    available = True

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.downloads = 0
        self.uploads = 0
        self.fail_download = False

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str | None = None
    ) -> StorageObject:
        self.objects[key] = data
        self.uploads += 1
        return StorageObject(
            "r2", "test", key, f"r2://test/{key}", None, None, len(data)
        )

    async def get_bytes(self, key: str) -> bytes:
        self.downloads += 1
        if self.fail_download:
            raise OSError("offline")
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def delete_object(self, key: str) -> None:
        del self.objects[key]

    async def list_objects(self, prefix: str) -> list[StorageObject]:
        return [
            StorageObject("r2", "test", key, f"r2://test/{key}", None, None, len(data))
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    async def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        return f"https://example.invalid/{key}"


@pytest.fixture
def assets_root(tmp_path: Path) -> Path:
    docs = tmp_path / "src/plugins/sample/docs"
    demos = docs / "demos"
    demos.mkdir(parents=True)
    (docs / "README.MD").write_text("# Example\n", encoding="utf-8")
    (demos / "sample.webp").write_bytes(b"RIFFexampleWEBP")
    (demos / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "locale": "zh-CN",
                "targets": {"guide:sample": {"normal": "sample.webp"}},
            }
        ),
        encoding="utf-8",
    )
    qr = tmp_path / docs_assets.SUPPORT_QR_PATH
    qr.parent.mkdir(parents=True)
    qr.write_bytes(b"png-example")
    for name in docs_assets.STATIC_ASSET_PATHS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"resource:{name}".encode())
    return tmp_path


def _remove_resources(root: Path, lock: docs_assets.AssetLock) -> None:
    for entry in lock.files:
        if not entry.path.endswith("/manifest.json"):
            (root / entry.path).unlink(missing_ok=True)


async def test_publish_is_content_addressed_and_lock_is_written_after_verification(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    first = await docs_assets.publish_assets(assets_root, storage=storage)
    second = await docs_assets.publish_assets(assets_root, storage=storage)

    assert first == second == docs_assets.load_lock(assets_root)
    assert storage.uploads == 1
    assert storage.downloads == 2
    assert docs_assets.check_assets(assets_root).ready


async def test_empty_checkout_downloads_without_rendering_and_second_start_is_offline(
    assets_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryStorage()
    lock = await docs_assets.publish_assets(assets_root, storage=storage)
    expected = {
        entry.path: (assets_root / entry.path).read_bytes() for entry in lock.files
    }
    _remove_resources(assets_root, lock)
    shutil.rmtree(assets_root / "output")
    storage.downloads = 0

    assert (await docs_assets.pull_assets(assets_root, storage=storage)).ready
    assert storage.downloads == 1
    assert all(
        (assets_root / name).read_bytes() == data for name, data in expected.items()
    )

    def offline() -> None:
        raise AssertionError("valid local assets must not initialize storage")

    monkeypatch.setattr(docs_assets, "_storage", offline)
    assert (await docs_assets.pull_assets(assets_root)).ready


async def test_valid_package_cache_repairs_corruption_without_network(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    await docs_assets.publish_assets(assets_root, storage=storage)
    image = assets_root / "src/plugins/sample/docs/demos/sample.webp"
    image.write_bytes(b"corrupt")
    storage.fail_download = True

    assert (await docs_assets.pull_assets(assets_root, storage=storage)).ready
    assert image.read_bytes() == b"RIFFexampleWEBP"


async def test_force_pull_downloads_again(assets_root: Path) -> None:
    storage = MemoryStorage()
    await docs_assets.publish_assets(assets_root, storage=storage)
    storage.downloads = 0
    assert (
        await docs_assets.pull_assets(assets_root, force=True, storage=storage)
    ).ready
    assert storage.downloads == 1


async def test_sync_startup_entrypoint_supports_an_existing_event_loop(
    assets_root: Path,
) -> None:
    await docs_assets.publish_assets(assets_root, storage=MemoryStorage())
    docs_assets.init_docs_assets(assets_root)
    assert docs_assets.check_assets(assets_root).ready


async def test_revision_change_installs_new_files_and_removes_only_managed_old_files(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    await docs_assets.publish_assets(assets_root, storage=storage)
    production = assets_root / "production"
    shutil.copytree(
        assets_root,
        production,
        ignore=shutil.ignore_patterns("production", "output"),
    )
    await docs_assets.pull_assets(production, force=True, storage=storage)
    demos = assets_root / "src/plugins/sample/docs/demos"
    (demos / "sample.webp").rename(demos / "new.webp")
    manifest = demos / "manifest.json"
    manifest.write_text(manifest.read_text().replace("sample.webp", "new.webp"))
    await docs_assets.publish_assets(assets_root, storage=storage)
    production_demos = production / "src/plugins/sample/docs/demos"
    shutil.copy2(manifest, production_demos / "manifest.json")
    shutil.copy2(
        assets_root / docs_assets.LOCK_FILENAME, production / docs_assets.LOCK_FILENAME
    )
    (production_demos / "unmanaged.png").write_bytes(b"keep me")

    assert (await docs_assets.pull_assets(production, storage=storage)).ready
    assert (production_demos / "new.webp").read_bytes() == b"RIFFexampleWEBP"
    assert not (production_demos / "sample.webp").exists()
    assert (production_demos / "unmanaged.png").read_bytes() == b"keep me"


async def test_invalid_download_does_not_overwrite_local_files(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    lock = await docs_assets.publish_assets(assets_root, storage=storage)
    storage.objects[lock.archive_key] = b"corrupt download"
    image = assets_root / "src/plugins/sample/docs/demos/sample.webp"
    before = image.read_bytes()

    with pytest.raises(DocsAssetsError, match="SHA-256"):
        await docs_assets.pull_assets(assets_root, force=True, storage=storage)
    assert image.read_bytes() == before


async def test_failed_publication_does_not_change_existing_lock(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    await docs_assets.publish_assets(assets_root, storage=storage)
    before = (assets_root / docs_assets.LOCK_FILENAME).read_bytes()
    storage.fail_download = True
    (assets_root / "src/plugins/sample/docs/demos/sample.webp").write_bytes(
        b"new-image"
    )

    with pytest.raises(DocsAssetsError, match="transfer failed"):
        await docs_assets.publish_assets(assets_root, storage=storage)
    assert (assets_root / docs_assets.LOCK_FILENAME).read_bytes() == before


async def test_source_change_is_rejected_before_downloading(assets_root: Path) -> None:
    storage = MemoryStorage()
    await docs_assets.publish_assets(assets_root, storage=storage)
    (assets_root / "src/plugins/sample/docs/README.MD").write_text("# Changed\n")
    storage.downloads = 0

    with pytest.raises(DocsAssetsError, match="build inputs changed"):
        await docs_assets.pull_assets(assets_root, force=True, storage=storage)
    assert storage.downloads == 0


async def test_publication_rejects_missing_manifest_target(assets_root: Path) -> None:
    (assets_root / "src/plugins/sample/docs/demos/sample.webp").unlink()
    storage = MemoryStorage()
    with pytest.raises(DocsAssetsError, match="missing generated asset declared"):
        await docs_assets.publish_assets(assets_root, storage=storage)
    assert storage.uploads == 0


@pytest.mark.parametrize(
    "contents", [None, b"version https://git-lfs.github.com/spec/v1\n"]
)
async def test_publish_rejects_missing_fonts_or_lfs_pointers(
    assets_root: Path, contents: bytes | None
) -> None:
    font = assets_root / "data/font/LXGW_NERD_NOTO.ttf"
    if contents is None:
        font.unlink()
    else:
        font.write_bytes(contents)
    storage = MemoryStorage()
    with pytest.raises(DocsAssetsError, match="missing"):
        await docs_assets.publish_assets(assets_root, storage=storage)
    assert storage.uploads == 0


async def test_font_revision_is_locked_and_damaged_font_is_restored(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    first = await docs_assets.publish_assets(assets_root, storage=storage)
    font = assets_root / "data/font/LXGW_NERD_NOTO.ttf"
    font.write_bytes(b"new font contents")
    second = await docs_assets.publish_assets(assets_root, storage=storage)
    assert first.input_hash != second.input_hash
    font.write_bytes(b"damaged")
    assert not docs_assets.check_assets(assets_root).ready
    assert (await docs_assets.pull_assets(assets_root, storage=storage)).ready
    assert font.read_bytes() == b"new font contents"


async def test_lock_rejects_omitted_static_resource(assets_root: Path) -> None:
    lock = await docs_assets.publish_assets(assets_root, storage=MemoryStorage())
    payload = asdict(lock)
    payload["files"] = [
        entry
        for entry in payload["files"]
        if entry["path"] not in docs_assets.STATIC_ASSET_PATHS
    ]
    (assets_root / docs_assets.LOCK_FILENAME).write_text(json.dumps(payload))
    with pytest.raises(DocsAssetsError, match="missing required"):
        docs_assets.load_lock(assets_root)


@pytest.mark.parametrize(
    "name",
    [
        "../escape.webp",
        "/tmp/escape.webp",
        "src/plugins/sample/docs/demos/../../escape.webp",
        "src/plugins/sample/docs/demos/bad.py",
        "src/plugins/sample/docs/demos/sub/image.webp",
    ],
)
async def test_lock_rejects_unsafe_or_unmanaged_paths(
    assets_root: Path, name: str
) -> None:
    lock = await docs_assets.publish_assets(assets_root, storage=MemoryStorage())
    payload = asdict(lock)
    payload["files"][0]["path"] = name
    (assets_root / docs_assets.LOCK_FILENAME).write_text(json.dumps(payload))
    with pytest.raises(DocsAssetsError, match="invalid generated asset path"):
        docs_assets.load_lock(assets_root)


async def test_archive_rejects_extra_member_before_installation(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    lock = await docs_assets.publish_assets(assets_root, storage=storage)
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        data = b"escape"
        info = tarfile.TarInfo("../escape.webp")
        info.size = len(data)
        archive.addfile(info, BytesIO(data))
    payload = zstandard.ZstdCompressor().compress(buffer.getvalue())
    digest = sha256(payload).hexdigest()
    malicious = replace(
        lock,
        archive_key=f"docs-assets/{digest}.tar.zst",
        archive_sha256=digest,
        archive_size=len(payload),
    )
    (assets_root / docs_assets.LOCK_FILENAME).write_text(json.dumps(asdict(malicious)))
    storage.objects[malicious.archive_key] = payload

    with pytest.raises(DocsAssetsError, match="invalid generated asset path"):
        await docs_assets.pull_assets(assets_root, force=True, storage=storage)
    assert not (assets_root.parent / "escape.webp").exists()


async def test_symlink_destination_is_rejected(
    assets_root: Path, tmp_path: Path
) -> None:
    await docs_assets.publish_assets(assets_root, storage=MemoryStorage())
    image = assets_root / "src/plugins/sample/docs/demos/sample.webp"
    image.unlink()
    image.symlink_to(tmp_path / "outside.webp")
    with pytest.raises(DocsAssetsError, match="symlink"):
        docs_assets.check_assets(assets_root)


async def test_remote_prune_previews_and_protects_current_and_explicit_rollbacks(
    assets_root: Path,
) -> None:
    storage = MemoryStorage()
    lock = await docs_assets.publish_assets(assets_root, storage=storage)
    revisions = ["a" * 64, "b" * 64, "c" * 64]
    for revision in revisions:
        storage.objects[f"docs-assets/{revision}.tar.zst"] = b"package"
    preview = await docs_assets.prune_remote_assets(
        assets_root, keep=revisions[:2], storage=storage
    )
    assert preview == (f"docs-assets/{revisions[2]}.tar.zst",)
    assert len(storage.objects) == 4
    deleted = await docs_assets.prune_remote_assets(
        assets_root, keep=revisions[:2], apply=True, storage=storage
    )
    assert deleted == preview
    assert lock.archive_key in storage.objects
    assert len(storage.objects) == 3


async def test_cache_keeps_current_and_two_previous_packages(assets_root: Path) -> None:
    storage = MemoryStorage()
    image = assets_root / "src/plugins/sample/docs/demos/sample.webp"
    locks = []
    for index in range(5):
        image.write_bytes(f"image-{index}".encode())
        locks.append(await docs_assets.publish_assets(assets_root, storage=storage))
    packages = list((assets_root / "output/docs-assets").glob("*.tar.zst"))
    assert len(packages) == 3
    assert {p.name for p in packages} == {
        f"{lock.archive_sha256}.tar.zst" for lock in locks[-3:]
    }
