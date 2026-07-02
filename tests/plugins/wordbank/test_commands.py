from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, call

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
import pytest

from src.lib.messages import empty_message, text_message
from src.plugins.wordbank import entry_commands as entry_commands_module
from src.plugins.wordbank.database.types import WordbankSearchItem, WordbankSearchPage
from src.plugins.wordbank.handlers import commands as commands_module
from src.plugins.wordbank.handlers.commands import (
    build_group_detail_message,
    build_shape_from_text_and_images,
    dispatch_wordbank_command,
    handle_add_text_result,
    handle_add_with_media_result,
    handle_delete,
    handle_guided_add_shape_result,
    handle_guided_study_shape_result,
    handle_pending_entries,
    handle_response_content_update,
    handle_response_weight_update,
    handle_study_media_with_rule_result,
    handle_study_with_media_result,
    handle_trigger_content_update,
    handle_trigger_probability_update,
    parse_rank_period,
    parse_search_args,
    render_search_page_message,
)
from src.plugins.wordbank.handlers.media_helpers import build_message_shape_from_message
from src.plugins.wordbank.handlers.parsers import (
    parse_group_view_args,
    parse_guided_search_mode_choice,
    parse_search_session_command,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_event,
    shape_from_image,
    shape_from_message,
    shape_from_text,
    shape_to_summary_text,
)
from src.plugins.wordbank.services.core import WordbankAddResult, WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleError
from tests.plugins.water.helpers import build_group_message_event


def _add_result(
    *,
    trigger_group_id: int = 34,
    trigger_variant_id: int = 35,
    response_item_id: int = 12,
    trigger_text: str = "晚安",
    response_text: str = "做个好梦",
    trigger_shape: MessageShape | None = None,
    response_shape: MessageShape | None = None,
) -> WordbankAddResult:
    return WordbankAddResult(
        trigger_group_id=trigger_group_id,
        trigger_variant_id=trigger_variant_id,
        response_item_id=response_item_id,
        trigger_text=trigger_text,
        response_text=response_text,
        scope="current_group",
        probability=1.0,
        weight=3,
        trigger_shape=trigger_shape,
        response_shape=response_shape,
    )


def _search_item(*, trigger_group_id: int = 12) -> WordbankSearchItem:
    return WordbankSearchItem(
        trigger_group_id=trigger_group_id,
        status="approved",
        trigger_text=f"晚安{trigger_group_id}",
        response_text=f"做个好梦{trigger_group_id}",
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
    )


def test_parse_guided_search_mode_choice_supports_combined_dimensions() -> None:
    parsed = parse_guided_search_mode_choice("12")
    assert parsed.field == "all"
    assert parsed.requires_query is True
    assert parsed.requires_creator is False

    creator_filtered = parse_guided_search_mode_choice("123")
    assert creator_filtered.field == "all"
    assert creator_filtered.requires_query is True
    assert creator_filtered.requires_creator is True

    creator_only = parse_guided_search_mode_choice("3")
    assert creator_only.field == "all"
    assert creator_only.requires_query is False
    assert creator_only.requires_creator is True


def test_parse_guided_search_mode_choice_rejects_invalid_combinations() -> None:
    with pytest.raises(RuleError):
        parse_guided_search_mode_choice("11")

    with pytest.raises(RuleError):
        parse_guided_search_mode_choice("4")


def test_parse_search_session_command_supports_page_detail_delete_and_exit() -> None:
    page = parse_search_session_command("page 2")
    detail = parse_search_session_command("详情 271 3")
    compact_detail = parse_search_session_command("详情271 4")
    delete = parse_search_session_command("del 1 2 2")
    exit_cmd = parse_search_session_command("exit")

    assert page.action == "page"
    assert page.page == 2
    assert detail.action == "detail"
    assert detail.trigger_group_id == 271
    assert detail.page == 3
    assert compact_detail.action == "detail"
    assert compact_detail.trigger_group_id == 271
    assert compact_detail.page == 4
    assert delete.action == "delete"
    assert delete.delete_indexes == (1, 2)
    assert exit_cmd.action == "exit"


