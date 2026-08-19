from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
import sys

import arrow
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.instances import log_db
from src.database.log.ops import TraceEventLogOps
from src.database.log.tables import LogBase, TraceEventLog
from src.lib.db.connectors import ColdPolicy
from src.lib.utils.common import get_current_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query structured trace logs")
    parser.add_argument("--trace-id")
    parser.add_argument("--component")
    parser.add_argument("--source-kind")
    parser.add_argument("--status")
    parser.add_argument("--record-date", type=int)
    parser.add_argument("--job-id")
    parser.add_argument("--shard-key")
    parser.add_argument("--start-month")
    parser.add_argument("--end-month")
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args()


def _resolve_window(args: argparse.Namespace) -> tuple[arrow.Arrow, arrow.Arrow]:
    if args.start_month:
        start = arrow.get(args.start_month, "YYYYMM").floor("month")
    elif args.record_date:
        start = arrow.get(str(args.record_date), "YYYYMMDD").floor("month")
    else:
        start = (
            arrow.get(get_current_time())
            .to("Asia/Shanghai")
            .shift(months=-1)
            .floor("month")
        )

    if args.end_month:
        end = arrow.get(args.end_month, "YYYYMM").ceil("month")
    elif args.record_date:
        end = arrow.get(str(args.record_date), "YYYYMMDD").ceil("month")
    else:
        end = arrow.get(get_current_time()).to("Asia/Shanghai").ceil("month")
    return start, end


async def main() -> None:
    args = parse_args()
    await log_db.init(LogBase)
    start, end = _resolve_window(args)

    async def _query(session: AsyncSession) -> Sequence[TraceEventLog]:
        return await TraceEventLogOps(session).query_trace_events(
            trace_id=args.trace_id,
            component=args.component,
            source_kind=args.source_kind,
            status=args.status,
            record_date=args.record_date,
            job_id=args.job_id,
            shard_key=args.shard_key,
            limit=args.limit,
        )

    shard_rows = await log_db.map_reduce(
        start.datetime,
        end.datetime,
        _query,
        cold_policy=ColdPolicy.HYDRATE,
    )
    rows = [item for result in shard_rows for item in result]
    rows.sort(key=lambda row: row.created_at, reverse=True)
    rendered = [
        {
            "trace_id": row.trace_id,
            "source_kind": row.source_kind,
            "component": row.component,
            "level": row.level,
            "event_name": row.event_name,
            "status": row.status,
            "summary": row.summary,
            "record_date": row.record_date,
            "job_id": row.job_id,
            "shard_key": row.shard_key,
            "batch_size": row.batch_size,
            "attempt": row.attempt,
            "created_at": row.created_at,
            "payload_json": row.payload_json or {},
        }
        for row in rows[: args.limit]
    ]
    sys.stdout.write(f"{json.dumps(rendered, ensure_ascii=False, indent=2)}\n")


if __name__ == "__main__":
    asyncio.run(main())
