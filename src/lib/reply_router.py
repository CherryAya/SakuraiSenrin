from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from sqlalchemy import Integer, String, Text, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.lib.backup import register_backup_database
from src.lib.db.connectors import StateStore
from src.lib.db.orm import TimeMixin
from src.lib.utils.common import get_current_time
from src.logger import logger


class ReplyContextBase(DeclarativeBase):
    """Global reply-context database base."""


class ReplyContext(ReplyContextBase, TimeMixin):
    __tablename__ = "reply_context"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    context_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    message_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sender_bot_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


reply_context_db = StateStore(
    namespace="reply_router_db",
    filename="reply_context.db",
)
register_backup_database(reply_context_db)

ReplyResolvedBy = Literal["message_id", "message_hash"]
ReplyResolveReason = Literal[
    "no_reply",
    "message_id_not_found",
    "get_msg_failed",
    "sender_mismatch",
    "hash_not_found",
    "ambiguous_hash",
]
ReplyTextMatcher = Callable[[str], bool]
ReplyRouteHandler = Callable[[Bot, MessageEvent, "ResolvedReplyTarget"], Awaitable[Any]]
ReplyLegacyRule = Callable[[MessageEvent], Awaitable[bool]]
ReplyLegacyHandler = Callable[[Bot, MessageEvent], Awaitable[Any]]


@dataclass(slots=True, frozen=True)
class ReplyContextSpec:
    context_kind: str
    payload: Mapping[str, Any]
    enable_hash_fallback: bool = True


@dataclass(slots=True, frozen=True)
class ReplyContextRecord:
    context_kind: str
    message_id: str
    message_hash: str
    sender_bot_id: str
    origin_message_type: str
    origin_target_id: str
    source_kind: str
    payload: Mapping[str, Any]
    status: str
    created_at: int
    updated_at: int


@dataclass(slots=True, frozen=True)
class ResolvedReplyTarget:
    record: ReplyContextRecord
    resolved_by: ReplyResolvedBy
    resolved_reply_message_id: str
    message_hash: str

    @property
    def context_kind(self) -> str:
        return self.record.context_kind

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.record.payload


@dataclass(slots=True, frozen=True)
class ReplyRoute:
    name: str
    context_kinds: tuple[str, ...]
    text_matcher: ReplyTextMatcher
    handler: ReplyRouteHandler
    legacy_rule: ReplyLegacyRule | None = None
    legacy_handler: ReplyLegacyHandler | None = None


@dataclass(slots=True, frozen=True)
class ReplyMessageSnapshot:
    message: Message | Sequence[object] | str
    sender_user_id: str


