from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot
import pytest

from src.lib.message_plan import render_message_plan_input
from src.plugins.wordbank import pending_batch as pending_batch_module
from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.message_model import (
    combine_shapes,
    shape_from_image,
    shape_from_text,
)
from src.plugins.wordbank.pending_batch import (
    _build_pending_detail_message,
    send_pending_entries_review,
)
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService
from tests.plugins.water.helpers import build_group_message_event


@pytest.mark.asyncio
async def test_build_pending_detail_message_returns_image_blocks() -> None:
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=b"bytes")),
    )
    item = WordbankSearchItem(
        trigger_group_id=12,
        status="pending",
        trigger_text="[图片:8]",
        response_text="做个好梦 [图片:7]",
        trigger_shape=shape_from_image(8),
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        created_at=1_700_000_000,
        rule={"roles": "admin"},
        response_item_ids=(300,),
    )

    entry = await _build_pending_detail_message(
        item,
        index=1,
        locale="zh-CN",
        media_service=media_service,
    )

    rendered = render_message_plan_input(entry)
    assert "序号: 1" in str(rendered)
    assert "状态: 待审核" in str(rendered)
    assert "创建者: 10001" in str(rendered)
    assert "提交时间: 2023-11-15 06:13" in str(rendered)
    assert "规则: 概率 1 | 角色 管理" in str(rendered)
    assert "触发词: 图片消息" in str(rendered)
    assert "触发词详情:" not in str(rendered)
    assert "[图片:8]" not in str(rendered)
    assert "[图片:7]" not in str(rendered)
    assert sum(1 for segment in rendered if segment.type == "image") == 1


@pytest.mark.asyncio
async def test_send_pending_entries_review_uses_message_plan_for_summary_and_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = WordbankSearchItem(
        trigger_group_id=12,
        status="pending",
        trigger_text="[图片:8]",
        response_text="做个好梦 [图片:7]",
        trigger_shape=shape_from_image(8),
        response_shape=combine_shapes(shape_from_text("做个好梦"), shape_from_image(7)),
        scope="current_group",
        probability=1.0,
        weight=3,
        created_by="10001",
        created_at=1_700_000_000,
        rule={"roles": "admin"},
        response_item_ids=(300,),
    )
    record_message_ref = AsyncMock(return_value=None)
    service = cast(
        WordbankService,
        SimpleNamespace(
            list_pending_entries=AsyncMock(return_value=[item]),
            record_message_ref=record_message_ref,
        ),
    )
    media_service = cast(
        WordbankMediaService,
        SimpleNamespace(load_canonical_storage_bytes=AsyncMock(return_value=b"bytes")),
    )
    deliver_plan = AsyncMock(return_value=SimpleNamespace(results=({"message_id": 1},)))
    monkeypatch.setattr(
        pending_batch_module,
        "build_mutation_actor",
        lambda event: SimpleNamespace(
            group_id="10001",
            can_moderate_group=True,
            is_superuser=False,
        ),
    )
    monkeypatch.setattr(pending_batch_module, "deliver_message_plan", deliver_plan)

    bot = cast(Bot, SimpleNamespace())
    event = build_group_message_event("#wordbank.pending", message_id=1)

    await send_pending_entries_review(
        bot,
        event,
        text="#wordbank.pending",
        locale="zh-CN",
        service=service,
        media_service=media_service,
        source_kind="wordbank_approval",
        fallback_nickname="回 - 樱井千凛·Senrinです♡",
    )

    assert deliver_plan.await_count == 1
    await_args = deliver_plan.await_args
    assert await_args is not None
    plan = await_args.kwargs["plan"]
    assert plan.force_forward is True
    assert len(plan.messages) == 2
    summary_message = render_message_plan_input(plan.messages[0])
    rendered_detail = render_message_plan_input(plan.messages[1])
    assert "待审核词条" in str(summary_message)
    assert "回复我发送：通过 1 2 5-8、拒绝 all，或直接用 y / n" in str(summary_message)
    assert "后续节点按“序号”字段对应批量处理编号。" in str(summary_message)
    assert "本页数量: 1" in str(summary_message)
    assert "序号: 1" in str(rendered_detail)
    assert "状态: 待审核" in str(rendered_detail)
    assert "触发词: 图片消息" in str(rendered_detail)
    assert "触发词详情:" not in str(rendered_detail)
    assert sum(1 for segment in rendered_detail if segment.type == "image") == 1
    assert record_message_ref.await_count == 1
