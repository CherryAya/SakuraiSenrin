from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.lib.reply_router import reply_context_repo


@pytest.fixture(autouse=True)
def _disable_runtime_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.hooks.processor._runtime_sync",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "src.hooks.processor._runtime_check",
        AsyncMock(return_value=None),
    )


@pytest.fixture(autouse=True)
async def _clear_reply_contexts() -> None:
    """回复路由测试依赖 reply_context，逐例清理避免相互污染。"""
    await reply_context_repo.clear_all_contexts()


async def seed_reply_context(
    *,
    message_id: int,
    context_kind: str,
    payload: dict[str, Any],
    sender_bot_id: str = "99999",
) -> None:
    """写入一条 reply_context，供回复路由用例命中。"""
    await reply_context_repo.upsert_context(
        context_kind=context_kind,
        message_id=str(message_id),
        message_hash=f"test-hash-{message_id}",
        sender_bot_id=sender_bot_id,
        origin_message_type="group",
        origin_target_id="20001",
        source_kind="test",
        payload=payload,
    )
