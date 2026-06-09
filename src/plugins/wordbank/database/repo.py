"""Wordbank repository."""

from collections import defaultdict

from sqlalchemy import or_, select

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time

from .instances import wordbank_log_db, wordbank_main_db
from .ops import (
    WordbankDeleteVoteOps,
    WordbankDeleteVoteSupportOps,
    WordbankEntryOps,
    WordbankImageOps,
    WordbankResponseOps,
    WordbankTriggerOps,
)
from .tables import (
    WordbankDeleteVote,
    WordbankEntry,
    WordbankImage,
    WordbankLog,
    WordbankLogBase,
    WordbankMainBase,
    WordbankResponse,
    WordbankTrigger,
)
from .types import (
    WordbankDeleteVoteMutation,
    WordbankDeleteVoteRecord,
    WordbankEntryRecord,
    WordbankImagePayload,
    WordbankImageRecord,
    WordbankLogPayload,
    WordbankResponseRecord,
    WordbankSearchItem,
    WordbankTriggerRecord,
)
from .writers import wordbank_log_writer


class WordbankRepository:
    """Repository for wordbank entries, media and logs."""

    @classmethod
    async def init_all_tables(cls) -> None:
        await wordbank_main_db.init(WordbankMainBase)
        await wordbank_log_db.init(WordbankLogBase)

    @staticmethod
    def _to_trigger_record(row: WordbankTrigger) -> WordbankTriggerRecord:
        return WordbankTriggerRecord(
            id=row.id,
            entry_id=row.entry_id,
            kind=row.kind,
            trigger_text=row.trigger_text,
            normalized_text=row.normalized_text,
            trigger_mode=row.trigger_mode,
            canonical_image_id=row.canonical_image_id,
        )

    @staticmethod
    def _to_response_record(row: WordbankResponse) -> WordbankResponseRecord:
        return WordbankResponseRecord(
            id=row.id,
            entry_id=row.entry_id,
            kind=row.kind,
            text=row.text,
            canonical_image_id=row.canonical_image_id,
            weight=row.weight,
        )

    @staticmethod
    def _to_image_record(image: WordbankImage) -> WordbankImageRecord:
        return WordbankImageRecord(
            id=image.id,
            canonical_image_id=image.canonical_image_id,
            md5=image.md5,
            dhash=image.dhash,
            phash=image.phash,
            width=image.width,
            height=image.height,
            file_size=image.file_size,
            storage_path=image.storage_path,
        )

    @staticmethod
    def _to_delete_vote_record(
        vote: WordbankDeleteVote,
        *,
        support_count: int,
    ) -> WordbankDeleteVoteRecord:
        return WordbankDeleteVoteRecord(
            id=vote.id,
            entry_id=vote.entry_id,
            group_id=vote.group_id,
            created_by=vote.created_by,
            status=vote.status,
            threshold=vote.threshold,
            support_count=support_count,
            reason=vote.reason,
            created_at=vote.created_at,
            updated_at=vote.updated_at,
        )

    async def create_text_entry(
        self,
        *,
        trigger_text: str,
        normalized_text: str,
        response_text: str,
        trigger_mode: str,
        rule: dict,
        scope: str,
        priority: int,
        probability: float,
        weight: int,
        group_id: str,
        created_by: str,
    ) -> WordbankEntryRecord:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            entry = WordbankEntry(
                status="approved",
                enabled=1,
                scope=scope,
                priority=priority,
                probability=probability,
                weight=weight,
                rule=rule,
                group_id=group_id,
                created_by=created_by,
                approved_by=created_by,
                deleted_at=0,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            await session.flush()

            trigger = WordbankTrigger(
                entry_id=entry.id,
                kind="text",
                trigger_text=trigger_text,
                normalized_text=normalized_text,
                trigger_mode=trigger_mode,
                canonical_image_id=None,
                created_at=now,
                updated_at=now,
            )
            response = WordbankResponse(
                entry_id=entry.id,
                kind="text",
                text=response_text,
                canonical_image_id=None,
                weight=weight,
                created_at=now,
                updated_at=now,
            )
            session.add_all([trigger, response])
            await session.flush()

            return WordbankEntryRecord(
                id=entry.id,
                status=entry.status,
                enabled=entry.enabled,
                scope=entry.scope,
                priority=entry.priority,
                probability=entry.probability,
                weight=entry.weight,
                rule=dict(entry.rule or {}),
                group_id=entry.group_id,
                created_by=entry.created_by,
                deleted_at=entry.deleted_at,
                triggers=(self._to_trigger_record(trigger),),
                responses=(self._to_response_record(response),),
            )

    async def create_image_entry(
        self,
        *,
        canonical_image_id: int,
        response_text: str,
        rule: dict,
        scope: str,
        priority: int,
        probability: float,
        weight: int,
        group_id: str,
        created_by: str,
    ) -> WordbankEntryRecord:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            entry = WordbankEntry(
                status="approved",
                enabled=1,
                scope=scope,
                priority=priority,
                probability=probability,
                weight=weight,
                rule=rule,
                group_id=group_id,
                created_by=created_by,
                approved_by=created_by,
                deleted_at=0,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            await session.flush()

            trigger = WordbankTrigger(
                entry_id=entry.id,
                kind="image",
                trigger_text="",
                normalized_text="",
                trigger_mode="fullmatch",
                canonical_image_id=canonical_image_id,
                created_at=now,
                updated_at=now,
            )
            response = WordbankResponse(
                entry_id=entry.id,
                kind="text",
                text=response_text,
                canonical_image_id=None,
                weight=weight,
                created_at=now,
                updated_at=now,
            )
            session.add_all([trigger, response])
            await session.flush()

            return WordbankEntryRecord(
                id=entry.id,
                status=entry.status,
                enabled=entry.enabled,
                scope=entry.scope,
                priority=entry.priority,
                probability=entry.probability,
                weight=entry.weight,
                rule=dict(entry.rule or {}),
                group_id=entry.group_id,
                created_by=entry.created_by,
                deleted_at=entry.deleted_at,
                triggers=(self._to_trigger_record(trigger),),
                responses=(self._to_response_record(response),),
            )

    async def list_enabled_entries(self) -> list[WordbankEntryRecord]:
        async with wordbank_main_db.read_session() as session:
            entry_ops = WordbankEntryOps(session)
            entries = list(await entry_ops.get_active_entries())
            entry_ids = [entry.id for entry in entries]
            trigger_rows = await WordbankTriggerOps(session).get_by_entry_ids(entry_ids)
            response_rows = await WordbankResponseOps(session).get_by_entry_ids(
                entry_ids
            )

        triggers: dict[int, list[WordbankTriggerRecord]] = defaultdict(list)
        for row in trigger_rows:
            triggers[row.entry_id].append(self._to_trigger_record(row))

        responses: dict[int, list[WordbankResponseRecord]] = defaultdict(list)
        for row in response_rows:
            responses[row.entry_id].append(self._to_response_record(row))

        return [
            WordbankEntryRecord(
                id=entry.id,
                status=entry.status,
                enabled=entry.enabled,
                scope=entry.scope,
                priority=entry.priority,
                probability=entry.probability,
                weight=entry.weight,
                rule=dict(entry.rule or {}),
                group_id=entry.group_id,
                created_by=entry.created_by,
                deleted_at=entry.deleted_at,
                triggers=tuple(triggers.get(entry.id, [])),
                responses=tuple(responses.get(entry.id, [])),
            )
            for entry in entries
        ]

    async def search(
        self,
        keyword: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WordbankSearchItem]:
        keyword = keyword.strip()
        async with wordbank_main_db.read_session() as session:
            stmt = (
                select(WordbankEntry, WordbankTrigger, WordbankResponse)
                .join(WordbankTrigger, WordbankTrigger.entry_id == WordbankEntry.id)
                .join(WordbankResponse, WordbankResponse.entry_id == WordbankEntry.id)
                .where(WordbankEntry.deleted_at == 0)
                .order_by(WordbankEntry.id.desc())
                .limit(limit)
                .offset(offset)
            )
            if keyword:
                pattern = f"%{keyword}%"
                stmt = stmt.where(
                    or_(
                        WordbankTrigger.trigger_text.like(pattern),
                        WordbankResponse.text.like(pattern),
                    )
                )
            rows = (await session.execute(stmt)).all()

        return [
            WordbankSearchItem(
                entry_id=entry.id,
                trigger_text=trigger.trigger_text,
                trigger_mode=trigger.trigger_mode,
                response_text=response.text,
                scope=entry.scope,
                probability=entry.probability,
                weight=entry.weight,
                created_by=entry.created_by,
            )
            for entry, trigger, response in rows
        ]

    async def delete_entry(
        self,
        entry_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            return await WordbankEntryOps(session).mark_deleted(
                entry_id,
                now,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            )

    async def restore_entry(
        self,
        entry_id: int,
        *,
        actor_user_id: str,
        actor_group_id: str,
        can_moderate_group: bool,
        is_superuser: bool,
    ) -> bool:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            return await WordbankEntryOps(session).restore(
                entry_id,
                now,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            )

    async def request_delete_vote(
        self,
        *,
        entry_id: int,
        group_id: str,
        user_id: str,
        threshold: int,
        reason: str = "",
    ) -> WordbankDeleteVoteMutation | None:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            entry = await session.get(WordbankEntry, entry_id)
            if entry is None or entry.deleted_at != 0:
                return None
            if not self._entry_allows_group_vote(entry, group_id):
                return None

            vote_ops = WordbankDeleteVoteOps(session)
            support_ops = WordbankDeleteVoteSupportOps(session)
            vote = await vote_ops.get_open_vote_by_entry(entry_id, group_id)
            created = False
            if vote is None:
                vote = await vote_ops.create_vote(
                    {
                        "entry_id": entry_id,
                        "group_id": group_id,
                        "created_by": user_id,
                        "status": "open",
                        "threshold": threshold,
                        "reason": reason,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                created = True

            return await self._support_delete_vote_in_session(
                entry_id=entry_id,
                vote=vote,
                user_id=user_id,
                now=now,
                created=created,
                vote_ops=vote_ops,
                support_ops=support_ops,
                entry_ops=WordbankEntryOps(session),
            )

    async def support_delete_vote(
        self,
        *,
        vote_id: int,
        group_id: str,
        user_id: str,
    ) -> WordbankDeleteVoteMutation | None:
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            vote_ops = WordbankDeleteVoteOps(session)
            vote = await vote_ops.get_vote_by_id(vote_id)
            if vote is None:
                return None
            if vote.group_id != group_id:
                return None
            return await self._support_delete_vote_in_session(
                entry_id=vote.entry_id,
                vote=vote,
                user_id=user_id,
                now=now,
                created=False,
                vote_ops=vote_ops,
                support_ops=WordbankDeleteVoteSupportOps(session),
                entry_ops=WordbankEntryOps(session),
            )

    async def get_delete_vote(
        self,
        vote_id: int,
        *,
        group_id: str,
    ) -> WordbankDeleteVoteRecord | None:
        async with wordbank_main_db.read_session() as session:
            vote_ops = WordbankDeleteVoteOps(session)
            vote = await vote_ops.get_vote_by_id(vote_id)
            if vote is None or vote.group_id != group_id:
                return None
            support_count = await vote_ops.support_count(vote.id)
            return self._to_delete_vote_record(vote, support_count=support_count)

    @staticmethod
    def _entry_allows_group_vote(entry: WordbankEntry, group_id: str) -> bool:
        if not group_id:
            return False
        if entry.scope == "all_groups":
            return True
        if entry.scope in {"current_group", "self_in_current_group"}:
            return entry.group_id == group_id
        return False

    async def _support_delete_vote_in_session(
        self,
        *,
        entry_id: int,
        vote: WordbankDeleteVote,
        user_id: str,
        now: int,
        created: bool,
        vote_ops: WordbankDeleteVoteOps,
        support_ops: WordbankDeleteVoteSupportOps,
        entry_ops: WordbankEntryOps,
    ) -> WordbankDeleteVoteMutation:
        support_count = await vote_ops.support_count(vote.id)
        if vote.status != "open":
            return WordbankDeleteVoteMutation(
                vote=self._to_delete_vote_record(vote, support_count=support_count),
                created=created,
                already_supported=True,
                passed=vote.status == "passed",
                entry_deleted=False,
            )

        already_supported = await support_ops.has_supported(vote.id, user_id)
        if not already_supported:
            await support_ops.create_support(
                vote_id=vote.id,
                user_id=user_id,
                created_at=now,
            )
            support_count += 1

        passed = support_count >= vote.threshold
        entry_deleted = False
        if passed:
            entry_deleted = await entry_ops.mark_deleted_by_vote(entry_id, now)
            vote.status = "passed" if entry_deleted else "closed"
            vote.updated_at = now
            await vote_ops.update_status(vote.id, vote.status, now)

        return WordbankDeleteVoteMutation(
            vote=self._to_delete_vote_record(vote, support_count=support_count),
            created=created,
            already_supported=already_supported,
            passed=passed,
            entry_deleted=entry_deleted,
        )

    async def get_image_by_md5(self, md5: str) -> WordbankImageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = await WordbankImageOps(session).get_by_md5(md5)
        return self._to_image_record(row) if row else None

    async def get_image_candidates(
        self,
        dhash_prefix: str,
        *,
        limit: int = 128,
    ) -> list[WordbankImageRecord]:
        async with wordbank_main_db.read_session() as session:
            rows = await WordbankImageOps(session).get_by_dhash_prefix(
                dhash_prefix,
                limit,
            )
        return [self._to_image_record(row) for row in rows]

    async def create_image(
        self,
        payload: WordbankImagePayload,
    ) -> WordbankImageRecord:
        async with wordbank_main_db.write_session() as session:
            image = WordbankImage(**payload)
            session.add(image)
            await session.flush()
            if image.canonical_image_id is None:
                image.canonical_image_id = image.id
                await session.flush()
            return self._to_image_record(image)

    async def list_images(self) -> list[WordbankImageRecord]:
        async with wordbank_main_db.read_session() as session:
            rows = (await session.execute(select(WordbankImage))).scalars().all()
        return [self._to_image_record(row) for row in rows]

    async def save_log(
        self,
        payload: WordbankLogPayload,
        *,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        if policy == WritePolicy.BUFFERED:
            await wordbank_log_writer.add(payload)
            return

        async with wordbank_log_db.write_session() as session:
            session.add(WordbankLog(**payload))

    async def drain_logs(self) -> None:
        await wordbank_log_writer.drain()

    async def warm_up(self) -> None:
        await self.list_enabled_entries()
