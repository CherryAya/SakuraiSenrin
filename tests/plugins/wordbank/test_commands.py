from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.message import Message
import pytest

from src.plugins.wordbank.database.types import WordbankSearchItem, WordbankSearchPage
from src.plugins.wordbank.handlers.commands import (
    build_group_detail_message,
    build_message_shape_from_message,
    dispatch_wordbank_command,
    handle_add_text_result,
    handle_add_with_media_result,
    handle_delete,
    handle_guided_add_shape_result,
    handle_guided_study_shape_result,
    handle_study_media_with_rule_result,
    handle_study_with_media_result,
    parse_group_view_args,
    parse_guided_search_mode_choice,
    parse_search_args,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
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
            probability=1.0,
            weight=3,
            rule={},
            group_id="20001",
            created_by="10001",
            approved_by="10002",
            deleted_at=0,
            response_text=f"响应{index}",
            response_shape=shape_from_text(f"响应{index}"),
        )
        for index in range(1, 13)
    )
    detail = SimpleNamespace(
        trigger_group_id=271,
        status="approved",
        enabled=1,
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
            load_canonical_storage_bytes=AsyncMock(side_effect=[b"trigger-image"])
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
    assert "Trigger Group #271" in str(message)
    assert "响应 #11" in str(message)
    assert "响应 #12" in str(message)
    assert "响应 #10" not in str(message)


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
        trigger_shape=shape_from_message(Message("晚安")),
        response_shape=shape_from_message(Message("做个好梦")),
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
    from src.plugins.wordbank.handlers import commands as commands_module

    async def _fetch_image_bytes(
        _url: str,
        *,
        attempts: int = 3,
        retry_delay_seconds: float = 0.8,
    ) -> bytes | None:
        _ = attempts, retry_delay_seconds
        return b"image-bytes"

    monkeypatch.setattr(
        commands_module,
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
        Message("[CQ:at,qq=10002]早安[CQ:image,url=https://example.test/a.png]"),
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
        Message(" "),
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
        Message("第一行\n第二行  第三列"),
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
        trigger_shape=shape_from_message(Message("[CQ:at,qq=10002]早安")),
        response_shape=shape_from_message(Message("做个好梦")),
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
        trigger_shape=shape_from_message(Message("晚安")),
        response_shape=shape_from_message(Message("做个好梦")),
        weight_text="4",
    )

    assert add_message_entry.await_args is not None
    kwargs = add_message_entry.await_args.kwargs
    assert kwargs["raw_rule"]["weight"] == 4
    assert shape_to_summary_text(kwargs["trigger_shape"]) == "晚安"
    assert shape_to_summary_text(kwargs["response_shape"]) == "做个好梦"


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
