from types import SimpleNamespace, TracebackType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.plugins.water.database.repo import WaterRepository
from src.plugins.water.database.types import WaterSummaryRecord


class _DummySessionCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        _ = (exc_type, exc, tb)
        return False


def _fake_session(**kwargs: Any) -> _DummySessionCtx:
    _ = kwargs
    return _DummySessionCtx()


__all__ = [
    "Any",
    "AsyncMock",
    "SimpleNamespace",
    "TracebackType",
    "WaterRepository",
    "WaterSummaryRecord",
    "_fake_session",
    "pytest",
]
