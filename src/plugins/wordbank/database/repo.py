"""Wordbank repository."""

from collections import defaultdict
import re
import unicodedata

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.message_model import (
    MessageShape,
    fingerprint_shape,
    shape_from_payload,
    shape_to_payload,
)

from .instances import wordbank_log_db, wordbank_main_db
from .ops import (
    WordbankApprovalMessageOps,
    WordbankDeleteVoteOps,
    WordbankDeleteVoteSupportOps,
    WordbankEntryDetailOps,
    WordbankEntryOps,
    WordbankImageOps,
    WordbankResponseMessageOps,
    WordbankResponseOps,
    WordbankTriggerOps,
)
from .tables import (
    WordbankApprovalMessage,
    WordbankDeleteVote,
    WordbankEntry,
    WordbankImage,
    WordbankLog,
    WordbankLogBase,
    WordbankMainBase,
    WordbankResponse,
    WordbankResponseMessage,
    WordbankSearchDocument,
    WordbankTrigger,
)
from .types import (
    WordbankApprovalMessagePayload,
    WordbankApprovalMessageRecord,
    WordbankDeleteVoteMutation,
    WordbankDeleteVoteRecord,
    WordbankEntryDetail,
    WordbankEntryRecord,
    WordbankImagePayload,
    WordbankImageRecord,
    WordbankLogPayload,
    WordbankResponseMessagePayload,
    WordbankResponseMessageRecord,
    WordbankResponseRecord,
    WordbankSearchItem,
    WordbankSearchRequest,
    WordbankTriggerRecord,
)
from .writers import wordbank_log_writer

_SPACE_RE = re.compile(r"\s+")
_MAX_GRAM_SIZE = 3
_SEARCH_RESULT_CANDIDATE_MULTIPLIER = 6
_SEARCH_RESULT_MIN_CANDIDATES = 64


def _normalize_search_text(text_value: str) -> str:
    normalized = unicodedata.normalize("NFKC", text_value).strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.casefold()


def _condensed_search_text(text_value: str) -> str:
    return _normalize_search_text(text_value).replace(" ", "")


def _build_ngram_tokens(text_value: str) -> str:
    condensed = _condensed_search_text(text_value)
    if not condensed:
        return ""
    tokens: list[str] = []
    for gram_size in range(1, min(_MAX_GRAM_SIZE, len(condensed)) + 1):
        for index in range(0, len(condensed) - gram_size + 1):
            tokens.append(condensed[index : index + gram_size])
    deduped = list(dict.fromkeys(tokens))
    return " ".join(deduped)