def test_parse_rank_period_supports_default_and_aliases() -> None:
    assert parse_rank_period("") == "month"
    assert parse_rank_period("周榜") == "week"
    assert parse_rank_period("month") == "month"
    assert parse_rank_period("本季") == "season"
    assert parse_rank_period("总榜") == "total"


def test_wordbank_command_progress_spec_marks_rank_as_long_task() -> None:
    assert (
        entry_commands_module._build_wordbank_command_progress_spec(
            "rank",
            rest="周榜",
            locale="zh-CN",
        )
        is not None
    )
    assert (
        entry_commands_module._build_wordbank_command_progress_spec(
            "trigger",
            rest="set 12 [CQ:image,file=a.png]",
            locale="zh-CN",
        )
        is not None
    )
    assert (
        entry_commands_module._build_wordbank_command_progress_spec(
            "response",
            rest="set 12 新响应",
            locale="zh-CN",
        )
        is not None
    )
    assert (
        entry_commands_module._build_wordbank_command_progress_spec(
            "approve",
            rest="1",
            locale="zh-CN",
        )
        is None
    )


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_routes_rank_to_leaderboard_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle_rank = AsyncMock(return_value=text_message("RANK_OK"))
    monkeypatch.setattr(
        commands_module,
        "handle_creator_leaderboard",
        handle_rank,
    )

    message = await dispatch_wordbank_command(
        cast(WordbankService, SimpleNamespace()),
        event=build_group_message_event("#wordbank rank 周榜"),
        text="rank 周榜",
        locale="zh-CN",
    )

    assert message == text_message("RANK_OK")
    handle_rank.assert_awaited_once()
    await_args = handle_rank.await_args
    assert await_args is not None
    assert await_args.kwargs["text"] == "周榜"


@pytest.mark.asyncio
async def test_handle_pending_entries_renders_image_shapes() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(
            list_pending_entries=AsyncMock(
                return_value=[
                    WordbankSearchItem(
                        trigger_group_id=12,
                        status="pending",
                        trigger_text="[图片:8]",
                        response_text="做个好梦 [图片:7]",
                        trigger_shape=shape_from_image(8),
                        response_shape=combine_shapes(
                            shape_from_text("做个好梦"),
                            shape_from_image(7),
                        ),
                        scope="current_group",
                        probability=1.0,
                        weight=3,
                        created_by="10001",
                        response_item_ids=(300,),
                    )
                ]
            )
        ),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=b"bytes")),
    )
    event = build_group_message_event("#待审核词条", role="admin", user_id=10002)

    message = await handle_pending_entries(
        service,
        event=event,
        text="",
        locale="zh-CN",
        media_service=media_service,
    )

    assert not isinstance(message, str)
    assert "待审核词条 (第 1 页):" in str(message)
    assert "[图片:8]" not in str(message)
    assert "[图片:7]" not in str(message)
    assert sum(1 for segment in message if segment.type == "image") == 2


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_formats_search_with_locale() -> None:
    event = build_group_message_event("#wordbank search 晚安")
    service = cast(
        WordbankService,
        SimpleNamespace(
            search_page=AsyncMock(
                return_value=WordbankSearchPage(
                    items=(_search_item(),),
                    total_count=1,
                    offset=0,
                    limit=10,
                )
            )
        ),
    )

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="search 晚安",
        locale="zh-CN",
        media_service=cast(
            WordbankMediaService,
            SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=None)),
        ),
    )

    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "image"


