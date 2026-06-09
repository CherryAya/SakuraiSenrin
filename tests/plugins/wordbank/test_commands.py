from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers import commands
from src.plugins.wordbank.handlers.commands import (
    dispatch_wordbank_command,
    handle_delete,
    localize_command_error,
    parse_text_add_args,
    wordbank_help_text,
)
from src.plugins.wordbank.services.core import WordbankAddResult, WordbankService
from src.plugins.wordbank.services.rules import RuleError
from tests.plugins.water.helpers import build_group_message_event


def test_parse_text_add_args_keeps_fallback_message_and_i18n_key() -> None:
    with pytest.raises(RuleError) as exc_info:
        parse_text_add_args("晚安")

    assert str(exc_info.value) == "添加格式: wordbank add 触发词 => 响应词"
    assert exc_info.value.key == "wordbank.error.add_format"
    assert localize_command_error(exc_info.value, "zh-CN") == str(exc_info.value)


def test_wordbank_help_text_uses_requested_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_tr(locale: str, key: str, **params: object) -> str:
        calls.append((locale, key, params))
        return "localized-help"

    monkeypatch.setattr(commands, "tr", fake_tr)

    assert wordbank_help_text("lzh") == "localized-help"
    assert calls == [("lzh", "wordbank.help", {})]


async def test_dispatch_wordbank_command_formats_search_with_locale() -> None:
    event = build_group_message_event("#wordbank search 晚安")
    service = cast(
        WordbankService,
        SimpleNamespace(
            search=AsyncMock(
                return_value=[
                    WordbankSearchItem(
                        entry_id=12,
                        trigger_text="晚安",
                        trigger_mode="contains",
                        response_text="做个好梦",
                        scope="current_group",
                        probability=1.0,
                        weight=3,
                        created_by="10001",
                    )
                ]
            )
        ),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="search 晚安",
        locale="zh-CN",
    )

    assert message == "词库搜索结果:\n#12 [contains/current_group] 晚安 => 做个好梦"


async def test_handle_delete_localizes_result() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(delete_entry=AsyncMock(return_value=True)),
    )

    assert (
        await handle_delete(service, entry_id_text="12", locale="zh-CN")
        == "词条 #12 已删除。"
    )
    assert (
        await handle_delete(service, entry_id_text="abc", locale="zh-CN")
        == "词条 ID 必须是数字。"
    )


async def test_dispatch_add_uses_i18n_add_formatter() -> None:
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦")
    service = cast(
        WordbankService,
        SimpleNamespace(
            add_text_entry=AsyncMock(
                return_value=WordbankAddResult(
                    entry_id=12,
                    trigger_text="晚安",
                    response_text="做个好梦",
                    trigger_mode="contains",
                    scope="current_group",
                    probability=1.0,
                    weight=3,
                )
            )
        ),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="add 晚安 => 做个好梦",
        locale="zh-CN",
    )

    assert "词条已加入词库" in message
    assert "ID: 12" in message
    assert "触发: 晚安" in message
