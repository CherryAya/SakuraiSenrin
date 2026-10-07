"""Inspect and replay buffered-writer dead letters.

死信此前只存在内存，重启即丢且无法补偿。本脚本提供三件事：

* ``--list``   列出 sys_dead_letter 中未处理的死信（回溯）
* ``--replay`` 用原 flush 回调重放，成功后标记 resolved（补偿）
* ``--prune``  清理已处理的历史记录

用法:
    uv run python scripts/dead_letter.py --list
    uv run python scripts/dead_letter.py --replay --worker _flush_water_logs
    uv run python scripts/dead_letter.py --prune --keep 2000
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stub_plugin_packages() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for name, path in (
        ("src.plugins.water", project_root / "src" / "plugins" / "water"),
        ("src.plugins.wordbank", project_root / "src" / "plugins" / "wordbank"),
    ):
        if name not in sys.modules:
            module = ModuleType(name)
            module.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[name] = module
    if "src.config" not in sys.modules:
        config_module = ModuleType("src.config")
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


def _build_handlers() -> dict:
    """按 worker_name 收集原 flush 回调，供重放使用。"""
    from src.lib.db.batch import BatchWriter
    from src.plugins.water.database import writers as water_writers
    from src.plugins.wordbank.database import writers as wordbank_writers
    from src.services import writers as core_writers

    handlers: dict = {}
    for module in (core_writers, water_writers, wordbank_writers):
        for value in vars(module).values():
            if isinstance(value, BatchWriter):
                handlers[value.worker_name] = value.flush_callback
    return handlers


def _print_records(records: list) -> None:
    if not records:
        print("no unresolved dead letters")
        return
    print(
        f"{'id':>5}{'worker':<34}{'items':>7}{'attempts':>10}{'created_at':<20}error",
    )
    for record in records:
        created = (
            __import__("datetime")
            .datetime.fromtimestamp(record.created_at)
            .strftime("%Y-%m-%d %H:%M")
        )
        print(
            f"{record.id:>5}{record.worker_name[:34]:<34}"
            f"{record.item_count:>7}{record.attempts:>10}"
            f"{created:<20}{record.error[:70]}",
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect/replay dead letters")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list unresolved")
    group.add_argument("--replay", action="store_true", help="replay unresolved")
    group.add_argument("--prune", action="store_true", help="prune resolved")
    parser.add_argument("--worker", default=None, help="filter by worker name")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--keep", type=int, default=2000, help="--prune keep rows")
    parser.add_argument("--json", action="store_true", help="emit raw json")
    args = parser.parse_args()
    _stub_plugin_packages()

    # 独立脚本不走 on_startup，需自行应用建表/补丁
    from src.database.core.tables import CoreBase
    from src.database.instances import core_db
    from src.services.dead_letter_store import (
        DeadLetterOps,
        replay_dead_letters,
    )

    await core_db.init(CoreBase)

    if args.replay:
        replayed = await replay_dead_letters(
            worker_name=args.worker,
            limit=args.limit,
            handlers=_build_handlers(),
        )
        print(f"replayed {replayed} item(s)")
        return 0

    if args.prune:
        async with core_db.session(commit=True) as session:
            pruned = await DeadLetterOps(session).prune_resolved(args.keep)
        print(f"pruned {pruned} resolved row(s)")
        return 0

    async with core_db.session(commit=False) as session:
        records = await DeadLetterOps(session).list_unresolved(
            worker_name=args.worker,
            limit=args.limit,
        )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": record.id,
                        "worker_name": record.worker_name,
                        "item_count": record.item_count,
                        "attempts": record.attempts,
                        "created_at": record.created_at,
                        "error": record.error,
                    }
                    for record in records
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        return 0
    _print_records(records)
    return 1 if records else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
