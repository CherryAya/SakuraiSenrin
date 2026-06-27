"""Wordbank database payload and record types."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from src.plugins.wordbank.message_model import MessageShape


WordbankRankPeriod = Literal["week", "month", "season", "total"]


class WordbankTriggerGroupPayload(TypedDict):
    status: str
    enabled: int
    probability: float
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
    remote_storage_path: NotRequired[str]
    local_cache_path: NotRequired[str]
    cache_file_size: NotRequired[int]
    last_accessed_at: NotRequired[int]
    cache_last_hit_at: NotRequired[int]
    remote_sync_status: NotRequired[str]
    remote_synced_at: NotRequired[int]
    remote_etag: NotRequired[str]
    remote_object_size: NotRequired[int]
    created_at: int
    updated_at: int


class WordbankLogPayload(TypedDict):
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
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


WordbankMessageRefKind = Literal["response", "approval", "view"]


class WordbankMessageRoutePayload(TypedDict):
    message_id: str
    ref_kind: WordbankMessageRefKind
    shard_key: str
    created_at: int
    updated_at: int


class WordbankMessageRefPayload(TypedDict):
    message_id: str
    ref_kind: WordbankMessageRefKind
    shard_key: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
    source_message_id: str
    context_type: str
    current_page: int
    keyword: str
    field: str
    creator_id: str
    has_image: int
    group_ids_json: str
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
    probability: float
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
        return self.trigger_group.probability

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
    remote_storage_path: str = ""
    local_cache_path: str = ""
    cache_file_size: int = 0
    last_accessed_at: int = 0
    cache_last_hit_at: int = 0
    remote_sync_status: str = "pending"
    remote_synced_at: int = 0
    remote_etag: str = ""
    remote_object_size: int = 0
    created_at: int = 0
    updated_at: int = 0

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
    trigger_shape: MessageShape | None = None
    response_shape: MessageShape | None = None
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
class WordbankCreatorLeaderboardItem:
    created_by: str
    approved_count: int
    latest_created_at: int
    group_count: int = 0
    current_group_count: int = 0
    all_groups_count: int = 0
    self_count: int = 0
    private_only_count: int = 0


@dataclass(slots=True, frozen=True)
class WordbankCreatorLeaderboardSnapshot:
    period: WordbankRankPeriod
    range_start: int
    range_end: int
    total_creator_count: int
    total_approved_count: int
    items: tuple[WordbankCreatorLeaderboardItem, ...]


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
class WordbankMessageRouteRecord:
    message_id: str
    ref_kind: WordbankMessageRefKind
    shard_key: str


@dataclass(slots=True, frozen=True)
class WordbankResponseItemDetail:
    response_item_id: int
    status: str
    enabled: int
    scope: str
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
    probability: float
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
class WordbankMessageRefRecord:
    message_id: str
    ref_kind: WordbankMessageRefKind
    shard_key: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str
    source_message_id: str
    context_type: str
    current_page: int
    keyword: str
    field: str
    creator_id: str
    has_image: bool
    group_ids: tuple[int, ...]
