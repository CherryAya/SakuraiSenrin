from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers import commands
from src.plugins.wordbank.handlers.commands import (
    build_mutation_actor,
    dispatch_wordbank_command,
    handle_delete,
    handle_study_shortcut,
    localize_command_error,
    parse_search_args,
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
    search_mock = AsyncMock(
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
    service = cast(
        WordbankService,
        SimpleNamespace(search=search_mock),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="search 晚安",
        locale="zh-CN",
    )

    assert message == (
        "词库搜索结果 (第 1 页):\n#12 [contains/current_group] 晚安 => 做个好梦"
    )
    search_mock.assert_awaited_once_with("晚安", limit=11, offset=0)


async def test_dispatch_wordbank_search_supports_page_limit_and_more_hint() -> None:
    event = build_group_message_event("#wordbank search 晚安")
    items = [
        WordbankSearchItem(
            entry_id=index,
            trigger_text=f"晚安{index}",
            trigger_mode="contains",
            response_text=f"做个好梦{index}",
            scope="current_group",
            probability=1.0,
            weight=3,
            created_by="10001",
        )
        for index in range(11, 15)
    ]
    search_mock = AsyncMock(return_value=items)
    service = cast(
        WordbankService,
        SimpleNamespace(search=search_mock),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="search 晚安 --page 2 --limit 3",
        locale="zh-CN",
    )

    assert message == (
        "词库搜索结果 (第 2 页):\n"
        "#11 [contains/current_group] 晚安11 => 做个好梦11\n"
        "#12 [contains/current_group] 晚安12 => 做个好梦12\n"
        "#13 [contains/current_group] 晚安13 => 做个好梦13\n"
        "还有更多结果，可使用 --page 3 --limit 3 查看下一页。"
    )
    search_mock.assert_awaited_once_with("晚安", limit=4, offset=3)


def test_parse_search_args_rejects_invalid_pagination() -> None:
    parsed = parse_search_args("晚安 --page 2 --limit 5")

    assert parsed.keyword == "晚安"
    assert parsed.page == 2
    assert parsed.limit == 5

    with pytest.raises(RuleError) as page_error:
        parse_search_args("晚安 --page 0")
    assert page_error.value.key == "wordbank.error.search_page_invalid"

    with pytest.raises(RuleError) as limit_error:
        parse_search_args("晚安 --limit 21")
    assert limit_error.value.key == "wordbank.error.search_limit_invalid"
    assert limit_error.value.params == {"max_limit": 20}


async def test_handle_delete_localizes_result() -> None:
    delete_mock = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(delete_entry=delete_mock),
    )
    event = build_group_message_event("#wordbank delete 12", role="admin")

    assert (
        await handle_delete(
            service,
            event=event,
            entry_id_text="12",
            locale="zh-CN",
        )
        == "词条 #12 已删除。"
    )
    delete_mock.assert_awaited_once_with(
        12,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    assert (
        await handle_delete(
            service,
            event=event,
            entry_id_text="abc",
            locale="zh-CN",
        )
        == "词条 ID 必须是数字。"
    )


def test_build_mutation_actor_detects_roles_and_superuser() -> None:
    admin = build_mutation_actor(
        build_group_message_event("#wordbank delete 12", role="admin")
    )
    member = build_mutation_actor(
        build_group_message_event("#wordbank delete 12", role="member")
    )
    superuser = build_mutation_actor(
        build_group_message_event("#wordbank delete 12", user_id=1, role="member")
    )

    assert admin.user_id == "10001"
    assert admin.group_id == "20001"
    assert admin.can_moderate_group
    assert not admin.is_superuser
    assert not member.can_moderate_group
    assert superuser.is_superuser


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


async def test_handle_study_shortcut_supports_legacy_one_line_modes() -> None:
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=12,
            trigger_text="晚安",
            response_text="做个好梦",
            trigger_mode="contains",
            scope="self_in_current_group",
            probability=1.0,
            weight=3,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_text_entry=add_text_entry))
    event = build_group_message_event("#study m t 晚安 做个好梦")

    message = await handle_study_shortcut(
        service,
        event=event,
        text="m t 晚安 做个好梦",
        locale="zh-CN",
    )

    assert "词条已加入词库" in message
    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="做个好梦",
        raw_rule={"scope": {"self", "current_group"}},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
