"""Inspect sharedDB shard lifecycle state across all registered databases.

对标 Elasticsearch 的 _cat/indices 与 _cluster/health：一次性列出所有分片库
的分片状态、是否在线、是否已归档、体积与最近访问时间。

用法:
    uv run python scripts/db_shard_status.py
    uv run python scripts/db_shard_status.py --json
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lib.db.alias import AliasStore
from src.lib.db.connectors import SegmentStore
from src.lib.db.ops import BaseOps
from src.lib.types import JsonValue


class _NoopOps(BaseOps[DeclarativeBase]):
    """运维脚本只读健康快照，不需要真实 ops。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session


class _ShardHealth(TypedDict):
    """``AliasStore.shard_health()`` 的行结构。"""

    shard: str
    state: str
    is_active: bool
    online: bool
    archived: bool
    size_bytes: int
    last_access_at: int
    archived_at: JsonValue


class _ShardRow(TypedDict):
    shard: str
    state: str
    active: bool
    online: bool
    archived: bool
    size_mb: float
    last_access_at: int
    archived_at: JsonValue


class _StorePayload(TypedDict):
    namespace: str
    prefix: str
    tz: str
    active_window_months: int
    retention_months: int
    shards: list[_ShardRow]
    total_size_mb: float


def _load_stores() -> dict[str, AliasStore[_NoopOps]]:
    """加载所有已注册的分片库实例（含 StateStore 之外的分片库）。"""
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # 避免触发插件 bootstrap 的 NoneBot 副作用
    for name, path in (
        ("src.plugins.water", project_root / "src" / "plugins" / "water"),
        ("src.plugins.wordbank", project_root / "src" / "plugins" / "wordbank"),
    ):
        if name not in sys.modules:
            module = type(sys)(name)
            module.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[name] = module

    if "src.config" not in sys.modules:
        config_module = type(sys)("src.config")
        config_module.config = SimpleNamespace(  # type: ignore[attr-defined]
            SUPERUSERS={"1"},
            IGNORED_USERS=set(),
            MAIN_GROUP_ID="10001",
            DEBUG=False,
            DEV_TEST_GROUPS=set(),
            DEV_TEST_USERS=set(),
            DEBUG_SQL_ECHO=False,
        )
        sys.modules["src.config"] = config_module

    from src.lib.backup.registry import (
        ensure_backup_database_registrations_loaded,
        get_registered_backup_databases,
    )

    ensure_backup_database_registrations_loaded()
    stores: dict[str, AliasStore[_NoopOps]] = {}
    for db in get_registered_backup_databases():
        if isinstance(db, SegmentStore):
            alias = AliasStore(db, ops_class=_NoopOps, time_field="created_at")
            stores[f"{db.namespace}/{db.prefix}"] = alias
    return stores


@dataclass(slots=True)
class _Row:
    store: str
    shard: str
    state: str
    active: bool
    online: bool
    archived: bool
    size_mb: float
    last_access_at: int


def _human_ts(ts: int) -> str:
    if not ts:
        return "-"
    import datetime

    return datetime.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect db shard lifecycle")
    parser.add_argument("--json", action="store_true", help="emit raw json")
    args = parser.parse_args()

    stores = _load_stores()
    if not stores:
        print("no segment stores registered")
        return 1

    payload: dict[str, _StorePayload] = {}
    for store_name, store in stores.items():
        rows: list[_ShardRow] = []
        for info in cast("list[_ShardHealth]", store.shard_health()):
            rows.append(
                {
                    "shard": info["shard"],
                    "state": info["state"],
                    "active": info["is_active"],
                    "online": info["online"],
                    "archived": info["archived"],
                    "size_mb": round(info["size_bytes"] / 1024 / 1024, 2),
                    "last_access_at": info["last_access_at"],
                    "archived_at": info["archived_at"],
                },
            )
        payload[store_name] = {
            "namespace": store.namespace,
            "prefix": store.prefix,
            "tz": store.tz,
            "active_window_months": store.active_window_months,
            "retention_months": store.retention_months,
            "shards": rows,
            "total_size_mb": round(sum(r["size_mb"] for r in rows), 2),
        }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for store_name, info in payload.items():
        print(
            f"\n=== {store_name}  "
            f"(tz={info['tz']} active={info['active_window_months']}m "
            f"retention={info['retention_months']}m "
            f"total={info['total_size_mb']}MB) ===",
        )
        print(
            f"{'shard':<10}{'state':<7}{'act':<5}{'online':<8}"
            f"{'archived':<10}{'MB':>10}  {'last_access':<17}",
        )
        for row in info["shards"]:
            print(
                f"{row['shard']:<10}{row['state']:<7}"
                f"{'Y' if row['active'] else '-':<5}"
                f"{'Y' if row['online'] else '-':<8}"
                f"{'Y' if row['archived'] else '-':<10}"
                f"{row['size_mb']:>10}  {_human_ts(row['last_access_at']):<17}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
