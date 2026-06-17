"""Entry management helpers for the wordbank repository."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import arrow
from sqlalchemy import case, delete, func, or_, select, text

from src.lib.utils.common import get_current_time
from src.plugins.wordbank.message_model import MessageShape, fingerprint_shape, shape_to_payload

from .instances import (
    wordbank_log_db,
    wordbank_main_db,
    wordbank_message_ref_db,
    wordbank_message_route_db,
)
from .repo_shared import message_ref_time_ctx
from .tables import (
    WordbankDeleteVote,
    WordbankDeleteVoteSupport,
    WordbankImage,
    WordbankLog,
    WordbankMessageRef,
    WordbankMessageRoute,
    WordbankResponseItem,
    WordbankSearchDocument,
    WordbankSearchImageMap,
    WordbankTriggerGroup,
    WordbankTriggerVariant,
)
from .types import (
    WordbankCreatorLeaderboardItem,
    WordbankCreatorLeaderboardSnapshot,
    WordbankDeleteVoteMutation,
    WordbankDeleteVoteRecord,
    WordbankGroupDetail,
    WordbankRankPeriod,
    WordbankSearchItem,
    WordbankTriggerGroupRecord,
)


class WordbankRepositoryEntriesMixin:
    async def reset_all_data(
        self: Any,
        *,
        include_images: bool = True,
        include_logs: bool = False,
    ) -> None:
        route_rows = await self.list_message_ref_routes()
        for shard_key in {route.shard_key for route in route_rows}:
            async with wordbank_message_ref_db.write_session(
                time_ctx=message_ref_time_ctx(shard_key)
            ) as session:
                await session.execute(delete(WordbankMessageRef))
        async with wordbank_message_route_db.write_session() as session:
            await session.execute(delete(WordbankMessageRoute))
        if include_logs:
            for source in wordbank_log_db.iter_backup_sources():
                if source.shard_key is None:
                    continue
                async with wordbank_log_db.write_session(
                    time_ctx=message_ref_time_ctx(source.shard_key)
                ) as session:
                    await session.execute(delete(WordbankLog))
        async with wordbank_main_db.write_session() as session:
            await session.execute(delete(WordbankDeleteVoteSupport))
            await session.execute(delete(WordbankDeleteVote))
            await session.execute(delete(WordbankSearchImageMap))
            await session.execute(delete(WordbankSearchDocument))
            await session.execute(text("DELETE FROM wordbank_search_trigger_fts"))
            await session.execute(text("DELETE FROM wordbank_search_response_fts"))
            await session.execute(delete(WordbankResponseItem))
            await session.execute(delete(WordbankTriggerVariant))
            await session.execute(delete(WordbankTriggerGroup))
            if include_images:
                await session.execute(delete(WordbankImage))

    async def list_enabled_entries(self: Any) -> list[WordbankTriggerGroupRecord]:
        async with wordbank_main_db.read_session() as session:
            group_rows = (
                (
                    await session.execute(
                        select(WordbankTriggerGroup)
                        .where(
                            WordbankTriggerGroup.status == "approved",
                            WordbankTriggerGroup.enabled == 1,
                            WordbankTriggerGroup.deleted_at == 0,
                        )
                        .order_by(WordbankTriggerGroup.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            group_ids = [group.id for group in group_rows]
            variant_rows = await self._load_variants_by_group_ids(session, group_ids)
            response_rows = await self._load_responses_by_group_ids(
                session,
                group_ids,
                include_deleted=False,
                active_only=True,
            )
        variants_by_group: dict[int, list[WordbankTriggerVariant]] = defaultdict(list)
        for variant in variant_rows:
            variants_by_group[variant.trigger_group_id].append(variant)
        responses_by_group: dict[int, list[WordbankResponseItem]] = defaultdict(list)
        for response in response_rows:
            responses_by_group[response.trigger_group_id].append(response)
        return [
            self._to_group_record(
                group,
                variants_by_group.get(group.id, []),
                responses_by_group.get(group.id, []),
            )
            for group in group_rows
        ]

    async def list_pending_entries(
        self: Any,
        *,
        keyword: str = "",
        limit: int = 10,
        offset: int = 0,
        actor_group_id: str = "",
        can_moderate_group: bool = False,
        is_superuser: bool = False,
    ) -> list[WordbankSearchItem]:
        async with wordbank_main_db.read_session() as session:
            stmt = (
                select(
                    WordbankTriggerGroup, WordbankTriggerVariant, WordbankResponseItem
                )
                .join(
                    WordbankTriggerVariant,
                    WordbankTriggerVariant.trigger_group_id == WordbankTriggerGroup.id,
                )
                .join(
                    WordbankResponseItem,
                    WordbankResponseItem.trigger_group_id == WordbankTriggerGroup.id,
                )
                .where(
                    WordbankResponseItem.status == "pending",
                    WordbankResponseItem.deleted_at == 0,
                )
                .order_by(WordbankResponseItem.id.asc())
                .limit(limit)
                .offset(offset)
            )
            if not is_superuser:
                if not (can_moderate_group and actor_group_id):
                    return []
                stmt = stmt.where(WordbankTriggerGroup.group_id == actor_group_id)
            if keyword:
                pattern = f"%{keyword}%"
                stmt = stmt.where(
                    or_(
                        WordbankTriggerVariant.trigger_text.like(pattern),
                        WordbankResponseItem.text.like(pattern),
                    )
                )
            rows = (await session.execute(stmt)).all()
        return [
            self._pending_item_from_rows(group, variant, response)
            for group, variant, response in rows
        ]

    async def get_creator_leaderboard(
        self: Any,
        *,
        period: WordbankRankPeriod,
        limit: int = 10,
        now_ts: int | None = None,
    ) -> WordbankCreatorLeaderboardSnapshot:
        now = arrow.get(now_ts or get_current_time()).to("Asia/Shanghai")
        range_end_ts = now.int_timestamp
        if period == "week":
            range_start = now.floor("week")
            query_end_ts = range_start.shift(weeks=1).int_timestamp
        elif period == "month":
            range_start = now.floor("month")
            query_end_ts = range_start.shift(months=1).int_timestamp
        elif period == "season":
            quarter = (now.month - 1) // 3
            range_start = now.shift(months=-(now.month - (quarter * 3 + 1))).floor(
                "month"
            )
            query_end_ts = range_start.shift(months=3).int_timestamp
        else:
            earliest_stmt = select(func.min(WordbankResponseItem.created_at)).where(
                WordbankResponseItem.status == "approved"
            )
            async with wordbank_main_db.read_session() as session:
                earliest_created_at = await session.scalar(earliest_stmt)
            range_start = arrow.get(int(earliest_created_at or range_end_ts)).to(
                "Asia/Shanghai"
            )
            query_end_ts = range_end_ts + 1

        range_start_ts = range_start.int_timestamp
        filters = (
            WordbankResponseItem.status == "approved",
            WordbankResponseItem.created_at >= range_start_ts,
            WordbankResponseItem.created_at < query_end_ts,
        )
        base_stmt = (
            select(
                WordbankResponseItem.created_by.label("created_by"),
                func.count(WordbankResponseItem.id).label("approved_count"),
                func.max(WordbankResponseItem.created_at).label("latest_created_at"),
                func.count(
                    func.distinct(func.nullif(WordbankResponseItem.group_id, ""))
                ).label("group_count"),
                func.sum(
                    case(
                        (WordbankResponseItem.scope == "current_group", 1),
                        else_=0,
                    )
                ).label("current_group_count"),
                func.sum(
                    case(
                        (WordbankResponseItem.scope == "all_groups", 1),
                        else_=0,
                    )
                ).label("all_groups_count"),
                func.sum(
                    case(
                        (WordbankResponseItem.scope == "self", 1),
                        else_=0,
                    )
                ).label("self_count"),
                func.sum(
                    case(
                        (WordbankResponseItem.scope == "private_only", 1),
                        else_=0,
                    )
                ).label("private_only_count"),
            )
            .where(*filters)
            .group_by(WordbankResponseItem.created_by)
            .order_by(
                text("approved_count DESC"),
                text("latest_created_at DESC"),
                WordbankResponseItem.created_by.asc(),
            )
            .limit(max(1, limit))
        )
        stats_stmt = select(
            func.count(WordbankResponseItem.id),
            func.count(func.distinct(WordbankResponseItem.created_by)),
        ).where(*filters)
        async with wordbank_main_db.read_session() as session:
            rows = (await session.execute(base_stmt)).all()
            total_approved_count, total_creator_count = (
                await session.execute(stats_stmt)
            ).one()
        items = tuple(
            WordbankCreatorLeaderboardItem(
                created_by=str(row.created_by or ""),
                approved_count=int(row.approved_count or 0),
                latest_created_at=int(row.latest_created_at or 0),
                group_count=int(row.group_count or 0),
                current_group_count=int(row.current_group_count or 0),
                all_groups_count=int(row.all_groups_count or 0),
                self_count=int(row.self_count or 0),
                private_only_count=int(row.private_only_count or 0),
            )
            for row in rows
        )
        return WordbankCreatorLeaderboardSnapshot(
            period=period,
            range_start=range_start_ts,
            range_end=range_end_ts,
            total_creator_count=int(total_creator_count or 0),
            total_approved_count=int(total_approved_count or 0),
            items=items,
        )

    async def delete_response_item(
        self: Any,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if response is None or response.deleted_at != 0:
                return False
            if not self._response_item_allows_delete(
                response,
                actor_user_id=actor_user_id,
                is_superuser=is_superuser,
            ):
                return False
            response.deleted_at = now
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def restore_response_item(
        self: Any,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if response is None or response.deleted_at == 0:
                return False
            group = await session.get(WordbankTriggerGroup, response.trigger_group_id)
            if group is None or not self._response_item_allows_mutation(
                response,
                group=group,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            response.deleted_at = 0
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def update_trigger_probability(
        self: Any,
        trigger_group_id: int,
        *,
        probability: float,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            group = await session.get(WordbankTriggerGroup, trigger_group_id)
            if group is None or group.deleted_at != 0:
                return False
            if not self._trigger_group_allows_edit(
                group,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            group.probability = probability
            group.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, trigger_group_id)
            return True

    async def update_trigger_content(
        self: Any,
        trigger_group_id: int,
        *,
        trigger_shape: MessageShape,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        trigger_fingerprint = fingerprint_shape(trigger_shape)
        trigger_payload = shape_to_payload(trigger_shape)
        async with wordbank_main_db.write_session() as session:
            group = await session.get(WordbankTriggerGroup, trigger_group_id)
            if group is None or group.deleted_at != 0:
                return False
            if not self._trigger_group_allows_edit(
                group,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            variants = await self._load_variants_by_group_ids(session, [trigger_group_id])
            if not variants:
                return False
            for variant in variants:
                variant.trigger_text = trigger_fingerprint.summary_text
                variant.message_json = trigger_payload
                variant.exact_md5 = trigger_fingerprint.exact_md5
                variant.structure_key = trigger_fingerprint.structure_key
                variant.search_text = trigger_fingerprint.search_text
                variant.search_tokens = trigger_fingerprint.search_tokens
                variant.image_keys = trigger_fingerprint.image_keys
                variant.updated_at = now
            responses = await self._load_responses_by_group_ids(
                session,
                [trigger_group_id],
                include_deleted=True,
                active_only=False,
            )
            for response in responses:
                if response.deleted_at != 0:
                    continue
                response.status = "pending"
                response.approved_by = ""
                response.updated_at = now
            group.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, trigger_group_id)
            return True

    async def update_response_weight(
        self: Any,
        response_item_id: int,
        *,
        weight: int,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if response is None or response.deleted_at != 0:
                return False
            if not self._response_item_allows_edit(
                response,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            response.weight = weight
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def update_response_content(
        self: Any,
        response_item_id: int,
        *,
        response_shape: MessageShape,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        response_fingerprint = fingerprint_shape(response_shape)
        response_payload = shape_to_payload(response_shape)
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if response is None or response.deleted_at != 0:
                return False
            if not self._response_item_allows_edit(
                response,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            response.status = "pending"
            response.approved_by = ""
            response.text = response_fingerprint.summary_text
            response.message_json = response_payload
            response.exact_md5 = response_fingerprint.exact_md5
            response.structure_key = response_fingerprint.structure_key
            response.search_text = response_fingerprint.search_text
            response.search_tokens = response_fingerprint.search_tokens
            response.image_keys = response_fingerprint.image_keys
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def approve_response_item(
        self: Any,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if (
                response is None
                or response.status != "pending"
                or response.deleted_at != 0
            ):
                return False
            group = await session.get(WordbankTriggerGroup, response.trigger_group_id)
            if group is None or not self._group_allows_review(
                group,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            response.status = "approved"
            response.enabled = 1
            response.approved_by = actor_user_id
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def reject_response_item(
        self: Any,
        response_item_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if (
                response is None
                or response.status != "pending"
                or response.deleted_at != 0
            ):
                return False
            group = await session.get(WordbankTriggerGroup, response.trigger_group_id)
            if group is None or not self._group_allows_review(
                group,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            ):
                return False
            response.status = "rejected"
            response.enabled = 0
            response.approved_by = actor_user_id
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def request_delete_vote(
        self: Any,
        *,
        response_item_id: int,
        group_id: str,
        user_id: str,
        threshold: int,
        reason: str = "",
    ) -> WordbankDeleteVoteMutation | None:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
            if (
                response is None
                or response.deleted_at != 0
                or response.status != "approved"
            ):
                return None
            group = await session.get(WordbankTriggerGroup, response.trigger_group_id)
            if group is None or not self._response_item_allows_group_vote(
                response, group_id
            ):
                return None
            vote = (
                await session.execute(
                    select(WordbankDeleteVote)
                    .where(
                        WordbankDeleteVote.response_item_id == response_item_id,
                        WordbankDeleteVote.group_id == group_id,
                        WordbankDeleteVote.status == "open",
                    )
                    .order_by(WordbankDeleteVote.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            created = False
            if vote is None:
                vote = WordbankDeleteVote(
                    trigger_group_id=response.trigger_group_id,
                    response_item_id=response_item_id,
                    group_id=group_id,
                    created_by=user_id,
                    status="open",
                    threshold=threshold,
                    reason=reason,
                    created_at=now,
                    updated_at=now,
                )
                session.add(vote)
                await session.flush()
                created = True
            return await self._support_delete_vote_in_session(
                session,
                response_item=response,
                vote=vote,
                user_id=user_id,
                now=now,
                created=created,
            )

    async def support_delete_vote(
        self: Any,
        *,
        vote_id: int,
        group_id: str,
        user_id: str,
    ) -> WordbankDeleteVoteMutation | None:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            vote = await session.get(WordbankDeleteVote, vote_id)
            if vote is None or vote.group_id != group_id:
                return None
            response = await session.get(WordbankResponseItem, vote.response_item_id)
            if response is None:
                return None
            return await self._support_delete_vote_in_session(
                session,
                response_item=response,
                vote=vote,
                user_id=user_id,
                now=now,
                created=False,
            )

    async def get_delete_vote(
        self: Any,
        vote_id: int,
        *,
        group_id: str,
    ) -> WordbankDeleteVoteRecord | None:
        async with wordbank_main_db.read_session() as session:
            vote = await session.get(WordbankDeleteVote, vote_id)
            if vote is None or vote.group_id != group_id:
                return None
            support_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(WordbankDeleteVoteSupport)
                    .where(WordbankDeleteVoteSupport.vote_id == vote.id)
                )
                or 0
            )
        return self._to_delete_vote_record(vote, support_count=support_count)

    async def get_group_detail(
        self: Any,
        trigger_group_id: int,
        response_item_id: int | None = None,
    ) -> WordbankGroupDetail | None:
        async with wordbank_main_db.read_session() as session:
            bundle = await self._load_group_bundle_in_session(
                session,
                trigger_group_id,
                include_deleted=True,
            )
        if bundle is None or not bundle.variants:
            return None
        return self._group_detail_from_bundle(bundle, response_item_id=response_item_id)

    @staticmethod
    def _response_item_allows_delete(
        response: WordbankResponseItem,
        *,
        actor_user_id: str,
        is_superuser: bool,
    ) -> bool:
        if is_superuser:
            return True
        return response.created_by == actor_user_id

    @staticmethod
    def _trigger_group_allows_edit(
        group: WordbankTriggerGroup,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        _ = group, actor_user_id, actor_group_id, can_moderate_group
        return is_superuser

    @staticmethod
    def _response_item_allows_mutation(
        response: WordbankResponseItem,
        *,
        group: WordbankTriggerGroup,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        if is_superuser:
            return True
        if response.created_by == actor_user_id:
            return True
        return bool(
            can_moderate_group and actor_group_id and group.group_id == actor_group_id
        )

    @staticmethod
    def _response_item_allows_edit(
        response: WordbankResponseItem,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        _ = actor_group_id, can_moderate_group
        if is_superuser:
            return True
        return response.created_by == actor_user_id

    @staticmethod
    def _group_allows_review(
        group: WordbankTriggerGroup,
        *,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        if is_superuser:
            return True
        return bool(
            can_moderate_group and actor_group_id and group.group_id == actor_group_id
        )

    @staticmethod
    def _response_item_allows_group_vote(
        response: WordbankResponseItem,
        group_id: str,
    ) -> bool:
        if not group_id:
            return False
        if response.scope == "all_groups":
            return True
        if response.scope in {"current_group", "self_in_current_group"}:
            return response.group_id == group_id
        return False

    async def _support_delete_vote_in_session(
        self: Any,
        session: Any,
        *,
        response_item: WordbankResponseItem,
        vote: WordbankDeleteVote,
        user_id: str,
        now: int,
        created: bool,
    ) -> WordbankDeleteVoteMutation:
        support_count = int(
            await session.scalar(
                select(func.count())
                .select_from(WordbankDeleteVoteSupport)
                .where(WordbankDeleteVoteSupport.vote_id == vote.id)
            )
            or 0
        )
        if vote.status != "open":
            return WordbankDeleteVoteMutation(
                vote=self._to_delete_vote_record(vote, support_count=support_count),
                created=created,
                already_supported=True,
                passed=vote.status == "passed",
                response_item_deleted=False,
            )
        already_supported = (
            await session.execute(
                select(WordbankDeleteVoteSupport.id).where(
                    WordbankDeleteVoteSupport.vote_id == vote.id,
                    WordbankDeleteVoteSupport.user_id == user_id,
                )
            )
        ).scalar_one_or_none() is not None
        if not already_supported:
            session.add(
                WordbankDeleteVoteSupport(
                    vote_id=vote.id,
                    user_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            support_count += 1
        passed = support_count >= vote.threshold
        response_item_deleted = False
        if passed:
            if response_item.deleted_at == 0:
                response_item.deleted_at = now
                response_item.updated_at = now
                response_item_deleted = True
            vote.status = "passed" if response_item_deleted else "closed"
            vote.updated_at = now
            await session.flush()
            if response_item_deleted:
                await self._refresh_group_in_session(
                    session, response_item.trigger_group_id
                )
        return WordbankDeleteVoteMutation(
            vote=self._to_delete_vote_record(vote, support_count=support_count),
            created=created,
            already_supported=already_supported,
            passed=passed,
            response_item_deleted=response_item_deleted,
        )