def _build_fts_query(text_value: str) -> str:
    condensed = _condensed_search_text(text_value)
    if not condensed:
        return ""
    gram_size = min(_MAX_GRAM_SIZE, len(condensed))
    tokens = [
        condensed[index : index + gram_size]
        for index in range(0, len(condensed) - gram_size + 1)
    ]
    return " AND ".join(f'"{token}"' for token in dict.fromkeys(tokens))


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
            trigger_text=row.trigger_text,
            message_shape=shape_from_payload(row.message_json),
            exact_md5=row.exact_md5,
            structure_key=row.structure_key,
            search_text=row.search_text,
            search_tokens=row.search_tokens,
            image_keys=row.image_keys,
            trigger_mode=row.trigger_mode,
        )

    @staticmethod
    def _to_response_record(row: WordbankResponse) -> WordbankResponseRecord:
        return WordbankResponseRecord(
            id=row.id,
            entry_id=row.entry_id,
            text=row.text,
            message_shape=shape_from_payload(row.message_json),
            exact_md5=row.exact_md5,
            structure_key=row.structure_key,
            search_text=row.search_text,
            search_tokens=row.search_tokens,
            image_keys=row.image_keys,
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
            hash_version=image.hash_version,
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

    @staticmethod
    def _to_response_message_record(
        row: WordbankResponseMessage,
    ) -> WordbankResponseMessageRecord:
        return WordbankResponseMessageRecord(
            message_id=row.message_id,
            entry_id=row.entry_id,
            trigger_id=row.trigger_id,
            response_id=row.response_id,
            group_id=row.group_id,
            user_id=row.user_id,
            message_type=row.message_type,
        )

    @staticmethod
    def _to_approval_message_record(
        row: WordbankApprovalMessage,
    ) -> WordbankApprovalMessageRecord:
        return WordbankApprovalMessageRecord(
            message_id=row.message_id,
            entry_id=row.entry_id,
            group_id=row.group_id,
            user_id=row.user_id,
            source_message_id=row.source_message_id,
            message_type=row.message_type,
        )

    @staticmethod
    def _to_entry_detail(
        entry: WordbankEntry,
        trigger: WordbankTrigger,
        response: WordbankResponse,
    ) -> WordbankEntryDetail:
        return WordbankEntryDetail(
            entry_id=entry.id,
            status=entry.status,
            enabled=entry.enabled,
            scope=entry.scope,
            probability=entry.probability,
            weight=entry.weight,
            group_id=entry.group_id,
            created_by=entry.created_by,
            deleted_at=entry.deleted_at,
            trigger_text=trigger.trigger_text,
            trigger_mode=trigger.trigger_mode,
            response_text=response.text,
        )

    @staticmethod
    def _format_trigger_text(trigger: WordbankTrigger) -> str:
        return trigger.trigger_text

    @staticmethod
    def _format_response_text(response: WordbankResponse) -> str:
        return response.text

    @classmethod
    def _document_payload(
        cls,
        entry: WordbankEntry,
        trigger: WordbankTrigger,
        response: WordbankResponse,
    ) -> dict[str, object]:
        return {
            "entry_id": entry.id,
            "status": entry.status,
            "scope": entry.scope,
            "group_id": entry.group_id,
            "created_by": entry.created_by,
            "deleted_at": entry.deleted_at,
            "probability": entry.probability,
            "weight": entry.weight,
            "trigger_text": trigger.trigger_text,
            "trigger_mode": trigger.trigger_mode,
            "trigger_exact_md5": trigger.exact_md5,
            "trigger_structure_key": trigger.structure_key,
            "trigger_image_keys": trigger.image_keys,
            "response_text": response.text,
            "response_exact_md5": response.exact_md5,
            "response_structure_key": response.structure_key,
            "response_image_keys": response.image_keys,
            "trigger_tokens": trigger.search_tokens,
            "response_tokens": response.search_tokens,
            "updated_at": entry.updated_at,
        }

    @staticmethod
    def _to_search_item(
        document: WordbankSearchDocument,
        *,
        score: float = 0.0,
        matched_by: str = "",
    ) -> WordbankSearchItem:
        return WordbankSearchItem(
            entry_id=document.entry_id,
            status=document.status,
            trigger_text=document.trigger_text,
            trigger_mode=document.trigger_mode,
            response_text=document.response_text,
            scope=document.scope,
            probability=document.probability,
            weight=document.weight,
            created_by=document.created_by,
            score=score,
            matched_by=matched_by,
        )

    async def create_message_entry(
        self,
        *,
        trigger_shape: MessageShape,
        response_shape: MessageShape,
        rule: dict,
        scope: str,
        priority: int,
        probability: float,
        weight: int,
        group_id: str,
        created_by: str,
        trigger_mode: str = "strict",
    ) -> WordbankEntryRecord:
        now = get_current_time()
        trigger_fingerprint = fingerprint_shape(trigger_shape)
        response_fingerprint = fingerprint_shape(response_shape)
        async with wordbank_main_db.write_session() as session:
            entry = WordbankEntry(
                status="pending",
                enabled=1,
                scope=scope,
                priority=priority,
                probability=probability,
                weight=weight,
                rule=rule,
                group_id=group_id,
                created_by=created_by,
                approved_by="",
                deleted_at=0,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            await session.flush()

            trigger = WordbankTrigger(
                entry_id=entry.id,
                trigger_text=trigger_fingerprint.summary_text,
                message_json=shape_to_payload(trigger_shape),
                exact_md5=trigger_fingerprint.exact_md5,
                structure_key=trigger_fingerprint.structure_key,
                search_text=trigger_fingerprint.search_text,
                search_tokens=trigger_fingerprint.search_tokens,
                image_keys=trigger_fingerprint.image_keys,
                trigger_mode=trigger_mode,
                created_at=now,
                updated_at=now,
            )
            response = WordbankResponse(
                entry_id=entry.id,
                text=response_fingerprint.summary_text,
                message_json=shape_to_payload(response_shape),
                exact_md5=response_fingerprint.exact_md5,
                structure_key=response_fingerprint.structure_key,
                search_text=response_fingerprint.search_text,
                search_tokens=response_fingerprint.search_tokens,
                image_keys=response_fingerprint.image_keys,
                weight=weight,
                created_at=now,
                updated_at=now,
            )
            session.add_all([trigger, response])
            await session.flush()
            await self._refresh_search_document_in_session(session, entry.id)

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

    async def ensure_search_index(self) -> None:
        async with wordbank_main_db.read_session() as session:
            entry_count = await session.scalar(
                select(func.count()).select_from(WordbankEntry)
            )
            doc_count = await session.scalar(
                select(func.count()).select_from(WordbankSearchDocument)
            )
            trigger_fts_count = await session.scalar(
                text("SELECT COUNT(*) FROM wordbank_search_trigger_fts")
            )
            response_fts_count = await session.scalar(
                text("SELECT COUNT(*) FROM wordbank_search_response_fts")
            )
        if (
            int(entry_count or 0) != int(doc_count or 0)
            or int(entry_count or 0) != int(trigger_fts_count or 0)
            or int(entry_count or 0) != int(response_fts_count or 0)
        ):
            await self.rebuild_search_index()

    async def rebuild_search_index(self) -> None:
        async with wordbank_main_db.read_session() as session:
            rows = (
                await session.execute(
                    select(WordbankEntry, WordbankTrigger, WordbankResponse)
                    .join(
                        WordbankTrigger,
                        WordbankTrigger.entry_id == WordbankEntry.id,
                    )
                    .join(
                        WordbankResponse,
                        WordbankResponse.entry_id == WordbankEntry.id,
                    )
                    .order_by(WordbankEntry.id.asc())
                )
            ).all()

        documents = [
            self._document_payload(entry, trigger, response)
            for entry, trigger, response in rows
        ]
        async with wordbank_main_db.write_session() as session:
            await session.execute(delete(WordbankSearchDocument))
            await session.execute(text("DELETE FROM wordbank_search_trigger_fts"))
            await session.execute(text("DELETE FROM wordbank_search_response_fts"))
            if documents:
                await session.execute(
                    sqlite_insert(WordbankSearchDocument),
                    documents,
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO wordbank_search_trigger_fts(rowid, tokens)
                        VALUES (:entry_id, :trigger_tokens)
                        """
                    ),
                    documents,
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO wordbank_search_response_fts(rowid, tokens)
                        VALUES (:entry_id, :response_tokens)
                        """
                    ),
                    documents,
                )

    async def search(
        self,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WordbankSearchItem]:
        async with wordbank_main_db.read_session() as session:
            candidate_limit = max(
                _SEARCH_RESULT_MIN_CANDIDATES,
                (offset + limit) * _SEARCH_RESULT_CANDIDATE_MULTIPLIER,
            )
            text_scores, text_sources = await self._search_text_scores(
                session,
                request.keyword,
                field=request.field,
                limit=candidate_limit,
            )
            image_scores, image_sources = await self._search_image_scores(
                session,
                request.image_scores,
                field=request.field,
                creator_id=request.creator_id,
            )

            candidate_ids = set(text_scores) | set(image_scores)
            if not candidate_ids:
                stmt = (
                    select(WordbankSearchDocument)
                    .where(WordbankSearchDocument.deleted_at == 0)
                    .order_by(WordbankSearchDocument.entry_id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                if request.creator_id:
                    stmt = stmt.where(
                        WordbankSearchDocument.created_by == request.creator_id
                    )
                documents = (await session.execute(stmt)).scalars().all()
                if request.keyword or request.image_scores or request.has_image:
                    return []
                return [self._to_search_item(document) for document in documents]

            stmt = select(WordbankSearchDocument).where(
                WordbankSearchDocument.entry_id.in_(candidate_ids),
                WordbankSearchDocument.deleted_at == 0,
            )
            if request.creator_id:
                stmt = stmt.where(
                    WordbankSearchDocument.created_by == request.creator_id
                )
            documents = (await session.execute(stmt)).scalars().all()

        ranked: list[tuple[float, int, str, WordbankSearchDocument]] = []
        for document in documents:
            text_score = text_scores.get(document.entry_id, 0.0)
            image_score = image_scores.get(document.entry_id, 0.0)
            final_score = max(text_score, image_score)
            if text_score and image_score:
                final_score = min(1.0, final_score + 0.2 * min(text_score, image_score))
            matched_by = ",".join(
                part
                for part in {
                    text_sources.get(document.entry_id, ""),
                    image_sources.get(document.entry_id, ""),
                }
                if part
            )
            ranked.append((final_score, document.entry_id, matched_by, document))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        paged = ranked[offset : offset + limit]
        return [
            self._to_search_item(document, score=score, matched_by=matched_by)
            for score, _, matched_by, document in paged
        ]

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
        keyword = keyword.strip()
        async with wordbank_main_db.read_session() as session:
            stmt = (
                select(WordbankEntry, WordbankTrigger, WordbankResponse)
                .join(WordbankTrigger, WordbankTrigger.entry_id == WordbankEntry.id)
                .join(WordbankResponse, WordbankResponse.entry_id == WordbankEntry.id)
                .where(
                    WordbankEntry.status == "pending",
                    WordbankEntry.deleted_at == 0,
                )
                .order_by(WordbankEntry.id.asc())
                .limit(limit)
                .offset(offset)
            )
            if not is_superuser:
                if not (can_moderate_group and actor_group_id):
                    return []
                stmt = stmt.where(WordbankEntry.group_id == actor_group_id)
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
                status=entry.status,
                trigger_text=self._format_trigger_text(trigger),
                trigger_mode=trigger.trigger_mode,
                response_text=self._format_response_text(response),
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
            ok = await WordbankEntryOps(session).mark_deleted(
                entry_id,
                now,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            )
            if ok:
                await self._refresh_search_document_in_session(session, entry_id)
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
        now = get_current_time()
        async with wordbank_main_db.write_session() as session:
            ok = await WordbankEntryOps(session).restore(
                entry_id,
                now,
                actor_user_id=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            )
            if ok:
                await self._refresh_search_document_in_session(session, entry_id)
            return ok

    async def approve_entry(
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
            ok = await WordbankEntryOps(session).approve_pending(
                entry_id,
                now,
                approved_by=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            )
            if ok:
                await self._refresh_search_document_in_session(session, entry_id)
            return ok

    async def reject_entry(
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
            ok = await WordbankEntryOps(session).reject_pending(
                entry_id,
                now,
                reviewed_by=actor_user_id,
                actor_group_id=actor_group_id,
                can_moderate_group=can_moderate_group,
                is_superuser=is_superuser,
            )
            if ok:
                await self._refresh_search_document_in_session(session, entry_id)
            return ok

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
            if entry.status != "approved":
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

    async def record_response_message(
        self,
        payload: WordbankResponseMessagePayload,
    ) -> None:
        async with wordbank_main_db.write_session() as session:
            await WordbankResponseMessageOps(session).upsert_response_message(payload)

    async def record_approval_message(
        self,
        payload: WordbankApprovalMessagePayload,
    ) -> None:
        async with wordbank_main_db.write_session() as session:
            await WordbankApprovalMessageOps(session).upsert_approval_message(payload)

    async def get_response_message(
        self,
        message_id: str,
    ) -> WordbankResponseMessageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = await WordbankResponseMessageOps(session).get_by_message_id(
                message_id
            )
        return self._to_response_message_record(row) if row else None

    async def get_approval_message(
        self,
        message_id: str,
    ) -> WordbankApprovalMessageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = await WordbankApprovalMessageOps(session).get_by_message_id(
                message_id
            )
        return self._to_approval_message_record(row) if row else None

    async def get_entry_detail(
        self,
        entry_id: int,
        *,
        trigger_id: int | None = None,
        response_id: int | None = None,
    ) -> WordbankEntryDetail | None:
        async with wordbank_main_db.read_session() as session:
            row = await WordbankEntryDetailOps(session).get_detail(
                entry_id,
                trigger_id=trigger_id,
                response_id=response_id,
            )
        if row is None:
            return None
        entry, trigger, response = row
        return self._to_entry_detail(entry, trigger, response)

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
            if entry_deleted:
                await self._refresh_search_document_in_session(
                    vote_ops.session,
                    entry_id,
                )

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

    async def _search_text_scores(
        self,
        session: AsyncSession,
        keyword: str,
        *,
        field: str,
        limit: int,
    ) -> tuple[dict[int, float], dict[int, str]]:
        query = _build_fts_query(keyword)
        if not query:
            return {}, {}
        scores: dict[int, float] = {}
        sources: dict[int, str] = {}
        if field == "trigger":
            tables = ["trigger"]
        elif field == "response":
            tables = ["response"]
        else:
            tables = ["trigger", "response"]
        for table_name in tables:
            sql = text(
                f"""
                SELECT rowid AS entry_id
                FROM wordbank_search_{table_name}_fts
                WHERE wordbank_search_{table_name}_fts MATCH :query
                ORDER BY bm25(wordbank_search_{table_name}_fts)
                LIMIT :limit
                """
            )
            rows = (await session.execute(sql, {"query": query, "limit": limit})).all()
            total = len(rows)
            for index, row in enumerate(rows):
                entry_id = int(row.entry_id)
                score = (total - index) / max(total, 1)
                if score > scores.get(entry_id, 0.0):
                    scores[entry_id] = score
                    sources[entry_id] = f"text:{table_name}"
        return scores, sources

    async def _search_image_scores(
        self,
        session: AsyncSession,
        image_scores: dict[int, float],
        *,
        field: str,
        creator_id: str,
    ) -> tuple[dict[int, float], dict[int, str]]:
        if not image_scores:
            return {}, {}
        stmt = select(WordbankSearchDocument).where(
            WordbankSearchDocument.deleted_at == 0,
        )
        if creator_id:
            stmt = stmt.where(WordbankSearchDocument.created_by == creator_id)
        canonical_ids = tuple(image_scores)
        trigger_patterns = [
            WordbankSearchDocument.trigger_image_keys.like(f"%|{canonical_id}|%")
            for canonical_id in canonical_ids
        ]
        response_patterns = [
            WordbankSearchDocument.response_image_keys.like(f"%|{canonical_id}|%")
            for canonical_id in canonical_ids
        ]
        if field == "trigger":
            stmt = stmt.where(or_(*trigger_patterns))
        elif field == "response":
            stmt = stmt.where(or_(*response_patterns))
        else:
            stmt = stmt.where(
                or_(
                    *trigger_patterns,
                    *response_patterns,
                )
            )
        documents = (await session.execute(stmt)).scalars().all()
        scores: dict[int, float] = {}
        sources: dict[int, str] = {}
        for document in documents:
            matched: list[tuple[float, str]] = []
            for canonical_id, score in image_scores.items():
                if (
                    field in {"all", "trigger"}
                    and f"|{canonical_id}|" in document.trigger_image_keys
                ):
                    matched.append((score, "image:trigger"))
                if (
                    field in {"all", "response"}
                    and f"|{canonical_id}|" in document.response_image_keys
                ):
                    matched.append((score, "image:response"))
            if not matched:
                continue
            score, source = max(matched, key=lambda item: item[0])
            scores[document.entry_id] = score
            sources[document.entry_id] = source
        return scores, sources

    async def _refresh_search_document_in_session(
        self,
        session: AsyncSession,
        entry_id: int,
    ) -> None:
        row = (
            await session.execute(
                select(WordbankEntry, WordbankTrigger, WordbankResponse)
                .join(WordbankTrigger, WordbankTrigger.entry_id == WordbankEntry.id)
                .join(WordbankResponse, WordbankResponse.entry_id == WordbankEntry.id)
                .where(WordbankEntry.id == entry_id)
                .order_by(WordbankTrigger.id.asc(), WordbankResponse.id.asc())
                .limit(1)
            )
        ).first()
        if row is None:
            await session.execute(
                delete(WordbankSearchDocument).where(
                    WordbankSearchDocument.entry_id == entry_id
                )
            )
            await session.execute(
                text("DELETE FROM wordbank_search_trigger_fts WHERE rowid = :entry_id"),
                {"entry_id": entry_id},
            )
            await session.execute(
                text(
                    "DELETE FROM wordbank_search_response_fts WHERE rowid = :entry_id"
                ),
                {"entry_id": entry_id},
            )
            return

        entry, trigger, response = row
        payload = self._document_payload(entry, trigger, response)
        await session.execute(
            sqlite_insert(WordbankSearchDocument)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=[WordbankSearchDocument.entry_id],
                set_=payload,
            )
        )
        await session.execute(
            text("DELETE FROM wordbank_search_trigger_fts WHERE rowid = :entry_id"),
            {"entry_id": entry_id},
        )
        await session.execute(
            text("DELETE FROM wordbank_search_response_fts WHERE rowid = :entry_id"),
            {"entry_id": entry_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO wordbank_search_trigger_fts(rowid, tokens)
                VALUES (:entry_id, :trigger_tokens)
                """
            ),
            payload,
        )
        await session.execute(
            text(
                """
                INSERT INTO wordbank_search_response_fts(rowid, tokens)
                VALUES (:entry_id, :response_tokens)
                """
            ),
            payload,
        )
