"""High-level wordbank service."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
import random
from typing import Any

from src.database.consts import WritePolicy
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.types import (
    WordbankApprovalMessageRecord,
    WordbankGroupDetail,
    WordbankLogPayload,
    WordbankResponseItemRecord,
    WordbankResponseMessageRecord,
    WordbankSearchItem,
    WordbankSearchPage,
    WordbankSearchRequest,
)
from src.plugins.wordbank.message_model import (
    MessageShape,
    fingerprint_shape,
    shape_to_summary_text,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.matching import (
    MatchCandidate,
    RuntimeIndex,
    SelectedMatch,
)
from src.plugins.wordbank.services.rules import RuleContext, canonicalize_rule


@dataclass(slots=True, frozen=True)
class WordbankAddResult:
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    trigger_text: str
    response_text: str
    trigger_mode: str
    scope: str
    probability: float
    weight: int
    status: str = "pending"
    created_group: bool = False
    trigger_shape: MessageShape | None = None
    response_shape: MessageShape | None = None


@dataclass(slots=True, frozen=True)
class WordbankDeleteVoteResult:
    vote_id: int
    trigger_group_id: int
    response_item_id: int
    status: str
    support_count: int
    threshold: int
    created: bool
    already_supported: bool
    passed: bool
    response_item_deleted: bool


class WordbankService:
    def __init__(
        self,
        repository: WordbankRepository,
        *,
        debounce_seconds: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        self.repository = repository
        self.debounce_seconds = debounce_seconds
        self.rng = rng or random.Random()
        self._index = RuntimeIndex()
        self._initialized = False
        self._rebuild_task: asyncio.Task[None] | None = None
        self._dirty_group_ids: set[int] = set()
        self._call_history: dict[int, deque[int]] = defaultdict(deque)

    @property
    def index(self) -> RuntimeIndex:
        return self._index

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.repository.init_all_tables()
        await self.repository.ensure_search_index()
        await self.rebuild_index()
        self._initialized = True

    async def rebuild_index(self) -> None:
        records = await self.repository.list_enabled_entries()
        self._index = RuntimeIndex.build(records)

    def mark_dirty(self, trigger_group_id: int | None = None) -> None:
        if trigger_group_id is None:
            self._dirty_group_ids.clear()
        else:
            self._dirty_group_ids.add(trigger_group_id)
        if self._rebuild_task is not None and not self._rebuild_task.done():
            self._rebuild_task.cancel()
        self._rebuild_task = asyncio.create_task(self._debounced_rebuild())

    async def _debounced_rebuild(self) -> None:
        await asyncio.sleep(self.debounce_seconds)
        if not self._dirty_group_ids:
            await self.rebuild_index()
            return
        dirty_group_ids = tuple(self._dirty_group_ids)
        self._dirty_group_ids.clear()
        for trigger_group_id in dirty_group_ids:
            await self._refresh_runtime_group(trigger_group_id)

    async def _refresh_runtime_group(self, trigger_group_id: int) -> None:
        current_group = self._index.groups.pop(trigger_group_id, None)
        if current_group is not None:
            for variants in self._index.exact_match.values():
                variants[:] = [
                    variant
                    for variant in variants
                    if variant.trigger_group_id != trigger_group_id
                ]
        record = await self.repository.get_trigger_group_record(
            trigger_group_id,
            include_deleted=False,
            active_only=True,
        )
        if (
            record is None
            or record.status != "approved"
            or record.enabled != 1
            or not record.responses
        ):
            empty_keys = [
                key for key, variants in self._index.exact_match.items() if not variants
            ]
            for key in empty_keys:
                self._index.exact_match.pop(key, None)
            return
        refreshed = RuntimeIndex.build([record])
        if trigger_group_id in refreshed.groups:
            self._index.groups[trigger_group_id] = refreshed.groups[trigger_group_id]
        for key, variants in refreshed.exact_match.items():
            self._index.exact_match.setdefault(key, []).extend(variants)

    async def add_message_entry(
        self,
        *,
        trigger_shape: MessageShape,
        response_shape: MessageShape,
        group_id: str,
        user_id: str,
        is_group: bool,
        raw_rule: dict[str, Any] | None = None,
        trigger_mode: str = "strict",
    ) -> WordbankAddResult:
        if trigger_shape.is_empty():
            raise WordbankUserError(
                "触发词不能为空", key="wordbank.error.trigger_empty"
            )
        if response_shape.is_empty():
            raise WordbankUserError(
                "响应词不能为空", key="wordbank.error.response_empty"
            )
        rule = canonicalize_rule(
            raw_rule,
            is_group=is_group,
            short_trigger=False,
        )
        created = await self.repository.create_or_append_response(
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            trigger_mode=trigger_mode,
            rule=dict(rule.rule),
            scope=rule.scope,
            priority=rule.priority,
            probability=rule.probability,
            weight=rule.weight,
            group_id=group_id if is_group else "",
            created_by=user_id,
        )
        self.mark_dirty(created.trigger_group_id)
        return WordbankAddResult(
            trigger_group_id=created.trigger_group_id,
            trigger_variant_id=created.trigger_variant_id,
            response_item_id=created.response_item_id,
            status=created.status,
            created_group=created.created_group,
            trigger_text=shape_to_summary_text(trigger_shape),
            response_text=shape_to_summary_text(response_shape),
            trigger_mode=trigger_mode,
            scope=rule.scope,
            probability=rule.probability,
            weight=rule.weight,
            trigger_shape=trigger_shape,
            response_shape=response_shape,
        )

    async def delete_response_item(
        self,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        response_item = await self._get_response_item_for_mutation(response_item_id)
        ok = await self.repository.delete_response_item(
            response_item_id,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and response_item is not None:
            self.mark_dirty(response_item.trigger_group_id)
        return ok

    async def restore_response_item(
        self,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        response_item = await self._get_response_item_for_mutation(response_item_id)
        ok = await self.repository.restore_response_item(
            response_item_id,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and response_item is not None:
            self.mark_dirty(response_item.trigger_group_id)
        return ok

    async def approve_response_item(
        self,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        response_item = await self._get_response_item_for_mutation(response_item_id)
        ok = await self.repository.approve_response_item(
            response_item_id,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and response_item is not None:
            self.mark_dirty(response_item.trigger_group_id)
        return ok

    async def reject_response_item(
        self,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        response_item = await self._get_response_item_for_mutation(response_item_id)
        ok = await self.repository.reject_response_item(
            response_item_id,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and response_item is not None:
            self.mark_dirty(response_item.trigger_group_id)
        return ok

    async def request_delete_vote(
        self,
        *,
        response_item_id: int,
        group_id: str,
        user_id: str,
        threshold: int = 3,
        reason: str = "",
    ) -> WordbankDeleteVoteResult | None:
        mutation = await self.repository.request_delete_vote(
            response_item_id=response_item_id,
            group_id=group_id,
            user_id=user_id,
            threshold=threshold,
            reason=reason,
        )
        if mutation is None:
            return None
        if mutation.response_item_deleted:
            self.mark_dirty(mutation.vote.trigger_group_id)
        return WordbankDeleteVoteResult(
            vote_id=mutation.vote.id,
            trigger_group_id=mutation.vote.trigger_group_id,
            response_item_id=mutation.vote.response_item_id,
            status=mutation.vote.status,
            support_count=mutation.vote.support_count,
            threshold=mutation.vote.threshold,
            created=mutation.created,
            already_supported=mutation.already_supported,
            passed=mutation.passed,
            response_item_deleted=mutation.response_item_deleted,
        )

    async def support_delete_vote(
        self,
        *,
        vote_id: int,
        group_id: str,
        user_id: str,
    ) -> WordbankDeleteVoteResult | None:
        mutation = await self.repository.support_delete_vote(
            vote_id=vote_id,
            group_id=group_id,
            user_id=user_id,
        )
        if mutation is None:
            return None
        if mutation.response_item_deleted:
            self.mark_dirty(mutation.vote.trigger_group_id)
        return WordbankDeleteVoteResult(
            vote_id=mutation.vote.id,
            trigger_group_id=mutation.vote.trigger_group_id,
            response_item_id=mutation.vote.response_item_id,
            status=mutation.vote.status,
            support_count=mutation.vote.support_count,
            threshold=mutation.vote.threshold,
            created=mutation.created,
            already_supported=mutation.already_supported,
            passed=mutation.passed,
            response_item_deleted=mutation.response_item_deleted,
        )

    async def get_delete_vote(
        self,
        vote_id: int,
        *,
        group_id: str,
    ) -> WordbankDeleteVoteResult | None:
        vote = await self.repository.get_delete_vote(vote_id, group_id=group_id)
        if vote is None:
            return None
        return WordbankDeleteVoteResult(
            vote_id=vote.id,
            trigger_group_id=vote.trigger_group_id,
            response_item_id=vote.response_item_id,
            status=vote.status,
            support_count=vote.support_count,
            threshold=vote.threshold,
            created=False,
            already_supported=False,
            passed=vote.status == "passed",
            response_item_deleted=False,
        )

    async def record_response_message(
        self,
        *,
        message_id: str,
        trigger_group_id: int,
        trigger_variant_id: int,
        response_item_id: int,
        group_id: str,
        user_id: str,
        message_type: str,
    ) -> None:
        message_id = message_id.strip()
        if not message_id:
            return
        now = get_current_time()
        await self.repository.record_response_message(
            {
                "message_id": message_id,
                "trigger_group_id": trigger_group_id,
                "trigger_variant_id": trigger_variant_id,
                "response_item_id": response_item_id,
                "group_id": group_id,
                "user_id": user_id,
                "message_type": message_type,
                "created_at": now,
                "updated_at": now,
            }
        )

    async def record_approval_message(
        self,
        *,
        message_id: str,
        trigger_group_id: int,
        response_item_id: int,
        group_id: str,
        user_id: str,
        source_message_id: str,
        message_type: str,
    ) -> None:
        message_id = message_id.strip()
        if not message_id:
            return
        now = get_current_time()
        await self.repository.record_approval_message(
            {
                "message_id": message_id,
                "trigger_group_id": trigger_group_id,
                "response_item_id": response_item_id,
                "group_id": group_id,
                "user_id": user_id,
                "source_message_id": source_message_id,
                "message_type": message_type,
                "created_at": now,
                "updated_at": now,
            }
        )

    async def get_response_message(
        self,
        message_id: str,
    ) -> WordbankResponseMessageRecord | None:
        message_id = message_id.strip()
        if not message_id:
            return None
        return await self.repository.get_response_message(message_id)

    async def get_approval_message(
        self,
        message_id: str,
    ) -> WordbankApprovalMessageRecord | None:
        message_id = message_id.strip()
        if not message_id:
            return None
        return await self.repository.get_approval_message(message_id)

    async def get_group_detail(
        self,
        trigger_group_id: int,
        *,
        response_item_id: int | None = None,
    ) -> WordbankGroupDetail | None:
        return await self.repository.get_group_detail(
            trigger_group_id,
            response_item_id=response_item_id,
        )

    async def search(
        self,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WordbankSearchItem]:
        return await self.repository.search(request, limit=limit, offset=offset)

    async def search_page(
        self,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> WordbankSearchPage:
        return await self.repository.search_page(request, limit=limit, offset=offset)

    async def list_pending_entries(
        self,
        *,
        keyword: str = "",
        limit: int = 10,
        offset: int = 0,
        actor_group_id: str = "",
        can_moderate_group: bool = False,
        is_superuser: bool = False,
    ) -> list[WordbankSearchItem]:
        return await self.repository.list_pending_entries(
            keyword=keyword,
            limit=limit,
            offset=offset,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )

    async def match_message(
        self,
        shape: MessageShape,
        *,
        context: RuleContext,
        message_type: str = "message",
    ) -> SelectedMatch | None:
        fingerprint = fingerprint_shape(shape)
        candidates = self._index.find_message(fingerprint)
        return await self._select_and_log(
            candidates,
            context=context,
            message_type=message_type,
        )

    async def _select_and_log(
        self,
        candidates: Sequence[MatchCandidate],
        *,
        context: RuleContext,
        message_type: str,
    ) -> SelectedMatch | None:
        call_counts = self._current_call_counts(candidates)
        selected = self._index.select(
            candidates,
            context=context,
            call_counts=call_counts,
            rng=self.rng,
        )
        if selected is None:
            return None
        now = get_current_time()
        self._call_history[selected.response.id].append(now)
        await self.repository.save_log(
            WordbankLogPayload(
                trigger_group_id=selected.candidate.group.id,
                trigger_variant_id=selected.candidate.trigger.id,
                response_item_id=selected.response.id,
                group_id=context.group_id,
                user_id=context.user_id,
                message_type=message_type,
                matched_text=selected.candidate.matched_text,
                created_at=now,
            ),
            policy=WritePolicy.BUFFERED,
        )
        return selected

    def _current_call_counts(
        self,
        candidates: Sequence[MatchCandidate],
    ) -> dict[int, int]:
        now = get_current_time()
        counts: dict[int, int] = {}
        for candidate in candidates:
            for response in candidate.group.responses:
                call_count = response.rule.get("call_count")
                if not isinstance(call_count, dict):
                    continue
                window = int(call_count.get("window_seconds", 0))
                if window <= 0:
                    counts[response.id] = 0
                    continue
                history = self._call_history[response.id]
                while history and now - history[0] > window:
                    history.popleft()
                counts[response.id] = len(history)
        return counts

    async def _get_response_item_for_mutation(
        self,
        response_item_id: int,
    ) -> WordbankResponseItemRecord | None:
        return await self.repository.get_response_item_record(
            response_item_id,
            include_deleted=True,
        )


def format_search_items(
    items: Sequence[WordbankSearchItem],
    *,
    locale: LocaleCode,
    page: int = 1,
    limit: int = 10,
    has_more: bool = False,
) -> str:
    if not items:
        return tr(locale, "wordbank.search.empty", page=page)
    lines = [tr(locale, "wordbank.search.title", page=page)]
    for item in items:
        response_preview = " / ".join(item.response_summaries[:3]) or item.response_text
        if item.has_more_responses:
            response_preview = f"{response_preview} (+{item.remaining_response_count})"
        lines.append(
            tr(
                locale,
                "wordbank.search.item",
                entry_id=item.trigger_group_id,
                status=item.status,
                trigger_mode=item.trigger_mode,
                scope=item.scope,
                trigger_text=item.trigger_text,
                response_text=response_preview,
            )
        )
    if has_more:
        lines.append(
            tr(locale, "wordbank.search.more", next_page=page + 1, limit=limit)
        )
    return "\n".join(lines)


def format_pending_items(
    items: Sequence[WordbankSearchItem],
    *,
    locale: LocaleCode,
    page: int = 1,
    limit: int = 10,
    has_more: bool = False,
) -> str:
    if not items:
        return tr(locale, "wordbank.approval.pending_empty", page=page)
    lines = [tr(locale, "wordbank.approval.pending_title", page=page)]
    for item in items:
        response_item_id = (
            item.response_item_ids[0]
            if item.response_item_ids
            else item.trigger_group_id
        )
        lines.append(
            tr(
                locale,
                "wordbank.approval.pending_item",
                entry_id=response_item_id,
                trigger_mode=item.trigger_mode,
                scope=item.scope,
                trigger_text=item.trigger_text,
                response_text=item.response_text,
                created_by=item.created_by,
            )
        )
    if has_more:
        lines.append(
            tr(
                locale,
                "wordbank.approval.pending_more",
                next_page=page + 1,
                limit=limit,
            )
        )
    return "\n".join(lines)


def format_add_result(result: WordbankAddResult, *, locale: LocaleCode) -> str:
    key = (
        "wordbank.add.pending" if result.status == "pending" else "wordbank.add.success"
    )
    return tr(
        locale,
        key,
        entry_id=result.response_item_id,
        status=result.status,
        trigger_text=result.trigger_text,
        response_text=result.response_text,
        trigger_mode=result.trigger_mode,
        scope=result.scope,
        probability=f"{result.probability:g}",
        weight=result.weight,
    )


def format_response_summary(
    text: str,
    *,
    shape: MessageShape | None = None,
) -> str:
    if shape is None:
        return text
    return shape_to_summary_text(shape)
