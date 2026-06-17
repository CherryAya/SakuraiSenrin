"""Message reference helpers for the wordbank repository."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .instances import wordbank_message_ref_db, wordbank_message_route_db
from .repo_shared import message_ref_time_ctx
from .tables import WordbankMessageRef, WordbankMessageRoute
from .types import WordbankMessageRefKind, WordbankMessageRefPayload, WordbankMessageRouteRecord, WordbankMessageRefRecord


class WordbankRepositoryMessageRefsMixin:
    async def _delete_message_ref_from_shard(
        self: Any,
        *,
        message_id: str,
        shard_key: str,
    ) -> None:
        async with wordbank_message_ref_db.write_session(
            time_ctx=message_ref_time_ctx(shard_key)
        ) as session:
            await session.execute(
                delete(WordbankMessageRef).where(
                    WordbankMessageRef.message_id == message_id
                )
            )

    async def record_message_ref(
        self: Any,
        payload: WordbankMessageRefPayload,
    ) -> None:
        previous_route = await self.get_message_ref_route(payload["message_id"])
        async with wordbank_message_ref_db.write_session(
            time_ctx=message_ref_time_ctx(payload["shard_key"])
        ) as session:
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
            await session.execute(stmt)
        if (
            previous_route is not None
            and previous_route.shard_key != payload["shard_key"]
        ):
            await self._delete_message_ref_from_shard(
                message_id=payload["message_id"],
                shard_key=previous_route.shard_key,
            )
        await self._upsert_message_route(
            {
                "message_id": payload["message_id"],
                "ref_kind": payload["ref_kind"],
                "shard_key": payload["shard_key"],
                "created_at": payload["created_at"],
                "updated_at": payload["updated_at"],
            }
        )

    async def get_message_ref_route(
        self: Any,
        message_id: str,
    ) -> WordbankMessageRouteRecord | None:
        async with wordbank_message_route_db.read_session() as session:
            row = (
                await session.execute(
                    select(WordbankMessageRoute).where(
                        WordbankMessageRoute.message_id == message_id
                    )
                )
            ).scalar_one_or_none()
        return self._to_message_route_record(row) if row else None

    async def list_message_ref_routes(self: Any) -> list[WordbankMessageRouteRecord]:
        async with wordbank_message_route_db.read_session() as session:
            rows = (
                (
                    await session.execute(
                        select(WordbankMessageRoute).order_by(
                            WordbankMessageRoute.id.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [self._to_message_route_record(row) for row in rows]

    async def get_message_ref(
        self: Any,
        message_id: str,
        *,
        expected_kind: WordbankMessageRefKind | None = None,
    ) -> WordbankMessageRefRecord | None:
        route = await self.get_message_ref_route(message_id)
        if route is None:
            return None
        if expected_kind is not None and route.ref_kind != expected_kind:
            return None
        async with wordbank_message_ref_db.read_session(
            time_ctx=message_ref_time_ctx(route.shard_key)
        ) as session:
            row = (
                await session.execute(
                    select(WordbankMessageRef).where(
                        WordbankMessageRef.message_id == message_id
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        if expected_kind is not None and row.ref_kind != expected_kind:
            return None
        return self._to_message_ref_record(row)
