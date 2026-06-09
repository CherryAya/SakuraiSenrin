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


class WordbankEntry(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_entry"
    __table_args__ = (
        Index("idx_wordbank_entry_status", "status", "enabled", "deleted_at"),
        Index("idx_wordbank_entry_scope", "scope", "group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
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


class WordbankTrigger(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_trigger"
    __table_args__ = (
        Index(
            "idx_wordbank_trigger_text",
            "kind",
            "trigger_mode",
            "normalized_text",
        ),
        Index("idx_wordbank_trigger_image", "kind", "canonical_image_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WordbankResponse(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_response"
    __table_args__ = (Index("idx_wordbank_response_entry", "entry_id", "weight"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    canonical_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


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
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WordbankDeleteVote(WordbankMainBase, TimeMixin):
    __tablename__ = "wordbank_delete_vote"
    __table_args__ = (
        Index("idx_wordbank_delete_vote_entry", "entry_id", "status"),
        Index("idx_wordbank_delete_vote_group", "group_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
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
        Index("idx_wordbank_response_message_entry", "entry_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    trigger_id: Mapped[int] = mapped_column(Integer, nullable=False)
    response_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class WordbankLog(WordbankLogBase):
    __tablename__ = "wordbank_log"
    __table_args__ = (
        Index("idx_wordbank_log_entry_time", "entry_id", "created_at"),
        Index("idx_wordbank_log_group_time", "group_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    message_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text"
    )
    matched_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
