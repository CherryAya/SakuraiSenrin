import asyncio
from types import SimpleNamespace
from typing import cast

from nonebot.adapters.onebot.v11.message import Message, MessageSegment
import pytest

from src.plugins.wordbank.handlers import media_helpers
from src.plugins.wordbank.services.media import WordbankMediaService


@pytest.mark.asyncio
async def test_fetch_image_bytes_from_message_runs_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 0
    max_running = 0

    async def _fake_fetch(url: str, **_: object) -> bytes | None:
        nonlocal current, max_running
        current += 1
        max_running = max(max_running, current)
        await asyncio.sleep(0.01)
        current -= 1
        return url.encode("utf-8")

    monkeypatch.setattr(media_helpers, "fetch_image_bytes_with_retry", _fake_fetch)
    message = Message(
        [
            MessageSegment("image", {"url": "https://example.test/1.png"}),
            MessageSegment("image", {"url": "https://example.test/2.png"}),
            MessageSegment("image", {"url": "https://example.test/3.png"}),
        ]
    )

    items = await media_helpers.fetch_image_bytes_from_message(message, limit=3)

    assert items == (
        b"https://example.test/1.png",
        b"https://example.test/2.png",
        b"https://example.test/3.png",
    )
    assert max_running > 1


@pytest.mark.asyncio
async def test_ingest_image_bytes_items_runs_concurrently_and_preserves_order() -> None:
    current = 0
    max_running = 0

    async def _fake_ingest(data: bytes) -> SimpleNamespace:
        nonlocal current, max_running
        current += 1
        max_running = max(max_running, current)
        await asyncio.sleep(0.01)
        current -= 1
        return SimpleNamespace(canonical_id=int(data.decode("utf-8")))

    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=_fake_ingest),
    )

    canonical_ids = await media_helpers.ingest_image_bytes_items(
        media_service,
        (b"3", b"7", b"11"),
    )

    assert canonical_ids == (3, 7, 11)
    assert max_running > 1
