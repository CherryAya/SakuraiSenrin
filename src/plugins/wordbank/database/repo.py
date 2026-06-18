"""Wordbank repository."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.lib.utils.common import get_current_time
from src.plugins.wordbank.message_model import (
    MessageShape,
    fingerprint_shape,
    shape_to_payload,
)

from .instances import (
    wordbank_log_db,
    wordbank_main_db,
    wordbank_message_ref_db,
    wordbank_message_route_db,
)
from .repo_entries import WordbankRepositoryEntriesMixin
from .repo_media import WordbankRepositoryMediaMixin
from .repo_message_refs import WordbankRepositoryMessageRefsMixin
from .repo_records import WordbankRepositoryRecordsMixin
from .repo_runtime import WordbankRepositoryRuntimeMixin
from .repo_search import WordbankRepositorySearchMixin
from .tables import (
    WordbankLogBase,
    WordbankMainBase,
    WordbankMessageRefBase,
    WordbankMessageRoute,
    WordbankMessageRouteBase,
)
from .types import WordbankCreatedResponse, WordbankMessageRoutePayload


class WordbankRepository(
    WordbankRepositoryRecordsMixin,
    WordbankRepositoryRuntimeMixin,
    WordbankRepositorySearchMixin,
    WordbankRepositoryMessageRefsMixin,
    WordbankRepositoryEntriesMixin,
    WordbankRepositoryMediaMixin,
):
    """Repository for wordbank trigger groups, responses, media and logs."""

    @classmethod
    async def init_all_tables(cls) -> None:
        await wordbank_main_db.init(WordbankMainBase)
        await wordbank_log_db.init(WordbankLogBase)
        await wordbank_message_route_db.init(WordbankMessageRouteBase)
        await wordbank_message_ref_db.init(WordbankMessageRefBase)
        await cls._drop_legacy_main_tables()
        await cls._ensure_main_fts_tables()

    @staticmethod
    async def _ensure_main_fts_tables() -> None:
        async with wordbank_main_db.write_session() as session:
            await session.execute(
                text(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS wordbank_search_trigger_fts
                    USING fts5(tokens)
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS wordbank_search_response_fts
                    USING fts5(tokens)
                    """
                )
            )

    @staticmethod
    async def _drop_legacy_main_tables() -> None:
        async with wordbank_main_db.write_session() as session:
            for table_name in (
                "wordbank_response_message",
                "wordbank_approval_message",
                "wordbank_view_message",
                "wordbank_entry",
                "wordbank_trigger",
                "wordbank_response",
            ):
                await session.execute(text(f"DROP TABLE IF EXISTS {table_name}"))

    async def create_or_append_response(
        self,
        *,
        trigger_shape: MessageShape,
        response_shape: MessageShape,
        rule: dict,
        scope: str,
        priority: int,
        trigger_probability: float,
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
                probability=trigger_probability,
                created_at=created_at,
                updated_at=updated_at,
            )
            from .tables import WordbankResponseItem

            response_item = WordbankResponseItem(
                trigger_group_id=group.id,
                status=status,
                enabled=enabled,
                scope=scope,
                priority=priority,
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
        trigger_probability: float,
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
            trigger_probability=trigger_probability,
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

    async def _upsert_message_route(
        self,
        payload: WordbankMessageRoutePayload,
    ) -> None:
        async with wordbank_message_route_db.write_session() as session:
            stmt = sqlite_insert(WordbankMessageRoute).values(payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=[WordbankMessageRoute.message_id],
                set_={
                    "ref_kind": stmt.excluded.ref_kind,
                    "shard_key": stmt.excluded.shard_key,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)
