from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from src.lib.object_storage import GitHubObjectStorageClient, R2ObjectStorageClient


def _json_response(status_code: int, payload: Any) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


async def test_github_object_storage_put_get_delete() -> None:
    objects: dict[str, tuple[bytes, str]] = {}
    requests: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.split("/contents/", 1)[1]
        requests.append((request.method, key))
        if request.method == "GET":
            item = objects.get(key)
            if item is None:
                return _json_response(404, {"message": "Not Found"})
            data, sha = item
            return _json_response(
                200,
                {
                    "sha": sha,
                    "content": base64.b64encode(data).decode("ascii"),
                },
            )
        if request.method == "PUT":
            payload = json.loads(request.content.decode("utf-8"))
            data = base64.b64decode(payload["content"].encode("ascii"))
            sha = f"sha-{len(objects) + 1}"
            objects[key] = (data, sha)
            return _json_response(201, {"content": {"sha": sha}})
        if request.method == "DELETE":
            objects.pop(key, None)
            return _json_response(200, {"content": None})
        return _json_response(405, {"message": "method not allowed"})

    client = GitHubObjectStorageClient(
        token="token",
        repo="owner/repo",
        branch="main",
        transport=httpx.MockTransport(_handler),
    )

    stored = await client.put_bytes("wordbank/media/a.webp", b"image")
    loaded = await client.get_bytes("wordbank/media/a.webp")
    exists = await client.exists("wordbank/media/a.webp")
    url = await client.presign_get_url("wordbank/media/a.webp")
    await client.delete_object("wordbank/media/a.webp")

    assert stored.uri == "github://owner/repo/wordbank/media/a.webp"
    assert stored.public_url == (
        "https://raw.githubusercontent.com/owner/repo/main/wordbank/media/a.webp"
    )
    assert loaded == b"image"
    assert exists is True
    assert url == stored.public_url
    assert objects == {}
    assert ("PUT", "wordbank/media/a.webp") in requests
    assert ("DELETE", "wordbank/media/a.webp") in requests


async def test_github_object_storage_list_objects() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.split("/contents/", 1)[1]
        if request.method == "GET" and key == "wordbank/media":
            return _json_response(
                200,
                [
                    {
                        "type": "file",
                        "path": "wordbank/media/a.webp",
                        "sha": "sha-a",
                        "size": 12,
                        "download_url": "https://example.com/a.webp",
                    },
                    {
                        "type": "file",
                        "path": "wordbank/media/b.source",
                        "sha": "sha-b",
                        "size": 34,
                        "download_url": None,
                    },
                ],
            )
        return _json_response(404, {"message": "Not Found"})

    client = GitHubObjectStorageClient(
        token="token",
        repo="owner/repo",
        branch="main",
        transport=httpx.MockTransport(_handler),
    )

    objects = await client.list_objects("wordbank/media")

    assert [item.key for item in objects] == [
        "wordbank/media/a.webp",
        "wordbank/media/b.source",
    ]
    assert objects[0].public_url == "https://example.com/a.webp"
    assert (
        objects[1].public_url
        == "https://raw.githubusercontent.com/owner/repo/main/wordbank/media/b.source"
    )


async def test_r2_object_storage_list_objects() -> None:
    class _FakeR2Client:
        async def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["Bucket"] == "bucket"
            assert kwargs["Prefix"] == "wordbank/media"
            if kwargs.get("ContinuationToken") is None:
                return {
                    "Contents": [
                        {
                            "Key": "wordbank/media/a.webp",
                            "Size": 12,
                            "ETag": '"etag-a"',
                        },
                    ],
                    "IsTruncated": True,
                    "NextContinuationToken": "page-2",
                }
            return {
                "Contents": [
                    {"Key": "wordbank/media/b.webp", "Size": 34, "ETag": '"etag-b"'},
                ],
                "IsTruncated": False,
            }

    class _FakeContext:
        async def __aenter__(self) -> _FakeR2Client:
            return _FakeR2Client()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            return None

    class _TestR2ObjectStorageClient(R2ObjectStorageClient):
        def _client_context(self) -> _FakeContext:
            return _FakeContext()

    client = _TestR2ObjectStorageClient(
        access_key_id="key",
        secret_access_key="secret",
        bucket="bucket",
        endpoint="https://example.r2.cloudflarestorage.com",
        public_base_url="https://cdn.example.com",
    )

    objects = await client.list_objects("wordbank/media")

    assert [item.key for item in objects] == [
        "wordbank/media/a.webp",
        "wordbank/media/b.webp",
    ]
    assert objects[0].etag == "etag-a"
    assert objects[1].public_url == "https://cdn.example.com/wordbank/media/b.webp"


async def test_r2_object_storage_put_bytes_sets_content_length() -> None:
    class _FakeR2Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def put_object(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {"ETag": '"etag-a"'}

    fake_client = _FakeR2Client()

    class _FakeContext:
        async def __aenter__(self) -> _FakeR2Client:
            return fake_client

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            return None

    class _TestR2ObjectStorageClient(R2ObjectStorageClient):
        def _client_context(self) -> _FakeContext:
            return _FakeContext()

    client = _TestR2ObjectStorageClient(
        access_key_id="key",
        secret_access_key="secret",
        bucket="bucket",
        endpoint="https://example.r2.cloudflarestorage.com",
        public_base_url="https://cdn.example.com",
    )

    stored = await client.put_bytes(
        "wordbank/media/a.webp",
        b"image",
        content_type="image/webp",
    )

    assert stored.etag == "etag-a"
    assert fake_client.calls == [
        {
            "Bucket": "bucket",
            "Key": "wordbank/media/a.webp",
            "Body": b"image",
            "ContentLength": 5,
            "ContentType": "image/webp",
        }
    ]
