from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

from nonebot.adapters.onebot.v11.message import Message
from sqlalchemy import Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.backup import register_backup_database
from src.lib.db.connectors import StateStore
from src.lib.db.orm import TimeMixin
from src.lib.utils.common import get_current_time


class MessageAssetBase(DeclarativeBase):
    """Global message asset database base."""


class MessageAsset(MessageAssetBase, TimeMixin):
    __tablename__ = "message_asset"
    __table_args__ = (UniqueConstraint("asset_key", name="uq_message_asset_asset_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sender_bot_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    origin_message_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="",
    )
    origin_target_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
    )
    message_shape_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="plain",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_verify_error: Mapped[str] = mapped_column(Text, nullable=False, default="")


message_asset_db = StateStore(
    namespace="message_asset_db",
    filename="message_asset.db",
)
register_backup_database(message_asset_db)


AssetKind = Literal["single_message", "forward_node"]
MessageShapeKind = Literal["plain", "rich", "contains_reply", "contains_at"]


@dataclass(slots=True, frozen=True)
class MessageAssetRecord:
    asset_key: str
    content_hash: str
    asset_kind: AssetKind
    source_kind: str
    message_id: str
    sender_bot_id: str
    origin_message_type: str
    origin_target_id: str
    message_shape_kind: MessageShapeKind
    status: str
    last_verify_error: str
    created_at: int
    updated_at: int


@dataclass(slots=True, frozen=True)
class MessageAssetDescriptor:
    asset_key: str
    content_hash: str
    reusable_globally: bool
    message_shape_kind: MessageShapeKind
    normalized_content: str
    disqualify_reason: str = ""


def serialize_message(message: Message | str) -> str:
    if isinstance(message, str):
        return json.dumps(
            [{"type": "text", "data": {"text": message}}],
            ensure_ascii=False,
        )
    payload: list[dict[str, object]] = []
    for segment in message:
        payload.append(
            {
                "type": segment.type,
                "data": {key: str(value) for key, value in segment.data.items()},
            }
        )
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def describe_message_asset(message: Message | str) -> MessageAssetDescriptor:
    serialized = serialize_message(message)
    if isinstance(message, str):
        shape_kind: MessageShapeKind = "plain"
        reusable = bool(message)
        reason = "" if reusable else "empty_message"
    else:
        segment_types = [segment.type for segment in message]
        if "reply" in segment_types:
            shape_kind = "contains_reply"
            reusable = False
            reason = "contains_reply"
        elif "at" in segment_types:
            shape_kind = "contains_at"
            reusable = False
            reason = "contains_at"
        elif all(segment.type == "text" for segment in message):
            shape_kind = "plain"
            reusable = True
            reason = ""
        else:
            shape_kind = "rich"
            reusable = True
            reason = ""
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return MessageAssetDescriptor(
        asset_key=digest,
        content_hash=digest,
        reusable_globally=reusable,
        message_shape_kind=shape_kind,
        normalized_content=serialized,
        disqualify_reason=reason,
    )


class MessageAssetRepository:
    @classmethod
    async def init_all_tables(cls) -> None:
        await message_asset_db.init(MessageAssetBase)

    @staticmethod
    def _to_record(row: MessageAsset) -> MessageAssetRecord:
        return MessageAssetRecord(
            asset_key=row.asset_key,
            content_hash=row.content_hash,
            asset_kind=row.asset_kind,  # type: ignore[arg-type]
            source_kind=row.source_kind,
            message_id=row.message_id,
            sender_bot_id=row.sender_bot_id,
            origin_message_type=row.origin_message_type,
            origin_target_id=row.origin_target_id,
            message_shape_kind=row.message_shape_kind,  # type: ignore[arg-type]
            status=row.status,
            last_verify_error=row.last_verify_error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_asset(self, asset_key: str) -> MessageAssetRecord | None:
        async with message_asset_db.read_session() as session:
            row = (
                await session.execute(
                    select(MessageAsset).where(MessageAsset.asset_key == asset_key)
                )
            ).scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def upsert_asset(
        self,
        *,
        asset_key: str,
        content_hash: str,
        asset_kind: AssetKind,
        source_kind: str,
        message_id: str,
        sender_bot_id: str,
        origin_message_type: str,
        origin_target_id: str,
        message_shape_kind: MessageShapeKind,
        status: str = "active",
        last_verify_error: str = "",
    ) -> MessageAssetRecord:
        now = get_current_time()
        async with message_asset_db.write_session() as session:
            row = (
                await session.execute(
                    select(MessageAsset).where(MessageAsset.asset_key == asset_key)
                )
            ).scalar_one_or_none()
            if row is None:
                row = MessageAsset(
                    asset_key=asset_key,
                    content_hash=content_hash,
                    asset_kind=asset_kind,
                    source_kind=source_kind,
                    message_id=message_id,
                    sender_bot_id=sender_bot_id,
                    origin_message_type=origin_message_type,
                    origin_target_id=origin_target_id,
                    message_shape_kind=message_shape_kind,
                    status=status,
                    last_verify_error=last_verify_error,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.content_hash = content_hash
                row.asset_kind = asset_kind
                row.source_kind = source_kind
                row.message_id = message_id
                row.sender_bot_id = sender_bot_id
                row.origin_message_type = origin_message_type
                row.origin_target_id = origin_target_id
                row.message_shape_kind = message_shape_kind
                row.status = status
                row.last_verify_error = last_verify_error
                row.updated_at = now
            await session.flush()
            return self._to_record(row)

    async def mark_stale(
        self,
        asset_key: str,
        *,
        last_verify_error: str,
    ) -> None:
        now = get_current_time()
        async with message_asset_db.write_session() as session:
            row = (
                await session.execute(
                    select(MessageAsset).where(MessageAsset.asset_key == asset_key)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = "stale"
            row.last_verify_error = last_verify_error
            row.updated_at = now


message_asset_repo = MessageAssetRepository()
