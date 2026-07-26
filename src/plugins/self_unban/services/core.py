from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from src.database.core.consts import GroupStatus
from src.database.core.ops import BlacklistOps
from src.database.instances import core_db
from src.lib.admin_notifications import deliver_admin_notification_plan
from src.lib.cache.field import BlacklistCacheItem
from src.lib.consts import GLOBAL_GROUP_FLAG, PERMANENT_BAN_FLAG
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.message_plan import DeliveryPlan
from src.lib.utils.common import get_current_time
from src.plugins.self_unban.database import self_unban_repo
from src.repositories import blacklist_repo, group_repo
from src.services.info import resolve_group_name

MAX_SELF_UNBAN_ATTEMPTS = 2
MIN_REASON_LENGTH = 10

SelfUnbanKind = Literal["user", "group"]


@dataclass(slots=True, frozen=True)
class PreparedSelfUnbanRequest:
    kind: SelfUnbanKind
    subject_type: str
    subject_id: str
    scope_group_id: str
    requester_user_id: str
    remaining_attempts_before: int
    locale: LocaleCode
    source_hint: str

    @property
    def lock_key(self) -> str:
        return f"{self.subject_type}:{self.subject_id}"


@dataclass(slots=True, frozen=True)
class SelfUnbanSubmissionResult:
    final_message: str
    should_retry: bool = False


