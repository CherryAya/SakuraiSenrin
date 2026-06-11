"""Wordbank repository."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
import json
import re
import unicodedata

from sqlalchemy import delete, exists, func, or_, select, text
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
from .tables import (
    WordbankApprovalMessage,
    WordbankDeleteVote,
    WordbankDeleteVoteSupport,
    WordbankImage,
    WordbankLog,
    WordbankLogBase,
    WordbankMainBase,
    WordbankResponseItem,
    WordbankResponseMessage,
    WordbankSearchDocument,
    WordbankSearchImageMap,
    WordbankTriggerGroup,
    WordbankTriggerVariant,
)
from .types import (
    WordbankApprovalMessagePayload,
    WordbankApprovalMessageRecord,
    WordbankCreatedResponse,
    WordbankDeleteVoteMutation,
    WordbankDeleteVoteRecord,
    WordbankGroupDetail,
    WordbankImagePayload,
    WordbankImageRecord,
    WordbankLogPayload,
    WordbankResponseItemDetail,
    WordbankResponseItemRecord,
    WordbankResponseMessagePayload,
    WordbankResponseMessageRecord,
    WordbankSearchItem,
    WordbankSearchPage,
    WordbankSearchRequest,
    WordbankTriggerGroupRecord,
    WordbankTriggerVariantRecord,
)
from .writers import wordbank_log_writer

_SPACE_RE = re.compile(r"\s+")
_MAX_GRAM_SIZE = 3
_SEARCH_RESULT_CANDIDATE_MULTIPLIER = 6
_SEARCH_RESULT_MIN_CANDIDATES = 64
_SEARCH_PREVIEW_LIMIT = 3


@dataclass(slots=True)
class _GroupBundle:
    group: WordbankTriggerGroup
    variants: list[WordbankTriggerVariant]
    responses: list[WordbankResponseItem]


def _normalize_search_text(text_value: str) -> str:
    normalized = unicodedata.normalize("NFKC", text_value).strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.casefold()


def _condensed_search_text(text_value: str) -> str:
    return _normalize_search_text(text_value).replace(" ", "")


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


def _parse_image_keys(image_keys: str) -> tuple[int, ...]:
    values = [part for part in image_keys.strip("|").split("|") if part]
    parsed: list[int] = []
    for value in values:
        if value.isdigit():
            parsed.append(int(value))
    return tuple(parsed)


def _merge_image_keys(image_keys_values: Sequence[str]) -> str:
    merged: list[int] = []
    seen: set[int] = set()
    for image_keys in image_keys_values:
        for image_id in _parse_image_keys(image_keys):
            if image_id in seen:
                continue
            seen.add(image_id)
            merged.append(image_id)
    if not merged:
        return ""
    return "|" + "|".join(str(image_id) for image_id in merged) + "|"


def _group_status_from_responses(
    responses: Sequence[WordbankResponseItem],
) -> tuple[str, int, int]:
    undeleted = [response for response in responses if response.deleted_at == 0]
    active = [
        response
        for response in undeleted
        if response.status == "approved" and response.enabled == 1
    ]
    if active:
        return "approved", 1, 0
    pending = [response for response in undeleted if response.status == "pending"]
    if pending:
        return "pending", 0, 0
    rejected = [response for response in undeleted if response.status == "rejected"]
    if rejected:
        return "rejected", 0, 0
    deleted_at = max((response.deleted_at for response in responses), default=0)
    return "empty", 0, deleted_at


def _representative_response(
    responses: Sequence[WordbankResponseItem],
) -> WordbankResponseItem | None:
    if not responses:
        return None

    def _sort_key(response: WordbankResponseItem) -> tuple[int, int, int]:
        active_rank = (
            0
            if response.deleted_at == 0
            and response.status == "approved"
            and response.enabled == 1
            else 1
        )
        deleted_rank = 0 if response.deleted_at == 0 else 1
        return (active_rank, deleted_rank, response.id)

    return min(responses, key=_sort_key)


def _search_preview_responses(
    responses: Sequence[WordbankResponseItem],
) -> tuple[WordbankResponseItem, ...]:
    visible = [response for response in responses if response.deleted_at == 0]
    visible.sort(
        key=lambda response: (
            0 if response.status == "approved" and response.enabled == 1 else 1,
            response.id,
        )
    )
    return tuple(visible[:_SEARCH_PREVIEW_LIMIT])


class WordbankRepository:
    """Repository for wordbank trigger groups, responses, media and logs."""

    @classmethod
    async def init_all_tables(cls) -> None:
        await wordbank_main_db.init(WordbankMainBase)
        await wordbank_log_db.init(WordbankLogBase)

    @staticmethod
    def _to_trigger_variant_record(
        row: WordbankTriggerVariant,
    ) -> WordbankTriggerVariantRecord:
        return WordbankTriggerVariantRecord(
            id=row.id,
            trigger_group_id=row.trigger_group_id,
            trigger_text=row.trigger_text,
            message_shape=shape_from_payload(row.message_json),
            exact_md5=row.exact_md5,
            structure_key=row.structure_key,
            search_text=row.search_text,
            search_tokens=row.search_tokens,
            image_keys=row.image_keys,
        )

    @staticmethod
    def _to_response_item_record(
        row: WordbankResponseItem,
    ) -> WordbankResponseItemRecord:
        return WordbankResponseItemRecord(
            id=row.id,
            trigger_group_id=row.trigger_group_id,
            status=row.status,
            enabled=row.enabled,
            scope=row.scope,
            priority=row.priority,
            probability=row.probability,
            weight=row.weight,
            rule=dict(row.rule or {}),
            group_id=row.group_id,
            created_by=row.created_by,
            approved_by=row.approved_by,
            deleted_at=row.deleted_at,
            text=row.text,
            message_shape=shape_from_payload(row.message_json),
            exact_md5=row.exact_md5,
            structure_key=row.structure_key,
            search_text=row.search_text,
            search_tokens=row.search_tokens,
            image_keys=row.image_keys,
        )

    @classmethod
    def _to_group_record(
        cls,
        group: WordbankTriggerGroup,
        variants: Sequence[WordbankTriggerVariant],
        responses: Sequence[WordbankResponseItem],
    ) -> WordbankTriggerGroupRecord:
        return WordbankTriggerGroupRecord(
            id=group.id,
            status=group.status,
            enabled=group.enabled,
            group_id=group.group_id,
            created_by=group.created_by,
            deleted_at=group.deleted_at,
            trigger_variants=tuple(
                cls._to_trigger_variant_record(item) for item in variants
            ),
            responses=tuple(cls._to_response_item_record(item) for item in responses),
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
            trigger_group_id=vote.trigger_group_id,
            response_item_id=vote.response_item_id,
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
            trigger_group_id=row.trigger_group_id,
            trigger_variant_id=row.trigger_variant_id,
            response_item_id=row.response_item_id,
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
            trigger_group_id=row.trigger_group_id,
            response_item_id=row.response_item_id,
            group_id=row.group_id,
            user_id=row.user_id,
            source_message_id=row.source_message_id,
            message_type=row.message_type,
        )

    @staticmethod
    def _search_item_from_document(
        document: WordbankSearchDocument,
        *,
        score: float = 0.0,
        matched_by: str = "",
    ) -> WordbankSearchItem:
        raw_summaries = json.loads(document.response_preview_json or "[]")
        response_summaries = tuple(str(item) for item in raw_summaries if str(item))
        return WordbankSearchItem(
            trigger_group_id=document.trigger_group_id,
            status=document.status,
            trigger_text=document.trigger_text,
            response_text=document.response_text,
            response_summaries=response_summaries
            or ((document.response_text,) if document.response_text else ()),
            response_count=document.response_count,
            active_response_count=document.active_response_count,
            scope=document.scope,
            probability=document.probability,
            weight=document.weight,
            created_by=document.created_by,
            score=score,
            matched_by=matched_by,
        )

    @staticmethod
    def _pending_item_from_rows(
        group: WordbankTriggerGroup,
        variant: WordbankTriggerVariant,
        response: WordbankResponseItem,
    ) -> WordbankSearchItem:
        return WordbankSearchItem(
            trigger_group_id=group.id,
            status=response.status,
            trigger_text=variant.trigger_text,
            response_text=response.text,
            response_summaries=(response.text,),
            response_count=1,
            active_response_count=1
            if response.status == "approved" and response.enabled == 1
            else 0,
            scope=response.scope,
            probability=response.probability,
            weight=response.weight,
            created_by=response.created_by,
            response_item_ids=(response.id,),
        )

    @classmethod
    def _group_detail_from_bundle(
        cls,
        bundle: _GroupBundle,
        *,
        response_item_id: int | None = None,
    ) -> WordbankGroupDetail:
        variant = bundle.variants[0]
        responses = tuple(
            WordbankResponseItemDetail(
                response_item_id=response.id,
                status=response.status,
                enabled=response.enabled,
                scope=response.scope,
                probability=response.probability,
                weight=response.weight,
                rule=dict(response.rule or {}),
                group_id=response.group_id,
                created_by=response.created_by,
                approved_by=response.approved_by,
                deleted_at=response.deleted_at,
                response_text=response.text,
            )
            for response in bundle.responses
        )
        return WordbankGroupDetail(
            trigger_group_id=bundle.group.id,
            status=bundle.group.status,
            enabled=bundle.group.enabled,
            group_id=bundle.group.group_id,
            created_by=bundle.group.created_by,
            deleted_at=bundle.group.deleted_at,
            trigger_text=variant.trigger_text,
            trigger_variant_id=variant.id,
            responses=responses,
            selected_response_item_id=response_item_id,
        )

    @staticmethod
    def _document_payload(bundle: _GroupBundle) -> dict[str, object] | None:
        if not bundle.variants:
            return None
        visible_responses = [
            response for response in bundle.responses if response.deleted_at == 0
        ]
        if not visible_responses:
            return None
        variant = bundle.variants[0]
        preview_responses = _search_preview_responses(bundle.responses)
        representative = _representative_response(visible_responses)
        if representative is None:
            return None
        preview_summaries = [
            response.text for response in preview_responses if response.text
        ]
        response_tokens = " ".join(
            token
            for response in visible_responses
            for token in [response.search_tokens]
            if token
        )
        return {
            "trigger_group_id": bundle.group.id,
            "status": bundle.group.status,
            "enabled": bundle.group.enabled,
            "group_id": bundle.group.group_id,
            "created_by": bundle.group.created_by,
            "deleted_at": bundle.group.deleted_at,
            "scope": representative.scope,
            "probability": representative.probability,
            "weight": representative.weight,
            "trigger_text": variant.trigger_text,
            "trigger_exact_md5": variant.exact_md5,
            "trigger_structure_key": variant.structure_key,
            "trigger_image_keys": variant.image_keys,
            "response_text": representative.text,
            "response_preview_json": json.dumps(preview_summaries, ensure_ascii=False),
            "response_count": len(visible_responses),
            "active_response_count": sum(
                1
                for response in visible_responses
                if response.status == "approved" and response.enabled == 1
            ),
            "response_image_keys": _merge_image_keys(
                [response.image_keys for response in visible_responses]
            ),
            "trigger_tokens": variant.search_tokens,
            "response_tokens": response_tokens,
            "updated_at": max(
                [bundle.group.updated_at, variant.updated_at]
                + [response.updated_at for response in bundle.responses]
            ),
        }

    @staticmethod
    def _image_map_payloads(payload: dict[str, object]) -> list[dict[str, int | str]]:
        raw_group_id = payload.get("trigger_group_id", 0)
        trigger_group_id = (
            int(raw_group_id) if isinstance(raw_group_id, (int, str)) else 0
        )
        rows: list[dict[str, int | str]] = []
        for side, key in (
            ("trigger", "trigger_image_keys"),
            ("response", "response_image_keys"),
        ):
            for canonical_image_id in _parse_image_keys(str(payload[key])):
                rows.append(
                    {
                        "trigger_group_id": trigger_group_id,
                        "side": side,
                        "canonical_image_id": canonical_image_id,
                    }
                )
        return rows

    async def create_or_append_response(
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
        status: str = "pending",
        enabled: int = 1,
        approved_by: str = "",
        deleted_at: int = 0,
        created_at: int | None = None,
        updated_at: int | None = None,
    ) -> WordbankCreatedResponse:
        now = get_current_time()
        created_at = created_at or now
        updated_at = updated_at or now
        trigger_fingerprint = fingerprint_shape(trigger_shape)
        response_fingerprint = fingerprint_shape(response_shape)
        trigger_payload = shape_to_payload(trigger_shape)
        response_payload = shape_to_payload(response_shape)
        async with wordbank_main_db.write_session() as session:
            group, variant, created_group = await self._find_or_create_group_in_session(
                session,
                trigger_text=trigger_fingerprint.summary_text,
                trigger_payload=trigger_payload,
                trigger_exact_md5=trigger_fingerprint.exact_md5,
                trigger_structure_key=trigger_fingerprint.structure_key,
                trigger_search_text=trigger_fingerprint.search_text,
                trigger_search_tokens=trigger_fingerprint.search_tokens,
                trigger_image_keys=trigger_fingerprint.image_keys,
                group_id=group_id,
                created_by=created_by,
                created_at=created_at,
                updated_at=updated_at,
            )
            response_item = WordbankResponseItem(
                trigger_group_id=group.id,
                status=status,
                enabled=enabled,
                scope=scope,
                priority=priority,
                probability=probability,
                weight=weight,
                rule=rule,
                group_id=group_id,
                created_by=created_by,
                approved_by=approved_by,
                deleted_at=deleted_at,
                text=response_fingerprint.summary_text,
                message_json=response_payload,
                exact_md5=response_fingerprint.exact_md5,
                structure_key=response_fingerprint.structure_key,
                search_text=response_fingerprint.search_text,
                search_tokens=response_fingerprint.search_tokens,
                image_keys=response_fingerprint.image_keys,
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(response_item)
            await session.flush()
            await self._refresh_group_in_session(session, group.id)
            refreshed_group = await self._load_group_bundle_in_session(
                session,
                group.id,
                include_deleted=True,
            )
            if refreshed_group is None:
                raise RuntimeError("wordbank group refresh failed")
            return WordbankCreatedResponse(
                trigger_group_id=group.id,
                trigger_variant_id=variant.id,
                response_item_id=response_item.id,
                status=response_item.status,
                created_group=created_group,
                created_variant=False,
                trigger_group=self._to_group_record(
                    refreshed_group.group,
                    refreshed_group.variants,
                    refreshed_group.responses,
                ),
                response_item=self._to_response_item_record(response_item),
            )

    async def import_message_entry(
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
        status: str,
        enabled: int,
        approved_by: str,
        deleted_at: int,
        created_at: int,
        updated_at: int,
    ) -> WordbankCreatedResponse:
        return await self.create_or_append_response(
            trigger_shape=trigger_shape,
            response_shape=response_shape,
            rule=rule,
            scope=scope,
            priority=priority,
            probability=probability,
            weight=weight,
            group_id=group_id,
            created_by=created_by,
            status=status,
            enabled=enabled,
            approved_by=approved_by,
            deleted_at=deleted_at,
            created_at=created_at,
            updated_at=updated_at,
        )

    async def reset_all_data(
        self,
        *,
        include_images: bool = True,
    ) -> None:
        async with wordbank_main_db.write_session() as session:
            await session.execute(delete(WordbankApprovalMessage))
            await session.execute(delete(WordbankResponseMessage))
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

    async def list_enabled_entries(self) -> list[WordbankTriggerGroupRecord]:
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

    async def ensure_search_index(self) -> None:
        async with wordbank_main_db.read_session() as session:
            expected_stmt = (
                select(func.count())
                .select_from(WordbankTriggerGroup)
                .where(
                    exists(
                        select(1).where(
                            WordbankResponseItem.trigger_group_id
                            == WordbankTriggerGroup.id,
                            WordbankResponseItem.deleted_at == 0,
                        )
                    )
                )
            )
            group_count = int(await session.scalar(expected_stmt) or 0)
            doc_count = int(
                await session.scalar(
                    select(func.count()).select_from(WordbankSearchDocument)
                )
                or 0
            )
            trigger_fts_count = int(
                await session.scalar(
                    text("SELECT COUNT(*) FROM wordbank_search_trigger_fts")
                )
                or 0
            )
            response_fts_count = int(
                await session.scalar(
                    text("SELECT COUNT(*) FROM wordbank_search_response_fts")
                )
                or 0
            )
            image_entry_count = int(
                await session.scalar(
                    select(
                        func.count(
                            func.distinct(WordbankSearchImageMap.trigger_group_id)
                        )
                    )
                )
                or 0
            )
            expected_image_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(WordbankSearchDocument)
                    .where(
                        or_(
                            WordbankSearchDocument.trigger_image_keys != "",
                            WordbankSearchDocument.response_image_keys != "",
                        )
                    )
                )
                or 0
            )
        if (
            group_count != doc_count
            or group_count != trigger_fts_count
            or group_count != response_fts_count
            or expected_image_count != image_entry_count
        ):
            await self.rebuild_search_index()

    async def rebuild_search_index(self) -> None:
        async with wordbank_main_db.read_session() as session:
            group_rows = (
                (
                    await session.execute(
                        select(WordbankTriggerGroup).order_by(
                            WordbankTriggerGroup.id.asc()
                        )
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
                include_deleted=True,
                active_only=False,
            )
        variants_by_group: dict[int, list[WordbankTriggerVariant]] = defaultdict(list)
        for variant in variant_rows:
            variants_by_group[variant.trigger_group_id].append(variant)
        responses_by_group: dict[int, list[WordbankResponseItem]] = defaultdict(list)
        for response in response_rows:
            responses_by_group[response.trigger_group_id].append(response)
        documents: list[dict[str, object]] = []
        image_map_rows: list[dict[str, int | str]] = []
        for group in group_rows:
            bundle = _GroupBundle(
                group=group,
                variants=variants_by_group.get(group.id, []),
                responses=responses_by_group.get(group.id, []),
            )
            payload = self._document_payload(bundle)
            if payload is None:
                continue
            documents.append(payload)
            image_map_rows.extend(self._image_map_payloads(payload))
        async with wordbank_main_db.write_session() as session:
            await session.execute(delete(WordbankSearchDocument))
            await session.execute(delete(WordbankSearchImageMap))
            await session.execute(text("DELETE FROM wordbank_search_trigger_fts"))
            await session.execute(text("DELETE FROM wordbank_search_response_fts"))
            if documents:
                await session.execute(sqlite_insert(WordbankSearchDocument), documents)
                await session.execute(
                    text(
                        """
                        INSERT INTO wordbank_search_trigger_fts(rowid, tokens)
                        VALUES (:trigger_group_id, :trigger_tokens)
                        """
                    ),
                    documents,
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO wordbank_search_response_fts(rowid, tokens)
                        VALUES (:trigger_group_id, :response_tokens)
                        """
                    ),
                    documents,
                )
            if image_map_rows:
                await session.execute(
                    sqlite_insert(WordbankSearchImageMap), image_map_rows
                )

    async def find_trigger_group_by_shape(
        self,
        shape: MessageShape,
        *,
        include_deleted: bool = False,
    ) -> WordbankTriggerGroupRecord | None:
        fingerprint = fingerprint_shape(shape)
        payload = shape_to_payload(shape)
        async with wordbank_main_db.read_session() as session:
            group = await self._find_group_by_fingerprint_in_session(
                session,
                exact_md5=fingerprint.exact_md5,
                message_json=payload,
                include_deleted=include_deleted,
            )
            if group is None:
                return None
            bundle = await self._load_group_bundle_in_session(
                session,
                group.id,
                include_deleted=include_deleted,
            )
        if bundle is None:
            return None
        return self._to_group_record(bundle.group, bundle.variants, bundle.responses)

    async def list_group_response_items(
        self,
        trigger_group_id: int,
        *,
        include_deleted: bool = False,
    ) -> list[WordbankResponseItemRecord]:
        async with wordbank_main_db.read_session() as session:
            bundle = await self._load_group_bundle_in_session(
                session,
                trigger_group_id,
                include_deleted=include_deleted,
            )
        if bundle is None:
            return []
        return [
            self._to_response_item_record(response) for response in bundle.responses
        ]

    async def get_response_item_record(
        self,
        response_item_id: int,
        *,
        include_deleted: bool = False,
    ) -> WordbankResponseItemRecord | None:
        async with wordbank_main_db.read_session() as session:
            response = await session.get(WordbankResponseItem, response_item_id)
        if response is None:
            return None
        if not include_deleted and response.deleted_at != 0:
            return None
        return self._to_response_item_record(response)

    async def get_trigger_group_record(
        self,
        trigger_group_id: int,
        *,
        include_deleted: bool = False,
        active_only: bool = False,
    ) -> WordbankTriggerGroupRecord | None:
        async with wordbank_main_db.read_session() as session:
            group = await session.get(WordbankTriggerGroup, trigger_group_id)
            if group is None:
                return None
            variants = await self._load_variants_by_group_ids(
                session, [trigger_group_id]
            )
            responses = await self._load_responses_by_group_ids(
                session,
                [trigger_group_id],
                include_deleted=include_deleted,
                active_only=active_only,
            )
        return self._to_group_record(group, variants, responses)

    async def search(
        self,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WordbankSearchItem]:
        page = await self.search_page(request, limit=limit, offset=offset)
        return list(page.items)

    async def search_page(
        self,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> WordbankSearchPage:
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
                if request.keyword or request.image_scores or request.has_image:
                    return WordbankSearchPage(
                        items=(), total_count=0, offset=offset, limit=limit
                    )
                count_stmt = (
                    select(func.count())
                    .select_from(WordbankSearchDocument)
                    .where(WordbankSearchDocument.deleted_at == 0)
                )
                stmt = (
                    select(WordbankSearchDocument)
                    .where(WordbankSearchDocument.deleted_at == 0)
                    .order_by(
                        WordbankSearchDocument.updated_at.desc(),
                        WordbankSearchDocument.trigger_group_id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
                if request.creator_id:
                    creator_filter = (
                        WordbankSearchDocument.created_by == request.creator_id
                    )
                    count_stmt = count_stmt.where(creator_filter)
                    stmt = stmt.where(creator_filter)
                documents = (await session.execute(stmt)).scalars().all()
                total_count = int(await session.scalar(count_stmt) or 0)
                return WordbankSearchPage(
                    items=tuple(
                        self._search_item_from_document(document)
                        for document in documents
                    ),
                    total_count=total_count,
                    offset=offset,
                    limit=limit,
                )

            stmt = select(WordbankSearchDocument).where(
                WordbankSearchDocument.trigger_group_id.in_(candidate_ids),
                WordbankSearchDocument.deleted_at == 0,
            )
            if request.creator_id:
                stmt = stmt.where(
                    WordbankSearchDocument.created_by == request.creator_id
                )
            documents = (await session.execute(stmt)).scalars().all()

        ranked: list[tuple[float, int, str, WordbankSearchDocument]] = []
        for document in documents:
            text_score = text_scores.get(document.trigger_group_id, 0.0)
            image_score = image_scores.get(document.trigger_group_id, 0.0)
            final_score = self._rank_search_document(
                document,
                request=request,
                text_score=text_score,
                image_score=image_score,
                text_sources=text_sources.get(document.trigger_group_id, set()),
                image_sources=image_sources.get(document.trigger_group_id, set()),
            )
            matched_by = ",".join(
                sorted(
                    text_sources.get(document.trigger_group_id, set())
                    | image_sources.get(document.trigger_group_id, set())
                )
            )
            ranked.append(
                (final_score, document.trigger_group_id, matched_by, document)
            )
        ranked.sort(
            key=lambda item: (item[0], item[3].updated_at, item[1]), reverse=True
        )
        total_count = len(ranked)
        paged = ranked[offset : offset + limit]
        return WordbankSearchPage(
            items=tuple(
                self._search_item_from_document(
                    document, score=score, matched_by=matched_by
                )
                for score, _, matched_by, document in paged
            ),
            total_count=total_count,
            offset=offset,
            limit=limit,
        )

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

    async def delete_response_item(
        self,
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
            response.deleted_at = now
            response.updated_at = now
            await session.flush()
            await self._refresh_group_in_session(session, response.trigger_group_id)
            return True

    async def restore_response_item(
        self,
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

    async def approve_response_item(
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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

    async def record_response_message(
        self,
        payload: WordbankResponseMessagePayload,
    ) -> None:
        async with wordbank_main_db.write_session() as session:
            stmt = sqlite_insert(WordbankResponseMessage).values(payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=[WordbankResponseMessage.message_id],
                set_={
                    "trigger_group_id": stmt.excluded.trigger_group_id,
                    "trigger_variant_id": stmt.excluded.trigger_variant_id,
                    "response_item_id": stmt.excluded.response_item_id,
                    "group_id": stmt.excluded.group_id,
                    "user_id": stmt.excluded.user_id,
                    "message_type": stmt.excluded.message_type,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)

    async def record_approval_message(
        self,
        payload: WordbankApprovalMessagePayload,
    ) -> None:
        async with wordbank_main_db.write_session() as session:
            stmt = sqlite_insert(WordbankApprovalMessage).values(payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=[WordbankApprovalMessage.message_id],
                set_={
                    "trigger_group_id": stmt.excluded.trigger_group_id,
                    "response_item_id": stmt.excluded.response_item_id,
                    "group_id": stmt.excluded.group_id,
                    "user_id": stmt.excluded.user_id,
                    "source_message_id": stmt.excluded.source_message_id,
                    "message_type": stmt.excluded.message_type,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)

    async def get_response_message(
        self,
        message_id: str,
    ) -> WordbankResponseMessageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = (
                await session.execute(
                    select(WordbankResponseMessage).where(
                        WordbankResponseMessage.message_id == message_id
                    )
                )
            ).scalar_one_or_none()
        return self._to_response_message_record(row) if row else None

    async def get_approval_message(
        self,
        message_id: str,
    ) -> WordbankApprovalMessageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = (
                await session.execute(
                    select(WordbankApprovalMessage).where(
                        WordbankApprovalMessage.message_id == message_id
                    )
                )
            ).scalar_one_or_none()
        return self._to_approval_message_record(row) if row else None

    async def get_group_detail(
        self,
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

    async def get_image_by_md5(self, md5: str) -> WordbankImageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = (
                await session.execute(
                    select(WordbankImage).where(WordbankImage.md5 == md5)
                )
            ).scalar_one_or_none()
        return self._to_image_record(row) if row else None

    async def get_image_candidates(
        self,
        dhash_prefix: str,
        *,
        limit: int = 128,
    ) -> list[WordbankImageRecord]:
        async with wordbank_main_db.read_session() as session:
            rows = (
                (
                    await session.execute(
                        select(WordbankImage)
                        .where(WordbankImage.dhash.startswith(dhash_prefix))
                        .order_by(WordbankImage.id.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
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

    def _rank_search_document(
        self,
        document: WordbankSearchDocument,
        *,
        request: WordbankSearchRequest,
        text_score: float,
        image_score: float,
        text_sources: set[str],
        image_sources: set[str],
    ) -> float:
        final_score = max(text_score, image_score)
        if text_score and image_score:
            final_score += 0.2 * min(text_score, image_score)
        normalized_keyword = (
            _normalize_search_text(request.keyword) if request.keyword else ""
        )
        if normalized_keyword:
            if request.field in {
                "all",
                "trigger",
            } and normalized_keyword in _normalize_search_text(document.trigger_text):
                final_score += 0.25
            if request.field in {
                "all",
                "response",
            } and normalized_keyword in _normalize_search_text(document.response_text):
                final_score += 0.25
            if len(text_sources) > 1:
                final_score += 0.08
        if request.field == "trigger" and "text:trigger" in text_sources:
            final_score += 0.06
        if request.field == "response" and "text:response" in text_sources:
            final_score += 0.06
        if request.field == "trigger" and "image:trigger" in image_sources:
            final_score += 0.06
        if request.field == "response" and "image:response" in image_sources:
            final_score += 0.06
        return final_score

    async def _search_text_scores(
        self,
        session: AsyncSession,
        keyword: str,
        *,
        field: str,
        limit: int,
    ) -> tuple[dict[int, float], dict[int, set[str]]]:
        query = _build_fts_query(keyword)
        if not query:
            return {}, {}
        scores: dict[int, float] = {}
        sources: dict[int, set[str]] = defaultdict(set)
        tables = (
            ["trigger"]
            if field == "trigger"
            else ["response"]
            if field == "response"
            else ["trigger", "response"]
        )
        for table_name in tables:
            sql = text(
                f"""
                SELECT rowid AS trigger_group_id
                FROM wordbank_search_{table_name}_fts
                WHERE wordbank_search_{table_name}_fts MATCH :query
                ORDER BY bm25(wordbank_search_{table_name}_fts)
                LIMIT :limit
                """
            )
            rows = (await session.execute(sql, {"query": query, "limit": limit})).all()
            total = len(rows)
            for index, row in enumerate(rows):
                trigger_group_id = int(row.trigger_group_id)
                score = (total - index) / max(total, 1)
                if score > scores.get(trigger_group_id, 0.0):
                    scores[trigger_group_id] = score
                sources[trigger_group_id].add(f"text:{table_name}")
        return scores, sources

    async def _search_image_scores(
        self,
        session: AsyncSession,
        image_scores: dict[int, float],
        *,
        field: str,
        creator_id: str,
    ) -> tuple[dict[int, float], dict[int, set[str]]]:
        if not image_scores:
            return {}, {}
        stmt = (
            select(
                WordbankSearchImageMap.trigger_group_id,
                WordbankSearchImageMap.side,
                WordbankSearchImageMap.canonical_image_id,
            )
            .join(
                WordbankSearchDocument,
                WordbankSearchDocument.trigger_group_id
                == WordbankSearchImageMap.trigger_group_id,
            )
            .where(
                WordbankSearchDocument.deleted_at == 0,
                WordbankSearchImageMap.canonical_image_id.in_(tuple(image_scores)),
            )
        )
        if creator_id:
            stmt = stmt.where(WordbankSearchDocument.created_by == creator_id)
        if field == "trigger":
            stmt = stmt.where(WordbankSearchImageMap.side == "trigger")
        elif field == "response":
            stmt = stmt.where(WordbankSearchImageMap.side == "response")
        rows = (await session.execute(stmt)).all()
        scores: dict[int, float] = {}
        sources: dict[int, set[str]] = defaultdict(set)
        for row in rows:
            trigger_group_id = int(row.trigger_group_id)
            score = float(image_scores.get(int(row.canonical_image_id), 0.0))
            if score > scores.get(trigger_group_id, 0.0):
                scores[trigger_group_id] = score
            sources[trigger_group_id].add(f"image:{row.side}")
        return scores, sources

    async def _find_or_create_group_in_session(
        self,
        session: AsyncSession,
        *,
        trigger_text: str,
        trigger_payload: str,
        trigger_exact_md5: str,
        trigger_structure_key: str,
        trigger_search_text: str,
        trigger_search_tokens: str,
        trigger_image_keys: str,
        group_id: str,
        created_by: str,
        created_at: int,
        updated_at: int,
    ) -> tuple[WordbankTriggerGroup, WordbankTriggerVariant, bool]:
        group = await self._find_group_by_fingerprint_in_session(
            session,
            exact_md5=trigger_exact_md5,
            message_json=trigger_payload,
            include_deleted=True,
        )
        if group is not None:
            variant = (
                await session.execute(
                    select(WordbankTriggerVariant)
                    .where(WordbankTriggerVariant.trigger_group_id == group.id)
                    .order_by(WordbankTriggerVariant.id.asc())
                    .limit(1)
                )
            ).scalar_one()
            return group, variant, False

        group = WordbankTriggerGroup(
            status="pending",
            enabled=0,
            group_id=group_id,
            created_by=created_by,
            deleted_at=0,
            created_at=created_at,
            updated_at=updated_at,
        )
        session.add(group)
        await session.flush()
        variant = WordbankTriggerVariant(
            trigger_group_id=group.id,
            trigger_text=trigger_text,
            message_json=trigger_payload,
            exact_md5=trigger_exact_md5,
            structure_key=trigger_structure_key,
            search_text=trigger_search_text,
            search_tokens=trigger_search_tokens,
            image_keys=trigger_image_keys,
            created_at=created_at,
            updated_at=updated_at,
        )
        session.add(variant)
        await session.flush()
        return group, variant, True

    async def _find_group_by_fingerprint_in_session(
        self,
        session: AsyncSession,
        *,
        exact_md5: str,
        message_json: str,
        include_deleted: bool,
    ) -> WordbankTriggerGroup | None:
        stmt = (
            select(WordbankTriggerGroup)
            .join(
                WordbankTriggerVariant,
                WordbankTriggerVariant.trigger_group_id == WordbankTriggerGroup.id,
            )
            .where(
                WordbankTriggerVariant.exact_md5 == exact_md5,
                WordbankTriggerVariant.message_json == message_json,
            )
            .order_by(WordbankTriggerGroup.id.asc())
            .limit(1)
        )
        if not include_deleted:
            stmt = stmt.where(WordbankTriggerGroup.deleted_at == 0)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _load_group_bundle_in_session(
        self,
        session: AsyncSession,
        trigger_group_id: int,
        *,
        include_deleted: bool,
    ) -> _GroupBundle | None:
        group = await session.get(WordbankTriggerGroup, trigger_group_id)
        if group is None:
            return None
        variants = list(
            await self._load_variants_by_group_ids(session, [trigger_group_id])
        )
        responses = list(
            await self._load_responses_by_group_ids(
                session,
                [trigger_group_id],
                include_deleted=include_deleted,
                active_only=False,
            )
        )
        return _GroupBundle(group=group, variants=variants, responses=responses)

    async def _load_variants_by_group_ids(
        self,
        session: AsyncSession,
        group_ids: Sequence[int],
    ) -> Sequence[WordbankTriggerVariant]:
        if not group_ids:
            return []
        return (
            (
                await session.execute(
                    select(WordbankTriggerVariant)
                    .where(WordbankTriggerVariant.trigger_group_id.in_(group_ids))
                    .order_by(WordbankTriggerVariant.id.asc())
                )
            )
            .scalars()
            .all()
        )

    async def _load_responses_by_group_ids(
        self,
        session: AsyncSession,
        group_ids: Sequence[int],
        *,
        include_deleted: bool,
        active_only: bool,
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
        return (await session.execute(stmt)).scalars().all()

    async def _refresh_group_in_session(
        self,
        session: AsyncSession,
        trigger_group_id: int,
    ) -> None:
        bundle = await self._load_group_bundle_in_session(
            session,
            trigger_group_id,
            include_deleted=True,
        )
        if bundle is None:
            await self._delete_group_search_rows_in_session(session, trigger_group_id)
            return
        status, enabled, deleted_at = _group_status_from_responses(bundle.responses)
        bundle.group.status = status
        bundle.group.enabled = enabled
        bundle.group.deleted_at = deleted_at
        if bundle.responses:
            bundle.group.updated_at = max(
                [bundle.group.updated_at]
                + [response.updated_at for response in bundle.responses]
            )
        await session.flush()
        payload = self._document_payload(bundle)
        if payload is None:
            await self._delete_group_search_rows_in_session(session, trigger_group_id)
            return
        await session.execute(
            sqlite_insert(WordbankSearchDocument)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=[WordbankSearchDocument.trigger_group_id],
                set_=payload,
            )
        )
        await session.execute(
            text(
                """
                DELETE FROM wordbank_search_trigger_fts
                WHERE rowid = :trigger_group_id
                """
            ),
            {"trigger_group_id": trigger_group_id},
        )
        await session.execute(
            text(
                """
                DELETE FROM wordbank_search_response_fts
                WHERE rowid = :trigger_group_id
                """
            ),
            {"trigger_group_id": trigger_group_id},
        )
        await session.execute(
            delete(WordbankSearchImageMap).where(
                WordbankSearchImageMap.trigger_group_id == trigger_group_id
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO wordbank_search_trigger_fts(rowid, tokens)
                VALUES (:trigger_group_id, :trigger_tokens)
                """
            ),
            payload,
        )
        await session.execute(
            text(
                """
                INSERT INTO wordbank_search_response_fts(rowid, tokens)
                VALUES (:trigger_group_id, :response_tokens)
                """
            ),
            payload,
        )
        image_map_rows = self._image_map_payloads(payload)
        if image_map_rows:
            await session.execute(sqlite_insert(WordbankSearchImageMap), image_map_rows)

    async def _delete_group_search_rows_in_session(
        self,
        session: AsyncSession,
        trigger_group_id: int,
    ) -> None:
        await session.execute(
            delete(WordbankSearchDocument).where(
                WordbankSearchDocument.trigger_group_id == trigger_group_id
            )
        )
        await session.execute(
            delete(WordbankSearchImageMap).where(
                WordbankSearchImageMap.trigger_group_id == trigger_group_id
            )
        )
        await session.execute(
            text(
                """
                DELETE FROM wordbank_search_trigger_fts
                WHERE rowid = :trigger_group_id
                """
            ),
            {"trigger_group_id": trigger_group_id},
        )
        await session.execute(
            text(
                """
                DELETE FROM wordbank_search_response_fts
                WHERE rowid = :trigger_group_id
                """
            ),
            {"trigger_group_id": trigger_group_id},
        )

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
        self,
        session: AsyncSession,
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
