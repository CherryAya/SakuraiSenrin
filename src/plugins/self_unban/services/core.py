from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from nonebot.adapters.onebot.v11.bot import Bot

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
from src.repositories import blacklist_repo, group_repo, member_repo
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
    user_remaining_attempts_before: int
    locale: LocaleCode
    source_hint: str
    group_remaining_attempts_before: int = 0
    target_group_name: str = ""

    @property
    def lock_keys(self) -> tuple[str, ...]:
        keys = [f"user:{self.requester_user_id}"]
        if self.kind == "group":
            keys.append(f"group:{self.subject_id}")
        return tuple(keys)


@dataclass(slots=True, frozen=True)
class ManagedBannedGroupOption:
    index: int
    group_id: str
    group_name: str
    prepared: PreparedSelfUnbanRequest


@dataclass(slots=True, frozen=True)
class SelfUnbanSelectionSession:
    requester_user_id: str
    locale: LocaleCode
    user_candidate: PreparedSelfUnbanRequest | None
    group_candidates: tuple[ManagedBannedGroupOption, ...]


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

    @asynccontextmanager
    async def _acquire_subject_locks(
        self,
        keys: tuple[str, ...],
    ) -> AsyncIterator[None]:
        unique_keys = sorted(set(keys))
        locks = [self._get_subject_lock(key) for key in unique_keys]
        for lock in locks:
            await lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

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

    async def count_user_consumed_attempts(self, requester_user_id: str) -> int:
        await self.ensure_initialized()
        return await self_unban_repo.count_user_consumed_attempts(requester_user_id)

    async def count_group_consumed_attempts(self, group_id: str) -> int:
        await self.ensure_initialized()
        return await self_unban_repo.count_consumed_attempts(
            subject_type="group",
            subject_id=group_id,
        )

    async def prepare_selection_session(
        self,
        *,
        requester_user_id: str,
        locale: LocaleCode,
        current_group_id: str | None = None,
    ) -> SelfUnbanSelectionSession | str:
        await self.ensure_initialized()
        user_consumed = await self.count_user_consumed_attempts(requester_user_id)
        user_remaining = MAX_SELF_UNBAN_ATTEMPTS - user_consumed
        has_self_target = await self._has_user_target(
            requester_user_id=requester_user_id,
            current_group_id=current_group_id,
        )
        user_candidate = await self._build_user_candidate(
            requester_user_id=requester_user_id,
            locale=locale,
            current_group_id=current_group_id,
            user_remaining=user_remaining,
        )
        managed_banned_groups = await self._list_managed_banned_groups(
            requester_user_id
        )

        if user_remaining <= 0:
            if has_self_target or managed_banned_groups:
                return tr(
                    locale,
                    "self_unban.limit.user",
                    limit=MAX_SELF_UNBAN_ATTEMPTS,
                )
            return tr(locale, "self_unban.no_targets")

        group_candidates = await self._build_group_candidates(
            requester_user_id=requester_user_id,
            locale=locale,
            user_remaining=user_remaining,
            managed_groups=managed_banned_groups,
        )
        if user_candidate is None and not group_candidates:
            if managed_banned_groups:
                return tr(locale, "self_unban.no_groups_available")
            return tr(locale, "self_unban.no_targets")

        return SelfUnbanSelectionSession(
            requester_user_id=requester_user_id,
            locale=locale,
            user_candidate=user_candidate,
            group_candidates=group_candidates,
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

        async with self._acquire_subject_locks(prepared.lock_keys):
            user_consumed = await self.count_user_consumed_attempts(
                prepared.requester_user_id
            )
            if user_consumed >= MAX_SELF_UNBAN_ATTEMPTS:
                await self_unban_repo.create_attempt(
                    subject_type="user",
                    subject_id=prepared.requester_user_id,
                    scope_group_id=prepared.scope_group_id,
                    requester_user_id=prepared.requester_user_id,
                    reason=normalized_reason,
                    result="rejected_limit_user",
                    consumes_quota=False,
                )
                return SelfUnbanSubmissionResult(
                    final_message=tr(
                        prepared.locale,
                        "self_unban.limit.user",
                        limit=MAX_SELF_UNBAN_ATTEMPTS,
                    )
                )

            if prepared.kind == "group":
                group_consumed = await self.count_group_consumed_attempts(
                    prepared.subject_id
                )
                if group_consumed >= MAX_SELF_UNBAN_ATTEMPTS:
                    await self_unban_repo.create_attempt(
                        subject_type="group",
                        subject_id=prepared.subject_id,
                        scope_group_id=prepared.scope_group_id,
                        requester_user_id=prepared.requester_user_id,
                        reason=normalized_reason,
                        result="rejected_limit_group",
                        consumes_quota=False,
                    )
                    return SelfUnbanSubmissionResult(
                        final_message=tr(
                            prepared.locale,
                            "self_unban.limit.group",
                            limit=MAX_SELF_UNBAN_ATTEMPTS,
                        )
                    )
                return await self._submit_group_request(
                    bot,
                    prepared=prepared,
                    reason=normalized_reason,
                    user_consumed_before=user_consumed,
                    group_consumed_before=group_consumed,
                )

            return await self._submit_user_request(
                bot,
                prepared=prepared,
                reason=normalized_reason,
                user_consumed_before=user_consumed,
            )

    async def _build_user_candidate(
        self,
        *,
        requester_user_id: str,
        locale: LocaleCode,
        current_group_id: str | None,
        user_remaining: int,
    ) -> PreparedSelfUnbanRequest | None:
        if user_remaining <= 0:
            return None
        global_blacklist = await self._get_active_blacklist(
            user_id=requester_user_id,
            group_id=GLOBAL_GROUP_FLAG,
        )
        if global_blacklist is not None:
            return PreparedSelfUnbanRequest(
                kind="user",
                subject_type="user",
                subject_id=requester_user_id,
                scope_group_id=GLOBAL_GROUP_FLAG,
                requester_user_id=requester_user_id,
                user_remaining_attempts_before=user_remaining,
                locale=locale,
                source_hint="private_global",
            )

        if not current_group_id:
            return None
        group_blacklist = await self._get_active_blacklist(
            user_id=requester_user_id,
            group_id=current_group_id,
        )
        if group_blacklist is None:
            return None
        return PreparedSelfUnbanRequest(
            kind="user",
            subject_type="user",
            subject_id=requester_user_id,
            scope_group_id=current_group_id,
            requester_user_id=requester_user_id,
            user_remaining_attempts_before=user_remaining,
            locale=locale,
            source_hint="group_scope",
        )

    async def _has_user_target(
        self,
        *,
        requester_user_id: str,
        current_group_id: str | None,
    ) -> bool:
        if (
            await self._get_active_blacklist(
                user_id=requester_user_id,
                group_id=GLOBAL_GROUP_FLAG,
            )
            is not None
        ):
            return True
        if not current_group_id:
            return False
        return (
            await self._get_active_blacklist(
                user_id=requester_user_id,
                group_id=current_group_id,
            )
            is not None
        )

    async def _list_managed_banned_groups(
        self,
        requester_user_id: str,
    ) -> list[tuple[str, str]]:
        members = await member_repo.get_admin_member_by_uid(requester_user_id)
        unique_group_ids = list(
            dict.fromkeys(str(member.group_id) for member in members)
        )
        results: list[tuple[str, str]] = []
        for member in members:
            group_id = str(member.group_id)
            if group_id not in unique_group_ids:
                continue
            group = await group_repo.get_group(group_id)
            if group is None or not group.status.is_banned:
                continue
            group_name = group.display_name or member.group.group_name or group_id
            results.append((group_id, group_name))
            unique_group_ids.remove(group_id)
        return sorted(results, key=lambda item: (item[1], item[0]))

    async def _build_group_candidates(
        self,
        *,
        requester_user_id: str,
        locale: LocaleCode,
        user_remaining: int,
        managed_groups: list[tuple[str, str]],
    ) -> tuple[ManagedBannedGroupOption, ...]:
        options: list[ManagedBannedGroupOption] = []
        for index, (group_id, group_name) in enumerate(managed_groups, start=1):
            group_consumed = await self.count_group_consumed_attempts(group_id)
            group_remaining = MAX_SELF_UNBAN_ATTEMPTS - group_consumed
            if group_remaining <= 0:
                continue
            prepared = PreparedSelfUnbanRequest(
                kind="group",
                subject_type="group",
                subject_id=group_id,
                scope_group_id=group_id,
                requester_user_id=requester_user_id,
                user_remaining_attempts_before=user_remaining,
                group_remaining_attempts_before=group_remaining,
                locale=locale,
                source_hint="managed_group",
                target_group_name=group_name,
            )
            options.append(
                ManagedBannedGroupOption(
                    index=index,
                    group_id=group_id,
                    group_name=group_name,
                    prepared=prepared,
                )
            )
        return tuple(options)

    async def _submit_user_request(
        self,
        bot: Bot,
        *,
        prepared: PreparedSelfUnbanRequest,
        reason: str,
        user_consumed_before: int,
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
            subject_type="user",
            subject_id=prepared.requester_user_id,
            scope_group_id=prepared.scope_group_id,
            requester_user_id=prepared.requester_user_id,
            reason=reason,
            result="approved",
            consumes_quota=True,
        )
        used_count = user_consumed_before + 1
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
        user_consumed_before: int,
        group_consumed_before: int,
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

        restore_result = await group_repo.restore_pre_ban_status(prepared.subject_id)
        if restore_result is None:
            return SelfUnbanSubmissionResult(
                final_message=tr(
                    prepared.locale,
                    "self_unban.group.cache_miss",
                    group_id=prepared.subject_id,
                )
            )
        await self_unban_repo.create_attempt(
            subject_type="user",
            subject_id=prepared.requester_user_id,
            scope_group_id=prepared.scope_group_id,
            requester_user_id=prepared.requester_user_id,
            reason=reason,
            result="approved",
            consumes_quota=True,
        )
        await self_unban_repo.create_attempt(
            subject_type="group",
            subject_id=prepared.subject_id,
            scope_group_id=prepared.scope_group_id,
            requester_user_id=prepared.requester_user_id,
            reason=reason,
            result="approved_group_quota",
            consumes_quota=True,
        )
        user_used_count = user_consumed_before + 1
        user_remaining = max(0, MAX_SELF_UNBAN_ATTEMPTS - user_used_count)
        group_used_count = group_consumed_before + 1
        group_remaining = max(0, MAX_SELF_UNBAN_ATTEMPTS - group_used_count)
        await self._notify_group_success(
            bot,
            prepared=prepared,
            reason=reason,
            user_used_count=user_used_count,
            user_remaining=user_remaining,
            group_used_count=group_used_count,
            group_remaining=group_remaining,
            restored_status=restore_result.restored_status,
            used_fallback=restore_result.used_fallback,
        )
        message_key = (
            "self_unban.group.success_fallback"
            if restore_result.used_fallback
            else "self_unban.group.success"
        )
        return SelfUnbanSubmissionResult(
            final_message=tr(
                prepared.locale,
                message_key,
                group_name=prepared.target_group_name or prepared.subject_id,
                group_id=prepared.subject_id,
                status=restore_result.restored_status,
                user_used=user_used_count,
                limit=MAX_SELF_UNBAN_ATTEMPTS,
                user_remaining=user_remaining,
                group_used=group_used_count,
                group_remaining=group_remaining,
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
        user_used_count: int,
        user_remaining: int,
        group_used_count: int,
        group_remaining: int,
        restored_status: str,
        used_fallback: bool,
    ) -> None:
        group_name = prepared.target_group_name or await resolve_group_name(
            bot,
            prepared.subject_id,
        )
        note_key = (
            "self_unban.note.group_restored_fallback"
            if used_fallback
            else "self_unban.note.group_restored"
        )
        await deliver_admin_notification_plan(
            bot,
            plan=DeliveryPlan(
                messages=(
                    tr(
                        prepared.locale,
                        "self_unban.admin_report_group",
                        subject_kind=tr(
                            prepared.locale, "self_unban.subject_kind.group"
                        ),
                        subject_id=f"{prepared.subject_id} ({group_name})",
                        scope=f"group:{prepared.subject_id}",
                        requester_user_id=prepared.requester_user_id,
                        reason=reason,
                        user_used=user_used_count,
                        limit=MAX_SELF_UNBAN_ATTEMPTS,
                        user_remaining=user_remaining,
                        group_used=group_used_count,
                        group_remaining=group_remaining,
                        note=tr(
                            prepared.locale,
                            note_key,
                            status=restored_status,
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
    "ManagedBannedGroupOption",
    "PreparedSelfUnbanRequest",
    "SelfUnbanSelectionSession",
    "SelfUnbanSubmissionResult",
    "self_unban_service",
]
