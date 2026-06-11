"""Wordbank database payload and record types."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    from src.plugins.wordbank.message_model import MessageShape


class WordbankTriggerGroupPayload(TypedDict):
    status: str
    enabled: int
    group_id: str
    created_by: str
    deleted_at: int
    created_at: int
    updated_at: int


class WordbankTriggerVariantPayload(TypedDict):
    trigger_group_id: int
    trigger_text: str
    message_json: str
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str
    created_at: int
    updated_at: int


class WordbankResponseItemPayload(TypedDict):
    trigger_group_id: int
    status: str
    enabled: int
    scope: str
    priority: int
    probability: float
    weight: int
    rule: dict
    group_id: str
    created_by: str
    approved_by: str
    deleted_at: int
    text: str
    message_json: str
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str
    created_at: int
    updated_at: int


class WordbankImagePayload(TypedDict):
    canonical_image_id: NotRequired[int | None]
    md5: str
    dhash: str
    phash: str
    width: int
    height: int
    file_size: int
    hash_version: int
    storage_path: str
    created_at: int
    updated_at: int


class WordbankLogPayload(TypedDict):
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
    matched_text: str
    created_at: int


class WordbankDeleteVotePayload(TypedDict):
    trigger_group_id: int
    response_item_id: int
    group_id: str
    created_by: str
    status: str
    threshold: int
    reason: str
    created_at: int
    updated_at: int


class WordbankResponseMessagePayload(TypedDict):
    message_id: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
    created_at: int
    updated_at: int


class WordbankApprovalMessagePayload(TypedDict):
    message_id: str
    trigger_group_id: int
    response_item_id: int
    group_id: str
    user_id: str
    source_message_id: str
    message_type: str
    created_at: int
    updated_at: int


class WordbankViewMessagePayload(TypedDict):
    message_id: str
    context_type: str
    trigger_group_id: int
    current_page: int
    keyword: str
    field: str
    creator_id: str
    has_image: int
    group_ids_json: str
    group_id: str
    user_id: str
    message_type: str
    created_at: int
    updated_at: int


@dataclass(slots=True, frozen=True)
class WordbankTriggerVariantRecord:
    id: int
    trigger_group_id: int
    trigger_text: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str


@dataclass(slots=True, frozen=True)
class WordbankResponseItemRecord:
    id: int
    trigger_group_id: int
    status: str
    enabled: int
    scope: str
    priority: int
    probability: float
    weight: int
    rule: dict
    group_id: str
    created_by: str
    approved_by: str
    deleted_at: int
    text: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str


@dataclass(slots=True, frozen=True)
class WordbankTriggerGroupRecord:
    id: int
    status: str
    enabled: int
    group_id: str
    created_by: str
    deleted_at: int
    trigger_variants: tuple[WordbankTriggerVariantRecord, ...] = dataclass_field(
        default_factory=tuple
    )
    responses: tuple[WordbankResponseItemRecord, ...] = dataclass_field(
        default_factory=tuple
    )


@dataclass(slots=True, frozen=True)
class WordbankCreatedResponse:
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    status: str
    created_group: bool
    created_variant: bool
    trigger_group: WordbankTriggerGroupRecord
    response_item: WordbankResponseItemRecord

    @property
    def probability(self) -> float:
        return self.response_item.probability

    @property
    def weight(self) -> int:
        return self.response_item.weight

    @property
    def scope(self) -> str:
        return self.response_item.scope


@dataclass(slots=True, frozen=True)
class WordbankImageRecord:
    id: int
    canonical_image_id: int | None
    md5: str
    dhash: str
    phash: str
    width: int
    height: int
    file_size: int
    hash_version: int
    storage_path: str

    @property
    def canonical_id(self) -> int:
        return self.canonical_image_id or self.id


@dataclass(slots=True, frozen=True)
class WordbankSearchRequest:
    keyword: str = ""
    field: str = "all"
    creator_id: str = ""
    has_image: bool = False
    image_scores: dict[int, float] = dataclass_field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WordbankSearchItem:
    trigger_group_id: int
    status: str
    trigger_text: str
    response_text: str
    response_summaries: tuple[str, ...] = dataclass_field(default_factory=tuple)
    response_count: int = 1
    active_response_count: int = 1
    scope: str = "all_groups"
    probability: float = 1.0
    weight: int = 3
    created_by: str = ""
    score: float = 0.0
    matched_by: str = ""
    response_item_ids: tuple[int, ...] = dataclass_field(default_factory=tuple)
    trigger_preview_image_id: int | None = None
    response_preview_image_id: int | None = None

    @property
    def has_more_responses(self) -> bool:
        return self.response_count > len(self.response_summaries)

    @property
    def remaining_response_count(self) -> int:
        return max(0, self.response_count - len(self.response_summaries))


@dataclass(slots=True, frozen=True)
class WordbankSearchPage:
    items: tuple[WordbankSearchItem, ...]
    total_count: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total_count


@dataclass(slots=True, frozen=True)
class WordbankDeleteVoteRecord:
    id: int
    trigger_group_id: int
    response_item_id: int
    group_id: str
    created_by: str
    status: str
    threshold: int
    support_count: int
    reason: str
    created_at: int
    updated_at: int


@dataclass(slots=True, frozen=True)
class WordbankDeleteVoteMutation:
    vote: WordbankDeleteVoteRecord
    created: bool
    already_supported: bool
    passed: bool
    response_item_deleted: bool


@dataclass(slots=True, frozen=True)
class WordbankResponseMessageRecord:
    message_id: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str


@dataclass(slots=True, frozen=True)
class WordbankApprovalMessageRecord:
    message_id: str
    trigger_group_id: int
    response_item_id: int
    group_id: str
    user_id: str
    source_message_id: str
    message_type: str


@dataclass(slots=True, frozen=True)
class WordbankResponseItemDetail:
    response_item_id: int
    status: str
    enabled: int
    scope: str
    probability: float
    weight: int
    rule: dict
    group_id: str
    created_by: str
    approved_by: str
    deleted_at: int
    response_text: str
    response_shape: MessageShape


@dataclass(slots=True, frozen=True)
class WordbankGroupDetail:
    trigger_group_id: int
    status: str
    enabled: int
    group_id: str
    created_by: str
    deleted_at: int
    trigger_text: str
    trigger_shape: MessageShape
    trigger_variant_id: int
    responses: tuple[WordbankResponseItemDetail, ...]
    selected_response_item_id: int | None = None

    @property
    def selected_response(self) -> WordbankResponseItemDetail | None:
        if not self.responses:
            return None
        if self.selected_response_item_id is None:
            return self.responses[0]
        for response in self.responses:
            if response.response_item_id == self.selected_response_item_id:
                return response
        return self.responses[0]


@dataclass(slots=True, frozen=True)
class WordbankViewMessageRecord:
    message_id: str
    context_type: str
    trigger_group_id: int
    current_page: int
    keyword: str
    field: str
    creator_id: str
    has_image: bool
    group_ids: tuple[int, ...]
    group_id: str
    user_id: str
    message_type: str