@pytest.mark.asyncio
async def test_render_search_page_message_fallback_renders_image_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        commands_module,
        "render_search_results_card_message",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=b"bytes")),
    )
    page = WordbankSearchPage(
        items=(
            WordbankSearchItem(
                trigger_group_id=12,
                status="approved",
                trigger_text="[图片:8]",
                response_text="做个好梦 [图片:7]",
                trigger_shape=shape_from_image(8),
                response_shape=combine_shapes(
                    shape_from_text("做个好梦"),
                    shape_from_image(7),
                ),
                response_summaries=("做个好梦 [图片:7]",),
                scope="current_group",
                probability=1.0,
                weight=3,
                created_by="10001",
            ),
        ),
        total_count=1,
        offset=0,
        limit=10,
    )

    message = await render_search_page_message(
        page,
        parsed=parse_search_args("晚安"),
        locale="zh-CN",
        has_image=False,
        media_service=media_service,
    )

    assert "[图片:8]" not in str(message)
    assert "[图片:7]" not in str(message)
    assert sum(1 for segment in message if segment.type == "image") == 2


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_rejects_add_subcommand() -> None:
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦")
    service = cast(WordbankService, SimpleNamespace())

    with pytest.raises(RuntimeError, match="unified submission flow"):
        await dispatch_wordbank_command(
            service,
            event=event,
            text="add [图片触发] => 做个好梦",
            locale="zh-CN",
        )


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_rejects_disabled_vote_subcommand() -> None:
    event = build_group_message_event("#wordbank support 3")
    service = cast(WordbankService, SimpleNamespace())

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="support 3",
        locale="zh-CN",
        media_service=cast(
            WordbankMediaService,
            SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=None)),
        ),
    )

    assert "未知词库子命令: support" in str(message)


def test_parse_group_view_args_supports_page_flag_and_positional_page() -> None:
    parsed = parse_group_view_args("271 --page 3")
    assert parsed.trigger_group_id == 271
    assert parsed.page == 3

    positional = parse_group_view_args("271 2")
    assert positional.trigger_group_id == 271
    assert positional.page == 2


@pytest.mark.asyncio
async def test_build_group_detail_message_renders_requested_page() -> None:
    responses = tuple(
        SimpleNamespace(
            response_item_id=index,
            status="approved",
            enabled=1,
            scope="current_group",
            weight=3,
            rule={},
            group_id="20001",
            created_by="10001",
            approved_by="10002",
            deleted_at=0,
            response_text=f"响应{index}",
            response_shape=(
                combine_shapes(shape_from_text(f"响应{index}"), shape_from_image(index))
                if index >= 11
                else shape_from_text(f"响应{index}")
            ),
        )
        for index in range(1, 13)
    )
    detail = SimpleNamespace(
        trigger_group_id=271,
        status="approved",
        enabled=1,
        probability=0.4,
        group_id="20001",
        created_by="10001",
        deleted_at=0,
        trigger_text="jrlp",
        trigger_shape=combine_shapes(shape_from_text("jrlp"), shape_from_image(7)),
        trigger_variant_id=12,
        responses=responses,
    )
    service = cast(
        WordbankService,
        SimpleNamespace(get_group_detail=AsyncMock(return_value=detail)),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            load_canonical_storage_bytes=AsyncMock(
                side_effect=[b"trigger-image", b"response-11", b"response-12"]
            )
        ),
    )

    message, returned_detail, total_pages = await build_group_detail_message(
        service,
        trigger_group_id=271,
        page=2,
        locale="zh-CN",
        media_service=media_service,
    )

    assert total_pages == 2
    assert returned_detail is detail
    assert len(message) == 1
    assert message[0].type == "image"
    load_bytes = cast(AsyncMock, media_service.load_canonical_storage_bytes)
    assert load_bytes.await_args_list == [
        call(7),
        call(11),
        call(12),
    ]


@pytest.mark.asyncio
async def test_handle_add_text_result_calls_add_message_entry_with_text_shapes() -> (
    None
):
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦")

    result = await handle_add_text_result(
        service,
        event=event,
        text="晚安 => 做个好梦",
    )

    assert result.response_item_id == 12
    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert isinstance(kwargs["trigger_shape"], MessageShape)
    assert isinstance(kwargs["response_shape"], MessageShape)
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"


@pytest.mark.asyncio
async def test_handle_add_text_result_supports_chinese_scope_and_role_flags() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦 -s 本群 -r 管理")

    await handle_add_text_result(
        service,
        event=event,
        text="晚安 => 做个好梦 -s 本群 -r 管理",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"]["scope"] == "current_group"
    assert kwargs["raw_rule"]["roles"] == "admin"


@pytest.mark.asyncio
async def test_handle_add_text_result_rejects_numeric_scope_shortcut() -> None:
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=AsyncMock(return_value=_add_result())),
    )
    event = build_group_message_event("#wordbank add 晚安 => 做个好梦 -s 1")

    with pytest.raises(RuleError, match="不支持的生效范围"):
        await handle_add_text_result(
            service,
            event=event,
            text="晚安 => 做个好梦 -s 1",
        )


