"""High-level wordbank service."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import random
from typing import Any

from src.database.consts import WritePolicy
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.logger import logger
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankLogPayload,
    WordbankMessageRefKind,
    WordbankMessageRefRecord,
    WordbankRankPeriod,
    WordbankResponseItemRecord,
    WordbankSearchItem,
    WordbankSearchPage,
    WordbankSearchRequest,
    WordbankTriggerGroupRecord,
)
from src.plugins.wordbank.debug import (
    describe_batch_errors,
    describe_shape,
    elapsed_ms,
    log_perf,
    perf_start,
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
    RuntimeResponseItem,
    SelectedMatch,
)
from src.plugins.wordbank.services.presentation import (
    WordbankAddResult,
    WordbankBatchAddItemResult,
    WordbankBatchAddResult,
    WordbankDeleteVoteResult,
    WordbankLeaderboardCardData,
    WordbankLeaderboardCardItem,
    rank_period_label,
    rank_range_text,
)
from src.plugins.wordbank.services.rules import (
    MAX_CALL_COUNT_WINDOW_SECONDS,
    RuleContext,
    canonicalize_rule,
)
from src.repositories import user_repo


@dataclass(slots=True)
class _CallCountCacheEntry:
    count: int
    expires_at: int
    counted_until: int


class WordbankService:
    def __init__(
        self,
        repository: WordbankRepository,
        *,
        debounce_seconds: float = 1.0,
        rng: random.Random | None = None,
        call_count_cache_ttl_seconds: int = 3,
    ) -> None:
        self.repository = repository
        self.debounce_seconds = debounce_seconds
        self.rng = rng or random.Random()
        self.call_count_cache_ttl_seconds = max(int(call_count_cache_ttl_seconds), 0)
        self._index = RuntimeIndex()
        self._initialized = False
        self._rebuild_task: asyncio.Task[None] | None = None
        self._dirty_group_ids: set[int] = set()
        self._call_count_cache: dict[tuple[int, int], _CallCountCacheEntry] = {}

    @property
    def index(self) -> RuntimeIndex:
        return self._index

    async def initialize(self) -> None:
        if self._initialized:
            log_perf("service.initialize.cached", initialized=True)
            return
        start = perf_start()
        init_tables_start = perf_start()
        await self.repository.init_all_tables()
        init_tables_ms = elapsed_ms(init_tables_start)
        search_index_start = perf_start()
        await self.repository.ensure_search_index()
        search_index_ms = elapsed_ms(search_index_start)
        rebuild_start = perf_start()
        await self.rebuild_index()
        rebuild_ms = elapsed_ms(rebuild_start)
        self._initialized = True
        log_perf(
            "service.initialize.done",
            start=start,
            init_tables_ms=f"{init_tables_ms:.2f}",
            ensure_search_index_ms=f"{search_index_ms:.2f}",
            rebuild_index_ms=f"{rebuild_ms:.2f}",
            runtime_groups=len(self._index.groups),
            exact_buckets=len(self._index.exact_match),
        )

    async def rebuild_index(self) -> None:
        start = perf_start()
        records = await self.repository.list_enabled_entries()
        self._index = RuntimeIndex.build(records)
        response_count = sum(
            len(group.responses) for group in self._index.groups.values()
        )
        trigger_count = sum(
            len(variants) for variants in self._index.exact_match.values()
        )
        log_perf(
            "service.rebuild_index.done",
            start=start,
            source_records=len(records),
            runtime_groups=len(self._index.groups),
            runtime_responses=response_count,
            runtime_triggers=trigger_count,
            exact_buckets=len(self._index.exact_match),
        )

    def mark_dirty(self, trigger_group_id: int | None = None) -> None:
        if trigger_group_id is None:
            self._dirty_group_ids.clear()
        else:
            self._dirty_group_ids.add(trigger_group_id)
        if self._rebuild_task is not None and not self._rebuild_task.done():
            self._rebuild_task.cancel()
            log_perf(
                "service.mark_dirty.cancel_rebuild_task",
                trigger_group_id=trigger_group_id or "all",
            )
        self._rebuild_task = asyncio.create_task(self._debounced_rebuild())
        log_perf(
            "service.mark_dirty.scheduled",
            trigger_group_id=trigger_group_id or "all",
            pending_groups=len(self._dirty_group_ids),
        )

    async def _debounced_rebuild(self) -> None:
        start = perf_start()
        await asyncio.sleep(self.debounce_seconds)
        if not self._dirty_group_ids:
            await self.rebuild_index()
            log_perf(
                "service.debounced_rebuild.full",
                start=start,
                debounce_seconds=self.debounce_seconds,
            )
            return
        dirty_group_ids = tuple(self._dirty_group_ids)
        self._dirty_group_ids.clear()
        for trigger_group_id in dirty_group_ids:
            await self._refresh_runtime_group(trigger_group_id)
        log_perf(
            "service.debounced_rebuild.partial",
            start=start,
            debounce_seconds=self.debounce_seconds,
            refreshed_groups=len(dirty_group_ids),
        )

    async def _refresh_runtime_group(self, trigger_group_id: int) -> None:
        start = perf_start()
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
            log_perf(
                "service.refresh_runtime_group.removed",
                start=start,
                trigger_group_id=trigger_group_id,
                had_runtime_group=current_group is not None,
            )
            return
        refreshed = RuntimeIndex.build([record])
        if trigger_group_id in refreshed.groups:
            self._index.groups[trigger_group_id] = refreshed.groups[trigger_group_id]
        for key, variants in refreshed.exact_match.items():
            self._index.exact_match.setdefault(key, []).extend(variants)
        refreshed_group = refreshed.groups.get(trigger_group_id)
        log_perf(
            "service.refresh_runtime_group.updated",
            start=start,
            trigger_group_id=trigger_group_id,
            response_count=(
                len(refreshed_group.responses) if refreshed_group is not None else 0
            ),
            trigger_variants=sum(
                len(variants) for variants in refreshed.exact_match.values()
            ),
        )

    async def add_message_entry(
        self,
        *,
        trigger_shape: MessageShape,
        response_shape: MessageShape,
        group_id: str,
        user_id: str,
        is_group: bool,
        raw_rule: dict[str, Any] | None = None,
    ) -> WordbankAddResult:
        start = perf_start()
        if trigger_shape.is_empty():
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        if response_shape.is_empty():
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.response_empty"),
                key="wordbank.error.response_empty",
            )
        rule = canonicalize_rule(
            raw_rule,
            is_group=is_group,
            short_trigger=False,
        )
        created = await self.repository.create_or_append_response(
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            rule=dict(rule.rule),
            scope=rule.scope,
            priority=rule.priority,
            trigger_probability=rule.probability,
            weight=rule.weight,
            group_id=group_id if is_group else "",
            created_by=user_id,
        )
        self.mark_dirty(created.trigger_group_id)
        log_perf(
            "service.add_message_entry.done",
            start=start,
            trigger_group_id=created.trigger_group_id,
            response_item_id=created.response_item_id,
            status=created.status,
            scope=rule.scope,
            probability=f"{created.probability:g}",
            weight=rule.weight,
            trigger_atoms=len(trigger_shape.atoms),
            response_atoms=len(response_shape.atoms),
            is_group=is_group,
        )
        return WordbankAddResult(
            trigger_group_id=created.trigger_group_id,
            trigger_variant_id=created.trigger_variant_id,
            response_item_id=created.response_item_id,
            status=created.status,
            created_group=created.created_group,
            trigger_text=shape_to_summary_text(trigger_shape),
            response_text=shape_to_summary_text(response_shape),
            scope=rule.scope,
            probability=created.probability,
            weight=rule.weight,
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            created_by=user_id,
            created_at=created.response_item.created_at,
            rule=dict(rule.rule),
        )

    async def add_message_entries(
        self,
        *,
        trigger_shape: MessageShape,
        response_shapes: Sequence[MessageShape],
        group_id: str,
        user_id: str,
        is_group: bool,
        raw_rule: dict[str, Any] | None = None,
    ) -> WordbankBatchAddResult:
        start = perf_start()
        items: list[WordbankBatchAddItemResult] = []
        success = 0
        logger.debug(
            "[Wordbank][batch_add] start | "
            f"responses={len(response_shapes)} trigger={describe_shape(trigger_shape)} "
            f"group_id={group_id or '-'} user_id={user_id} is_group={is_group}"
        )
        for index, response_shape in enumerate(response_shapes, start=1):
            try:
                result = await self.add_message_entry(
                    trigger_shape=trigger_shape,
                    response_shape=response_shape,
                    raw_rule=raw_rule,
                    group_id=group_id,
                    user_id=user_id,
                    is_group=is_group,
                )
            except Exception as exc:
                items.append(
                    WordbankBatchAddItemResult(
                        index=index,
                        ok=False,
                        error=str(exc),
                    )
                )
                logger.debug(
                    "[Wordbank][batch_add] item failed | "
                    f"index={index} error_type={type(exc).__name__} error={exc} "
                    f"response={describe_shape(response_shape)}"
                )
                continue
            success += 1
            items.append(
                WordbankBatchAddItemResult(
                    index=index,
                    ok=True,
                    result=result,
                )
            )
        batch = WordbankBatchAddResult(
            total=len(response_shapes),
            success=success,
            failed=max(0, len(response_shapes) - success),
            items=tuple(items),
        )
        log_perf(
            "service.add_message_entries.done",
            start=start,
            total=batch.total,
            success=batch.success,
            failed=batch.failed,
        )
        if batch.failed > 0:
            batch_errors = describe_batch_errors(
                [item.error for item in batch.items if not item.ok]
            )
            logger.debug(
                "[Wordbank][batch_add] summary | "
                f"total={batch.total} success={batch.success} failed={batch.failed} "
                f"errors={batch_errors}"
            )
        return batch

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

    async def update_trigger_probability(
        self,
        trigger_group_id: int,
        *,
        probability: float,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        _ = actor_user_id, actor_group_id, can_moderate_group
        group = await self._get_trigger_group_for_mutation(trigger_group_id)
        ok = await self.repository.update_trigger_probability(
            trigger_group_id,
            probability=probability,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and group is not None:
            self.mark_dirty(group.id)
        return ok

    async def update_trigger_content(
        self,
        trigger_group_id: int,
        *,
        trigger_shape: MessageShape,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        if trigger_shape.is_empty():
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        existing = await self.repository.find_trigger_group_by_shape(
            trigger_shape,
            include_deleted=True,
        )
        if existing is not None and existing.id != trigger_group_id:
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.trigger_group_duplicate"),
                key="wordbank.error.trigger_group_duplicate",
            )
        group = await self._get_trigger_group_for_mutation(trigger_group_id)
        ok = await self.repository.update_trigger_content(
            trigger_group_id,
            trigger_shape=trigger_shape,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and group is not None:
            self.mark_dirty(group.id)
        return ok

    async def update_response_weight(
        self,
        response_item_id: int,
        *,
        weight: int,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        response_item = await self._get_response_item_for_mutation(response_item_id)
        ok = await self.repository.update_response_weight(
            response_item_id,
            weight=weight,
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        if ok and response_item is not None:
            self.mark_dirty(response_item.trigger_group_id)
        return ok

    async def update_response_content(
        self,
        response_item_id: int,
        *,
        response_shape: MessageShape,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        if response_shape.is_empty():
            raise WordbankUserError(
                tr("zh-CN", "wordbank.error.response_empty"),
                key="wordbank.error.response_empty",
            )
        response_item = await self._get_response_item_for_mutation(response_item_id)
        ok = await self.repository.update_response_content(
            response_item_id,
            response_shape=response_shape,
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

    async def record_message_ref(
        self,
        *,
        ref_kind: WordbankMessageRefKind,
        message_id: str,
        trigger_group_id: int,
        group_id: str,
        user_id: str,
        message_type: str,
        trigger_variant_id: int = 0,
        response_item_id: int = 0,
        source_message_id: str = "",
        context_type: str = "",
        current_page: int = 1,
        keyword: str = "",
        field: str = "",
        creator_id: str = "",
        has_image: bool = False,
        group_ids: Sequence[int] = (),
    ) -> None:
        message_id = message_id.strip()
        if not message_id:
            return
        now = get_current_time()
        shard_key = datetime.fromtimestamp(now, UTC).strftime("%Y_%m")
        start = perf_start()
        await self.repository.record_message_ref(
            {
                "ref_kind": ref_kind,
                "message_id": message_id,
                "shard_key": shard_key,
                "trigger_group_id": trigger_group_id,
                "trigger_variant_id": trigger_variant_id,
                "response_item_id": response_item_id,
                "group_id": group_id,
                "user_id": user_id,
                "message_type": message_type,
                "source_message_id": source_message_id,
                "context_type": context_type,
                "current_page": current_page,
                "keyword": keyword,
                "field": field,
                "creator_id": creator_id,
                "has_image": 1 if has_image else 0,
                "group_ids_json": json.dumps(list(group_ids), ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            }
        )
        log_perf(
            "service.record_message_ref.done",
            start=start,
            ref_kind=ref_kind,
            message_id=message_id,
            shard_key=shard_key,
            trigger_group_id=trigger_group_id,
            response_item_id=response_item_id,
            message_type=message_type,
        )

    async def get_message_ref(
        self,
        message_id: str,
        *,
        expected_kind: WordbankMessageRefKind | None = None,
    ) -> WordbankMessageRefRecord | None:
        message_id = message_id.strip()
        if not message_id:
            return None
        return await self.repository.get_message_ref(
            message_id,
            expected_kind=expected_kind,
        )

    async def list_message_refs_by_response_item_ids(
        self,
        response_item_ids: Sequence[int],
        *,
        expected_kind: WordbankMessageRefKind | None = None,
    ) -> list[WordbankMessageRefRecord]:
        return await self.repository.list_message_refs_by_response_item_ids(
            response_item_ids,
            expected_kind=expected_kind,
        )

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
        start = perf_start()
        items = await self.repository.search(request, limit=limit, offset=offset)
        log_perf(
            "service.search.done",
            start=start,
            keyword=request.keyword or "-",
            field=request.field,
            creator_id=request.creator_id or "-",
            has_image=request.has_image,
            limit=limit,
            offset=offset,
            items=len(items),
        )
        return items

    async def search_page(
        self,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> WordbankSearchPage:
        start = perf_start()
        page = await self.repository.search_page(request, limit=limit, offset=offset)
        log_perf(
            "service.search_page.done",
            start=start,
            keyword=request.keyword or "-",
            field=request.field,
            creator_id=request.creator_id or "-",
            has_image=request.has_image,
            limit=limit,
            offset=offset,
            items=len(page.items),
            total_count=page.total_count,
        )
        return page

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

    async def build_creator_leaderboard(
        self,
        *,
        period: WordbankRankPeriod = "month",
        locale: LocaleCode,
        limit: int = 10,
        now_ts: int | None = None,
    ) -> WordbankLeaderboardCardData:
        generated_at = now_ts or get_current_time()
        snapshot = await self.repository.get_creator_leaderboard(
            period=period,
            limit=limit,
            now_ts=generated_at,
        )
        names = await asyncio.gather(
            *(
                self._resolve_creator_display_name(item.created_by, locale=locale)
                for item in snapshot.items
            )
        )
        items: list[WordbankLeaderboardCardItem] = []
        total_approved_count = max(snapshot.total_approved_count, 1)
        for index, (row, display_name) in enumerate(
            zip(snapshot.items, names, strict=False),
            start=1,
        ):
            items.append(
                WordbankLeaderboardCardItem(
                    user_id=row.created_by,
                    display_name=display_name,
                    approved_count=row.approved_count,
                    current_rank=index,
                    share=row.approved_count / total_approved_count,
                    latest_created_at=row.latest_created_at,
                    group_count=row.group_count,
                    current_group_count=row.current_group_count,
                    all_groups_count=row.all_groups_count,
                    self_count=row.self_count,
                    private_only_count=row.private_only_count,
                )
            )
        champion_count = items[0].approved_count if items else 0
        runner_up_count = items[1].approved_count if len(items) > 1 else 0
        return WordbankLeaderboardCardData(
            title=tr(locale, "wordbank.rank.title"),
            subtitle=tr(
                locale,
                "wordbank.rank.subtitle",
                period=rank_period_label(snapshot.period, locale),
            ),
            period=snapshot.period,
            badge_text=rank_period_label(snapshot.period, locale),
            range_text=rank_range_text(
                snapshot.range_start,
                snapshot.range_end,
                locale=locale,
            ),
            generated_at=generated_at,
            total_creator_count=snapshot.total_creator_count,
            total_approved_count=snapshot.total_approved_count,
            champion_gap=max(0, champion_count - runner_up_count),
            top_share=(
                champion_count / snapshot.total_approved_count
                if snapshot.total_approved_count > 0
                else 0.0
            ),
            items=tuple(items),
            range_start=snapshot.range_start,
            range_end=snapshot.range_end,
        )

    async def match_message(
        self,
        shape: MessageShape,
        *,
        context: RuleContext,
        message_type: str = "message",
    ) -> SelectedMatch | None:
        start = perf_start()
        fingerprint = fingerprint_shape(shape)
        candidates = self._index.find_message(fingerprint)
        selected = await self._select_and_log(
            candidates,
            context=context,
            message_type=message_type,
        )
        log_perf(
            "service.match_message.done",
            start=start,
            message_type=message_type,
            group_id=context.group_id or "-",
            user_id=context.user_id,
            shape_atoms=len(shape.atoms),
            candidate_groups=len(candidates),
            selected_response_id=selected.response.id if selected is not None else "-",
        )
        return selected

    async def _select_and_log(
        self,
        candidates: Sequence[MatchCandidate],
        *,
        context: RuleContext,
        message_type: str,
    ) -> SelectedMatch | None:
        now = get_current_time()
        start = perf_start()
        call_count_start = perf_start()
        call_counts = await self._current_call_counts(candidates, now_ts=now)
        call_count_ms = elapsed_ms(call_count_start)
        select_start = perf_start()
        selected = self._index.select(
            candidates,
            context=context,
            call_counts=call_counts,
            rng=self.rng,
        )
        select_ms = elapsed_ms(select_start)
        if selected is None:
            log_perf(
                "service.select_and_log.miss",
                start=start,
                message_type=message_type,
                candidate_groups=len(candidates),
                call_count_windows=len(call_counts),
                call_count_ms=f"{call_count_ms:.2f}",
                select_ms=f"{select_ms:.2f}",
            )
            return None
        save_log_start = perf_start()
        await self.repository.save_log(
            WordbankLogPayload(
                trigger_group_id=selected.candidate.group.id,
                trigger_variant_id=selected.candidate.trigger.id,
                response_item_id=selected.response.id,
                group_id=context.group_id,
                user_id=context.user_id,
                message_type=message_type,
                created_at=now,
            ),
            policy=WritePolicy.IMMEDIATE,
        )
        save_log_ms = elapsed_ms(save_log_start)
        self._increment_call_count_cache(selected.response, now_ts=now)
        log_perf(
            "service.select_and_log.hit",
            start=start,
            message_type=message_type,
            candidate_groups=len(candidates),
            call_count_windows=len(call_counts),
            call_count_ms=f"{call_count_ms:.2f}",
            select_ms=f"{select_ms:.2f}",
            save_log_ms=f"{save_log_ms:.2f}",
            trigger_group_id=selected.candidate.group.id,
            response_item_id=selected.response.id,
        )
        return selected

    async def _current_call_counts(
        self,
        candidates: Sequence[MatchCandidate],
        *,
        now_ts: int,
    ) -> dict[int, int]:
        start = perf_start()
        self._prune_call_count_cache(now_ts)
        counts: dict[int, int] = {}
        missing_windows: dict[int, int] = {}
        cache_hits = 0
        skipped_invalid_windows = 0
        for candidate in candidates:
            for response in candidate.group.responses:
                call_count = response.rule.get("call_count")
                if not isinstance(call_count, dict):
                    continue
                window = int(call_count.get("window_seconds", 0))
                trigger_group_id = response.trigger_group_id
                existing_count = counts.get(trigger_group_id)
                if window <= 0:
                    counts[trigger_group_id] = (
                        existing_count if existing_count is not None else 0
                    )
                    continue
                if window > MAX_CALL_COUNT_WINDOW_SECONDS:
                    skipped_invalid_windows += 1
                    continue
                cache_key = (trigger_group_id, window)
                cached = self._call_count_cache.get(cache_key)
                if cached is not None and cached.expires_at >= now_ts:
                    if existing_count is None or cached.count > existing_count:
                        counts[trigger_group_id] = cached.count
                    cache_hits += 1
                    continue
                current_window = missing_windows.get(trigger_group_id)
                if current_window is None or window > current_window:
                    missing_windows[trigger_group_id] = window
        if missing_windows:
            fresh_counts = await self.repository.count_trigger_group_calls_in_windows(
                missing_windows,
                now_ts=now_ts,
            )
            expires_at = now_ts + self.call_count_cache_ttl_seconds
            for trigger_group_id, window in missing_windows.items():
                count = fresh_counts.get(trigger_group_id, 0)
                existing_count = counts.get(trigger_group_id)
                if existing_count is None or count > existing_count:
                    counts[trigger_group_id] = count
                self._call_count_cache[(trigger_group_id, window)] = (
                    _CallCountCacheEntry(
                        count=count,
                        expires_at=expires_at,
                        counted_until=now_ts,
                    )
                )
        log_perf(
            "service.current_call_counts.done",
            start=start,
            candidates=len(candidates),
            cached=len(counts) - len(missing_windows),
            cache_hits=cache_hits,
            queried=len(missing_windows),
            skipped_invalid_windows=skipped_invalid_windows,
        )
        return counts

    def _prune_call_count_cache(self, now_ts: int) -> None:
        expired_keys = [
            cache_key
            for cache_key, entry in self._call_count_cache.items()
            if entry.expires_at < now_ts
        ]
        for cache_key in expired_keys:
            self._call_count_cache.pop(cache_key, None)
        if expired_keys:
            log_perf(
                "service.prune_call_count_cache.done",
                expired=len(expired_keys),
                remaining=len(self._call_count_cache),
            )

    def _increment_call_count_cache(
        self,
        response: RuntimeResponseItem,
        *,
        now_ts: int,
    ) -> None:
        call_count = response.rule.get("call_count")
        if not isinstance(call_count, dict):
            return
        window = int(call_count.get("window_seconds", 0))
        if window <= 0:
            return
        cache_key = (response.trigger_group_id, window)
        cached = self._call_count_cache.get(cache_key)
        if cached is None or cached.expires_at < now_ts:
            return
        self._call_count_cache[cache_key] = _CallCountCacheEntry(
            count=cached.count + 1,
            expires_at=cached.expires_at,
            counted_until=max(cached.counted_until, now_ts),
        )

    async def _get_response_item_for_mutation(
        self,
        response_item_id: int,
    ) -> WordbankResponseItemRecord | None:
        return await self.repository.get_response_item_record(
            response_item_id, include_deleted=True
        )

    async def _get_trigger_group_for_mutation(
        self,
        trigger_group_id: int,
    ) -> WordbankTriggerGroupRecord | None:
        return await self.repository.get_trigger_group_record(
            trigger_group_id, include_deleted=True, active_only=False
        )

    async def _resolve_creator_display_name(
        self,
        user_id: str,
        *,
        locale: LocaleCode = "zh-CN",
    ) -> str:
        name = await user_repo.get_name_by_uid(user_id)
        if name:
            return name
        suffix = (
            user_id[-4:] if user_id else tr(locale, "wordbank.creator.unknown_suffix")
        )
        return tr(locale, "wordbank.creator.fallback", suffix=suffix)
