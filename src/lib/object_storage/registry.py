"""Object storage provider registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import ObjectStorageClient, ObjectStorageConfigError


@dataclass(slots=True)
class ObjectStorageRegistry:
    _clients: dict[str, ObjectStorageClient] = field(default_factory=dict)

    def register(self, client: ObjectStorageClient) -> None:
        self._clients[client.provider] = client

    def get(self, provider: str | None) -> ObjectStorageClient | None:
        if not provider:
            return None
        return self._clients.get(provider)

    def require(self, provider: str) -> ObjectStorageClient:
        client = self.get(provider)
        if client is None:
            raise ObjectStorageConfigError(
                f"Object storage provider not found: {provider}"
            )
        if not client.available:
            raise ObjectStorageConfigError(
                f"Object storage provider is unavailable: {provider}"
            )
        return client
