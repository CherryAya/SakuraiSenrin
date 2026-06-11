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
    trigger_mode: str
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
    trigger_mode: str = "strict"

    @property
    def entry_id(self) -> int:
        return self.trigger_group_id

    @property
    def trigger_id(self) -> int:
        return self.id


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

    @property
    def entry_id(self) -> int:
        return self.id

    @property
    def response_id(self) -> int:
        return self.id


@dataclass(slots=True, frozen=True)
class WordbankTriggerGroupRecord:
    id: int
    status: str
    enabled: int
    group_id: str
    created_by: str
    deleted_at: int
    trigger_mode: str
    trigger_variants: tuple[WordbankTriggerVariantRecord, ...] = dataclass_field(
        default_factory=tuple
    )
    responses: tuple[WordbankResponseItemRecord, ...] = dataclass_field(
        default_factory=tuple
    )

    @property
    def entry_id(self) -> int:
        return self.id

    @property
    def triggers(self) -> tuple[WordbankTriggerVariantRecord, ...]:
        return self.trigger_variants


@dataclass(slots=True, frozen=True)
class WordbankTriggerRecord:
    id: int
    entry_id: int
    trigger_text: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str
    trigger_mode: str = "strict"

    @property
    def trigger_group_id(self) -> int:
        return self.entry_id


@dataclass(slots=True, frozen=True)
class WordbankResponseRecord:
    id: int
    entry_id: int
    text: str
    message_shape: MessageShape
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str
    weight: int
    status: str = "approved"
    enabled: int = 1
    scope: str | None = None
    priority: int | None = None
    probability: float | None = None
    rule: dict | None = None
    group_id: str = ""
    created_by: str = ""
    approved_by: str = ""
    deleted_at: int = 0

    @property
    def trigger_group_id(self) -> int:
        return self.entry_id


@dataclass(slots=True, frozen=True)
class WordbankEntryRecord:
    id: int
    status: str
    enabled: int
    scope: str
    priority: int
    probability: float
    weight: int
    rule: dict
    group_id: str
    created_by: str
    deleted_at: int
    triggers: tuple[WordbankTriggerRecord, ...] = dataclass_field(default_factory=tuple)
    responses: tuple[WordbankResponseRecord, ...] = dataclass_field(
        default_factory=tuple
    )

    @property
    def trigger_mode(self) -> str:
        return self.triggers[0].trigger_mode if self.triggers else "strict"

    @property
    def trigger_variants(self) -> tuple[WordbankTriggerRecord, ...]:
        return self.triggers


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
    def entry_id(self) -> int:
        return self.response_item_id

    @property
    def probability(self) -> float:
        return self.response_item.probability

    @property
    def weight(self) -> int:
        return self.response_item.weight

    @property
    def scope(self) -> str:
        return self.response_item.scope

    @property
    def triggers(self) -> tuple[WordbankTriggerVariantRecord, ...]:
        return self.trigger_group.trigger_variants

    @property
    def responses(self) -> tuple[WordbankResponseItemRecord, ...]:
        return self.trigger_group.responses


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


@dataclass(slots=True, frozen=True, init=False)
class WordbankSearchItem:
    trigger_group_id: int
    status: str
    trigger_text: str
    trigger_mode: str
    response_text: str
    response_summaries: tuple[str, ...] = ()
    response_count: int = 1
    active_response_count: int = 1
    scope: str = "all_groups"
    probability: float = 1.0
    weight: int = 3
    created_by: str = ""
    score: float = 0.0
    matched_by: str = ""
    response_item_ids: tuple[int, ...] = ()

    def __init__(
        self,
        *,
        trigger_group_id: int | None = None,
        entry_id: int | None = None,
        status: str,
        trigger_text: str,
        trigger_mode: str,
        response_text: str,
        response_summaries: tuple[str, ...] = (),
        response_count: int = 1,
        active_response_count: int = 1,
        scope: str = "all_groups",
        probability: float = 1.0,
        weight: int = 3,
        created_by: str = "",
        score: float = 0.0,
        matched_by: str = "",
        response_item_ids: tuple[int, ...] = (),
    ) -> None:
        object.__setattr__(
            self,
            "trigger_group_id",
            trigger_group_id if trigger_group_id is not None else entry_id or 0,
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "trigger_text", trigger_text)
        object.__setattr__(self, "trigger_mode", trigger_mode)
        object.__setattr__(self, "response_text", response_text)
        object.__setattr__(
            self,
            "response_summaries",
            response_summaries or ((response_text,) if response_text else ()),
        )
        object.__setattr__(self, "response_count", response_count)
        object.__setattr__(self, "active_response_count", active_response_count)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "created_by", created_by)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "matched_by", matched_by)
        object.__setattr__(self, "response_item_ids", response_item_ids)

    @property
    def entry_id(self) -> int:
        return self.trigger_group_id

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

    @property
    def entry_id(self) -> int:
        return self.response_item_id


