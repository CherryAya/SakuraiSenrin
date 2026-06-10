import asyncio
import importlib
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.message import Message
import pytest

from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.handlers import commands
from src.plugins.wordbank.handlers.commands import (
    build_forced_command_text,
    build_mutation_actor,
    dispatch_wordbank_command,
    fetch_first_image_bytes_from_message,
    handle_add_with_media,
    handle_delete,
    handle_guided_add_image_trigger,
    handle_guided_add_text,
    handle_guided_study_image_trigger,
    handle_guided_study_shortcut,
    handle_study_shortcut,
    handle_study_with_media,
    handle_study_with_media_result,
    localize_command_error,
    parse_search_args,
    parse_text_add_args,
    resolve_pending_image,
    start_ingest_first_image_from_message,
    wordbank_help_text,
)
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankDeleteVoteResult,
    WordbankService,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleError
from tests.plugins.water.helpers import build_group_message_event


def test_parse_text_add_args_keeps_fallback_message_and_i18n_key() -> None:
    with pytest.raises(RuleError) as exc_info:
        parse_text_add_args("晚安")

    assert str(exc_info.value) == (
        "添加格式: wordbank add 触发词 => 响应词；图片回复: wordbank add 触发词 [图片]"
    )
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


def test_build_forced_command_text_keeps_legacy_entrypoints_thin() -> None:
    assert (
        build_forced_command_text("add", "晚安 => 做个好梦") == "add 晚安 => 做个好梦"
    )
    assert build_forced_command_text(None, " search 晚安 ") == "search 晚安"
    assert build_forced_command_text("delete", " 12 ") == "delete 12"


