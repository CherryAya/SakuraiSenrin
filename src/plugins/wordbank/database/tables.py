"""Wordbank table definitions."""

from sqlalchemy import (
    JSON,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.db.orm import TimeMixin


class WordbankMainBase(DeclarativeBase):
    """Wordbank main database base."""


class WordbankLogBase(DeclarativeBase):
    """Wordbank sharded log database base."""


class WordbankTriggerGroup(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_trigger_group"
    __table_args__ = (
        Index("idx_wordbank_trigger_group_status", "status", "enabled", "deleted_at"),
        Index("idx_wordbank_trigger_group_scope", "group_id", "created_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WordbankTriggerVariant(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_trigger_variant"
    __table_args__ = (
        Index(
            "idx_wordbank_trigger_variant_exact_md5",
            "exact_md5",
            "trigger_group_id",
        ),
        Index(
            "idx_wordbank_trigger_variant_structure",
            "structure_key",
            "trigger_group_id",
        ),
        Index("idx_wordbank_trigger_variant_image_keys", "image_keys"),
        UniqueConstraint(
            "trigger_group_id",
            "exact_md5",
            "structure_key",
            name="uq_wordbank_trigger_variant_group_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    exact_md5: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    structure_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    search_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_keys: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WordbankResponseItem(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_response_item"
    __table_args__ = (
        Index(
            "idx_wordbank_response_item_group_status",
            "trigger_group_id",
            "status",
            "enabled",
            "deleted_at",
        ),
        Index(
            "idx_wordbank_response_item_scope",
            "scope",
            "group_id",
        ),
        Index(
            "idx_wordbank_response_item_created_by",
            "created_by",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    rule: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    deleted_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    exact_md5: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    structure_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    search_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_keys: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WordbankImage(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_image"
    __table_args__ = (
        UniqueConstraint("md5", name="uq_wordbank_image_md5"),
        Index("idx_wordbank_image_dhash", "dhash"),
        Index("idx_wordbank_image_phash", "phash"),
        Index("idx_wordbank_image_canonical", "canonical_image_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    md5: Mapped[str] = mapped_column(String(32), nullable=False)
    dhash: Mapped[str] = mapped_column(String(16), nullable=False)
    phash: Mapped[str] = mapped_column(String(16), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    hash_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WordbankSearchDocument(WordbankMainBase):
    __tablename__ = "wordbank_search_document"
    __table_args__ = (
        Index(
            "idx_wordbank_search_document_status",
            "deleted_at",
            "created_by",
            "status",
        ),
        Index(
            "idx_wordbank_search_document_trigger_md5",
            "trigger_exact_md5",
        ),
    )

    trigger_group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    probability: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_exact_md5: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
    )
    trigger_structure_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_image_keys: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_preview_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    response_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_response_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    response_image_keys: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class WordbankSearchImageMap(WordbankMainBase):
    __tablename__ = "wordbank_search_image_map"
    __table_args__ = (
        UniqueConstraint(
            "trigger_group_id",
            "side",
            "canonical_image_id",
            name="uq_wordbank_search_image_map_group_side_image",
        ),
        Index(
            "idx_wordbank_search_image_map_image_side",
            "canonical_image_id",
            "side",
        ),
        Index(
            "idx_wordbank_search_image_map_group_side",
            "trigger_group_id",
            "side",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_image_id: Mapped[int] = mapped_column(Integer, nullable=False)


class WordbankDeleteVote(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_delete_vote"
    __table_args__ = (
        Index(
            "idx_wordbank_delete_vote_response",
            "response_item_id",
            "status",
        ),
        Index("idx_wordbank_delete_vote_group", "group_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    response_item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WordbankDeleteVoteSupport(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_delete_vote_support"
    __table_args__ = (
        UniqueConstraint("vote_id", "user_id", name="uq_wordbank_vote_support_user"),
        Index("idx_wordbank_vote_support_vote", "vote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vote_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)


class WordbankResponseMessage(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_response_message"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_wordbank_response_message_id"),
        Index(
            "idx_wordbank_response_message_response",
            "response_item_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    trigger_variant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    response_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class WordbankApprovalMessage(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_approval_message"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_wordbank_approval_message_id"),
        Index(
            "idx_wordbank_approval_message_response",
            "response_item_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    response_item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_message_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
    )
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class WordbankLog(WordbankLogBase):
    __tablename__ = "wordbank_log"
    __table_args__ = (
        Index(
            "idx_wordbank_log_response_time",
            "response_item_id",
            "created_at",
        ),
        Index("idx_wordbank_log_group_time", "group_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_variant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    response_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    message_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text"
    )
    matched_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
