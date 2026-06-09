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
    WordbankLogPayload,
    WordbankSearchItem,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.matching import (
    MatchCandidate,
    RuntimeIndex,
    SelectedMatch,
    normalize_text,
)
from src.plugins.wordbank.services.rules import (
    RuleContext,
    canonicalize_rule,
    normalize_trigger_mode,
)


@dataclass(slots=True, frozen=True)
class WordbankAddResult:
    entry_id: int
    trigger_text: str
    response_text: str
    trigger_mode: str
    scope: str
    probability: float
    weight: int


@dataclass(slots=True, frozen=True)
class WordbankDeleteVoteResult:
    vote_id: int
    entry_id: int
    status: str
    support_count: int
    threshold: int
    created: bool
    already_supported: bool
    passed: bool
    entry_deleted: bool


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
        self._call_history: dict[int, deque[int]] = defaultdict(deque)

    @property
    def index(self) -> RuntimeIndex:
        return self._index

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.repository.init_all_tables()
        await self.rebuild_index()
        self._initialized = True

    async def rebuild_index(self) -> None:
        records = await self.repository.list_enabled_entries()
        self._index = RuntimeIndex.build(records)

    def mark_dirty(self) -> None:
        if self._rebuild_task is not None and not self._rebuild_task.done():
            self._rebuild_task.cancel()
        self._rebuild_task = asyncio.create_task(self._debounced_rebuild())

    async def _debounced_rebuild(self) -> None:
        await asyncio.sleep(self.debounce_seconds)
        await self.rebuild_index()

    async def add_text_entry(
        self,
        *,
        trigger_text: str,
        response_text: str,
        group_id: str,
        user_id: str,
        is_group: bool,
        raw_rule: dict[str, Any] | None = None,
        trigger_mode: str | None = None,
    ) -> WordbankAddResult:
        trigger_text = trigger_text.strip()
        response_text = response_text.strip()
        if not trigger_text:
            raise WordbankUserError(
                "触发词不能为空",
                key="wordbank.error.trigger_empty",
            )
        if not response_text:
            raise WordbankUserError(
                "响应词不能为空",
                key="wordbank.error.response_empty",
            )

        normalized = normalize_text(trigger_text)
        short_trigger = len(normalized.replace(" ", "")) <= 2
        event_trigger = normalized.startswith("event:")
        mode = normalize_trigger_mode(
            trigger_mode,
            short_trigger=short_trigger or event_trigger,
        )
        rule = canonicalize_rule(
            raw_rule,
            is_group=is_group,
            short_trigger=short_trigger,
        )
        entry = await self.repository.create_text_entry(
            trigger_text=trigger_text,
            normalized_text=normalized,
            response_text=response_text,
            trigger_mode=mode,
            rule=dict(rule.rule),
            scope=rule.scope,
            priority=rule.priority,
            probability=rule.probability,
            weight=rule.weight,
            group_id=group_id if is_group else "",
            created_by=user_id,
        )
        self.mark_dirty()
        return WordbankAddResult(
            entry_id=entry.id,
            trigger_text=trigger_text,
            response_text=response_text,
            trigger_mode=mode,
            scope=rule.scope,
            probability=rule.probability,
            weight=rule.weight,
        )

    async def add_image_entry(
        self,
        *,
        canonical_image_id: int,
        response_text: str,
        group_id: str,
        user_id: str,
        is_group: bool,
        raw_rule: dict[str, Any] | None = None,
    ) -> WordbankAddResult:
        response_text = response_text.strip()
        if not response_text:
            raise WordbankUserError(
                "响应词不能为空",
                key="wordbank.error.response_empty",
            )

        rule = canonicalize_rule(
            raw_rule,
            is_group=is_group,
            short_trigger=False,
        )
        entry = await self.repository.create_image_entry(
            canonical_image_id=canonical_image_id,
            response_text=response_text,
            rule=dict(rule.rule),
            scope=rule.scope,
            priority=rule.priority,
            probability=rule.probability,
            weight=rule.weight,
            group_id=group_id if is_group else "",
            created_by=user_id,
        )
        self.mark_dirty()
        return WordbankAddResult(
            entry_id=entry.id,
            trigger_text=f"image:{canonical_image_id}",
            response_text=response_text,
            trigger_mode="fullmatch",
            scope=rule.scope,
            probability=rule.probability,
            weight=rule.weight,
        )

    async def delete_entry(
        self,
        entry_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        ok = await self.repository.delete_entry(
            entry_id,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok:
            self.mark_dirty()
        return ok

    async def restore_entry(
        self,
        entry_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        ok = await self.repository.restore_entry(
            entry_id,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok:
            self.mark_dirty()
        return ok

    async def request_delete_vote(
        self,
        *,
        entry_id: int,
        group_id: str,
        user_id: str,
        threshold: int = 3,
        reason: str = "",
    ) -> WordbankDeleteVoteResult | None:
        mutation = await self.repository.request_delete_vote(
            entry_id=entry_id,
            group_id=group_id,
            user_id=user_id,
            threshold=threshold,
            reason=reason,
        )
        if mutation is None:
            return None
        if mutation.entry_deleted:
            self.mark_dirty()
        return WordbankDeleteVoteResult(
            vote_id=mutation.vote.id,
            entry_id=mutation.vote.entry_id,
            status=mutation.vote.status,
            support_count=mutation.vote.support_count,
            threshold=mutation.vote.threshold,
            created=mutation.created,
            already_supported=mutation.already_supported,
            passed=mutation.passed,
            entry_deleted=mutation.entry_deleted,
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
        if mutation.entry_deleted:
            self.mark_dirty()
        return WordbankDeleteVoteResult(
            vote_id=mutation.vote.id,
            entry_id=mutation.vote.entry_id,
            status=mutation.vote.status,
            support_count=mutation.vote.support_count,
            threshold=mutation.vote.threshold,
            created=mutation.created,
            already_supported=mutation.already_supported,
            passed=mutation.passed,
            entry_deleted=mutation.entry_deleted,
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
            entry_id=vote.entry_id,
            status=vote.status,
            support_count=vote.support_count,
            threshold=vote.threshold,
            created=False,
            already_supported=False,
            passed=vote.status == "passed",
            entry_deleted=False,
        )

    async def search(
        self,
        keyword: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WordbankSearchItem]:
        return await self.repository.search(keyword, limit=limit, offset=offset)

    async def match_text(
        self,
        text: str,
        *,
        context: RuleContext,
    ) -> SelectedMatch | None:
        candidates = self._index.find_text(text)
        return await self._select_and_log(
            candidates,
            context=context,
            message_type="text",
        )

    async def match_images(
        self,
        canonical_image_ids: Sequence[int],
        *,
        context: RuleContext,
    ) -> SelectedMatch | None:
        candidates = self._index.find_images(canonical_image_ids)
        return await self._select_and_log(
            candidates,
            context=context,
            message_type="image",
        )

    async def match_event(
        self,
        event_triggers: Sequence[str],
        *,
        context: RuleContext,
    ) -> SelectedMatch | None:
        candidates = self._index.find_texts(event_triggers)
        return await self._select_and_log(
            candidates,
            context=context,
            message_type="event",
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
        self._call_history[selected.candidate.entry.id].append(now)
        await self.repository.save_log(
            WordbankLogPayload(
                entry_id=selected.candidate.entry.id,
                trigger_id=selected.candidate.trigger.id,
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
            entry = candidate.entry
            call_count = entry.rule.get("call_count")
            if not isinstance(call_count, dict):
                continue
            window = int(call_count.get("window_seconds", 0))
            if window <= 0:
                counts[entry.id] = 0
                continue
            history = self._call_history[entry.id]
            while history and now - history[0] > window:
                history.popleft()
            counts[entry.id] = len(history)
        return counts


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
        lines.append(
            tr(
                locale,
                "wordbank.search.item",
                entry_id=item.entry_id,
                trigger_mode=item.trigger_mode,
                scope=item.scope,
                trigger_text=item.trigger_text,
                response_text=item.response_text,
            )
        )
    if has_more:
        lines.append(
            tr(locale, "wordbank.search.more", next_page=page + 1, limit=limit)
        )
    return "\n".join(lines)


def format_add_result(result: WordbankAddResult, *, locale: LocaleCode) -> str:
    return tr(
        locale,
        "wordbank.add.success",
        entry_id=result.entry_id,
        trigger_text=result.trigger_text,
        response_text=result.response_text,
        trigger_mode=result.trigger_mode,
        scope=result.scope,
        probability=f"{result.probability:g}",
        weight=result.weight,
    )
