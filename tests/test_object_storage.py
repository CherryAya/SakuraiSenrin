from __future__ import annotations

import base64
import json

import httpx

from src.lib.object_storage import GitHubObjectStorageClient


def _json_response(status_code: int, payload: dict) -> httpx.Response:
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