@dataclass(slots=True, frozen=True)
class WordbankDeleteVoteMutation:
    vote: WordbankDeleteVoteRecord
    created: bool
    already_supported: bool
    passed: bool
    entry_deleted: bool


@dataclass(slots=True, frozen=True, init=False)
class WordbankResponseMessageRecord:
    message_id: str
    trigger_group_id: int
    trigger_variant_id: int
    response_item_id: int
    group_id: str
    user_id: str
    message_type: str

    def __init__(
        self,
        *,
        message_id: str,
        trigger_group_id: int = 0,
        trigger_variant_id: int | None = None,
        response_item_id: int | None = None,
        entry_id: int | None = None,
        trigger_id: int | None = None,
        response_id: int | None = None,
        group_id: str,
        user_id: str,
        message_type: str,
    ) -> None:
        object.__setattr__(self, "message_id", message_id)
        object.__setattr__(self, "trigger_group_id", trigger_group_id)
        object.__setattr__(
            self,
            "trigger_variant_id",
            trigger_variant_id if trigger_variant_id is not None else trigger_id or 0,
        )
        object.__setattr__(
            self,
            "response_item_id",
            response_item_id
            if response_item_id is not None
            else response_id or entry_id or 0,
        )
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "message_type", message_type)

    @property
    def entry_id(self) -> int:
        return self.response_item_id

    @property
    def trigger_id(self) -> int:
        return self.trigger_variant_id

    @property
    def response_id(self) -> int:
        return self.response_item_id


@dataclass(slots=True, frozen=True, init=False)
class WordbankApprovalMessageRecord:
    message_id: str
    trigger_group_id: int
    response_item_id: int
    group_id: str
    user_id: str
    source_message_id: str
    message_type: str

    def __init__(
        self,
        *,
        message_id: str,
        trigger_group_id: int = 0,
        response_item_id: int | None = None,
        entry_id: int | None = None,
        group_id: str,
        user_id: str,
        source_message_id: str,
        message_type: str,
    ) -> None:
        object.__setattr__(self, "message_id", message_id)
        object.__setattr__(self, "trigger_group_id", trigger_group_id)
        object.__setattr__(
            self,
            "response_item_id",
            response_item_id if response_item_id is not None else entry_id or 0,
        )
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "source_message_id", source_message_id)
        object.__setattr__(self, "message_type", message_type)

    @property
    def entry_id(self) -> int:
        return self.response_item_id


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

    @property
    def entry_id(self) -> int:
        return self.response_item_id


@dataclass(slots=True, frozen=True)
class WordbankGroupDetail:
    trigger_group_id: int
    status: str
    enabled: int
    group_id: str
    created_by: str
    deleted_at: int
    trigger_text: str
    trigger_mode: str
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
class WordbankEntryDetail:
    entry_id: int
    status: str
    enabled: int
    scope: str
    probability: float
    weight: int
    group_id: str
    created_by: str
    deleted_at: int
    trigger_text: str
    trigger_mode: str
    response_text: str
    trigger_group_id: int = 0
    response_item_id: int = 0


def legacy_entry_detail_from_group(
    detail: WordbankGroupDetail,
) -> WordbankEntryDetail | None:
    selected = detail.selected_response
    if selected is None:
        return None
    return WordbankEntryDetail(
        entry_id=selected.response_item_id,
        status=selected.status,
        enabled=selected.enabled,
        scope=selected.scope,
        probability=selected.probability,
        weight=selected.weight,
        group_id=selected.group_id,
        created_by=selected.created_by,
        deleted_at=selected.deleted_at,
        trigger_text=detail.trigger_text,
        trigger_mode=detail.trigger_mode,
        response_text=selected.response_text,
        trigger_group_id=detail.trigger_group_id,
        response_item_id=selected.response_item_id,
    )