@pytest.mark.asyncio
async def test_handle_add_text_result_parses_event_trigger_shape() -> None:
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:poke]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add event:poke => 别戳啦")

    await handle_add_text_result(
        service,
        event=event,
        text="event:poke => 别戳啦",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:poke")
    assert shape_to_summary_text(kwargs["response_shape"]) == "别戳啦"


@pytest.mark.asyncio
async def test_handle_add_text_result_parses_bracket_event_alias_trigger_shape() -> (
    None
):
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:at]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add [@] => 我在")

    await handle_add_text_result(
        service,
        event=event,
        text="[@] => 我在",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:at")
    assert shape_to_summary_text(kwargs["response_shape"]) == "我在"


@pytest.mark.asyncio
async def test_handle_add_text_result_parses_cn_bracket_event_alias_shape() -> None:
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:join]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 【新人加入】 => 欢迎来到凛凛这里")

    await handle_add_text_result(
        service,
        event=event,
        text="【新人加入】 => 欢迎来到凛凛这里",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:join")
    assert shape_to_summary_text(kwargs["response_shape"]) == "欢迎来到凛凛这里"


@pytest.mark.asyncio
async def test_handle_add_text_result_parses_bot_join_alias_trigger_shape() -> None:
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:bot_join]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add [bot加群] => 凛凛来啦")

    await handle_add_text_result(
        service,
        event=event,
        text="[bot加群] => 凛凛来啦",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:bot_join")
    assert shape_to_summary_text(kwargs["response_shape"]) == "凛凛来啦"


@pytest.mark.asyncio
async def test_handle_add_text_result_parses_member_leave_alias_trigger_shape() -> None:
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:member_leave]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add [成员退群] => 下次见")

    await handle_add_text_result(
        service,
        event=event,
        text="[成员退群] => 下次见",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:member_leave")
    assert shape_to_summary_text(kwargs["response_shape"]) == "下次见"


@pytest.mark.asyncio
async def test_handle_add_text_result_allows_escaped_event_literal_trigger() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="event:poke"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add \\event:poke => 当文本处理")

    await handle_add_text_result(
        service,
        event=event,
        text="\\event:poke => 当文本处理",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_text("event:poke")
    assert shape_to_summary_text(kwargs["response_shape"]) == "当文本处理"


@pytest.mark.asyncio
async def test_handle_add_text_result_allows_escaped_bracket_event_literal() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="【戳一戳】"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add \\【戳一戳】 => 当文本处理")

    await handle_add_text_result(
        service,
        event=event,
        text="\\【戳一戳】 => 当文本处理",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_text("【戳一戳】")
    assert shape_to_summary_text(kwargs["response_shape"]) == "当文本处理"


@pytest.mark.asyncio
async def test_handle_add_text_result_preserves_response_whitespace_verbatim() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 晚安 => 第一行")

    await handle_add_text_result(
        service,
        event=event,
        text="晚安=>第一行\n第二行  第三列",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"].atoms[0].text == "晚安"
    assert kwargs["response_shape"].atoms[0].text == "第一行\n第二行  第三列"


@pytest.mark.asyncio
async def test_handle_add_with_media_result_builds_image_response_shape() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(response_text="[图片:7]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=7))
        ),
    )
    event = build_group_message_event(
        "#wordbank add 晚安 [CQ:image,url=https://example.test/a.png]"
    )

    await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text="晚安",
        image_bytes=b"image-bytes",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "[图片:7]"


@pytest.mark.asyncio
async def test_handle_add_with_media_result_preserves_trigger_whitespace_verbatim() -> (
    None
):
    add_message_entry = AsyncMock(return_value=_add_result(response_text="[图片:7]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=7))
        ),
    )
    event = build_group_message_event(
        "#wordbank add 晚安 [CQ:image,url=https://example.test/a.png]"
    )

    await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text="晚安  ",
        image_bytes=b"image-bytes",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"].atoms[0].text == "晚安  "


def test_parse_search_args_preserves_keyword_whitespace_verbatim() -> None:
    parsed = parse_search_args('  "第一行  第二列"   --page 2')

    assert parsed.keyword == "第一行  第二列"
    assert parsed.page == 2


@pytest.mark.asyncio
async def test_handle_add_with_media_result_builds_image_trigger_shape() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="[图片:9]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=9))
        ),
    )
    event = build_group_message_event(
        "#wordbank add [CQ:image,url=https://example.test/a.png] => 是这张图"
    )

    await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text="=> 是这张图",
        image_bytes=b"image-bytes",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[图片:9]"
    assert shape_to_summary_text(kwargs["response_shape"]) == "是这张图"


@pytest.mark.asyncio
async def test_handle_delete_rejects_non_creator_without_vote() -> None:
    delete_response_item = AsyncMock(return_value=False)
    service = cast(
        WordbankService,
        SimpleNamespace(delete_response_item=delete_response_item),
    )
    event = build_group_message_event("#wordbank del 18", role="admin", user_id=10002)

    message = await handle_delete(
        service,
        event=event,
        response_item_id_text="18",
        locale="zh-CN",
    )

    assert message == "未找到可删除的词条 #18，或你没有操作权限。"
    delete_response_item.assert_awaited_once_with(
        18,
        actor_user_id="10002",
        actor_group_id="20001",
        can_moderate_group=True,
        is_superuser=False,
    )


@pytest.mark.asyncio
async def test_handle_trigger_probability_update_passes_superuser_context() -> None:
    update_trigger_probability = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(update_trigger_probability=update_trigger_probability),
    )
    event = build_group_message_event("#wordbank trigger prob 18 0.3", user_id=1)

    message = await handle_trigger_probability_update(
        service,
        event=event,
        trigger_group_id=18,
        probability=0.3,
        locale="zh-CN",
    )

    assert message == "trigger group #18 的触发概率已更新为 0.3。"
    update_trigger_probability.assert_awaited_once_with(
        18,
        probability=0.3,
        actor_user_id="1",
        actor_group_id="20001",
        can_moderate_group=False,
        is_superuser=True,
    )