class ReplyContextRepository:
    def __init__(self) -> None:
        self._initialized = False

    async def init_all_tables(self) -> None:
        if self._initialized:
            return
        await reply_context_db.init(ReplyContextBase)
        self._initialized = True

    @staticmethod
    def _to_record(row: ReplyContext) -> ReplyContextRecord:
        payload = json.loads(row.payload_json or "{}")
        if not isinstance(payload, dict):
            payload = {}
        return ReplyContextRecord(
            context_kind=row.context_kind,
            message_id=row.message_id,
            message_hash=row.message_hash,
            sender_bot_id=row.sender_bot_id,
            origin_message_type=row.origin_message_type,
            origin_target_id=row.origin_target_id,
            source_kind=row.source_kind,
            payload=payload,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def upsert_context(
        self,
        *,
        context_kind: str,
        message_id: str,
        message_hash: str,
        sender_bot_id: str,
        origin_message_type: str,
        origin_target_id: str,
        source_kind: str,
        payload: Mapping[str, Any],
        status: str = "active",
    ) -> ReplyContextRecord:
        await self.init_all_tables()
        now = get_current_time()
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        async with reply_context_db.write_session() as session:
            row = (
                await session.execute(
                    select(ReplyContext).where(ReplyContext.message_id == message_id)
                )
            ).scalar_one_or_none()
            action = "create" if row is None else "update"
            if row is None:
                row = ReplyContext(
                    context_kind=context_kind,
                    message_id=message_id,
                    message_hash=message_hash,
                    sender_bot_id=sender_bot_id,
                    origin_message_type=origin_message_type,
                    origin_target_id=origin_target_id,
                    source_kind=source_kind,
                    payload_json=payload_json,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.context_kind = context_kind
                row.message_hash = message_hash
                row.sender_bot_id = sender_bot_id
                row.origin_message_type = origin_message_type
                row.origin_target_id = origin_target_id
                row.source_kind = source_kind
                row.payload_json = payload_json
                row.status = status
                row.updated_at = now
            await session.flush()
            logger.debug(
                "[ReplyRouter] upsert context "
                f"action={action} context_kind={context_kind} "
                f"message_id={message_id} hash={_short_hash(message_hash)} "
                "bot_id="
                f"{sender_bot_id} "
                f"origin={origin_message_type}:{origin_target_id or '-'} "
                f"source={source_kind} status={status}"
            )
            return self._to_record(row)

    async def get_context_by_message_id(
        self,
        message_id: str,
        *,
        allowed_context_kinds: Sequence[str] = (),
    ) -> ReplyContextRecord | None:
        await self.init_all_tables()
        async with reply_context_db.read_session() as session:
            row = (
                await session.execute(
                    select(ReplyContext).where(ReplyContext.message_id == message_id)
                )
            ).scalar_one_or_none()
        if row is None or row.status != "active":
            return None
        record = self._to_record(row)
        if allowed_context_kinds and record.context_kind not in allowed_context_kinds:
            return None
        return record

    async def list_contexts_by_message_hash(
        self,
        *,
        sender_bot_id: str,
        message_hash: str,
        allowed_context_kinds: Sequence[str] = (),
    ) -> list[ReplyContextRecord]:
        await self.init_all_tables()
        async with reply_context_db.read_session() as session:
            rows = (
                (
                    await session.execute(
                        select(ReplyContext)
                        .where(
                            ReplyContext.sender_bot_id == sender_bot_id,
                            ReplyContext.message_hash == message_hash,
                            ReplyContext.status == "active",
                        )
                        .order_by(
                            ReplyContext.updated_at.desc(), ReplyContext.id.desc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        records = [self._to_record(row) for row in rows]
        if not allowed_context_kinds:
            return records
        return [
            record for record in records if record.context_kind in allowed_context_kinds
        ]

    async def clear_all_contexts(self) -> int:
        await self.init_all_tables()
        async with reply_context_db.write_session() as session:
            total = await session.scalar(select(func.count()).select_from(ReplyContext))
            deleted = int(total or 0)
            await session.execute(delete(ReplyContext))
            return deleted


reply_context_repo = ReplyContextRepository()
_reply_routes: dict[str, ReplyRoute] = {}


def _short_hash(value: str, *, length: int = 12) -> str:
    if not value:
        return "-"
    return value[:length]


def _message_event_kind(event: MessageEvent) -> str:
    return "group" if isinstance(event, GroupMessageEvent) else "private"


def _event_log_fields(event: MessageEvent) -> str:
    return (
        f"event_message_id={getattr(event, 'message_id', '-')}"
        f" event_user_id={getattr(event, 'user_id', '-')}"
        f" event_group_id={getattr(event, 'group_id', '-') or '-'}"
        f" event_type={_message_event_kind(event)}"
    )


def get_reply_message_ids(event: MessageEvent) -> tuple[str, ...]:
    reply = getattr(event, "reply", None)
    if reply is None:
        return ()
    message_ids: list[str] = []
    for attr_name in ("real_id", "message_id"):
        value = getattr(reply, attr_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in message_ids:
            message_ids.append(text)
    return tuple(message_ids)


def _normalize_text_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_image_key(data: Mapping[str, object]) -> str:
    candidates = (
        "file_unique",
        "image_id",
        "id",
        "file_id",
        "file",
        "url",
        "path",
    )
    for key in candidates:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if text.startswith("base64://"):
            return "base64"
        if text.startswith("http://") or text.startswith("https://"):
            return text.split("?", 1)[0].rsplit("/", 1)[-1]
        if "/" in text or "\\" in text:
            return Path(text).name
        if text.startswith("b'") or text.startswith('b"'):
            return "bytes"
        return text
    return "image"


def _normalize_segment_payload(
    segment_type: str, data: Mapping[str, object]
) -> dict[str, object]:
    if segment_type == "text":
        return {"type": "text", "text": _normalize_text_value(data.get("text", ""))}
    if segment_type == "reply":
        return {"type": "reply"}
    if segment_type == "at":
        return {"type": "at", "target": _normalize_text_value(data.get("qq", ""))}
    if segment_type == "image":
        return {"type": "image", "key": _normalize_image_key(data)}
    return {
        "type": segment_type,
        "data": {
            str(key): _normalize_text_value(value)
            for key, value in sorted(data.items(), key=lambda item: str(item[0]))
        },
    }


def _iter_message_segments(
    message: Message | Sequence[object] | str,
) -> list[dict[str, object]]:
    if isinstance(message, str):
        return [{"type": "text", "data": {"text": message}}]
    if isinstance(message, Message):
        return [
            {
                "type": segment.type,
                "data": dict(segment.data),
            }
            for segment in message
        ]
    segments: list[dict[str, object]] = []
    for item in message:
        if isinstance(item, dict):
            segment_type = item.get("type")
            data = item.get("data")
            if isinstance(segment_type, str) and isinstance(data, dict):
                segments.append({"type": segment_type, "data": data})
                continue
        segment_type = getattr(item, "type", None)
        data = getattr(item, "data", None)
        if isinstance(segment_type, str) and isinstance(data, dict):
            segments.append({"type": segment_type, "data": data})
    return segments


def build_reply_message_hash(
    message: Message | Sequence[object] | str,
    *,
    sender_bot_id: str,
) -> str:
    normalized_segments = [
        _normalize_segment_payload(
            str(segment["type"]),
            segment["data"] if isinstance(segment["data"], Mapping) else {},
        )
        for segment in _iter_message_segments(message)
    ]
    serialized = json.dumps(
        {"bot_id": str(sender_bot_id), "segments": normalized_segments},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def fetch_reply_message_snapshot(
    bot: Bot,
    *,
    message_id: str,
) -> ReplyMessageSnapshot | None:
    try:
        raw_result = await bot.call_api(
            "get_msg",
            message_id=int(message_id) if message_id.isdigit() else message_id,
        )
    except Exception as exc:
        logger.debug(
            "[ReplyRouter] get_msg failed "
            f"message_id={message_id} bot_id={bot.self_id} error={exc}"
        )
        return None
    if not isinstance(raw_result, Mapping):
        logger.debug(
            "[ReplyRouter] get_msg invalid payload "
            f"message_id={message_id} bot_id={bot.self_id}"
        )
        return None
    sender = raw_result.get("sender")
    sender_user_id = ""
    if isinstance(sender, Mapping):
        sender_user_id = _normalize_text_value(sender.get("user_id", ""))
    if not sender_user_id:
        sender_user_id = _normalize_text_value(raw_result.get("user_id", ""))
    message = raw_result.get("message")
    if isinstance(message, Message):
        resolved_message: Message | Sequence[object] | str = message
    elif isinstance(message, str):
        resolved_message = message
    elif isinstance(message, Sequence):
        resolved_message = message
    else:
        resolved_message = _normalize_text_value(raw_result.get("raw_message", ""))
    return ReplyMessageSnapshot(
        message=resolved_message,
        sender_user_id=sender_user_id,
    )


async def record_reply_context(
    bot: Bot,
    *,
    message_id: str,
    context_spec: ReplyContextSpec,
    source_kind: str,
    origin_message_type: str,
    origin_target_id: str,
    fallback_message: Message | Sequence[object] | str,
) -> ReplyContextRecord | None:
    message_id = message_id.strip()
    if not message_id:
        logger.debug(
            "[ReplyRouter] record skipped reason=empty_message_id "
            f"context_kind={context_spec.context_kind}"
        )
        return None
    bot_id = str(bot.self_id)
    snapshot = await fetch_reply_message_snapshot(bot, message_id=message_id)
    if snapshot is not None and snapshot.sender_user_id == bot_id:
        message_hash = build_reply_message_hash(
            snapshot.message,
            sender_bot_id=bot_id,
        )
        hash_source = "get_msg"
    else:
        if snapshot is not None and snapshot.sender_user_id != bot_id:
            logger.debug(
                "[ReplyRouter] record sender mismatch fallback "
                f"context_kind={context_spec.context_kind} message_id={message_id} "
                "expected_bot_id="
                f"{bot_id} actual_sender_id={snapshot.sender_user_id or '-'}"
            )
        message_hash = build_reply_message_hash(
            fallback_message,
            sender_bot_id=bot_id,
        )
        hash_source = "fallback"
    logger.debug(
        "[ReplyRouter] record context "
        f"context_kind={context_spec.context_kind} message_id={message_id} "
        f"hash={_short_hash(message_hash)} bot_id={bot_id} "
        f"origin={origin_message_type}:{origin_target_id or '-'} "
        f"source={source_kind} hash_source={hash_source}"
    )
    return await reply_context_repo.upsert_context(
        context_kind=context_spec.context_kind,
        message_id=message_id,
        message_hash=message_hash,
        sender_bot_id=bot_id,
        origin_message_type=origin_message_type,
        origin_target_id=origin_target_id,
        source_kind=source_kind,
        payload=context_spec.payload,
    )


async def record_reply_context_from_send_result(
    bot: Bot,
    *,
    send_result: object,
    context_spec: ReplyContextSpec,
    source_kind: str,
    origin_message_type: str,
    origin_target_id: str,
    fallback_message: Message | Sequence[object] | str,
) -> ReplyContextRecord | None:
    if isinstance(send_result, Mapping):
        raw_message_id = send_result.get("message_id")
    else:
        raw_message_id = getattr(send_result, "message_id", None)
    if raw_message_id is None:
        logger.debug(
            "[ReplyRouter] record skipped reason=missing_send_result_message_id "
            f"context_kind={context_spec.context_kind}"
        )
        return None
    return await record_reply_context(
        bot,
        message_id=str(raw_message_id),
        context_spec=context_spec,
        source_kind=source_kind,
        origin_message_type=origin_message_type,
        origin_target_id=origin_target_id,
        fallback_message=fallback_message,
    )


def _dedupe_hash_records(
    records: Sequence[ReplyContextRecord],
) -> tuple[ReplyContextRecord, ...]:
    deduped: dict[tuple[str, str, str, str], ReplyContextRecord] = {}
    for record in records:
        payload_json = json.dumps(record.payload, ensure_ascii=False, sort_keys=True)
        key = (
            record.context_kind,
            record.sender_bot_id,
            record.message_hash,
            payload_json,
        )
        deduped.setdefault(key, record)
    return tuple(deduped.values())


def _event_cache(
    event: MessageEvent,
) -> dict[tuple[str, ...], ResolvedReplyTarget | None]:
    cache = getattr(event, "__reply_router_cache__", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    setattr(event, "__reply_router_cache__", cache)
    return cache


def _event_snapshot_cache(
    event: MessageEvent,
) -> dict[str, ReplyMessageSnapshot | None]:
    cache = getattr(event, "__reply_router_snapshot_cache__", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    setattr(event, "__reply_router_snapshot_cache__", cache)
    return cache


def _event_hash_cache(event: MessageEvent) -> dict[tuple[str, str], str]:
    cache = getattr(event, "__reply_router_hash_cache__", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    setattr(event, "__reply_router_hash_cache__", cache)
    return cache


async def _fetch_reply_message_snapshot_cached(
    bot: Bot,
    event: MessageEvent,
    *,
    message_id: str,
) -> ReplyMessageSnapshot | None:
    cache = _event_snapshot_cache(event)
    if message_id in cache:
        return cache[message_id]
    snapshot = await fetch_reply_message_snapshot(bot, message_id=message_id)
    cache[message_id] = snapshot
    return snapshot


def _build_reply_message_hash_cached(
    event: MessageEvent,
    *,
    message_id: str,
    message: Message | Sequence[object] | str,
    sender_bot_id: str,
) -> str:
    cache_key = (message_id, sender_bot_id)
    cache = _event_hash_cache(event)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    message_hash = build_reply_message_hash(
        message,
        sender_bot_id=sender_bot_id,
    )
    cache[cache_key] = message_hash
    return message_hash


async def resolve_reply_target(
    bot: Bot,
    event: MessageEvent,
    *,
    allowed_context_kinds: Sequence[str] = (),
    allow_hash_fallback: bool = True,
) -> ResolvedReplyTarget | None:
    cache_key = (
        *allowed_context_kinds,
        "__hash__" if allow_hash_fallback else "__direct__",
    )
    cache = _event_cache(event)
    if cache_key in cache:
        return cache[cache_key]
    reply_message_ids = get_reply_message_ids(event)
    logger.debug(
        "[ReplyRouter] resolve start "
        f"{_event_log_fields(event)} reply_candidates={reply_message_ids} "
        f"allowed_context_kinds={tuple(allowed_context_kinds)}"
    )
    if not reply_message_ids:
        logger.debug(
            f"[ReplyRouter] resolve failed {_event_log_fields(event)} reason=no_reply"
        )
        cache[cache_key] = None
        return None
    for reply_message_id in reply_message_ids:
        record = await reply_context_repo.get_context_by_message_id(
            reply_message_id,
            allowed_context_kinds=allowed_context_kinds,
        )
        logger.debug(
            "[ReplyRouter] message_id lookup "
            f"{_event_log_fields(event)} reply_message_id={reply_message_id} "
            f"matched={record is not None}"
        )
        if record is not None:
            resolved = ResolvedReplyTarget(
                record=record,
                resolved_by="message_id",
                resolved_reply_message_id=reply_message_id,
                message_hash=record.message_hash,
            )
            logger.debug(
                "[ReplyRouter] resolve success "
                f"{_event_log_fields(event)} resolved_by=message_id "
                f"context_kind={record.context_kind} message_id={record.message_id} "
                f"hash={_short_hash(record.message_hash)}"
            )
            cache[cache_key] = resolved
            return resolved
    if not allow_hash_fallback:
        cache[cache_key] = None
        return None
    bot_id = str(bot.self_id)
    for reply_message_id in reply_message_ids:
        snapshot = await _fetch_reply_message_snapshot_cached(
            bot,
            event,
            message_id=reply_message_id,
        )
        if snapshot is None:
            continue
        if snapshot.sender_user_id != bot_id:
            logger.debug(
                "[ReplyRouter] sender mismatch "
                f"{_event_log_fields(event)} reply_message_id={reply_message_id} "
                "expected_bot_id="
                f"{bot_id} actual_sender_id={snapshot.sender_user_id or '-'}"
            )
            continue
        message_hash = _build_reply_message_hash_cached(
            event,
            message_id=reply_message_id,
            message=snapshot.message,
            sender_bot_id=bot_id,
        )
        logger.debug(
            "[ReplyRouter] hash computed "
            f"{_event_log_fields(event)} reply_message_id={reply_message_id} "
            f"hash={_short_hash(message_hash)}"
        )
        matched_records = await reply_context_repo.list_contexts_by_message_hash(
            sender_bot_id=bot_id,
            message_hash=message_hash,
            allowed_context_kinds=allowed_context_kinds,
        )
        deduped = _dedupe_hash_records(matched_records)
        logger.debug(
            "[ReplyRouter] hash lookup "
            f"{_event_log_fields(event)} reply_message_id={reply_message_id} "
            f"hash={_short_hash(message_hash)} candidates={len(matched_records)} "
            f"deduped_candidates={len(deduped)}"
        )
        if len(deduped) == 1:
            record = deduped[0]
            resolved = ResolvedReplyTarget(
                record=record,
                resolved_by="message_hash",
                resolved_reply_message_id=reply_message_id,
                message_hash=message_hash,
            )
            logger.debug(
                "[ReplyRouter] resolve success "
                f"{_event_log_fields(event)} resolved_by=message_hash "
                f"context_kind={record.context_kind} message_id={record.message_id} "
                f"hash={_short_hash(message_hash)}"
            )
            cache[cache_key] = resolved
            return resolved
        if len(deduped) > 1:
            logger.debug(
                "[ReplyRouter] resolve failed "
                f"{_event_log_fields(event)} reason=ambiguous_hash "
                f"reply_message_id={reply_message_id} hash={_short_hash(message_hash)}"
            )
            cache[cache_key] = None
            return None
    logger.debug(
        "[ReplyRouter] resolve failed "
        f"{_event_log_fields(event)} reason=hash_not_found "
        f"reply_candidates={reply_message_ids}"
    )
    cache[cache_key] = None
    return None


def register_reply_route(route: ReplyRoute) -> None:
    _reply_routes[route.name] = route
    logger.debug(
        "[ReplyRouter] route registered "
        f"name={route.name} context_kinds={route.context_kinds}"
    )


def get_reply_route(name: str) -> ReplyRoute | None:
    return _reply_routes.get(name)


def build_reply_rule(route_name: str) -> Callable[[Bot, MessageEvent], Awaitable[bool]]:
    async def _rule(bot: Bot, event: MessageEvent) -> bool:
        route = get_reply_route(route_name)
        if route is None:
            logger.debug(
                f"[ReplyRouter] rule skipped reason=route_missing name={route_name}"
            )
            return False
        text = event.message.extract_plain_text()
        if not route.text_matcher(text):
            return False
        if route.legacy_rule is not None and await route.legacy_rule(event):
            logger.debug(
                "[ReplyRouter] rule matched via legacy bridge "
                f"name={route_name} {_event_log_fields(event)}"
            )
            return True
        target = await resolve_reply_target(
            bot,
            event,
            allowed_context_kinds=route.context_kinds,
        )
        if target is None:
            if route.legacy_rule is not None:
                return await route.legacy_rule(event)
            return False
        logger.debug(
            "[ReplyRouter] rule matched "
            f"name={route_name} context_kind={target.context_kind} "
            f"resolved_by={target.resolved_by} {_event_log_fields(event)}"
        )
        return True

    return _rule


async def dispatch_reply_route(
    route_name: str,
    bot: Bot,
    event: MessageEvent,
) -> Any:
    route = get_reply_route(route_name)
    if route is None:
        raise RuntimeError(f"reply route {route_name!r} is not registered")
    if (
        route.legacy_rule is not None
        and route.legacy_handler is not None
        and await route.legacy_rule(event)
    ):
        direct_target = await resolve_reply_target(
            bot,
            event,
            allowed_context_kinds=route.context_kinds,
            allow_hash_fallback=False,
        )
        if direct_target is not None:
            return await route.handler(bot, event, direct_target)
        return await route.legacy_handler(bot, event)
    target = await resolve_reply_target(
        bot,
        event,
        allowed_context_kinds=route.context_kinds,
    )
    if target is None:
        logger.debug(
            "[ReplyRouter] dispatch skipped reason=target_not_resolved "
            f"name={route_name} {_event_log_fields(event)}"
        )
        if route.legacy_handler is not None:
            return await route.legacy_handler(bot, event)
        return None
    logger.debug(
        "[ReplyRouter] dispatch matched "
        f"name={route_name} context_kind={target.context_kind} "
        f"resolved_by={target.resolved_by} {_event_log_fields(event)}"
    )
    return await route.handler(bot, event, target)


__all__ = [
    "ReplyContextRecord",
    "ReplyContextSpec",
    "ReplyLegacyHandler",
    "ReplyLegacyRule",
    "ReplyRoute",
    "ReplyRouteHandler",
    "ReplyTextMatcher",
    "ResolvedReplyTarget",
    "build_reply_message_hash",
    "build_reply_rule",
    "dispatch_reply_route",
    "fetch_reply_message_snapshot",
    "get_reply_message_ids",
    "get_reply_route",
    "record_reply_context",
    "record_reply_context_from_send_result",
    "register_reply_route",
    "reply_context_repo",
    "resolve_reply_target",
]