async def test_dispatch_wordbank_command_formats_search_with_locale() -> None:
    event = build_group_message_event("#wordbank search 晚安")
    search_mock = AsyncMock(
        return_value=[
            WordbankSearchItem(
                entry_id=12,
                status="approved",
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
        "词库搜索结果 (第 1 页):\n"
        "#12 [approved/contains/current_group] 晚安 => 做个好梦"
    )
    search_mock.assert_awaited_once_with("晚安", limit=11, offset=0)


async def test_dispatch_wordbank_search_supports_page_limit_and_more_hint() -> None:
    event = build_group_message_event("#wordbank search 晚安")
    items = [
        WordbankSearchItem(
            entry_id=index,
            status="approved",
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
        "#11 [approved/contains/current_group] 晚安11 => 做个好梦11\n"
        "#12 [approved/contains/current_group] 晚安12 => 做个好梦12\n"
        "#13 [approved/contains/current_group] 晚安13 => 做个好梦13\n"
        "还有更多结果，可使用 --page 3 --limit 3 查看下一页。"
    )
    search_mock.assert_awaited_once_with("晚安", limit=4, offset=3)


async def test_dispatch_pending_lists_reviewable_entries_for_group_admin() -> None:
    event = build_group_message_event("#wordbank pending 晚安", role="admin")
    list_pending_entries = AsyncMock(
        return_value=[
            WordbankSearchItem(
                entry_id=12,
                status="pending",
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
        SimpleNamespace(list_pending_entries=list_pending_entries),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="pending 晚安",
        locale="zh-CN",
    )

    assert message == (
        "待审核词条 (第 1 页):\n"
        "#12 [contains/current_group] 晚安 => 做个好梦  提交者: 10001"
    )
    list_pending_entries.assert_awaited_once_with(
        keyword="晚安",
        limit=11,
        offset=0,
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


async def test_dispatch_approval_requires_group_admin_or_superuser() -> None:
    event = build_group_message_event("#wordbank approve 12", role="member")
    approve_entry = AsyncMock()
    service = cast(WordbankService, SimpleNamespace(approve_entry=approve_entry))

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="approve 12",
        locale="zh-CN",
    )

    assert message == "需要当前群管理员/群主或超级用户才能审核词条。"
    approve_entry.assert_not_awaited()


async def test_dispatch_approval_and_rejection_update_pending_entries() -> None:
    event = build_group_message_event("#wordbank approve 12", role="admin")
    approve_entry = AsyncMock(return_value=True)
    reject_entry = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(approve_entry=approve_entry, reject_entry=reject_entry),
    )

    approved = await dispatch_wordbank_command(
        service,
        event=event,
        text="approve 12",
        locale="zh-CN",
    )
    rejected = await dispatch_wordbank_command(
        service,
        event=event,
        text="reject 13",
        locale="zh-CN",
    )

    assert approved == "词条 #12 已通过审核，稍后会参与被动匹配。"
    assert rejected == "词条 #13 已拒绝，不会参与被动匹配。"
    approve_entry.assert_awaited_once_with(
        12,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )
    reject_entry.assert_awaited_once_with(
        13,
        actor_user_id="10001",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


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
        SimpleNamespace(delete_entry=delete_mock, request_delete_vote=AsyncMock()),
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


async def test_handle_delete_starts_vote_when_user_cannot_delete() -> None:
    delete_mock = AsyncMock(return_value=False)
    request_vote = AsyncMock(
        return_value=WordbankDeleteVoteResult(
            vote_id=3,
            entry_id=12,
            status="open",
            support_count=1,
            threshold=3,
            created=True,
            already_supported=False,
            passed=False,
            entry_deleted=False,
        )
    )
    service = cast(
        WordbankService,
        SimpleNamespace(delete_entry=delete_mock, request_delete_vote=request_vote),
    )
    event = build_group_message_event("#wordbank delete 12", role="member")

    message = await handle_delete(
        service,
        event=event,
        entry_id_text="12",
        locale="zh-CN",
    )

    assert "已发起删除投票 #3" in message
    request_vote.assert_awaited_once_with(
        entry_id=12,
        group_id="20001",
        user_id="10001",
        threshold=3,
    )


async def test_dispatch_support_and_vote_commands_use_delete_vote_service() -> None:
    event = build_group_message_event("#wordbank support 3", user_id=10002)
    support_delete_vote = AsyncMock(
        return_value=WordbankDeleteVoteResult(
            vote_id=3,
            entry_id=12,
            status="open",
            support_count=2,
            threshold=3,
            created=False,
            already_supported=False,
            passed=False,
            entry_deleted=False,
        )
    )
    get_delete_vote = AsyncMock(
        return_value=WordbankDeleteVoteResult(
            vote_id=3,
            entry_id=12,
            status="open",
            support_count=2,
            threshold=3,
            created=False,
            already_supported=False,
            passed=False,
            entry_deleted=False,
        )
    )
    service = cast(
        WordbankService,
        SimpleNamespace(
            support_delete_vote=support_delete_vote,
            get_delete_vote=get_delete_vote,
        ),
    )

    support_message = await dispatch_wordbank_command(
        service,
        event=event,
        text="support 3",
        locale="zh-CN",
    )
    vote_message = await dispatch_wordbank_command(
        service,
        event=event,
        text="vote 3",
        locale="zh-CN",
    )

    assert support_message == ("已支持删除投票 #3。\n词条: #12\n当前支持票: 2/3")
    assert vote_message == "删除投票 #3\n词条: #12\n状态: open\n支持票: 2/3"
    support_delete_vote.assert_awaited_once_with(
        vote_id=3,
        group_id="20001",
        user_id="10002",
    )
    get_delete_vote.assert_awaited_once_with(3, group_id="20001")


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

    assert "词条已提交审核" in message
    assert "ID: 12" in message
    assert "触发: 晚安" in message


async def test_handle_add_with_media_plain_text_to_image_response() -> None:
    event = build_group_message_event(
        "#wordbank add 是这张图喔 [CQ:image,url=https://example.test/a.png]"
    )
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=7))
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=13,
            trigger_text="是这张图喔",
            response_text="",
            trigger_mode="contains",
            scope="current_group",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=7,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_text_entry=add_text_entry))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    message = await handle_add_with_media(
        service,
        media_service,
        event=event,
        text="是这张图喔",
        image_bytes=b"image-bytes",
        locale="zh-CN",
    )

    assert "触发: 是这张图喔" in message
    assert "响应: [图片:7]" in message
    ingest_image_bytes.assert_awaited_once_with(b"image-bytes")
    add_text_entry.assert_awaited_once_with(
        trigger_text="是这张图喔",
        response_text="",
        response_canonical_image_id=7,
        trigger_mode=None,
        raw_rule={},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_fetch_first_image_bytes_retries_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passive = importlib.import_module("src.plugins.wordbank.handlers.passive")
    fetch_image_bytes = AsyncMock(side_effect=[None, b"image-bytes"])
    monkeypatch.setattr(passive, "fetch_image_bytes", fetch_image_bytes)
    monkeypatch.setattr(commands.asyncio, "sleep", AsyncMock())

    data = await fetch_first_image_bytes_from_message(
        Message("[CQ:image,url=https://example.test/retry.png]")
    )

    assert data == b"image-bytes"
    assert fetch_image_bytes.await_count == 2


async def test_pending_image_resolves_download_failure_as_entry_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passive = importlib.import_module("src.plugins.wordbank.handlers.passive")
    fetch_image_bytes = AsyncMock(return_value=None)
    monkeypatch.setattr(passive, "fetch_image_bytes", fetch_image_bytes)
    monkeypatch.setattr(commands.asyncio, "sleep", AsyncMock())
    ingest_image_bytes = AsyncMock()
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    pending = start_ingest_first_image_from_message(
        media_service,
        Message("[CQ:image,url=https://example.test/missing.png]"),
    )

    assert pending is not None
    with pytest.raises(WordbankUserError) as exc_info:
        await resolve_pending_image(pending)
    assert exc_info.value.key == "wordbank.error.image_prepare_failed"
    assert "图片下载失败" in str(exc_info.value)
    ingest_image_bytes.assert_not_awaited()


async def test_pending_image_runs_download_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passive = importlib.import_module("src.plugins.wordbank.handlers.passive")
    gate = asyncio.Event()

    async def fake_fetch_image_bytes(_url: str) -> bytes:
        await gate.wait()
        return b"image-bytes"

    monkeypatch.setattr(passive, "fetch_image_bytes", fake_fetch_image_bytes)
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=12))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    pending = start_ingest_first_image_from_message(
        media_service,
        Message("[CQ:image,url=https://example.test/slow.png]"),
    )

    assert pending is not None
    await asyncio.sleep(0)
    assert not pending.task.done()
    gate.set()
    image = await resolve_pending_image(pending)
    assert image.canonical_id == 12
    ingest_image_bytes.assert_awaited_once_with(b"image-bytes")


