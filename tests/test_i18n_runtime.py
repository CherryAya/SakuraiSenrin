from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.lib.i18n.runtime import (
    finish_i18n,
    normalize_locale,
    send_i18n,
    send_private_i18n,
    tr,
)
from src.lib.message_assets import message_asset_repo
from src.lib.message_delivery import DeliveryTarget
from src.repositories.i18n import I18nRepository
from tests.plugins.water.helpers import build_group_message_event


def test_normalize_locale_aliases() -> None:
    assert normalize_locale("zh") == "zh-CN"
    assert normalize_locale("WYW") == "lzh"
    assert normalize_locale("抽象") == "x-meme"
    assert normalize_locale("unknown") is None


def test_tr_falls_back_to_zh_cn_for_partial_catalog() -> None:
    assert tr("x-meme", "admin.group.banned") == "已封禁"
    assert "wordbank / 词库 / wordbank.help" in tr("x-meme", "wordbank.help")
    assert "-s 本群|全局|自己|私聊" in tr("zh-CN", "wordbank.help")


@pytest.mark.asyncio
async def test_repository_resolve_locale_prefers_group_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = I18nRepository()

    async def fake_group_locale(self: I18nRepository, group_id: str) -> str | None:
        return "lzh" if group_id == "20001" else None

    async def fake_default_locale(self: I18nRepository) -> str:
        return "x-meme"

    monkeypatch.setattr(I18nRepository, "get_group_locale", fake_group_locale)
    monkeypatch.setattr(I18nRepository, "get_default_locale", fake_default_locale)

    assert await repo.resolve_locale("20001") == "lzh"
    assert await repo.resolve_locale("20002") == "x-meme"
    assert await repo.resolve_locale(None) == "x-meme"


@pytest.mark.asyncio
async def test_send_private_i18n_keeps_template_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    class _FakeBot:
        self_id = "99999"

        async def call_api(self, api: str, **data: object) -> dict[str, Any]:
            sent["api"] = api
            sent.update(data)
            sent["message"] = str(data["message"])
            return {"message_id": 1}

    async def fake_resolve_locale(group_id: str | None = None) -> str:
        assert group_id == "20001"
        return "zh-CN"

    monkeypatch.setattr("src.lib.i18n.runtime.resolve_locale", fake_resolve_locale)
    monkeypatch.setattr(
        message_asset_repo,
        "get_asset",
        AsyncMock(return_value=None),
    )

    await send_private_i18n(
        _FakeBot(),  # type: ignore[arg-type]
        42,
        "notice.invite.auto_reject",
        locale_group_id="20001",
        group_id="20001",
        group_name="测试群",
        inviter_id="10001",
        main_group_id="10086",
    )

    assert sent["api"] == "send_private_msg"
    assert sent["user_id"] == 42
    assert "群号：20001" in sent["message"]
    assert "测试群" in sent["message"]


@pytest.mark.asyncio
async def test_send_i18n_prefers_delivery_when_matcher_has_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = SimpleNamespace(
        bot=SimpleNamespace(self_id="99999", call_api=AsyncMock()),
        send=AsyncMock(),
        finish=AsyncMock(),
    )
    event = build_group_message_event("test")
    delivered: dict[str, Any] = {}

    async def fake_resolve_locale(group_id: str | None = None) -> str:
        assert group_id == "20001"
        return "zh-CN"

    async def fake_deliver_single_message(bot: object, **kwargs: Any) -> dict[str, Any]:
        delivered["bot"] = bot
        delivered.update(kwargs)
        return {"message_id": "1"}

    monkeypatch.setattr("src.lib.i18n.runtime.resolve_locale", fake_resolve_locale)
    monkeypatch.setattr(
        "src.lib.i18n.runtime.deliver_single_message",
        fake_deliver_single_message,
    )

    await send_i18n(
        cast(Any, matcher),
        event,
        "remove.leave_success",
        group_name="测试群",
    )

    assert delivered["target"] == DeliveryTarget(kind="group", target_id="20001")
    assert "测试群" in str(delivered["message"])
    matcher.send.assert_not_called()


@pytest.mark.asyncio
async def test_finish_i18n_prefers_delivery_when_matcher_has_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matcher = SimpleNamespace(
        bot=SimpleNamespace(self_id="99999", call_api=AsyncMock()),
        send=AsyncMock(),
        finish=AsyncMock(),
    )
    event = build_group_message_event("test")
    delivered: dict[str, Any] = {}

    async def fake_resolve_locale(group_id: str | None = None) -> str:
        assert group_id == "20001"
        return "zh-CN"

    async def fake_deliver_single_message(bot: object, **kwargs: Any) -> dict[str, Any]:
        delivered["bot"] = bot
        delivered.update(kwargs)
        return {"message_id": "1"}

    monkeypatch.setattr("src.lib.i18n.runtime.resolve_locale", fake_resolve_locale)
    monkeypatch.setattr(
        "src.lib.i18n.runtime.deliver_single_message",
        fake_deliver_single_message,
    )

    await finish_i18n(
        cast(Any, matcher),
        event,
        "remove.leave_success",
        group_name="测试群",
    )

    assert delivered["target"] == DeliveryTarget(kind="group", target_id="20001")
    assert "测试群" in str(delivered["message"])
    matcher.finish.assert_awaited_once_with()