@pytest.mark.asyncio
async def test_handle_trigger_content_update_builds_shape_and_passes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import commands as commands_module

    update_trigger_content = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(update_trigger_content=update_trigger_content),
    )
    event = build_group_message_event("#wordbank trigger set 18 新触发", user_id=1)

    async def _build_shape(
        _media_service: WordbankMediaService,
        *,
        text: str,
        message: Message,
        parse_trigger_text: bool = False,
    ) -> MessageShape:
        assert text == "新触发"
        assert str(message) == "#wordbank trigger set 18 新触发"
        assert parse_trigger_text is True
        return shape_from_text(f"{text} [图片:9]")

    monkeypatch.setattr(
        commands_module,
        "build_shape_from_text_and_images",
        _build_shape,
    )

    message = await handle_trigger_content_update(
        service,
        cast(WordbankMediaService, SimpleNamespace()),
        event=event,
        trigger_group_id=18,
        text="新触发",
        raw_message=text_message("#wordbank trigger set 18 新触发"),
        locale="zh-CN",
    )

    assert message == "trigger group #18 的触发词已更新，该组响应已重新进入待审核。"
    assert update_trigger_content.await_args is not None
    kwargs = update_trigger_content.await_args.kwargs
    assert kwargs["actor_user_id"] == "1"
    assert kwargs["actor_group_id"] == "20001"
    assert kwargs["can_moderate_group"] is False
    assert kwargs["is_superuser"] is True
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "新触发 [图片:9]"


