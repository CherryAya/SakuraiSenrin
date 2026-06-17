"""Session and search-sync helpers for the wordbank repository."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .repo_shared import GroupBundle, group_status_from_responses
from .tables import (
    WordbankResponseItem,
    WordbankSearchDocument,
    WordbankSearchImageMap,
    WordbankTriggerGroup,
    WordbankTriggerVariant,
)


class WordbankRepositoryRuntimeMixin:
    async def _find_or_create_group_in_session(
        self: Any,
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
        probability: float,
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
            probability=probability,
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
        self: Any,
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
        self: Any,
        session: AsyncSession,
        trigger_group_id: int,
        *,
        include_deleted: bool,
    ) -> GroupBundle | None:
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
        return GroupBundle(group=group, variants=variants, responses=responses)

    async def _load_variants_by_group_ids(
        self: Any,
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
        self: Any,
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
        self: Any,
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
        status, enabled, deleted_at = group_status_from_responses(bundle.responses)
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
        self: Any,
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
