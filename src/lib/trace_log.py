from __future__ import annotations

import asyncio
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Any, cast

from src.database.log.types import TraceEventLogPayload
from src.lib.utils.common import get_current_time
from src.logger import logger

if TYPE_CHECKING:
    from src.lib.db.batch import BatchWriter

TRACE_PAYLOAD_EXTRA_KEY = "sakurai_trace_payload"
TRACE_PERSIST_EXTRA_KEY = "sakurai_trace_persist"
_DEFAULT_LOG_ROLE = "app"
_file_sink_id: int | None = None
_trace_sink_id: int | None = None
_trace_logging_ready = False
_log_role = _DEFAULT_LOG_ROLE
_trace_tasks: set[asyncio.Task[None]] = set()
_trace_writer: "BatchWriter[TraceEventLogPayload] | None" = None


def _build_trace_writer() -> "BatchWriter[TraceEventLogPayload]":
    from src.database.instances import log_db
    from src.database.log.ops import TraceEventLogOps
    from src.lib.db.batch import BatchWriter, execute_batch_write

    async def _flush_trace_events(batch: list[TraceEventLogPayload]) -> None:
        await execute_batch_write(
            batch=batch,
            db_instance=log_db,
            ops_class=TraceEventLogOps,
            method=TraceEventLogOps.bulk_create_trace_event_logs,
            time_field="created_at",
            emit_trace=False,
        )

    return BatchWriter[TraceEventLogPayload](
        flush_callback=_flush_trace_events,
        batch_size=200,
        flush_interval=2.0,
        max_retries=3,
        retry_backoff=0.25,
        trace_persist=False,
    )


def _get_trace_writer() -> "BatchWriter[TraceEventLogPayload]":
    global _trace_writer
    if _trace_writer is None:
        _trace_writer = _build_trace_writer()
    return _trace_writer


def _track_trace_task(task: asyncio.Task[None]) -> None:
    _trace_tasks.add(task)

    def _on_done(done_task: asyncio.Task[None]) -> None:
        _trace_tasks.discard(done_task)
        if done_task.cancelled():
            return
        exc = done_task.exception()
        if exc is not None:
            logger.error(f"trace log task failed: {exc}")

    task.add_done_callback(_on_done)


def new_trace_id(component: str) -> str:
    normalized = "".join(
        ch if ch.isalnum() else "_" for ch in component.strip().lower()
    ).strip("_")
    prefix = normalized or "trace"
    return f"{prefix}_{token_hex(6)}"


def _format_file_record(record: dict[str, Any]) -> str:
    extra = record["extra"]
    trace_id = extra.get("trace_id", "-")
    component = extra.get("component", record["name"])
    event_name = extra.get("event_name", "-")
    source_kind = extra.get("source_kind", "-")
    status = extra.get("trace_status", "-")
    return (
        "{time:YYYY-MM-DD HH:mm:ss.SSS ZZ} | {level:<8} | "
        f"role={_log_role} | pid={{process.id}} | trace={trace_id} | "
        f"component={component} | source={source_kind} | event={event_name} | "
        f"status={status} | {{name}}:{{function}}:{{line}} | {{message}}\n"
        "{exception}"
    )


