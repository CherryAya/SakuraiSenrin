"""Wordbank repository."""

from collections import defaultdict

from sqlalchemy import or_, select

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time

from .instances import wordbank_log_db, wordbank_main_db
from .ops import (
    WordbankEntryOps,
    WordbankImageOps,
    WordbankResponseOps,
    WordbankTriggerOps,
)
from .tables import (
    WordbankEntry,
    WordbankImage,
    WordbankLog,
    WordbankLogBase,
    WordbankMainBase,
    WordbankResponse,
    WordbankTrigger,
)
from .types import (
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
