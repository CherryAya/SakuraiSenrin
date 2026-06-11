"""Migrate legacy PostgreSQL senrin_system records into current core.db."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any, cast

import nonebot

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.instances import core_db
from src.database.system_migration import (
    LegacyPgConfig,
    build_legacy_system_data,
    build_runtime_table_counts,
    initialize_sqlite_runtime,
    load_legacy_pg_config,
    migrate_legacy_system_data,
    write_report,
)
from src.logger import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy senrin_system data")
    parser.add_argument(
        "--old-repo",
        default="../SakuraiSenrin-old",
        help="path to the legacy repository root",
    )
    parser.add_argument("--pg-host", help="legacy PostgreSQL host override")
    parser.add_argument("--pg-port", type=int, help="legacy PostgreSQL port override")
    parser.add_argument("--pg-user", help="legacy PostgreSQL user override")
    parser.add_argument("--pg-password", help="legacy PostgreSQL password override")
    parser.add_argument(
        "--pg-database",
        default="senrin_system",
        help="legacy PostgreSQL database name",
    )
    parser.add_argument(
        "--report",
        default="./data/db/system-migration-report.json",
        help="where to write the migration report JSON",
    )
    parser.add_argument(
        "--no-reset-target",
        action="store_true",
        help="do not clear the target core tables before importing",
    )
    return parser.parse_args()


def build_pg_config(args: argparse.Namespace) -> LegacyPgConfig:
    defaults = load_legacy_pg_config(Path(args.old_repo))
    return LegacyPgConfig(
        host=args.pg_host or defaults.host,
        port=args.pg_port or defaults.port,
        user=args.pg_user or defaults.user,
        password=args.pg_password or defaults.password,
        database=args.pg_database or defaults.database,
    )


def fetch_legacy_system_rows(
    config: LegacyPgConfig,
) -> dict[str, list[dict[str, object]]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=config.database,
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            tables: dict[str, str] = {
                "users": """
                    SELECT
                        user_id,
                        user_name,
                        status,
                        operator_id,
                        effective_time,
                        create_time,
                        update_time,
                        remark
                    FROM user_info
                    ORDER BY id ASC
                """,
                "groups": """
                    SELECT
                        group_id,
                        group_name,
                        status,
                        operator_id,
                        effective_time,
                        create_time,
                        update_time,
                        remark
                    FROM group_info
                    ORDER BY id ASC
                """,
                "invitations": """
                    SELECT
                        id,
                        group_id,
                        group_name,
                        inviter_id,
                        flag,
                        sub_type,
                        status,
                        operator_id,
                        create_time,
                        update_time
                    FROM invitation_info
                    ORDER BY id ASC
                """,
                "invitation_messages": """
                    SELECT
                        report_message_id,
                        invitation_info_id
                    FROM invitation_report_message
                    ORDER BY id ASC
                """,
                "plugin_infos": """
                    SELECT
                        plugin_raw_name,
                        plugin_metadata_name,
                        plugin_module_name,
                        plugin_description,
                        plugin_usage,
                        trigger_type,
                        plugin_permission
                    FROM plugin_info
                    ORDER BY id ASC
                """,
            }
            rows: dict[str, list[dict[str, object]]] = {}
            for key, sql in tables.items():
                cursor.execute(sql)
                rows[key] = [dict(cast(Any, row)) for row in cursor.fetchall()]
    finally:
        conn.close()
    return rows


async def main() -> None:
    nonebot.init()
    args = parse_args()
    rows = await asyncio.to_thread(fetch_legacy_system_rows, build_pg_config(args))
    data = build_legacy_system_data(**rows)

    await initialize_sqlite_runtime()
    async with core_db.session() as session:
        report = await migrate_legacy_system_data(
            data,
            session,
            reset_target=not args.no_reset_target,
        )
        runtime_counts = await build_runtime_table_counts(session)

    report.inserted_counts.update(runtime_counts)
    report_path = Path(args.report)
    await asyncio.to_thread(write_report, report_path, report)
    logger.success(
        f"system migration completed: report={report_path} "
        f"users={report.inserted_counts['biz_user']} "
        f"groups={report.inserted_counts['biz_group']} "
        f"invitations={report.inserted_counts['biz_invitation']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