def configure_logging(*, log_role: str = _DEFAULT_LOG_ROLE) -> None:
    global _file_sink_id, _trace_sink_id, _log_role

    _log_role = log_role
    log_dir = Path("./data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    if _file_sink_id is None:
        _file_sink_id = logger.add(
            log_dir / f"{log_role}.log",
            level="DEBUG",
            rotation="00:00",
            retention="14 days",
            enqueue=True,
            backtrace=False,
            diagnose=False,
            format=cast(Any, _format_file_record),
        )

    if _trace_sink_id is None:
        _trace_sink_id = logger.add(
            _trace_log_sink,
            level="DEBUG",
            filter=lambda record: TRACE_PAYLOAD_EXTRA_KEY in record["extra"],
            enqueue=False,
            backtrace=False,
            diagnose=False,
        )


def mark_trace_logging_ready() -> None:
    global _trace_logging_ready
    _trace_logging_ready = True


async def shutdown_logging() -> None:
    global _trace_logging_ready
    _trace_logging_ready = False
    if _trace_tasks:
        await asyncio.gather(*tuple(_trace_tasks), return_exceptions=True)
    if _trace_writer is None:
        return
    await _trace_writer.flush_now()
    await _trace_writer.close()


async def flush_trace_logging() -> None:
    if _trace_tasks:
        await asyncio.gather(*tuple(_trace_tasks), return_exceptions=True)
    if _trace_writer is None:
        return
    await _trace_writer.flush_now()


def build_trace_payload(
    *,
    event_name: str,
    source_kind: str,
    component: str,
    status: str,
    summary: str,
    level: str = "INFO",
    trace_id: str | None = None,
    parent_trace_id: str | None = None,
    group_id: str | None = None,
    user_id: str | None = None,
    job_id: str | None = None,
    shard_key: str | None = None,
    record_date: int | None = None,
    batch_size: int | None = None,
    attempt: int | None = None,
    payload_json: dict[str, Any] | None = None,
) -> TraceEventLogPayload:
    payload: TraceEventLogPayload = {
        "created_at": get_current_time(),
        "trace_id": trace_id or new_trace_id(component),
        "source_kind": source_kind,
        "component": component,
        "level": level.upper(),
        "event_name": event_name,
        "status": status,
        "summary": summary[:255],
        "log_role": _log_role,
        "parent_trace_id": None,
        "group_id": None,
        "user_id": None,
        "job_id": None,
        "shard_key": None,
        "record_date": None,
        "batch_size": None,
        "attempt": None,
        "payload_json": {},
    }
    if parent_trace_id is not None:
        payload["parent_trace_id"] = parent_trace_id
    if group_id is not None:
        payload["group_id"] = group_id
    if user_id is not None:
        payload["user_id"] = user_id
    if job_id is not None:
        payload["job_id"] = job_id
    if shard_key is not None:
        payload["shard_key"] = shard_key
    if record_date is not None:
        payload["record_date"] = record_date
    if batch_size is not None:
        payload["batch_size"] = batch_size
    if attempt is not None:
        payload["attempt"] = attempt
    if payload_json:
        payload["payload_json"] = payload_json
    return payload


def log_trace_event(
    *,
    event_name: str,
    source_kind: str,
    component: str,
    status: str,
    summary: str,
    level: str = "INFO",
    trace_id: str | None = None,
    parent_trace_id: str | None = None,
    group_id: str | None = None,
    user_id: str | None = None,
    job_id: str | None = None,
    shard_key: str | None = None,
    record_date: int | None = None,
    batch_size: int | None = None,
    attempt: int | None = None,
    payload_json: dict[str, Any] | None = None,
    persist: bool = True,
) -> str:
    payload = build_trace_payload(
        event_name=event_name,
        source_kind=source_kind,
        component=component,
        status=status,
        summary=summary,
        level=level,
        trace_id=trace_id,
        parent_trace_id=parent_trace_id,
        group_id=group_id,
        user_id=user_id,
        job_id=job_id,
        shard_key=shard_key,
        record_date=record_date,
        batch_size=batch_size,
        attempt=attempt,
        payload_json=payload_json,
    )
    bound_logger = logger.bind(
        **{
            TRACE_PAYLOAD_EXTRA_KEY: payload,
            TRACE_PERSIST_EXTRA_KEY: persist,
            "trace_id": payload["trace_id"],
            "component": component,
            "event_name": event_name,
            "source_kind": source_kind,
            "trace_status": status,
        }
    )
    bound_logger.log(level.upper(), summary)
    return payload["trace_id"]


def _trace_log_sink(message: object) -> None:
    if not _trace_logging_ready:
        return
    record = cast(dict[str, Any], getattr(message, "record"))
    if not record["extra"].get(TRACE_PERSIST_EXTRA_KEY, True):
        return
    payload = record["extra"].get(TRACE_PAYLOAD_EXTRA_KEY)
    if not isinstance(payload, dict):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    writer = _get_trace_writer()
    task = loop.create_task(writer.add(cast(TraceEventLogPayload, payload)))
    _track_trace_task(task)


__all__ = [
    "TRACE_PAYLOAD_EXTRA_KEY",
    "build_trace_payload",
    "configure_logging",
    "flush_trace_logging",
    "log_trace_event",
    "mark_trace_logging_ready",
    "new_trace_id",
    "shutdown_logging",
]
