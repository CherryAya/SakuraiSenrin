"""对象存储协议与共享值类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ObjectStorageError(RuntimeError):
    """对象存储操作失败。"""


class ObjectStorageConfigError(ObjectStorageError):
    """存储提供方配置缺失。"""


@dataclass(slots=True, frozen=True)
class StorageObject:
    provider: str
    bucket: str
    key: str
    uri: str
    public_url: str | None
    etag: str | None
    size: int


class ObjectStorageClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def available(self) -> bool: ...

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StorageObject: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def delete_object(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def list_objects(self, prefix: str) -> list[StorageObject]: ...

    async def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str: ...
