"""GitHub Contents API object storage client."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from .types import ObjectStorageConfigError, ObjectStorageError, StorageObject


@dataclass(slots=True, frozen=True)
class GitHubObjectStorageClient:
    token: str | None
    repo: str | None
    branch: str | None
    public_base_url: str | None = None
    transport: httpx.AsyncBaseTransport | None = None

    @property
    def provider(self) -> str:
        return "github"

    @property
    def available(self) -> bool:
        return bool(self.token and self.repo and self.branch)

    @property
    def bucket(self) -> str:
        return self.repo or ""

    def _require_config(self) -> tuple[str, str, str]:
        if not self.token or not self.repo or not self.branch:
            raise ObjectStorageConfigError("GitHub object storage is not configured")
        return self.token, self.repo, self.branch

    def _client(self) -> httpx.AsyncClient:
        token, _, _ = self._require_config()
        return httpx.AsyncClient(
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
            transport=self.transport,
        )

    def _contents_url(self, key: str) -> str:
        _, repo, _ = self._require_config()
        return f"https://api.github.com/repos/{repo}/contents/{key.lstrip('/')}"

    def _raw_url(self, key: str) -> str:
        _, repo, branch = self._require_config()
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/{key.lstrip('/')}"
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{key.lstrip('/')}"

    def _uri(self, key: str) -> str:
        _, repo, _ = self._require_config()
        return f"github://{repo}/{key.lstrip('/')}"

    async def _get_metadata(self, key: str) -> dict[str, Any] | None:
        _, _, branch = self._require_config()
        async with self._client() as client:
            response = await client.get(
                self._contents_url(key),
                params={"ref": branch},
            )
        if response.status_code == 404:
            return None
        if response.is_error:
            raise ObjectStorageError(
                f"GitHub contents request failed: {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ObjectStorageError("GitHub contents response is not an object")
        return payload

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StorageObject:
        _ = content_type
        _, _, branch = self._require_config()
        normalized_key = key.lstrip("/")
        metadata = await self._get_metadata(normalized_key)
        payload: dict[str, Any] = {
            "message": f"store object {normalized_key}",
            "content": base64.b64encode(data).decode("ascii"),
            "branch": branch,
        }
        if metadata and isinstance(metadata.get("sha"), str):
            payload["sha"] = metadata["sha"]

        async with self._client() as client:
            response = await client.put(
                self._contents_url(normalized_key),
                json=payload,
            )
        if response.status_code not in {200, 201}:
            raise ObjectStorageError(
                f"GitHub contents upload failed: {response.status_code}"
            )
        body = response.json()
        content = body.get("content") if isinstance(body, dict) else None
        sha = content.get("sha") if isinstance(content, dict) else None
        return StorageObject(
            provider=self.provider,
            bucket=self.bucket,
            key=normalized_key,
            uri=self._uri(normalized_key),
            public_url=self._raw_url(normalized_key),
            etag=str(sha) if sha else None,
            size=len(data),
        )

    async def get_bytes(self, key: str) -> bytes:
        metadata = await self._get_metadata(key)
        if metadata is None:
            raise ObjectStorageError(f"GitHub object not found: {key}")
        content = metadata.get("content")
        if not isinstance(content, str):
            raise ObjectStorageError(f"GitHub object has no inline content: {key}")
        return base64.b64decode(content.encode("ascii"))

    async def delete_object(self, key: str) -> None:
        _, _, branch = self._require_config()
        normalized_key = key.lstrip("/")
        metadata = await self._get_metadata(normalized_key)
        if metadata is None:
            return
        sha = metadata.get("sha")
        if not isinstance(sha, str):
            raise ObjectStorageError(f"GitHub object has no sha: {normalized_key}")
        async with self._client() as client:
            response = await client.request(
                "DELETE",
                self._contents_url(normalized_key),
                json={
                    "message": f"delete object {normalized_key}",
                    "sha": sha,
                    "branch": branch,
                },
            )
        if response.status_code != 200:
            raise ObjectStorageError(
                f"GitHub contents delete failed: {response.status_code}"
            )

    async def exists(self, key: str) -> bool:
        return await self._get_metadata(key) is not None

    async def list_objects(self, prefix: str) -> list[StorageObject]:
        _, _, branch = self._require_config()
        normalized_prefix = prefix.strip("/")
        async with self._client() as client:
            response = await client.get(
                self._contents_url(normalized_prefix),
                params={"ref": branch},
            )
        if response.status_code == 404:
            return []
        if response.is_error:
            raise ObjectStorageError(
                f"GitHub contents list failed: {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, list):
            raise ObjectStorageError("GitHub contents list response is not an array")

        objects: list[StorageObject] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "file":
                continue
            path = item.get("path")
            if not isinstance(path, str) or not path.startswith(
                f"{normalized_prefix}/"
            ):
                continue
            sha = item.get("sha")
            size = item.get("size")
            download_url = item.get("download_url")
            objects.append(
                StorageObject(
                    provider=self.provider,
                    bucket=self.bucket,
                    key=path,
                    uri=self._uri(path),
                    public_url=(
                        str(download_url)
                        if isinstance(download_url, str) and download_url
                        else self._raw_url(path)
                    ),
                    etag=str(sha) if isinstance(sha, str) and sha else None,
                    size=int(size) if isinstance(size, int) else 0,
                )
            )
        return objects

    async def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        _ = expires_in
        return self._raw_url(key)