@pytest.mark.asyncio
async def test_handle_response_weight_update_uses_response_permissions() -> None:
    update_response_weight = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(update_response_weight=update_response_weight),
    )
    event = build_group_message_event("#wordbank response weight 18 5", user_id=10002)

    message = await handle_response_weight_update(
        service,
        event=event,
        response_item_id=18,
        weight=5,
        locale="zh-CN",
    )

    assert message == "词条 #18 的响应权重已更新为 5。"
    update_response_weight.assert_awaited_once_with(
        18,
        weight=5,
        actor_user_id="10002",
        actor_group_id="20001",
        can_moderate_group=False,
        is_superuser=False,
    )


@pytest.mark.asyncio
async def test_handle_response_content_update_builds_shape_and_passes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import commands as commands_module

    update_response_content = AsyncMock(return_value=True)
    service = cast(
        WordbankService,
        SimpleNamespace(update_response_content=update_response_content),
    )
    event = build_group_message_event(
        "#wordbank response set 18 新响应",
        role="admin",
        user_id=10002,
    )

    async def _build_shape(
        _media_service: WordbankMediaService,
        *,
        text: str,
        message: Message,
    ) -> MessageShape:
        assert text == "新响应"
        assert str(message) == "#wordbank response set 18 新响应"
        return shape_from_text(text)

    monkeypatch.setattr(
        commands_module,
        "build_shape_from_text_and_images",
        _build_shape,
    )

    message = await handle_response_content_update(
        service,
        cast(WordbankMediaService, SimpleNamespace()),
        event=event,
        response_item_id=18,
        text="新响应",
        raw_message=text_message("#wordbank response set 18 新响应"),
        locale="zh-CN",
    )

    assert message == "词条 #18 的响应内容已更新，并重新进入待审核。"
    assert update_response_content.await_args is not None
    kwargs = update_response_content.await_args.kwargs
    assert kwargs["actor_user_id"] == "10002"
    assert kwargs["actor_group_id"] == "20001"
    assert kwargs["can_moderate_group"] is True
    assert kwargs["is_superuser"] is False
    assert shape_to_summary_text(kwargs["response_shape"]) == "新响应"


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_routes_trigger_set_to_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import commands as commands_module

    handle_trigger_content = AsyncMock(return_value="ok")
    monkeypatch.setattr(
        commands_module,
        "handle_trigger_content_update",
        handle_trigger_content,
    )
    event = build_group_message_event("#wordbank trigger set 18 新触发", user_id=1)
    service = cast(WordbankService, SimpleNamespace())
    media_service = cast(WordbankMediaService, SimpleNamespace())
    raw_message = text_message("#wordbank trigger set 18 新触发")

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="trigger set 18 新触发",
        raw_message=raw_message,
        locale="zh-CN",
        media_service=media_service,
    )

    assert message == "ok"
    handle_trigger_content.assert_awaited_once_with(
        service,
        media_service,
        event=event,
        trigger_group_id=18,
        text="新触发",
        raw_message=raw_message,
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_routes_response_set_to_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import commands as commands_module

    handle_response_content = AsyncMock(return_value="ok")
    monkeypatch.setattr(
        commands_module,
        "handle_response_content_update",
        handle_response_content,
    )
    event = build_group_message_event("#wordbank response set 18 新响应", user_id=10002)
    service = cast(WordbankService, SimpleNamespace())
    media_service = cast(WordbankMediaService, SimpleNamespace())
    raw_message = text_message("#wordbank response set 18 新响应")

    message = await dispatch_wordbank_command(
        service,
        event=event,
        text="response set 18 新响应",
        raw_message=raw_message,
        locale="zh-CN",
        media_service=media_service,
    )

    assert message == "ok"
    handle_response_content.assert_awaited_once_with(
        service,
        media_service,
        event=event,
        response_item_id=18,
        text="新响应",
        raw_message=raw_message,
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_requires_raw_message_for_trigger_set() -> None:
    event = build_group_message_event("#wordbank trigger set 18 新触发", user_id=1)

    with pytest.raises(
        RuntimeError,
        match="wordbank raw message is required for trigger set",
    ):
        await dispatch_wordbank_command(
            cast(WordbankService, SimpleNamespace()),
            event=event,
            text="trigger set 18 新触发",
            raw_message=None,
            locale="zh-CN",
            media_service=cast(WordbankMediaService, SimpleNamespace()),
        )


@pytest.mark.asyncio
async def test_dispatch_wordbank_command_requires_raw_message_for_response_set() -> (
    None
):
    event = build_group_message_event("#wordbank response set 18 新响应", user_id=10002)

    with pytest.raises(
        RuntimeError,
        match="wordbank raw message is required for response set",
    ):
        await dispatch_wordbank_command(
            cast(WordbankService, SimpleNamespace()),
            event=event,
            text="response set 18 新响应",
            raw_message=None,
            locale="zh-CN",
            media_service=cast(WordbankMediaService, SimpleNamespace()),
        )


@pytest.mark.asyncio
async def test_build_shape_from_text_and_images_combines_text_and_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import media_helpers

    async def _fetch_image_bytes(
        _message: Message,
        *,
        limit: int = 4,
        task: object | None = None,
    ) -> tuple[bytes, ...]:
        _ = limit, task
        return (b"image-bytes",)

    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_from_message",
        _fetch_image_bytes,
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=9))
        ),
    )

    shape = await build_shape_from_text_and_images(
        media_service,
        text="新的响应",
        message=empty_message() + MessageSegment.image("https://example.test/a.png"),
    )

    assert shape_to_summary_text(shape) == "新的响应 [图片:9]"


