"""Wordbank data access helpers."""

from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.db.ops import BaseOps

from .tables import (
    WordbankDeleteVote,
    WordbankDeleteVoteSupport,
    WordbankEntry,
    WordbankImage,
    WordbankLog,
    WordbankResponse,
    WordbankTrigger,
)
from .types import WordbankDeleteVotePayload, WordbankLogPayload


class WordbankEntryOps(BaseOps[WordbankEntry]):
    async def get_active_entries(self) -> Sequence[WordbankEntry]:
        stmt = (
            select(WordbankEntry)
            .where(
                WordbankEntry.status == "approved",
                WordbankEntry.enabled == 1,
                WordbankEntry.deleted_at == 0,
            )
            .order_by(WordbankEntry.priority.desc(), WordbankEntry.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def mark_deleted(
        self,
        entry_id: int,
        deleted_at: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        conditions = self._mutation_permission_conditions(
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        stmt = (
            update(WordbankEntry)
            .where(WordbankEntry.id == entry_id, WordbankEntry.deleted_at == 0)
            .where(*conditions)
            .values(deleted_at=deleted_at, updated_at=deleted_at)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def restore(
        self,
        entry_id: int,
        updated_at: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        conditions = self._mutation_permission_conditions(
            actor_user_id=actor_user_id,
            actor_group_id=actor_group_id,
            can_moderate_group=can_moderate_group,
            is_superuser=is_superuser,
        )
        stmt = (
            update(WordbankEntry)
            .where(WordbankEntry.id == entry_id, WordbankEntry.deleted_at != 0)
            .where(*conditions)
            .values(deleted_at=0, updated_at=updated_at)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    async def mark_deleted_by_vote(
        self,
        entry_id: int,
        deleted_at: int,
    ) -> bool:
        stmt = (
            update(WordbankEntry)
            .where(WordbankEntry.id == entry_id, WordbankEntry.deleted_at == 0)
            .values(deleted_at=deleted_at, updated_at=deleted_at)
        )
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount > 0

    @staticmethod
    def _mutation_permission_conditions(
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> tuple:
        if is_superuser:
            return ()
        allowed = [WordbankEntry.created_by == actor_user_id]
        if can_moderate_group and actor_group_id:
            allowed.append(WordbankEntry.group_id == actor_group_id)
        return (or_(*allowed),)


class WordbankTriggerOps(BaseOps[WordbankTrigger]):
    async def get_by_entry_ids(
        self,
        entry_ids: Sequence[int],
    ) -> Sequence[WordbankTrigger]:
        if not entry_ids:
            return []
        stmt = (
            select(WordbankTrigger)
            .where(WordbankTrigger.entry_id.in_(entry_ids))
            .order_by(WordbankTrigger.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class WordbankResponseOps(BaseOps[WordbankResponse]):
    async def get_by_entry_ids(
        self,
        entry_ids: Sequence[int],
    ) -> Sequence[WordbankResponse]:
        if not entry_ids:
            return []
        stmt = (
            select(WordbankResponse)
            .where(WordbankResponse.entry_id.in_(entry_ids))
            .order_by(WordbankResponse.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class WordbankImageOps(BaseOps[WordbankImage]):
    async def get_by_md5(self, md5: str) -> WordbankImage | None:
        stmt = select(WordbankImage).where(WordbankImage.md5 == md5)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_dhash_prefix(
        self, prefix: str, limit: int
    ) -> Sequence[WordbankImage]:
        stmt = (
            select(WordbankImage)
            .where(WordbankImage.dhash.startswith(prefix))
            .order_by(WordbankImage.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


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
        stmt = select(WordbankDeleteVote).where(WordbankDeleteVote.id == vote_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_vote_by_entry(
        self,
        entry_id: int,
        group_id: str,
    ) -> WordbankDeleteVote | None:
        stmt = (
            select(WordbankDeleteVote)
            .where(
                WordbankDeleteVote.entry_id == entry_id,
                WordbankDeleteVote.group_id == group_id,
                WordbankDeleteVote.status == "open",
            )
            .order_by(WordbankDeleteVote.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

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
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)


class WordbankDeleteVoteSupportOps(BaseOps[WordbankDeleteVoteSupport]):
    async def has_supported(self, vote_id: int, user_id: str) -> bool:
        stmt = select(WordbankDeleteVoteSupport.id).where(
            WordbankDeleteVoteSupport.vote_id == vote_id,
            WordbankDeleteVoteSupport.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

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


class WordbankLogOps(BaseOps[WordbankLog]):
    async def bulk_insert_logs(self, data: list[WordbankLogPayload]) -> int:
        if not data:
            return 0
        stmt = sqlite_insert(WordbankLog).values(data)
        result = await self.session.execute(stmt)
        return cast(CursorResult, result).rowcount
