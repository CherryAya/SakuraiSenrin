import asyncio
from io import BytesIO
from pathlib import Path

import arrow
from PIL import Image
import pytest
from sqlalchemy import text

from src.database.consts import WritePolicy
from src.lib.db.connectors import ColdPolicy
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.instances import (
    wordbank_log_db,
    wordbank_main_db,
    wordbank_message_ref_db,
    wordbank_message_route_db,
)
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.types import WordbankSearchRequest
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleContext


def _context(*, user_id: str = "10001", group_id: str = "20001") -> RuleContext:
    return RuleContext(
        group_id=group_id,
        user_id=user_id,
        message_type="group",
        sender_role="member",
    )


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def _build_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> WordbankService:
    from src.lib.db import connectors as connectors_module

    monkeypatch.setattr(connectors_module, "GLOBAL_DB_ROOT", tmp_path)
    service = WordbankService(WordbankRepository(), debounce_seconds=0.01)
    await service.initialize()
    return service


__all__ = [
    "BytesIO",
    "ColdPolicy",
    "Image",
    "Path",
    "RuleContext",
    "WordbankMediaService",
    "WordbankRepository",
    "WordbankSearchRequest",
    "WordbankService",
    "WritePolicy",
    "_build_service",
    "_context",
    "_png",
    "arrow",
    "asyncio",
    "combine_shapes",
    "get_current_time",
    "pytest",
    "shape_from_image",
    "shape_from_text",
    "text",
    "wordbank_log_db",
    "wordbank_main_db",
    "wordbank_message_ref_db",
    "wordbank_message_route_db",
]