@pytest.mark.asyncio
async def test_build_shape_from_text_and_images_keeps_event_literal_for_response_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import media_helpers

    async def _fetch_image_bytes(
        _message: Message,
        *,
        limit: int = 4,
        task: object | None = None,
    ) -> tuple[bytes, ...]:
        _ = limit, task
        return ()

    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_from_message",
        _fetch_image_bytes,
    )
    media_service = cast(WordbankMediaService, SimpleNamespace())

    shape = await build_shape_from_text_and_images(
        media_service,
        text="event:poke",
        message=empty_message(),
    )

    assert shape == shape_from_text("event:poke")


@pytest.mark.asyncio
async def test_handle_guided_add_shape_result_uses_scope_and_strict_mode() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add")

    await handle_guided_add_shape_result(
        service,
        event=event,
        trigger_shape=shape_from_message(text_message("晚安")),
        response_shape=shape_from_message(text_message("做个好梦")),
        scope_text="1",
        advanced_text="跳过",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"]["scope"] == "current_group"


@pytest.mark.asyncio
async def test_build_message_shape_from_message_preserves_mixed_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.wordbank.handlers import media_helpers

    async def _fetch_image_bytes(
        _url: str,
        *,
        attempts: int = 3,
        retry_delay_seconds: float = 0.8,
    ) -> bytes | None:
        _ = attempts, retry_delay_seconds
        return b"image-bytes"

    monkeypatch.setattr(
        media_helpers,
        "fetch_image_bytes_with_retry",
        _fetch_image_bytes,
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(return_value=SimpleNamespace(canonical_id=7))
        ),
    )

    shape = await build_message_shape_from_message(
        media_service,
        empty_message()
        + MessageSegment.at("10002")
        + MessageSegment.text("早安")
        + MessageSegment("image", {"url": "https://example.test/a.png"}),
    )

    assert shape_to_summary_text(shape) == "[@:10002] 早安 [图片:7]"


@pytest.mark.asyncio
async def test_build_message_shape_from_message_preserves_single_space_text() -> None:
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(),
        ),
    )

    shape = await build_message_shape_from_message(
        media_service,
        text_message(" "),
    )

    assert shape.is_empty()


@pytest.mark.asyncio
async def test_build_message_shape_from_message_preserves_user_text_verbatim() -> None:
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(),
        ),
    )

    shape = await build_message_shape_from_message(
        media_service,
        text_message("第一行\n第二行  第三列"),
    )

    assert shape.atoms[0].text == "第一行\n第二行  第三列"


