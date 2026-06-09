"""High-level wordbank service."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
import random
from typing import Any

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.types import (
    WordbankLogPayload,
    WordbankSearchItem,
)
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
            raise ValueError("触发词不能为空")
        if not response_text:
            raise ValueError("响应词不能为空")

        normalized = normalize_text(trigger_text)
        short_trigger = len(normalized.replace(" ", "")) <= 2
        mode = normalize_trigger_mode(trigger_mode, short_trigger=short_trigger)
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
            raise ValueError("响应词不能为空")

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

    async def delete_entry(self, entry_id: int) -> bool:
        ok = await self.repository.delete_entry(entry_id)
        if ok:
            self.mark_dirty()
        return ok

    async def restore_entry(self, entry_id: int) -> bool:
        ok = await self.repository.restore_entry(entry_id)
        if ok:
            self.mark_dirty()
        return ok

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


def format_search_items(items: Sequence[WordbankSearchItem]) -> str:
    if not items:
        return "没有找到匹配词条。"
    lines = ["词库搜索结果:"]
    for item in items:
        lines.append(
            f"#{item.entry_id} [{item.trigger_mode}/{item.scope}] "
            f"{item.trigger_text} => {item.response_text}"
        )
    return "\n".join(lines)


def format_add_result(result: WordbankAddResult) -> str:
    return (
        "词条已加入词库\n"
        f"ID: {result.entry_id}\n"
        f"触发: {result.trigger_text}\n"
        f"响应: {result.response_text}\n"
        f"模式: {result.trigger_mode}\n"
        f"范围: {result.scope}\n"
        f"概率: {result.probability:g}\n"
        f"权重: {result.weight}"
    )
