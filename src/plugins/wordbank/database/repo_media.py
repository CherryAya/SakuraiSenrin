"""Media and log helpers for the wordbank repository."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time

from .instances import wordbank_log_db, wordbank_main_db, wordbank_message_ref_db
from .tables import WordbankImage, WordbankLog
from .types import WordbankImagePayload, WordbankImageRecord, WordbankLogPayload
from .writers import wordbank_log_writer


class WordbankRepositoryMediaMixin:
    async def get_image_by_md5(self: Any, md5: str) -> WordbankImageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = (
                await session.execute(
                    select(WordbankImage).where(WordbankImage.md5 == md5)
                )
            ).scalar_one_or_none()
        return self._to_image_record(row) if row else None

    async def get_image_by_id(self: Any, image_id: int) -> WordbankImageRecord | None:
        async with wordbank_main_db.read_session() as session:
            row = (
                await session.execute(
                    select(WordbankImage).where(WordbankImage.id == image_id)
                )
            ).scalar_one_or_none()
        return self._to_image_record(row) if row else None

    async def get_image_candidates(
        self: Any,
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
        self: Any,
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

    async def list_images(self: Any) -> list[WordbankImageRecord]:
        async with wordbank_main_db.read_session() as session:
            rows = (await session.execute(select(WordbankImage))).scalars().all()
        return [self._to_image_record(row) for row in rows]

    async def update_image_remote_sync(
        self: Any,
        image_id: int,
        *,
        remote_storage_path: str,
        remote_sync_status: str,
        remote_synced_at: int,
        remote_etag: str = "",
        remote_object_size: int = 0,
        storage_path: str | None = None,
        updated_at: int | None = None,
    ) -> WordbankImageRecord | None:
        async with wordbank_main_db.write_session() as session:
            image = await session.get(WordbankImage, image_id)
            if image is None:
                return None
            image.remote_storage_path = remote_storage_path
            image.remote_sync_status = remote_sync_status
            image.remote_synced_at = remote_synced_at
            image.remote_etag = remote_etag
            image.remote_object_size = remote_object_size
            if storage_path is not None:
                image.storage_path = storage_path
            image.updated_at = updated_at or get_current_time()
            await session.flush()
            return self._to_image_record(image)

    async def update_image_cache_metadata(
        self: Any,
        image_id: int,
        *,
        local_cache_path: str,
        cache_file_size: int,
        last_accessed_at: int | None = None,
        cache_last_hit_at: int | None = None,
        updated_at: int | None = None,
    ) -> WordbankImageRecord | None:
        async with wordbank_main_db.write_session() as session:
            image = await session.get(WordbankImage, image_id)
            if image is None:
                return None
            image.local_cache_path = local_cache_path
            image.cache_file_size = cache_file_size
            if last_accessed_at is not None:
                image.last_accessed_at = last_accessed_at
            if cache_last_hit_at is not None:
                image.cache_last_hit_at = cache_last_hit_at
            image.updated_at = updated_at or get_current_time()
            await session.flush()
            return self._to_image_record(image)

    async def list_cached_images(self: Any) -> list[WordbankImageRecord]:
        async with wordbank_main_db.read_session() as session:
            rows = (
                (
                    await session.execute(
                        select(WordbankImage).where(
                            WordbankImage.local_cache_path != ""
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [self._to_image_record(row) for row in rows]

    async def list_images_for_remote_sync(
        self: Any,
        *,
        limit: int = 200,
        id_start: int = 0,
        only_unsynced: bool = True,
    ) -> list[WordbankImageRecord]:
        async with wordbank_main_db.read_session() as session:
            stmt = select(WordbankImage).where(WordbankImage.id >= id_start)
            if only_unsynced:
                stmt = stmt.where(
                    or_(
                        WordbankImage.remote_storage_path == "",
                        WordbankImage.remote_sync_status != "synced",
                    )
                )
            rows = (
                (
                    await session.execute(
                        stmt.order_by(WordbankImage.id.asc()).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [self._to_image_record(row) for row in rows]

    async def save_log(
        self: Any,
        payload: WordbankLogPayload,
        *,
        policy: WritePolicy = WritePolicy.BUFFERED,
    ) -> None:
        if policy == WritePolicy.BUFFERED:
            await wordbank_log_writer.add(payload)
            return
        async with wordbank_log_db.write_session(
            time_ctx=datetime.fromtimestamp(payload["created_at"], UTC)
        ) as session:
            session.add(WordbankLog(**payload))

    async def count_trigger_group_calls_in_windows(
        self: Any,
        trigger_group_windows: dict[int, int],
        *,
        now_ts: int | None = None,
    ) -> dict[int, int]:
        if not trigger_group_windows:
            return {}
        normalized_windows = {
            int(trigger_group_id): max(int(window_seconds), 0)
            for trigger_group_id, window_seconds in trigger_group_windows.items()
            if int(window_seconds) > 0
        }
        if not normalized_windows:
            return {}
        now_ts = get_current_time() if now_ts is None else now_ts
        max_window = max(normalized_windows.values())
        start_time = datetime.fromtimestamp(now_ts - max_window, UTC)
        end_time = datetime.fromtimestamp(now_ts, UTC)
        trigger_group_ids = tuple(normalized_windows)

        async def _query_shard(session: AsyncSession) -> list[tuple[int, int]]:
            rows = (
                await session.execute(
                    select(WordbankLog.trigger_group_id, WordbankLog.created_at).where(
                        WordbankLog.trigger_group_id.in_(trigger_group_ids),
                        WordbankLog.created_at >= now_ts - max_window,
                        WordbankLog.created_at <= now_ts,
                    )
                )
            ).all()
            return [
                (int(trigger_group_id), int(created_at))
                for trigger_group_id, created_at in rows
            ]

        shard_results = await wordbank_log_db.map_reduce(
            start_time,
            end_time,
            _query_shard,
            cold_policy=wordbank_log_db.cold_policy,
        )
        counts: Counter[int] = Counter()
        for rows in shard_results:
            for trigger_group_id, created_at in rows:
                window_seconds = normalized_windows.get(trigger_group_id, 0)
                if window_seconds <= 0:
                    continue
                if created_at < now_ts - window_seconds:
                    continue
                counts[trigger_group_id] += 1
        return {
            trigger_group_id: counts.get(trigger_group_id, 0)
            for trigger_group_id in normalized_windows
        }

    async def drain_logs(self: Any) -> None:
        await wordbank_log_writer.drain()

    async def warm_up(self: Any) -> None:
        await self.list_enabled_entries()

    async def archive_event_shards(self: Any) -> None:
        await wordbank_log_db.run_archiver_task()
        await wordbank_message_ref_db.run_archiver_task()