@pytest.mark.asyncio
async def test_handle_guided_add_shape_result_accepts_message_shapes() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add")

    await handle_guided_add_shape_result(
        service,
        event=event,
        trigger_shape=shape_from_message(
            empty_message() + MessageSegment.at("10002") + MessageSegment.text("早安")
        ),
        response_shape=shape_from_message(text_message("做个好梦")),
        scope_text="1",
        advanced_text="跳过",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[@:10002] 早安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"


@pytest.mark.asyncio
async def test_handle_guided_study_shape_result_builds_rules_and_shapes() -> None:
    add_message_entry = AsyncMock(return_value=_add_result())
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#study")

    await handle_guided_study_shape_result(
        service,
        event=event,
        trig_mode_text="a",
        group_block_text="t",
        trigger_shape=shape_from_message(text_message("晚安")),
        response_shape=shape_from_message(text_message("做个好梦")),
        weight_text="4",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"]["weight"] == 4
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"


@pytest.mark.asyncio
async def test_handle_study_shortcut_result_parses_event_trigger_shape() -> None:
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:join]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#study event:join => 欢迎加入")

    await commands_module.handle_study_shortcut_result(
        service,
        event=event,
        text="event:join => 欢迎加入",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:join")
    assert shape_to_summary_text(kwargs["response_shape"]) == "欢迎加入"


@pytest.mark.asyncio
async def test_handle_study_shortcut_result_parses_bracket_event_alias_shape() -> None:
    add_message_entry = AsyncMock(
        return_value=_add_result(trigger_text="[事件:event:member_leave]")
    )
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#study [成员退群] => 下次见")

    await commands_module.handle_study_shortcut_result(
        service,
        event=event,
        text="[成员退群] => 下次见",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_event("event:member_leave")
    assert shape_to_summary_text(kwargs["response_shape"]) == "下次见"


@pytest.mark.asyncio
async def test_handle_add_text_result_keeps_event_literal_in_response_plain_text() -> (
    None
):
    add_message_entry = AsyncMock(return_value=_add_result(response_text="event:poke"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 晚安 => event:poke")

    await handle_add_text_result(
        service,
        event=event,
        text="晚安 => event:poke",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_text("晚安")
    assert kwargs["response_shape"] == shape_from_text("event:poke")


@pytest.mark.asyncio
async def test_handle_add_text_result_keeps_bracket_literal_response_text() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(response_text="【戳一戳】"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    event = build_group_message_event("#wordbank add 晚安 => 【戳一戳】")

    await handle_add_text_result(
        service,
        event=event,
        text="晚安 => 【戳一戳】",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["trigger_shape"] == shape_from_text("晚安")
    assert kwargs["response_shape"] == shape_from_text("【戳一戳】")


@pytest.mark.asyncio
async def test_handle_study_media_with_rule_result_supports_two_image_messages() -> (
    None
):
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="[图片:7]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(
                side_effect=[
                    SimpleNamespace(canonical_id=7),
                    SimpleNamespace(canonical_id=8),
                ]
            )
        ),
    )
    event = build_group_message_event("#study a t")

    await handle_study_media_with_rule_result(
        service,
        media_service,
        event=event,
        source="",
        raw_rule={"scope": "all_groups"},
        image_bytes=(b"left", b"right"),
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[图片:7]"
    assert shape_to_summary_text(kwargs["response_shape"]) == "[图片:8]"


@pytest.mark.asyncio
async def test_study_media_result_supports_whitespace_legacy_tail() -> None:
    add_message_entry = AsyncMock(return_value=_add_result(trigger_text="[图片:7]"))
    service = cast(
        WordbankService,
        SimpleNamespace(add_message_entry=add_message_entry),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(
            ingest_image_bytes=AsyncMock(
                side_effect=[
                    SimpleNamespace(canonical_id=7),
                    SimpleNamespace(canonical_id=8),
                ]
            )
        ),
    )
    event = build_group_message_event("#study a f [image] [image]")

    await handle_study_with_media_result(
        service,
        media_service,
        event=event,
        text="a f ",
        image_bytes=b"left",
        extra_image_bytes=(b"right",),
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"] == {"scope": "all_groups"}
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "[图片:7]"
    assert shape_to_summary_text(kwargs["response_shape"]) == "[图片:8]"
