from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.lib.admin_notifications import (
    deliver_admin_notification_plan,
    parse_admin_notification_group_ids,
    resolve_admin_notification_targets,
)
from src.lib.message_plan import DeliveryPlan


def test_parse_admin_notification_group_ids_dedupes_and_trims() -> None:
    assert parse_admin_notification_group_ids(["10001", " 10002 ", 10001, ""]) == (
        "10001",
        "10002",
    )


def test_parse_admin_notification_group_ids_supports_legacy_json_string() -> None:
    assert parse_admin_notification_group_ids('["10001", "10002"]') == (
        "10001",
        "10002",
    )


def test_parse_admin_notification_group_ids_rejects_non_array_json() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        parse_admin_notification_group_ids('{"group_id":"10001"}')


def test_resolve_admin_notification_targets_supports_private_and_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib import admin_notifications as notify_module

    monkeypatch.setattr(notify_module.config, "SUPERUSERS", {"2", "1"})
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_PRIVATE_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_IDS",
        ("20001", "20002"),
        raising=False,
    )

    targets = resolve_admin_notification_targets()

    assert [(item.channel, item.target_id) for item in targets] == [
        ("private_superuser", "1"),
        ("private_superuser", "2"),
        ("group", "20001"),
        ("group", "20002"),
    ]


@pytest.mark.asyncio
async def test_deliver_admin_notification_plan_invokes_callback_for_each_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib import admin_notifications as notify_module

    monkeypatch.setattr(notify_module.config, "SUPERUSERS", {"1"})
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_PRIVATE_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_IDS",
        ("20001",),
        raising=False,
    )
    deliver_mock = AsyncMock(
        side_effect=[
            SimpleNamespace(
                results=(SimpleNamespace(message_id="10"),),
                used_forward=False,
            ),
            SimpleNamespace(
                results=(SimpleNamespace(message_id="20"),),
                used_forward=False,
            ),
        ]
    )
    monkeypatch.setattr(notify_module, "deliver_message_plan", deliver_mock)
    callback = AsyncMock()
    bot = cast(Any, SimpleNamespace())

    deliveries = await deliver_admin_notification_plan(
        bot,
        plan=DeliveryPlan(messages=("hello",), source_kind="test_notify"),
        on_delivered=callback,
    )

    assert len(deliveries) == 2
    targets = [call.kwargs["target"].target_id for call in deliver_mock.await_args_list]
    assert targets == ["1", "20001"]
    assert callback.await_count == 2


def test_resolve_admin_notification_targets_falls_back_to_legacy_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib import admin_notifications as notify_module

    monkeypatch.setattr(notify_module.config, "SUPERUSERS", set())
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_PRIVATE_ENABLED",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_IDS",
        (),
        raising=False,
    )
    monkeypatch.setattr(
        notify_module.config,
        "ADMIN_NOTIFY_GROUP_IDS_JSON",
        '["30001", "30002"]',
        raising=False,
    )

    targets = resolve_admin_notification_targets()

    assert [(item.channel, item.target_id) for item in targets] == [
        ("group", "30001"),
        ("group", "30002"),
    ]
