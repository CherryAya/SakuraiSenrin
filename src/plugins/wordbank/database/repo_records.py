"""Record conversion helpers for the wordbank repository."""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import cast

from src.plugins.wordbank.message_model import MessageShape, shape_from_payload

from .repo_shared import (
    GroupBundle,
    decode_group_ids,
    first_image_id,
    merge_image_keys,
    representative_response,
    search_preview_responses,
)
from .tables import (
    WordbankDeleteVote,
    WordbankImage,
    WordbankMessageRef,
    WordbankMessageRoute,
    WordbankResponseItem,
    WordbankSearchDocument,
    WordbankTriggerGroup,
    WordbankTriggerVariant,
)
from .types import (
    WordbankDeleteVoteRecord,
    WordbankGroupDetail,
    WordbankImageRecord,
    WordbankMessageRefKind,
    WordbankMessageRefRecord,
    WordbankMessageRouteRecord,
    WordbankResponseItemDetail,
    WordbankResponseItemRecord,
    WordbankSearchItem,
    WordbankTriggerGroupRecord,
    WordbankTriggerVariantRecord,
)


class WordbankRepositoryRecordsMixin:
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
            probability=group.probability,
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
            remote_storage_path=image.remote_storage_path,
            local_cache_path=image.local_cache_path,
            cache_file_size=image.cache_file_size,
            last_accessed_at=image.last_accessed_at,
            cache_last_hit_at=image.cache_last_hit_at,
            remote_sync_status=image.remote_sync_status,
            remote_synced_at=image.remote_synced_at,
            remote_etag=image.remote_etag,
            remote_object_size=image.remote_object_size,
            created_at=image.created_at,
            updated_at=image.updated_at,
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
    def _to_message_route_record(
        row: WordbankMessageRoute,
    ) -> WordbankMessageRouteRecord:
        return WordbankMessageRouteRecord(
            message_id=row.message_id,
            ref_kind=cast(WordbankMessageRefKind, row.ref_kind),
            shard_key=row.shard_key,
        )

    @staticmethod
    def _to_message_ref_record(
        row: WordbankMessageRef,
    ) -> WordbankMessageRefRecord:
        return WordbankMessageRefRecord(
            message_id=row.message_id,
            ref_kind=cast(WordbankMessageRefKind, row.ref_kind),
            shard_key=row.shard_key,
            trigger_group_id=row.trigger_group_id,
            trigger_variant_id=row.trigger_variant_id,
            response_item_id=row.response_item_id,
            group_id=row.group_id,
            user_id=row.user_id,
            message_type=row.message_type,
            source_message_id=row.source_message_id,
            context_type=row.context_type,
            current_page=row.current_page,
            keyword=row.keyword,
            field=row.field,
            creator_id=row.creator_id,
            has_image=bool(row.has_image),
            group_ids=decode_group_ids(row.group_ids_json),
        )

    @staticmethod
    def _search_item_from_document(
        document: WordbankSearchDocument,
        *,
        score: float = 0.0,
        matched_by: str = "",
        trigger_shape: MessageShape | None = None,
        response_shape: MessageShape | None = None,
    ) -> WordbankSearchItem:
        raw_summaries = json.loads(document.response_preview_json or "[]")
        response_summaries = tuple(str(item) for item in raw_summaries if str(item))
        return WordbankSearchItem(
            trigger_group_id=document.trigger_group_id,
            status=document.status,
            trigger_text=document.trigger_text,
            response_text=document.response_text,
            trigger_shape=trigger_shape,
            response_shape=response_shape,
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
            trigger_preview_image_id=first_image_id(document.trigger_image_keys),
            response_preview_image_id=first_image_id(document.response_image_keys),
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
            trigger_shape=shape_from_payload(variant.message_json),
            response_shape=shape_from_payload(response.message_json),
            response_summaries=(response.text,),
            response_count=1,
            active_response_count=1
            if response.status == "approved" and response.enabled == 1
            else 0,
            scope=response.scope,
            probability=group.probability,
            weight=response.weight,
            created_by=response.created_by,
            response_item_ids=(response.id,),
        )

    @classmethod
    def _group_detail_from_bundle(
        cls,
        bundle: GroupBundle,
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
                weight=response.weight,
                rule=dict(response.rule or {}),
                group_id=response.group_id,
                created_by=response.created_by,
                approved_by=response.approved_by,
                deleted_at=response.deleted_at,
                response_text=response.text,
                response_shape=shape_from_payload(response.message_json),
            )
            for response in bundle.responses
        )
        return WordbankGroupDetail(
            trigger_group_id=bundle.group.id,
            status=bundle.group.status,
            enabled=bundle.group.enabled,
            probability=bundle.group.probability,
            group_id=bundle.group.group_id,
            created_by=bundle.group.created_by,
            deleted_at=bundle.group.deleted_at,
            trigger_text=variant.trigger_text,
            trigger_shape=shape_from_payload(variant.message_json),
            trigger_variant_id=variant.id,
            responses=responses,
            selected_response_item_id=response_item_id,
        )

    @staticmethod
    def _document_payload(bundle: GroupBundle) -> dict[str, object] | None:
        if not bundle.variants:
            return None
        visible_responses = [
            response for response in bundle.responses if response.deleted_at == 0
        ]
        if not visible_responses:
            return None
        variant = bundle.variants[0]
        preview_responses = search_preview_responses(bundle.responses)
        representative = representative_response(visible_responses)
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
            "probability": bundle.group.probability,
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
            "response_image_keys": merge_image_keys(
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
        from .repo_shared import parse_image_keys

        raw_group_id = payload.get("trigger_group_id", 0)
        trigger_group_id = (
            int(raw_group_id) if isinstance(raw_group_id, (int, str)) else 0
        )
        rows: list[dict[str, int | str]] = []
        for side, key in (
            ("trigger", "trigger_image_keys"),
            ("response", "response_image_keys"),
        ):
            for canonical_image_id in parse_image_keys(str(payload[key])):
                rows.append(
                    {
                        "trigger_group_id": trigger_group_id,
                        "side": side,
                        "canonical_image_id": canonical_image_id,
                    }
                )
        return rows
