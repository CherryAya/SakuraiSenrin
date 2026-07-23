from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from nonebot.adapters.onebot.v11.bot import Bot

from src.config import config
from src.lib.message_delivery import DeliveryTarget
from src.lib.message_plan import DeliveryPlan, DeliveryPlanResult, deliver_message_plan
from src.logger import logger

AdminNotificationChannel = Literal["private_superuser", "group"]
AdminNotificationCallback = Callable[
    ["AdminNotificationTarget", DeliveryPlanResult], Awaitable[None] | None
]


@dataclass(slots=True, frozen=True)
class AdminNotificationTarget:
    channel: AdminNotificationChannel
    target_id: str

    @property
    def delivery_target(self) -> DeliveryTarget:
        kind: Literal["private", "group"] = (
            "private" if self.channel == "private_superuser" else "group"
        )
        return DeliveryTarget(kind=kind, target_id=self.target_id)


@dataclass(slots=True, frozen=True)
class AdminNotificationDelivery:
    target: AdminNotificationTarget
    plan_result: DeliveryPlanResult


def parse_admin_notification_group_ids(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()

    if isinstance(raw, (list, tuple, set, frozenset)):
        payload = raw
    else:
        raise ValueError("ADMIN_NOTIFY_GROUP_IDS must be an array-like value")

    group_ids: list[str] = []
    for item in payload:
        value = str(item).strip()
        if value and value not in group_ids:
            group_ids.append(value)
    return tuple(group_ids)


def resolve_admin_notification_targets() -> tuple[AdminNotificationTarget, ...]:
    targets: list[AdminNotificationTarget] = []
    if getattr(config, "ADMIN_NOTIFY_PRIVATE_ENABLED", True):
        for superuser_id in sorted(config.SUPERUSERS):
            targets.append(
                AdminNotificationTarget(
                    channel="private_superuser",
                    target_id=str(superuser_id),
                )
            )
    if getattr(config, "ADMIN_NOTIFY_GROUP_ENABLED", False):
        for group_id in parse_admin_notification_group_ids(
            getattr(config, "ADMIN_NOTIFY_GROUP_IDS", ())
        ):
            targets.append(
                AdminNotificationTarget(
                    channel="group",
                    target_id=group_id,
                )
            )
    return tuple(targets)


async def deliver_admin_notification_plan(
    bot: Bot,
    *,
    plan: DeliveryPlan,
    on_delivered: AdminNotificationCallback | None = None,
) -> tuple[AdminNotificationDelivery, ...]:
    targets = resolve_admin_notification_targets()
    if not targets:
        logger.warning(
            "[AdminNotify] no targets configured "
            f"source_kind={plan.source_kind or '-'}"
        )
        return ()

    deliveries: list[AdminNotificationDelivery] = []
    for target in targets:
        try:
            plan_result = await deliver_message_plan(
                bot,
                plan=plan,
                target=target.delivery_target,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "[AdminNotify] delivery failed "
                f"source_kind={plan.source_kind or '-'} "
                f"channel={target.channel} target_id={target.target_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            continue
        logger.debug(
            "[AdminNotify] delivered "
            f"source_kind={plan.source_kind or '-'} "
            f"channel={target.channel} target_id={target.target_id} "
            f"message_count={len(plan_result.results)} "
            f"used_forward={plan_result.used_forward}"
        )
        if on_delivered is not None:
            maybe_awaitable = on_delivered(target, plan_result)
            if maybe_awaitable is not None:
                await maybe_awaitable
        deliveries.append(
            AdminNotificationDelivery(
                target=target,
                plan_result=plan_result,
            )
        )
    return tuple(deliveries)


__all__ = [
    "AdminNotificationDelivery",
    "AdminNotificationTarget",
    "deliver_admin_notification_plan",
    "parse_admin_notification_group_ids",
    "resolve_admin_notification_targets",
]
