from __future__ import annotations

from typing import Any

import pytest

from src.lib.i18n.runtime import normalize_locale, send_private_i18n, tr
from src.repositories.i18n import I18nRepository


def test_normalize_locale_aliases() -> None:
    assert normalize_locale("zh") == "zh-CN"
    assert normalize_locale("WYW") == "lzh"
    assert normalize_locale("抽象") == "x-meme"
    assert normalize_locale("unknown") is None


def test_tr_falls_back_to_zh_cn_for_partial_catalog() -> None:
    assert tr("x-meme", "admin.group.banned") == "已封禁"


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
        async def send_private_msg(
            self,
            *,
            user_id: int,
            message: object,
        ) -> dict[str, Any]:
            sent["user_id"] = user_id
            sent["message"] = str(message)
            return {"message_id": 1}

    async def fake_resolve_locale(group_id: str | None = None) -> str:
        assert group_id == "20001"
        return "zh-CN"

    monkeypatch.setattr("src.lib.i18n.runtime.resolve_locale", fake_resolve_locale)

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

    assert sent["user_id"] == 42
    assert "群号：20001" in sent["message"]
    assert "测试群" in sent["message"]
