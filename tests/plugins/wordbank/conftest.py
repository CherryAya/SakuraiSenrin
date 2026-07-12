from unittest.mock import AsyncMock

import pytest


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