async def test_handle_add_with_media_text_and_image_response() -> None:
    event = build_group_message_event(
        "#wordbank add 晚安 => 做个好梦 [CQ:image,url=https://example.test/a.png]"
    )
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=8))
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=14,
            trigger_text="晚安",
            response_text="做个好梦",
            trigger_mode="contains",
            scope="current_group",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=8,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_text_entry=add_text_entry))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    message = await handle_add_with_media(
        service,
        media_service,
        event=event,
        text="晚安 => 做个好梦",
        image_bytes=b"image-bytes",
        locale="zh-CN",
    )

    assert "响应: 做个好梦 [图片:8]" in message
    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="做个好梦",
        response_canonical_image_id=8,
        trigger_mode=None,
        raw_rule={},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_handle_add_with_media_treats_empty_left_pair_as_image_trigger() -> None:
    event = build_group_message_event(
        "#wordbank add [CQ:image,url=https://example.test/a.png] => 是这张图喔"
    )
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=9))
    add_image_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=15,
            trigger_text="[图片:9]",
            response_text="是这张图喔",
            trigger_mode="fullmatch",
            scope="current_group",
            probability=1.0,
            weight=3,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_image_entry=add_image_entry))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    message = await handle_add_with_media(
        service,
        media_service,
        event=event,
        text="=> 是这张图喔",
        image_bytes=b"image-bytes",
        locale="zh-CN",
    )

    assert "触发: [图片:9]" in message
    assert "响应: 是这张图喔" in message
    add_image_entry.assert_awaited_once_with(
        canonical_image_id=9,
        response_text="是这张图喔",
        raw_rule={},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


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

    assert "词条已提交审核" in message
    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="做个好梦",
        raw_rule={"scope": {"self", "current_group"}},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_handle_study_with_media_reuses_wordbank_add_image_response() -> None:
    event = build_group_message_event(
        "#study 是这张图喔 [CQ:image,url=https://example.test/a.png]"
    )
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=7))
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=16,
            trigger_text="是这张图喔",
            response_text="",
            trigger_mode="contains",
            scope="current_group",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=7,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_text_entry=add_text_entry))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    message = await handle_study_with_media(
        service,
        media_service,
        event=event,
        text="是这张图喔",
        image_bytes=b"image-bytes",
        locale="zh-CN",
    )

    assert "触发: 是这张图喔" in message
    assert "响应: [图片:7]" in message
    add_text_entry.assert_awaited_once_with(
        trigger_text="是这张图喔",
        response_text="",
        response_canonical_image_id=7,
        trigger_mode=None,
        raw_rule={},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_handle_study_with_media_reuses_wordbank_add_image_trigger() -> None:
    event = build_group_message_event(
        "#study [CQ:image,url=https://example.test/a.png] => 是这张图喔"
    )
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=9))
    add_image_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=17,
            trigger_text="[图片:9]",
            response_text="是这张图喔",
            trigger_mode="fullmatch",
            scope="current_group",
            probability=1.0,
            weight=3,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_image_entry=add_image_entry))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    message = await handle_study_with_media(
        service,
        media_service,
        event=event,
        text="=> 是这张图喔",
        image_bytes=b"image-bytes",
        locale="zh-CN",
    )

    assert "触发: [图片:9]" in message
    assert "响应: 是这张图喔" in message
    add_image_entry.assert_awaited_once_with(
        canonical_image_id=9,
        response_text="是这张图喔",
        raw_rule={},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_handle_study_with_media_legacy_flags_two_images() -> None:
    event = build_group_message_event(
        "#study a f [CQ:image,url=https://example.test/a.png] "
        "[CQ:image,url=https://example.test/b.png]"
    )
    ingest_image_bytes = AsyncMock(
        side_effect=[
            SimpleNamespace(canonical_id=21),
            SimpleNamespace(canonical_id=22),
        ]
    )
    add_image_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=18,
            trigger_text="[图片:21]",
            response_text="",
            trigger_mode="fullmatch",
            scope="all_groups",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=22,
        )
    )
    add_text_entry = AsyncMock()
    service = cast(
        WordbankService,
        SimpleNamespace(
            add_image_entry=add_image_entry,
            add_text_entry=add_text_entry,
        ),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    result = await handle_study_with_media_result(
        service,
        media_service,
        event=event,
        text="a f",
        image_bytes=b"trigger-image",
        extra_image_bytes=(b"response-image",),
    )

    assert result.trigger_text == "[图片:21]"
    assert result.response_canonical_image_id == 22
    add_text_entry.assert_not_awaited()
    ingest_image_bytes.assert_any_await(b"trigger-image")
    ingest_image_bytes.assert_any_await(b"response-image")
    add_image_entry.assert_awaited_once_with(
        canonical_image_id=21,
        response_text="",
        response_canonical_image_id=22,
        raw_rule={"scope": "all_groups"},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_handle_study_with_media_legacy_text_trigger_image_response() -> None:
    event = build_group_message_event(
        "#study a f 晚安 [CQ:image,url=https://example.test/a.png]"
    )
    ingest_image_bytes = AsyncMock(return_value=SimpleNamespace(canonical_id=23))
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=19,
            trigger_text="晚安",
            response_text="",
            trigger_mode="contains",
            scope="all_groups",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=23,
        )
    )
    service = cast(WordbankService, SimpleNamespace(add_text_entry=add_text_entry))
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(ingest_image_bytes=ingest_image_bytes),
    )

    result = await handle_study_with_media_result(
        service,
        media_service,
        event=event,
        text="a f 晚安",
        image_bytes=b"response-image",
    )

    assert result.trigger_text == "晚安"
    assert result.response_canonical_image_id == 23
    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="",
        response_canonical_image_id=23,
        raw_rule={"scope": "all_groups"},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_guided_add_supports_image_response_and_image_trigger() -> None:
    event = build_group_message_event("#wordbank add")
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=17,
            trigger_text="晚安",
            response_text="配图",
            trigger_mode="contains",
            scope="current_group",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=8,
        )
    )
    add_image_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=18,
            trigger_text="[图片:9]",
            response_text="是这张图喔",
            trigger_mode="fullmatch",
            scope="current_group",
            probability=1.0,
            weight=3,
        )
    )
    service = cast(
        WordbankService,
        SimpleNamespace(
            add_text_entry=add_text_entry,
            add_image_entry=add_image_entry,
        ),
    )

    text_trigger_message = await handle_guided_add_text(
        service,
        event=event,
        trigger_text="晚安",
        response_text="配图",
        response_canonical_image_id=8,
        scope_text="1",
        locale="zh-CN",
    )
    image_trigger_message = await handle_guided_add_image_trigger(
        service,
        event=event,
        trigger_canonical_image_id=9,
        response_text="是这张图喔",
        scope_text="1",
        locale="zh-CN",
    )

    assert "响应: 配图 [图片:8]" in text_trigger_message
    assert "触发: [图片:9]" in image_trigger_message
    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="配图",
        response_canonical_image_id=8,
        trigger_mode=None,
        raw_rule={"scope": "current_group"},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    add_image_entry.assert_awaited_once_with(
        canonical_image_id=9,
        response_text="是这张图喔",
        response_canonical_image_id=None,
        raw_rule={"scope": "current_group"},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )


