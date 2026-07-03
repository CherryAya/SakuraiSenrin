import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.message import Message, MessageSegment
import pytest

from src.plugins.wordbank.handlers import media_helpers
from src.plugins.wordbank.message_model import shape_to_summary_text
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


@pytest.mark.asyncio
async def test_build_message_shape_from_message_uses_hint_without_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_image_bytes = AsyncMock(return_value=b"unexpected")
    ingest_image_bytes = AsyncMock()
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            resolve_canonical_id_from_hints=lambda hints: (
                7 if "ABCDEF1234567890ABCDEF1234567890.PNG" in hints else None
            ),
            resolve_canonical_id=lambda *_args, **_kwargs: None,
            ingest_image_bytes=ingest_image_bytes,
        ),
    )
    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_with_retry",
        fetch_image_bytes,
    )
    message = Message(
        [
            MessageSegment.text("早安"),
            MessageSegment(
                "image",
                {
                    "url": "https://example.test/static/abcdef1234567890abcdef1234567890.png?download=1",
                    "file": "ABCDEF1234567890ABCDEF1234567890.PNG",
                },
            ),
        ]
    )

    shape = await media_helpers.build_message_shape_from_message(media_service, message)

    assert shape_to_summary_text(shape) == "早安 [图片:7]"
    assert fetch_image_bytes.await_count == 0
    assert ingest_image_bytes.await_count == 0


@pytest.mark.asyncio
async def test_build_message_shape_from_message_keeps_text_when_image_download_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_image_bytes = AsyncMock(return_value=None)
    ingest_image_bytes = AsyncMock()
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            resolve_canonical_id_from_hints=lambda _hints: None,
            resolve_canonical_id=lambda *_args, **_kwargs: None,
            ingest_image_bytes=ingest_image_bytes,
        ),
    )
    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_with_retry",
        fetch_image_bytes,
    )
    message = Message(
        [
            MessageSegment.text("只有文字也要保留"),
            MessageSegment("image", {"url": "https://example.test/missing.png"}),
        ]
    )

    shape = await media_helpers.build_message_shape_from_message(media_service, message)

    assert shape_to_summary_text(shape) == "只有文字也要保留"
    assert fetch_image_bytes.await_count == 1
    assert ingest_image_bytes.await_count == 0