class SelfUnbanService:
    def __init__(self) -> None:
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._subject_locks: dict[str, asyncio.Lock] = {}

    async def ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self_unban_repo.init_all_tables()
            self._initialized = True

    async def reset_runtime_state(self) -> None:
        self._initialized = False
        self._subject_locks.clear()

    def _get_subject_lock(self, key: str) -> asyncio.Lock:
        lock = self._subject_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._subject_locks[key] = lock
        return lock

    @staticmethod
    def _normalize_reason(reason: str) -> str:
        return " ".join(reason.split()).strip()

    @staticmethod
    def _is_active_blacklist(item: BlacklistCacheItem | None) -> bool:
        if item is None:
            return False
        if item.expiry == PERMANENT_BAN_FLAG:
            return True
        return int(item.expiry) > get_current_time()

    async def _get_active_blacklist(
        self,
        *,
        user_id: str,
        group_id: str,
    ) -> BlacklistCacheItem | None:
        item = await blacklist_repo.get_blacklist(user_id, group_id)
        if self._is_active_blacklist(item):
            return item
        return None

    async def prepare_user_request(
        self,
        *,
        requester_user_id: str,
        scope_group_id: str,
        locale: LocaleCode,
        source_hint: str,
    ) -> PreparedSelfUnbanRequest | str:
        await self.ensure_initialized()
        blacklist = await self._get_active_blacklist(
            user_id=requester_user_id,
            group_id=scope_group_id,
        )
        global_blacklist = await self._get_active_blacklist(
            user_id=requester_user_id,
            group_id=GLOBAL_GROUP_FLAG,
        )
        if blacklist is None:
            if scope_group_id == GLOBAL_GROUP_FLAG:
                if source_hint == "private_global" and await self._has_any_group_ban(
                    requester_user_id
                ):
                    return tr(
                        locale,
                        "self_unban.user.not_banned_global_but_group",
                    )
                return tr(locale, "self_unban.user.not_banned_global")
            if global_blacklist is not None:
                return tr(
                    locale,
                    "self_unban.user.not_banned_group_but_global",
                    group_id=scope_group_id,
                )
            return tr(
                locale,
                "self_unban.user.not_banned_group",
                group_id=scope_group_id,
            )

        consumed = await self_unban_repo.count_consumed_attempts(
            subject_type="user",
            subject_id=requester_user_id,
        )
        remaining = MAX_SELF_UNBAN_ATTEMPTS - consumed
        if remaining <= 0:
            return tr(
                locale,
                "self_unban.limit.user",
                limit=MAX_SELF_UNBAN_ATTEMPTS,
            )
        return PreparedSelfUnbanRequest(
            kind="user",
            subject_type="user",
            subject_id=requester_user_id,
            scope_group_id=scope_group_id,
            requester_user_id=requester_user_id,
            remaining_attempts_before=remaining,
            locale=locale,
            source_hint=source_hint,
        )

    async def prepare_group_request(
        self,
        *,
        event: GroupMessageEvent,
        locale: LocaleCode,
    ) -> PreparedSelfUnbanRequest | str:
        await self.ensure_initialized()
        if getattr(event.sender, "role", "") not in {"admin", "owner"}:
            return tr(locale, "self_unban.group.permission_denied")

        group_id = str(event.group_id)
        group = await group_repo.get_group(group_id)
        if group is None:
            return tr(locale, "self_unban.group.cache_miss", group_id=group_id)
        if not group.status.is_banned:
            return tr(locale, "self_unban.group.not_banned")

        consumed = await self_unban_repo.count_consumed_attempts(
            subject_type="group",
            subject_id=group_id,
        )
        remaining = MAX_SELF_UNBAN_ATTEMPTS - consumed
        if remaining <= 0:
            return tr(
                locale,
                "self_unban.limit.group",
                limit=MAX_SELF_UNBAN_ATTEMPTS,
            )
        return PreparedSelfUnbanRequest(
            kind="group",
            subject_type="group",
            subject_id=group_id,
            scope_group_id=group_id,
            requester_user_id=str(event.user_id),
            remaining_attempts_before=remaining,
            locale=locale,
            source_hint="group",
        )

    async def submit_request(
        self,
        bot: Bot,
        *,
        prepared: PreparedSelfUnbanRequest,
        reason: str,
    ) -> SelfUnbanSubmissionResult:
        await self.ensure_initialized()
        normalized_reason = self._normalize_reason(reason)
        if len(normalized_reason) < MIN_REASON_LENGTH:
            return SelfUnbanSubmissionResult(
                final_message=tr(
                    prepared.locale,
                    "self_unban.reason_too_short",
                    min_length=MIN_REASON_LENGTH,
                ),
                should_retry=True,
            )

        async with self._get_subject_lock(prepared.lock_key):
            consumed = await self_unban_repo.count_consumed_attempts(
                subject_type=prepared.subject_type,
                subject_id=prepared.subject_id,
            )
            if consumed >= MAX_SELF_UNBAN_ATTEMPTS:
                await self_unban_repo.create_attempt(
                    subject_type=prepared.subject_type,
                    subject_id=prepared.subject_id,
                    scope_group_id=prepared.scope_group_id,
                    requester_user_id=prepared.requester_user_id,
                    reason=normalized_reason,
                    result="rejected_limit",
                    consumes_quota=False,
                )
                return SelfUnbanSubmissionResult(
                    final_message=tr(
                        prepared.locale,
                        (
                            "self_unban.limit.user"
                            if prepared.kind == "user"
                            else "self_unban.limit.group"
                        ),
                        limit=MAX_SELF_UNBAN_ATTEMPTS,
                    )
                )

            if prepared.kind == "user":
                return await self._submit_user_request(
                    bot,
                    prepared=prepared,
                    reason=normalized_reason,
                    consumed_before=consumed,
                )
            return await self._submit_group_request(
                bot,
                prepared=prepared,
                reason=normalized_reason,
                consumed_before=consumed,
            )

    async def _submit_user_request(
        self,
        bot: Bot,
        *,
        prepared: PreparedSelfUnbanRequest,
        reason: str,
        consumed_before: int,
    ) -> SelfUnbanSubmissionResult:
        blacklist = await self._get_active_blacklist(
            user_id=prepared.subject_id,
            group_id=prepared.scope_group_id,
        )
        global_blacklist = await self._get_active_blacklist(
            user_id=prepared.subject_id,
            group_id=GLOBAL_GROUP_FLAG,
        )
        if blacklist is None:
            if prepared.scope_group_id == GLOBAL_GROUP_FLAG:
                return SelfUnbanSubmissionResult(
                    final_message=tr(
                        prepared.locale,
                        "self_unban.user.not_banned_global",
                    )
                )
            if global_blacklist is not None:
                return SelfUnbanSubmissionResult(
                    final_message=tr(
                        prepared.locale,
                        "self_unban.user.not_banned_group_but_global",
                        group_id=prepared.scope_group_id,
                    )
                )
            return SelfUnbanSubmissionResult(
                final_message=tr(
                    prepared.locale,
                    "self_unban.user.not_banned_group",
                    group_id=prepared.scope_group_id,
                )
            )

        await blacklist_repo.set_unban(
            prepared.subject_id,
            prepared.scope_group_id,
            prepared.requester_user_id,
        )
        await self_unban_repo.create_attempt(
            subject_type=prepared.subject_type,
            subject_id=prepared.subject_id,
            scope_group_id=prepared.scope_group_id,
            requester_user_id=prepared.requester_user_id,
            reason=reason,
            result="approved",
            consumes_quota=True,
        )
        used_count = consumed_before + 1
        remaining = max(0, MAX_SELF_UNBAN_ATTEMPTS - used_count)
        still_global_banned = (
            prepared.scope_group_id != GLOBAL_GROUP_FLAG
            and await self._get_active_blacklist(
                user_id=prepared.subject_id,
                group_id=GLOBAL_GROUP_FLAG,
            )
            is not None
        )
        await self._notify_user_success(
            bot,
            prepared=prepared,
            reason=reason,
            used_count=used_count,
            remaining=remaining,
            still_global_banned=still_global_banned,
        )
        if prepared.scope_group_id == GLOBAL_GROUP_FLAG:
            return SelfUnbanSubmissionResult(
                final_message=tr(
                    prepared.locale,
                    "self_unban.user.success_global",
                    used=used_count,
                    limit=MAX_SELF_UNBAN_ATTEMPTS,
                    remaining=remaining,
                )
            )
        if still_global_banned:
            return SelfUnbanSubmissionResult(
                final_message=tr(
                    prepared.locale,
                    "self_unban.user.success_group_still_global",
                    group_id=prepared.scope_group_id,
                    used=used_count,
                    limit=MAX_SELF_UNBAN_ATTEMPTS,
                    remaining=remaining,
                )
            )
        return SelfUnbanSubmissionResult(
            final_message=tr(
                prepared.locale,
                "self_unban.user.success_group",
                group_id=prepared.scope_group_id,
                used=used_count,
                limit=MAX_SELF_UNBAN_ATTEMPTS,
                remaining=remaining,
            )
        )

    async def _submit_group_request(
        self,
        bot: Bot,
        *,
        prepared: PreparedSelfUnbanRequest,
        reason: str,
        consumed_before: int,
    ) -> SelfUnbanSubmissionResult:
        group = await group_repo.get_group(prepared.subject_id)
        if group is None:
            return SelfUnbanSubmissionResult(
                final_message=tr(
                    prepared.locale,
                    "self_unban.group.cache_miss",
                    group_id=prepared.subject_id,
                )
            )
        if not group.status.is_banned:
            return SelfUnbanSubmissionResult(
                final_message=tr(prepared.locale, "self_unban.group.not_banned")
            )

        await group_repo.update_status(prepared.subject_id, GroupStatus.UNAUTHORIZED)
        await self_unban_repo.create_attempt(
            subject_type=prepared.subject_type,
            subject_id=prepared.subject_id,
            scope_group_id=prepared.scope_group_id,
            requester_user_id=prepared.requester_user_id,
            reason=reason,
            result="approved",
            consumes_quota=True,
        )
        used_count = consumed_before + 1
        remaining = max(0, MAX_SELF_UNBAN_ATTEMPTS - used_count)
        await self._notify_group_success(
            bot,
            prepared=prepared,
            reason=reason,
            used_count=used_count,
            remaining=remaining,
        )
        return SelfUnbanSubmissionResult(
            final_message=tr(
                prepared.locale,
                "self_unban.group.success",
                used=used_count,
                limit=MAX_SELF_UNBAN_ATTEMPTS,
                remaining=remaining,
            )
        )

    async def _notify_user_success(
        self,
        bot: Bot,
        *,
        prepared: PreparedSelfUnbanRequest,
        reason: str,
        used_count: int,
        remaining: int,
        still_global_banned: bool,
    ) -> None:
        scope_text = (
            "GLOBAL"
            if prepared.scope_group_id == GLOBAL_GROUP_FLAG
            else f"group:{prepared.scope_group_id}"
        )
        note = (
            tr(prepared.locale, "self_unban.note.global_still_active")
            if still_global_banned
            else "-"
        )
        await deliver_admin_notification_plan(
            bot,
            plan=DeliveryPlan(
                messages=(
                    tr(
                        prepared.locale,
                        "self_unban.admin_report",
                        subject_kind=tr(
                            prepared.locale, "self_unban.subject_kind.user"
                        ),
                        subject_id=prepared.subject_id,
                        scope=scope_text,
                        requester_user_id=prepared.requester_user_id,
                        reason=reason,
                        used=used_count,
                        limit=MAX_SELF_UNBAN_ATTEMPTS,
                        remaining=remaining,
                        note=note,
                    ),
                ),
                source_kind="self_unban_admin_report_user",
                allow_asset_reuse=False,
            ),
        )

    async def _notify_group_success(
        self,
        bot: Bot,
        *,
        prepared: PreparedSelfUnbanRequest,
        reason: str,
        used_count: int,
        remaining: int,
    ) -> None:
        group_name = await resolve_group_name(bot, prepared.subject_id)
        await deliver_admin_notification_plan(
            bot,
            plan=DeliveryPlan(
                messages=(
                    tr(
                        prepared.locale,
                        "self_unban.admin_report",
                        subject_kind=tr(
                            prepared.locale, "self_unban.subject_kind.group"
                        ),
                        subject_id=f"{prepared.subject_id} ({group_name})",
                        scope=f"group:{prepared.subject_id}",
                        requester_user_id=prepared.requester_user_id,
                        reason=reason,
                        used=used_count,
                        limit=MAX_SELF_UNBAN_ATTEMPTS,
                        remaining=remaining,
                        note=tr(
                            prepared.locale,
                            "self_unban.note.group_back_to_unauthorized",
                        ),
                    ),
                ),
                source_kind="self_unban_admin_report_group",
                allow_asset_reuse=False,
            ),
        )

    async def _has_any_group_ban(self, user_id: str) -> bool:
        async with core_db.session() as session:
            rows = await BlacklistOps(session).get_by_uid(user_id)
        for row in rows:
            if row.group_id == GLOBAL_GROUP_FLAG:
                continue
            if (
                row.ban_expiry == PERMANENT_BAN_FLAG
                or row.ban_expiry > get_current_time()
            ):
                return True
        return False


self_unban_service = SelfUnbanService()

__all__ = [
    "MAX_SELF_UNBAN_ATTEMPTS",
    "MIN_REASON_LENGTH",
    "PreparedSelfUnbanRequest",
    "SelfUnbanSubmissionResult",
    "self_unban_service",
]