async def test_guided_study_supports_image_response_and_image_trigger() -> None:
    event = build_group_message_event("#study")
    add_text_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=19,
            trigger_text="晚安",
            response_text="",
            trigger_mode="contains",
            scope="current_group",
            probability=1.0,
            weight=3,
            response_kind="image",
            response_canonical_image_id=10,
        )
    )
    add_image_entry = AsyncMock(
        return_value=WordbankAddResult(
            entry_id=20,
            trigger_text="[图片:11]",
            response_text="是这张图喔",
            trigger_mode="fullmatch",
            scope="current_group",
            probability=1.0,
            weight=3,
        )
    )
    service = cast(
        WordbankService,
        SimpleNamespace(
            add_text_entry=add_text_entry,
            add_image_entry=add_image_entry,
        ),
    )

    text_trigger_message = await handle_guided_study_shortcut(
        service,
        event=event,
        trig_mode_text="a",
        group_block_text="t",
        trigger_text="晚安",
        response_text="",
        response_canonical_image_id=10,
        weight_text="3",
        locale="zh-CN",
    )
    image_trigger_message = await handle_guided_study_image_trigger(
        service,
        event=event,
        trig_mode_text="a",
        group_block_text="t",
        trigger_canonical_image_id=11,
        response_text="是这张图喔",
        weight_text="3",
        locale="zh-CN",
    )

    assert "响应: [图片:10]" in text_trigger_message
    assert "触发: [图片:11]" in image_trigger_message
    add_text_entry.assert_awaited_once_with(
        trigger_text="晚安",
        response_text="",
        response_canonical_image_id=10,
        raw_rule={"scope": "current_group", "weight": 3},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
    add_image_entry.assert_awaited_once_with(
        canonical_image_id=11,
        response_text="是这张图喔",
        response_canonical_image_id=None,
        raw_rule={"scope": "current_group", "weight": 3},
        group_id="20001",
        user_id="10001",
        is_group=True,
    )
