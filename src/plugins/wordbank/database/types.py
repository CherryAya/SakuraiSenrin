"""Wordbank database payload and record types."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    from src.plugins.wordbank.message_model import MessageShape


class WordbankEntryPayload(TypedDict):
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
    created_at: int
    updated_at: int


class WordbankTriggerPayload(TypedDict):
    entry_id: int
    trigger_text: str
    message_json: str
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str
    trigger_mode: str
    created_at: int
    updated_at: int


class WordbankResponsePayload(TypedDict):
    entry_id: int
    text: str
    message_json: str
    exact_md5: str
    structure_key: str
    search_text: str
    search_tokens: str
    image_keys: str
    weight: int
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
    entry_id: int
    trigger_id: int
    group_id: str
    user_id: str
    message_type: str
    matched_text: str
    created_at: int


class WordbankDeleteVotePayload(TypedDict):
    entry_id: int
    group_id: str
    created_by: str
    status: str
    threshold: int
    reason: str
    created_at: int
    updated_at: int


class WordbankResponseMessagePayload(TypedDict):
    message_id: str
    entry_id: int
    trigger_id: int
    response_id: int
    group_id: str
    user_id: str
    message_type: str
    created_at: int
    updated_at: int


class WordbankApprovalMessagePayload(TypedDict):
    message_id: str
    entry_id: int
    group_id: str
    user_id: str
    source_message_id: str
    message_type: str
    created_at: int
    updated_at: int


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
    trigger_mode: str


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
    entry_id: int
    status: str
    trigger_text: str
    trigger_mode: str
    response_text: str
    scope: str
    probability: float
    weight: int
    created_by: str
    score: float = 0.0
    matched_by: str = ""


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
    entry_id: int
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
    entry_deleted: bool


@dataclass(slots=True, frozen=True)
class WordbankResponseMessageRecord:
    message_id: str
    entry_id: int
    trigger_id: int
    response_id: int
    group_id: str
    user_id: str
    message_type: str


@dataclass(slots=True, frozen=True)
class WordbankApprovalMessageRecord:
    message_id: str
    entry_id: int
    group_id: str
    user_id: str
    source_message_id: str
    message_type: str


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
