"""Search index and ranking helpers for the wordbank repository."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.plugins.wordbank.debug import elapsed_ms, log_perf, perf_start
from src.plugins.wordbank.message_model import (
    MessageShape,
    fingerprint_shape,
    shape_from_payload,
    shape_to_payload,
)

from .instances import wordbank_main_db
from .repo_shared import (
    _SEARCH_RESULT_CANDIDATE_MULTIPLIER,
    _SEARCH_RESULT_MIN_CANDIDATES,
    GroupBundle,
    build_fts_query,
    normalize_search_text,
)
from .tables import (
    WordbankResponseItem,
    WordbankSearchDocument,
    WordbankSearchImageMap,
    WordbankTriggerGroup,
    WordbankTriggerVariant,
)
from .types import (
    WordbankResponseItemRecord,
    WordbankSearchItem,
    WordbankSearchPage,
    WordbankSearchRequest,
    WordbankTriggerGroupRecord,
)


class WordbankRepositorySearchMixin:
    async def _load_group_bundles_by_ids(
        self: Any,
        session: AsyncSession,
        group_ids: list[int],
        *,
        include_deleted: bool = False,
        active_only: bool = False,
    ) -> list[GroupBundle]:
        if not group_ids:
            return []
        unique_group_ids = list(dict.fromkeys(group_ids))
        group_rows = (
            (
                await session.execute(
                    select(WordbankTriggerGroup).where(
                        WordbankTriggerGroup.id.in_(unique_group_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        variants = await self._load_variants_by_group_ids(session, unique_group_ids)
        responses = await self._load_responses_by_group_ids(
            session,
            unique_group_ids,
            include_deleted=include_deleted,
            active_only=active_only,
        )
        variants_by_group: dict[int, list[WordbankTriggerVariant]] = defaultdict(list)
        for variant in variants:
            variants_by_group[variant.trigger_group_id].append(variant)
        responses_by_group: dict[int, list[WordbankResponseItem]] = defaultdict(list)
        for response in responses:
            responses_by_group[response.trigger_group_id].append(response)
        return [
            GroupBundle(
                group=group,
                variants=variants_by_group.get(group.id, []),
                responses=responses_by_group.get(group.id, []),
            )
            for group in group_rows
        ]

    async def ensure_search_index(self: Any) -> None:
        start = perf_start()
        await self._ensure_main_fts_tables()
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
            log_perf(
                "repo.ensure_search_index.rebuild_needed",
                start=start,
                group_count=group_count,
                doc_count=doc_count,
                trigger_fts_count=trigger_fts_count,
                response_fts_count=response_fts_count,
                expected_image_count=expected_image_count,
                image_entry_count=image_entry_count,
            )
            await self.rebuild_search_index()
            return
        log_perf(
            "repo.ensure_search_index.ok",
            start=start,
            group_count=group_count,
            doc_count=doc_count,
            trigger_fts_count=trigger_fts_count,
            response_fts_count=response_fts_count,
            expected_image_count=expected_image_count,
            image_entry_count=image_entry_count,
        )

    async def rebuild_search_index(self: Any) -> None:
        start = perf_start()
        await self._ensure_main_fts_tables()
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
            bundle = GroupBundle(
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
        log_perf(
            "repo.rebuild_search_index.done",
            start=start,
            groups=len(group_rows),
            variants=len(variant_rows),
            responses=len(response_rows),
            documents=len(documents),
            image_map_rows=len(image_map_rows),
        )

    async def find_trigger_group_by_shape(
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[WordbankSearchItem]:
        page = await self.search_page(request, limit=limit, offset=offset)
        return list(page.items)

    async def search_page(
        self: Any,
        request: WordbankSearchRequest,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> WordbankSearchPage:
        start = perf_start()
        async with wordbank_main_db.read_session() as session:
            candidate_limit = max(
                _SEARCH_RESULT_MIN_CANDIDATES,
                (offset + limit) * _SEARCH_RESULT_CANDIDATE_MULTIPLIER,
            )
            text_start = perf_start()
            text_scores, text_sources = await self._search_text_scores(
                session,
                request.keyword,
                field=request.field,
                limit=candidate_limit,
            )
            text_ms = elapsed_ms(text_start)
            image_start = perf_start()
            image_scores, image_sources = await self._search_image_scores(
                session,
                request.image_scores,
                field=request.field,
                creator_id=request.creator_id,
            )
            image_ms = elapsed_ms(image_start)
            candidate_ids = set(text_scores) | set(image_scores)
            if not candidate_ids:
                if request.keyword or request.image_scores or request.has_image:
                    log_perf(
                        "repo.search_page.empty_candidates",
                        start=start,
                        keyword=request.keyword or "-",
                        field=request.field,
                        creator_id=request.creator_id or "-",
                        has_image=request.has_image,
                        candidate_limit=candidate_limit,
                        text_candidates=len(text_scores),
                        image_candidates=len(image_scores),
                        text_ms=f"{text_ms:.2f}",
                        image_ms=f"{image_ms:.2f}",
                    )
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
                bundles = await self._load_group_bundles_by_ids(
                    session,
                    [document.trigger_group_id for document in documents],
                    include_deleted=False,
                    active_only=True,
                )
                bundles_by_group_id = {bundle.group.id: bundle for bundle in bundles}
                total_count = int(await session.scalar(count_stmt) or 0)
                log_perf(
                    "repo.search_page.list_recent",
                    start=start,
                    field=request.field,
                    creator_id=request.creator_id or "-",
                    candidate_limit=candidate_limit,
                    text_candidates=len(text_scores),
                    image_candidates=len(image_scores),
                    text_ms=f"{text_ms:.2f}",
                    image_ms=f"{image_ms:.2f}",
                    documents=len(documents),
                    total_count=total_count,
                )
                return WordbankSearchPage(
                    items=tuple(
                        self._search_item_from_document(
                            document,
                            trigger_shape=(
                                shape_from_payload(bundle.variants[0].message_json)
                                if (
                                    (
                                        bundle := bundles_by_group_id.get(
                                            document.trigger_group_id
                                        )
                                    )
                                    is not None
                                    and bundle.variants
                                )
                                else None
                            ),
                            response_shape=(
                                shape_from_payload(bundle.responses[0].message_json)
                                if (
                                    (
                                        bundle := bundles_by_group_id.get(
                                            document.trigger_group_id
                                        )
                                    )
                                    is not None
                                    and bundle.responses
                                )
                                else None
                            ),
                        )
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
            bundles = await self._load_group_bundles_by_ids(
                session,
                [document.trigger_group_id for document in documents],
                include_deleted=False,
                active_only=True,
            )
            bundles_by_group_id = {bundle.group.id: bundle for bundle in bundles}

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
        page = WordbankSearchPage(
            items=tuple(
                self._search_item_from_document(
                    document,
                    score=score,
                    matched_by=matched_by,
                    trigger_shape=(
                        shape_from_payload(bundle.variants[0].message_json)
                        if (
                            (
                                bundle := bundles_by_group_id.get(
                                    document.trigger_group_id
                                )
                            )
                            is not None
                            and bundle.variants
                        )
                        else None
                    ),
                    response_shape=(
                        shape_from_payload(bundle.responses[0].message_json)
                        if (
                            (
                                bundle := bundles_by_group_id.get(
                                    document.trigger_group_id
                                )
                            )
                            is not None
                            and bundle.responses
                        )
                        else None
                    ),
                )
                for score, _, matched_by, document in paged
            ),
            total_count=total_count,
            offset=offset,
            limit=limit,
        )
        log_perf(
            "repo.search_page.ranked",
            start=start,
            keyword=request.keyword or "-",
            field=request.field,
            creator_id=request.creator_id or "-",
            has_image=request.has_image,
            candidate_limit=candidate_limit,
            text_candidates=len(text_scores),
            image_candidates=len(image_scores),
            documents=len(documents),
            ranked=len(ranked),
            returned=len(page.items),
            total_count=total_count,
            text_ms=f"{text_ms:.2f}",
            image_ms=f"{image_ms:.2f}",
        )
        return page

    def _rank_search_document(
        self: Any,
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
            normalize_search_text(request.keyword) if request.keyword else ""
        )
        if normalized_keyword:
            if request.field in {
                "all",
                "trigger",
            } and normalized_keyword in normalize_search_text(document.trigger_text):
                final_score += 0.25
            if request.field in {
                "all",
                "response",
            } and normalized_keyword in normalize_search_text(document.response_text):
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
        self: Any,
        session: AsyncSession,
        keyword: str,
        *,
        field: str,
        limit: int,
    ) -> tuple[dict[int, float], dict[int, set[str]]]:
        start = perf_start()
        query = build_fts_query(keyword)
        if not query:
            log_perf(
                "repo.search_text_scores.skipped",
                start=start,
                field=field,
                limit=limit,
                reason="empty_query",
            )
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
        log_perf(
            "repo.search_text_scores.done",
            start=start,
            field=field,
            limit=limit,
            tables=",".join(tables),
            query_len=len(query),
            matched_groups=len(scores),
        )
        return scores, sources

    async def _search_image_scores(
        self: Any,
        session: AsyncSession,
        image_scores: dict[int, float],
        *,
        field: str,
        creator_id: str,
    ) -> tuple[dict[int, float], dict[int, set[str]]]:
        start = perf_start()
        if not image_scores:
            log_perf(
                "repo.search_image_scores.skipped",
                start=start,
                field=field,
                creator_id=creator_id or "-",
                reason="empty_image_scores",
            )
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
        log_perf(
            "repo.search_image_scores.done",
            start=start,
            field=field,
            creator_id=creator_id or "-",
            input_scores=len(image_scores),
            matched_groups=len(scores),
            rows=len(rows),
        )
        return scores, sources
