"""预构建帮助图片的版本清单、R2 分发和本地同步。

导入本模块不读取配置、不加载插件；本地校验通过时不访问对象存储。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
from typing import TYPE_CHECKING

import zstandard

from src.lib.types import JsonValue
from src.logger import logger

if TYPE_CHECKING:
    from src.lib.object_storage.types import ObjectStorageClient

ROOT = Path(__file__).resolve().parents[2]
LOCK_FILENAME = "docs-assets.lock.json"
OBJECT_PREFIX = "docs-assets/"
SUPPORT_QR_PATH = "src/lib/assets/help-support-qr-double.png"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
TRANSFER_TIMEOUT = 120
TRANSFER_ATTEMPTS = 3
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class DocsAssetsError(RuntimeError):
    """资源缺失、清单不匹配或同步失败。"""


@dataclass(frozen=True, slots=True)
class AssetFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class AssetLock:
    version: int
    input_hash: str
    archive_key: str
    archive_sha256: str
    archive_size: int
    files: tuple[AssetFile, ...]


@dataclass(frozen=True, slots=True)
class AssetStatus:
    revision: str
    invalid_files: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.invalid_files


def _is_generated_path(name: str) -> bool:
    path = PurePosixPath(name)
    parts = path.parts
    if (
        path.is_absolute()
        or str(path) != name
        or "\\" in name
        or any(part in {".", ".."} for part in parts)
    ):
        return False
    if name == SUPPORT_QR_PATH:
        return True
    return (
        len(parts) >= 6
        and parts[:2] in {("src", "plugins"), ("src", "hooks")}
        and "docs" in parts[2:-2]
        and parts[-2] == "demos"
        and (path.suffix in {".png", ".webp"} or path.name == "manifest.json")
    )


def _asset_path(root: Path, name: str) -> Path:
    if not _is_generated_path(name):
        raise DocsAssetsError(f"invalid generated asset path: {name}")
    path = root / name
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise DocsAssetsError(f"asset path contains a symlink: {name}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise DocsAssetsError(f"asset path escapes repository: {name}")
    return path


def generated_paths(root: Path) -> tuple[Path, ...]:
    paths = [root / SUPPORT_QR_PATH]
    for owner in ("plugins", "hooks"):
        base = root / "src" / owner
        if base.is_dir():
            paths.extend(
                path
                for path in base.rglob("demos/*")
                if _is_generated_path(path.relative_to(root).as_posix())
            )
    return tuple(sorted((p for p in paths if p.is_file()), key=str))


def source_paths(root: Path) -> tuple[Path, ...]:
    """构建输入以及提交中的目标映射；不含生成图片和锁定清单。"""
    paths = [
        root / "scripts/build_docs.py",
        root / "src/lib/demo_theme.py",
        root / "src/lib/consts.py",
        root / "pyproject.toml",
        root / "uv.lock",
    ]
    for relative in (
        "src/lib/plugin_docs",
        "src/lib/i18n",
        "src/locales",
        "src/plugins",
        "src/hooks",
    ):
        base = root / relative
        if base.is_dir():
            paths.extend(base.rglob("*.py"))
            paths.extend(base.rglob("README.MD"))
            paths.extend(base.rglob("demos/manifest.json"))
    assets = root / "src/lib/assets"
    if assets.is_dir():
        paths.extend(
            p for p in assets.iterdir() if p.name != Path(SUPPORT_QR_PATH).name
        )
    fonts = root / "data/font"
    if fonts.is_dir():
        paths.extend(
            p for p in fonts.iterdir() if p.suffix.lower() in {".ttf", ".otf", ".ttc"}
        )
    return tuple(sorted({p for p in paths if p.is_file()}, key=str))


def input_fingerprint(root: Path) -> str:
    digest = sha256()
    for path in source_paths(root):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DocsAssetsError("invalid SHA-256 in asset lock")
    return value


def _size(value: object) -> int:
    if type(value) is not int or value < 0 or value > MAX_UNPACKED_BYTES:
        raise DocsAssetsError("invalid file size in asset lock")
    return value


def load_lock(root: Path) -> AssetLock:
    try:
        payload: JsonValue = json.loads(
            (root / LOCK_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise DocsAssetsError(
            f"cannot load {LOCK_FILENAME}; publish docs assets first"
        ) from exc
    if not isinstance(payload, dict):
        raise DocsAssetsError("invalid asset lock object")
    version = payload.get("version")
    if type(version) is not int or version != 1:
        raise DocsAssetsError("unsupported asset lock version")
    archive_hash = _digest(payload.get("archive_sha256"))
    key = payload.get("archive_key")
    if not isinstance(key, str) or key != f"{OBJECT_PREFIX}{archive_hash}.tar.zst":
        raise DocsAssetsError("asset archive key does not match its SHA-256")
    items = payload.get("files")
    if not isinstance(items, list) or not items or len(items) > 5000:
        raise DocsAssetsError("invalid asset file list")
    files: list[AssetFile] = []
    names: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise DocsAssetsError("invalid asset file entry")
        name = item.get("path")
        if not isinstance(name, str):
            raise DocsAssetsError("invalid asset file path")
        _asset_path(root, name)
        if name in names:
            raise DocsAssetsError(f"duplicate asset path: {name}")
        names.add(name)
        files.append(
            AssetFile(name, _digest(item.get("sha256")), _size(item.get("size")))
        )
    archive_size = _size(payload.get("archive_size"))
    if (
        archive_size > MAX_ARCHIVE_BYTES
        or sum(f.size for f in files) > MAX_UNPACKED_BYTES
    ):
        raise DocsAssetsError("asset package is too large")
    return AssetLock(
        1,
        _digest(payload.get("input_hash")),
        key,
        archive_hash,
        archive_size,
        tuple(files),
    )


def check_assets(root: Path, lock: AssetLock | None = None) -> AssetStatus:
    lock = lock or load_lock(root)
    if input_fingerprint(root) != lock.input_hash:
        raise DocsAssetsError(
            "docs build inputs changed; build and publish assets for this checkout"
        )
    invalid: list[str] = []
    for entry in lock.files:
        path = _asset_path(root, entry.path)
        try:
            matches = (
                path.stat().st_size == entry.size
                and sha256(path.read_bytes()).hexdigest() == entry.sha256
            )
        except OSError:
            matches = False
        if not matches:
            invalid.append(entry.path)
    return AssetStatus(lock.archive_sha256, tuple(invalid))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_archive(root: Path) -> tuple[AssetLock, bytes]:
    fingerprint = input_fingerprint(root)
    files: list[AssetFile] = []
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in generated_paths(root):
            name = path.relative_to(root).as_posix()
            _asset_path(root, name)
            data = path.read_bytes()
            files.append(AssetFile(name, sha256(data).hexdigest(), len(data)))
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, BytesIO(data))
    if not files or sum(f.size for f in files) > MAX_UNPACKED_BYTES:
        raise DocsAssetsError("missing or oversized generated assets")
    names = {entry.path for entry in files}
    if SUPPORT_QR_PATH not in names:
        raise DocsAssetsError("missing generated support QR asset")
    for entry in files:
        if not entry.path.endswith("/manifest.json"):
            continue
        payload: JsonValue = json.loads((root / entry.path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DocsAssetsError(f"invalid build manifest: {entry.path}")
        targets = payload.get("targets")
        if not isinstance(targets, dict):
            raise DocsAssetsError(f"invalid build manifest targets: {entry.path}")
        for variants in targets.values():
            if not isinstance(variants, dict):
                raise DocsAssetsError(f"invalid build manifest targets: {entry.path}")
            for filename in variants.values():
                if not isinstance(filename, str):
                    raise DocsAssetsError(
                        f"invalid build manifest filename: {entry.path}"
                    )
                name = (PurePosixPath(entry.path).parent / filename).as_posix()
                if name not in names:
                    raise DocsAssetsError(
                        f"missing generated asset declared by manifest: {name}"
                    )
    data = zstandard.ZstdCompressor(level=10).compress(buffer.getvalue())
    if len(data) > MAX_ARCHIVE_BYTES:
        raise DocsAssetsError("asset archive is too large")
    archive_hash = sha256(data).hexdigest()
    lock = AssetLock(
        1,
        fingerprint,
        f"{OBJECT_PREFIX}{archive_hash}.tar.zst",
        archive_hash,
        len(data),
        tuple(files),
    )
    if fingerprint != input_fingerprint(root):
        raise DocsAssetsError("build inputs changed while packing assets")
    return lock, data


def _validate_archive(root: Path, lock: AssetLock, data: bytes) -> dict[str, bytes]:
    if (
        len(data) != lock.archive_size
        or sha256(data).hexdigest() != lock.archive_sha256
    ):
        raise DocsAssetsError("asset archive size or SHA-256 mismatch")
    expected = {f.path: f for f in lock.files}
    extracted: dict[str, bytes] = {}
    try:
        with zstandard.ZstdDecompressor().stream_reader(BytesIO(data)) as reader:
            unpacked = reader.read(MAX_UNPACKED_BYTES + 1)
        if len(unpacked) > MAX_UNPACKED_BYTES:
            raise DocsAssetsError("decompressed archive is too large")
        with tarfile.open(fileobj=BytesIO(unpacked), mode="r:") as archive:
            for member in archive:
                _asset_path(root, member.name)
                entry = expected.get(member.name)
                if (
                    not member.isfile()
                    or entry is None
                    or member.name in extracted
                    or member.size != entry.size
                ):
                    raise DocsAssetsError(f"unexpected archive member: {member.name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise DocsAssetsError(f"cannot read archive member: {member.name}")
                content = stream.read(entry.size + 1)
                if (
                    len(content) != entry.size
                    or sha256(content).hexdigest() != entry.sha256
                ):
                    raise DocsAssetsError(f"asset checksum mismatch: {member.name}")
                extracted[member.name] = content
    except (tarfile.TarError, zstandard.ZstdError, OSError) as exc:
        raise DocsAssetsError("invalid asset archive") from exc
    if extracted.keys() != expected.keys():
        raise DocsAssetsError("archive does not contain all locked assets")
    return extracted


async def _transfer[T](operation: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(TRANSFER_ATTEMPTS):
        try:
            async with asyncio.timeout(TRANSFER_TIMEOUT):
                return await operation()
        except Exception as exc:
            if attempt + 1 == TRANSFER_ATTEMPTS:
                raise DocsAssetsError(
                    f"asset transfer failed ({type(exc).__name__})"
                ) from exc
            await asyncio.sleep(0.25 * (attempt + 1))
    raise AssertionError("unreachable")


def _storage() -> ObjectStorageClient:
    from src.lib.object_storage.factory import object_storage_registry

    return object_storage_registry.require("r2")


def _cache_root(root: Path) -> Path:
    return root / "output/docs-assets"


@asynccontextmanager
async def _process_lock(root: Path) -> AsyncGenerator[None]:
    directory = _cache_root(root)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".sync.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()

            def acquire() -> None:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def release() -> None:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release() -> None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

        try:
            async with asyncio.timeout(TRANSFER_TIMEOUT * TRANSFER_ATTEMPTS + 10):
                while True:
                    try:
                        acquire()
                        break
                    except OSError:
                        await asyncio.sleep(0.1)
        except TimeoutError as exc:
            raise DocsAssetsError(
                "timed out waiting for asset synchronization lock"
            ) from exc
        try:
            yield
        finally:
            release()


def _trim_cache(root: Path, current: str) -> None:
    packages = sorted(
        _cache_root(root).glob("*.tar.zst"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    others = [p for p in packages if p.name != f"{current}.tar.zst"]
    for path in others[2:]:
        path.unlink()


def _record_install(root: Path, lock: AssetLock) -> None:
    """仅清理上一套已管理的旧文件，不遍历删除工作区其他资源。"""
    state = _cache_root(root) / "installed.json"
    current = {entry.path for entry in lock.files}
    try:
        previous: JsonValue = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    if isinstance(previous, list):
        for name in previous:
            if isinstance(name, str) and name not in current:
                _asset_path(root, name).unlink(missing_ok=True)
    _atomic_write(state, (json.dumps(sorted(current), indent=2) + "\n").encode())


async def publish_assets(
    root: Path = ROOT, *, storage: ObjectStorageClient | None = None
) -> AssetLock:
    async with _process_lock(root):
        lock, data = build_archive(root)
        _validate_archive(root, lock, data)
        client = storage or _storage()
        if not await _transfer(lambda: client.exists(lock.archive_key)):
            await _transfer(
                lambda: client.put_bytes(
                    lock.archive_key, data, content_type="application/zstd"
                )
            )
        remote = await _transfer(lambda: client.get_bytes(lock.archive_key))
        _validate_archive(root, lock, remote)
        if input_fingerprint(root) != lock.input_hash:
            raise DocsAssetsError(
                "build inputs changed during publication; lock was not updated"
            )
        _atomic_write(_cache_root(root) / f"{lock.archive_sha256}.tar.zst", data)
        payload = json.dumps(asdict(lock), ensure_ascii=False, indent=2) + "\n"
        _atomic_write(root / LOCK_FILENAME, payload.encode())
        _record_install(root, lock)
        _trim_cache(root, lock.archive_sha256)
        logger.info(
            f"[DocsAssets] published revision={lock.archive_sha256[:12]} "
            f"files={len(lock.files)} bytes={len(data)}"
        )
        return lock


async def pull_assets(
    root: Path = ROOT,
    *,
    force: bool = False,
    storage: ObjectStorageClient | None = None,
) -> AssetStatus:
    async with _process_lock(root):
        lock = load_lock(root)
        status = check_assets(root, lock)
        if status.ready and not force:
            _record_install(root, lock)
            logger.info(
                f"[DocsAssets] local assets verified revision={status.revision[:12]}"
            )
            return status
        cache = _cache_root(root) / f"{lock.archive_sha256}.tar.zst"
        data: bytes | None = None
        if cache.is_file() and not force:
            candidate = cache.read_bytes()
            if (
                len(candidate) == lock.archive_size
                and sha256(candidate).hexdigest() == lock.archive_sha256
            ):
                data = candidate
        if data is None:
            client = storage or _storage()
            data = await _transfer(lambda: client.get_bytes(lock.archive_key))
        files = _validate_archive(root, lock, data)
        if load_lock(root) != lock or input_fingerprint(root) != lock.input_hash:
            raise DocsAssetsError(
                "checkout changed during synchronization; retry after updating code"
            )
        # 所有成员校验完成后才写文件；同版修复与启动前更新共用此路径。
        for name, content in files.items():
            target = _asset_path(root, name)
            if not target.is_file() or target.read_bytes() != content:
                _atomic_write(target, content)
        _atomic_write(cache, data)
        status = check_assets(root, lock)
        if not status.ready:
            raise DocsAssetsError(f"assets remain incomplete: {status.invalid_files}")
        _record_install(root, lock)
        _trim_cache(root, lock.archive_sha256)
        logger.info(
            f"[DocsAssets] synchronized revision={status.revision[:12]} "
            f"files={len(files)}"
        )
        return status


def _rollback_revisions(root: Path, current: str) -> set[str]:
    history = subprocess.run(
        ["git", "-C", str(root), "log", "-20", "--format=%H", "--", LOCK_FILENAME],
        capture_output=True,
        text=True,
        check=False,
    )
    rollbacks: set[str] = set()
    if history.returncode == 0:
        for commit in history.stdout.splitlines():
            previous = subprocess.run(
                ["git", "-C", str(root), "show", f"{commit}:{LOCK_FILENAME}"],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                payload: JsonValue = json.loads(previous.stdout)
                revision = _digest(
                    payload.get("archive_sha256") if isinstance(payload, dict) else None
                )
            except (ValueError, DocsAssetsError):
                continue
            if revision != current:
                rollbacks.add(revision)
            if len(rollbacks) == 2:
                break
    return rollbacks


async def prune_remote_assets(
    root: Path = ROOT,
    *,
    keep: Iterable[str] = (),
    apply: bool = False,
    storage: ObjectStorageClient | None = None,
) -> tuple[str, ...]:
    """预览或显式删除旧包；调用方须固定各部署仍在使用的版本。"""
    async with _process_lock(root):
        current = load_lock(root).archive_sha256
        protected = {current, *(_digest(value) for value in keep)}
        protected.update(await asyncio.to_thread(_rollback_revisions, root, current))
        client = storage or _storage()
        objects = await _transfer(lambda: client.list_objects(OBJECT_PREFIX))
        keys = sorted(
            obj.key
            for obj in objects
            if re.fullmatch(r"docs-assets/[0-9a-f]{64}\.tar\.zst", obj.key)
        )
        # 内容寻址的 key 不表示发布时间；仅在显式保留清单外清理。
        candidates = tuple(
            key
            for key in keys
            if PurePosixPath(key).name.removesuffix(".tar.zst") not in protected
        )
        if apply and len(protected) < 3:
            raise DocsAssetsError(
                "remote pruning requires --keep for at least two rollback revisions"
            )
        if apply:
            for key in candidates:
                await _transfer(lambda key=key: client.delete_object(key))
        return candidates


def init_docs_assets(root: Path = ROOT) -> None:
    """供同步入口 bot.py 使用，在插件加载前完成资源检查。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(pull_assets(root))
    else:
        # ASGI/重载器可能在已运行的事件循环中导入入口文件。
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(asyncio.run, pull_assets(root)).result()
