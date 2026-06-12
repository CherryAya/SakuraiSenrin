"""Wordbank data access helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .tables import (
    WordbankDeleteVote,
    WordbankDeleteVoteSupport,
    WordbankLog,
    WordbankMessageRef,
    WordbankMessageRoute,
    WordbankResponseItem,
    WordbankTriggerGroup,
    WordbankTriggerVariant,
)
from .types import (
    WordbankDeleteVotePayload,
    WordbankLogPayload,
    WordbankMessageRefPayload,
    WordbankMessageRoutePayload,
)


class WordbankTriggerGroupOps(BaseOps[WordbankTriggerGroup]):
    async def get_active_groups(self) -> Sequence[WordbankTriggerGroup]:
        stmt = (
            select(WordbankTriggerGroup)
            .where(
                WordbankTriggerGroup.status == "approved",
                WordbankTriggerGroup.enabled == 1,
                WordbankTriggerGroup.deleted_at == 0,
            )
            .order_by(WordbankTriggerGroup.id.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def update_runtime_state(
        self,
        trigger_group_id: int,
        *,
        status: str,
        enabled: int,
        deleted_at: int,
        updated_at: int,
    ) -> bool:
        stmt = (
            update(WordbankTriggerGroup)
            .where(WordbankTriggerGroup.id == trigger_group_id)
            .values(
                status=status,
                enabled=enabled,
                deleted_at=deleted_at,
                updated_at=updated_at,
            )
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0


class WordbankTriggerVariantOps(BaseOps[WordbankTriggerVariant]):
    async def get_by_group_ids(
        self,
        group_ids: Sequence[int],
    ) -> Sequence[WordbankTriggerVariant]:
        if not group_ids:
            return []
        stmt = (
            select(WordbankTriggerVariant)
            .where(WordbankTriggerVariant.trigger_group_id.in_(group_ids))
            .order_by(WordbankTriggerVariant.id.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()


class WordbankResponseItemOps(BaseOps[WordbankResponseItem]):
    async def get_by_group_ids(
        self,
        group_ids: Sequence[int],
        *,
        include_deleted: bool,
        active_only: bool = False,
    ) -> Sequence[WordbankResponseItem]:
        if not group_ids:
            return []
        stmt = (
            select(WordbankResponseItem)
            .where(WordbankResponseItem.trigger_group_id.in_(group_ids))
            .order_by(WordbankResponseItem.id.asc())
        )
        if not include_deleted:
            stmt = stmt.where(WordbankResponseItem.deleted_at == 0)
        if active_only:
            stmt = stmt.where(
                WordbankResponseItem.status == "approved",
                WordbankResponseItem.enabled == 1,
                WordbankResponseItem.deleted_at == 0,
            )
        return (await self.session.execute(stmt)).scalars().all()


class WordbankDeleteVoteOps(BaseOps[WordbankDeleteVote]):
    async def create_vote(
        self,
        payload: WordbankDeleteVotePayload,
    ) -> WordbankDeleteVote:
        vote = WordbankDeleteVote(**payload)
        self.session.add(vote)
        await self.session.flush()
        return vote

    async def get_vote_by_id(self, vote_id: int) -> WordbankDeleteVote | None:
        return await self.session.get(WordbankDeleteVote, vote_id)

    async def get_open_vote_by_response_item(
        self,
        response_item_id: int,
        group_id: str,
    ) -> WordbankDeleteVote | None:
        stmt = (
            select(WordbankDeleteVote)
            .where(
                WordbankDeleteVote.response_item_id == response_item_id,
                WordbankDeleteVote.group_id == group_id,
                WordbankDeleteVote.status == "open",
            )
            .order_by(WordbankDeleteVote.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_status(
        self,
        vote_id: int,
        status: str,
        updated_at: int,
    ) -> bool:
        stmt = (
            update(WordbankDeleteVote)
            .where(WordbankDeleteVote.id == vote_id)
            .values(status=status, updated_at=updated_at)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def support_count(self, vote_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(WordbankDeleteVoteSupport)
            .where(WordbankDeleteVoteSupport.vote_id == vote_id)
        )
        return int((await self.session.execute(stmt)).scalar_one() or 0)


class WordbankDeleteVoteSupportOps(BaseOps[WordbankDeleteVoteSupport]):
    async def has_supported(self, vote_id: int, user_id: str) -> bool:
        stmt = select(WordbankDeleteVoteSupport.id).where(
            WordbankDeleteVoteSupport.vote_id == vote_id,
            WordbankDeleteVoteSupport.user_id == user_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def create_support(
        self,
        *,
        vote_id: int,
        user_id: str,
        created_at: int,
    ) -> WordbankDeleteVoteSupport:
        support = WordbankDeleteVoteSupport(
            vote_id=vote_id,
            user_id=user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        self.session.add(support)
        await self.session.flush()
        return support


class WordbankMessageRouteOps(BaseOps[WordbankMessageRoute]):
    async def upsert_message_route(
        self,
        payload: WordbankMessageRoutePayload,
    ) -> int:
        stmt = sqlite_insert(WordbankMessageRoute).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WordbankMessageRoute.message_id],
            set_={
                "ref_kind": stmt.excluded.ref_kind,
                "shard_key": stmt.excluded.shard_key,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_by_message_id(
        self,
        message_id: str,
    ) -> WordbankMessageRoute | None:
        stmt = select(WordbankMessageRoute).where(
            WordbankMessageRoute.message_id == message_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class WordbankMessageRefOps(BaseOps[WordbankMessageRef]):
    async def upsert_message_ref(
        self,
        payload: WordbankMessageRefPayload,
    ) -> int:
        stmt = sqlite_insert(WordbankMessageRef).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WordbankMessageRef.message_id],
            set_={
                "ref_kind": stmt.excluded.ref_kind,
                "shard_key": stmt.excluded.shard_key,
                "trigger_group_id": stmt.excluded.trigger_group_id,
                "trigger_variant_id": stmt.excluded.trigger_variant_id,
                "response_item_id": stmt.excluded.response_item_id,
                "group_id": stmt.excluded.group_id,
                "user_id": stmt.excluded.user_id,
                "message_type": stmt.excluded.message_type,
                "source_message_id": stmt.excluded.source_message_id,
                "context_type": stmt.excluded.context_type,
                "current_page": stmt.excluded.current_page,
                "keyword": stmt.excluded.keyword,
                "field": stmt.excluded.field,
                "creator_id": stmt.excluded.creator_id,
                "has_image": stmt.excluded.has_image,
                "group_ids_json": stmt.excluded.group_ids_json,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount

    async def get_by_message_id(
        self,
        message_id: str,
    ) -> WordbankMessageRef | None:
        stmt = select(WordbankMessageRef).where(
            WordbankMessageRef.message_id == message_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class WordbankLogOps(BaseOps[WordbankLog]):
    async def bulk_insert_logs(self, data: list[WordbankLogPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WordbankLog).values(data)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount
