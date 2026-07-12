"""Cloudflare R2 object storage client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from typing import Any

from .types import ObjectStorageConfigError, StorageObject


@dataclass(slots=True, frozen=True)
class R2ObjectStorageClient:
    access_key_id: str | None
    secret_access_key: str | None
    bucket: str | None
    account_id: str | None = None
    endpoint: str | None = None
    public_base_url: str | None = None

    @property
    def provider(self) -> str:
        return "r2"

    @property
    def available(self) -> bool:
        return bool(
            self.access_key_id
            and self.secret_access_key
            and self.bucket
            and self.endpoint_url
        )

    @property
    def endpoint_url(self) -> str | None:
        if self.endpoint:
            return self.endpoint.rstrip("/")
        if self.account_id:
            return f"https://{self.account_id}.r2.cloudflarestorage.com"
        return None

    def _public_url(self, key: str) -> str | None:
        if not self.public_base_url:
            return None
        return f"{self.public_base_url.rstrip('/')}/{key.lstrip('/')}"

    def _uri(self, key: str) -> str:
        return f"r2://{self.bucket}/{key.lstrip('/')}"

    def _require_config(self) -> tuple[str, str, str, str]:
        endpoint = self.endpoint_url
        if (
            not self.access_key_id
            or not self.secret_access_key
            or not self.bucket
            or not endpoint
        ):
            raise ObjectStorageConfigError("R2 object storage is not configured")
        return self.access_key_id, self.secret_access_key, self.bucket, endpoint

    def _session(self) -> Any:
        try:
            import aioboto3
        except ImportError as exc:
            raise ObjectStorageConfigError(
                "aioboto3 is required for R2 storage"
            ) from exc
        return aioboto3.Session()

    def _client_context(self) -> Any:
        access_key_id, secret_access_key, _, endpoint = self._require_config()
        return self._session().client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def _sync_session(self) -> Any:
        try:
            import boto3
        except ImportError as exc:
            raise ObjectStorageConfigError("boto3 is required for R2 storage") from exc
        return boto3.Session()

    def _sync_client_context(self) -> Any:
        access_key_id, secret_access_key, _, endpoint = self._require_config()
        return self._sync_session().client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def _put_bytes_sync(
        self,
        bucket: str,
        normalized_key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        extra_args: dict[str, object] = {
            "ContentLength": len(data),
        }
        if content_type:
            extra_args["ContentType"] = content_type
        client = self._sync_client_context()
        try:
            response = client.put_object(
                Bucket=bucket,
                Key=normalized_key,
                Body=data,
                **extra_args,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        return dict(response)

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> StorageObject:
        _, _, bucket, _ = self._require_config()
        normalized_key = key.lstrip("/")
        response = await asyncio.to_thread(
            self._put_bytes_sync,
            bucket,
            normalized_key,
            data,
            content_type=content_type,
        )
        etag = str(response.get("ETag", "")).strip('"') or None
        return StorageObject(
            provider=self.provider,
            bucket=bucket,
            key=normalized_key,
            uri=self._uri(normalized_key),
            public_url=self._public_url(normalized_key),
            etag=etag,
            size=len(data),
        )

    async def get_bytes(self, key: str) -> bytes:
        _, _, bucket, _ = self._require_config()
        normalized_key = key.lstrip("/")
        async with self._client_context() as client:
            response = await client.get_object(Bucket=bucket, Key=normalized_key)
            body = response["Body"]
            try:
                return await body.read()
            finally:
                close = getattr(body, "close", None)
                if close is not None:
                    close()

    async def delete_object(self, key: str) -> None:
        _, _, bucket, _ = self._require_config()
        async with self._client_context() as client:
            await client.delete_object(Bucket=bucket, Key=key.lstrip("/"))

    async def exists(self, key: str) -> bool:
        _, _, bucket, _ = self._require_config()
        async with self._client_context() as client:
            try:
                await client.head_object(Bucket=bucket, Key=key.lstrip("/"))
            except Exception:
                return False
        return True

    async def list_objects(self, prefix: str) -> list[StorageObject]:
        _, _, bucket, _ = self._require_config()
        normalized_prefix = prefix.strip("/")
        continuation_token: str | None = None
        objects: list[StorageObject] = []
        async with self._client_context() as client:
            while True:
                params: dict[str, Any] = {
                    "Bucket": bucket,
                    "Prefix": normalized_prefix,
                    "MaxKeys": 1000,
                }
                if continuation_token:
                    params["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**params)
                for item in response.get("Contents", []) or []:
                    key = item.get("Key")
                    if not isinstance(key, str) or not key:
                        continue
                    size = item.get("Size")
                    etag = item.get("ETag")
                    objects.append(
                        StorageObject(
                            provider=self.provider,
                            bucket=bucket,
                            key=key,
                            uri=self._uri(key),
                            public_url=self._public_url(key),
                            etag=(
                                str(etag).strip('"')
                                if isinstance(etag, str) and etag
                                else None
                            ),
                            size=int(size) if isinstance(size, int) else 0,
                        )
                    )
                if not response.get("IsTruncated"):
                    break
                next_token = response.get("NextContinuationToken")
                continuation_token = (
                    str(next_token)
                    if isinstance(next_token, str) and next_token
                    else None
                )
                if continuation_token is None:
                    break
        return objects

    async def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        _, _, bucket, _ = self._require_config()
        async with self._client_context() as client:
            result = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key.lstrip("/")},
                ExpiresIn=expires_in,
            )
            if inspect.isawaitable(result):
                return str(await result)
            return str(result)
