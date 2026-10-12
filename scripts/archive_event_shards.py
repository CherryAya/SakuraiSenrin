"""Operational entrypoint for archiving sharded event databases."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import types
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = pkg


_ensure_pkg("src.plugins.wordbank", ROOT / "src" / "plugins" / "wordbank")
_ensure_pkg("src.plugins.water", ROOT / "src" / "plugins" / "water")

from src.lib.db.connectors import SegmentArchiveReport
from src.logger import logger
from src.plugins.water.database.instances import water_message, water_summary
from src.plugins.wordbank.database.instances import (
    wordbank_log_db,
    wordbank_message_ref_db,
)


class _ArchivableStore(Protocol):
    """``AliasStore`` 的最小归档视图（各分片库 ops 类型互不相同，故用鸭子类型收口）。"""

    @property
    def base_dir(self) -> Path: ...

    @property
    def prefix(self) -> str: ...

    async def run_archiver_task(self) -> "SegmentArchiveReport": ...


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive sharded event databases")
    parser.add_argument(
        "--target",
        choices=("wordbank", "water", "all"),
        default="all",
        help="which database group to archive",
    )
    parser.add_argument(
        "--include-water-summary",
        action="store_true",
        help="include water summary shards when target includes water",
    )
    parser.add_argument(
        "--cleanup-stale-sidecars",
        action="store_true",
        help="remove stale -wal/-shm files for already archived shards",
    )
    return parser.parse_args()


async def archive_targets(
    *,
    target: str,
    include_water_summary: bool = False,
) -> tuple[list[str], list[tuple[str, _ArchivableStore]], list[str]]:
    """归档指定目标，返回 (完成清单, 供清理用的库, 失败详情)。

    归档巡检返回结构化报告：失败分片必须如实带出（job 层此前只看"调用返回"
    就报 completed，与主进程归档 job 的契约不一致）。
    """
    completed: list[str] = []
    stores: list[tuple[str, _ArchivableStore]] = []
    failures: list[str] = []

    async def _run(label: str, store: _ArchivableStore) -> None:
        report = await store.run_archiver_task()
        for failure in report.failures:
            failures.append(
                f"{label}/{failure.shard_key}({failure.outcome.value}): "
                f"{failure.reason}"
            )
        completed.append(label)
        stores.append((label, store))

    if target in {"wordbank", "all"}:
        await _run("wordbank_logs", wordbank_log_db)
        await _run("wordbank_message_ref", wordbank_message_ref_db)
    if target in {"water", "all"}:
        await _run("water_message", water_message)
        if include_water_summary:
            await _run("water_summary", water_summary)
    return completed, stores, failures


def cleanup_stale_sidecars(
    stores: list[tuple[str, _ArchivableStore]],
) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    for label, store in stores:
        count = 0
        for archive_file in sorted(store.base_dir.glob(f"{store.prefix}_*.db.zst")):
            base_name = archive_file.name.removesuffix(".zst")
            db_path = store.base_dir / base_name
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{db_path}{suffix}")
                if sidecar.exists():
                    sidecar.unlink()
                    count += 1
        cleaned[label] = count
    return cleaned


async def main() -> None:
    args = parse_args()
    logger.info(
        "[archive-event-shards] starting "
        f"target={args.target} "
        f"include_water_summary={args.include_water_summary} "
        f"cleanup_stale_sidecars={args.cleanup_stale_sidecars}"
    )
    completed, stores, failures = await archive_targets(
        target=args.target,
        include_water_summary=args.include_water_summary,
    )
    cleaned: dict[str, int] = {}
    if args.cleanup_stale_sidecars:
        cleaned = await asyncio.to_thread(cleanup_stale_sidecars, stores)
    if failures:
        # 失败必须可见：不得再以 completed 成功日志掩盖（运维会据此判断归档
        # 是否生效）。非零退出码供监控探针直接判别。
        logger.error(
            "[archive-event-shards] FAILED "
            + f"archived={','.join(completed or ['none'])} "
            + f"failures={len(failures)}: {'; '.join(failures)}"
            + (f" cleaned={cleaned}" if cleaned else "")
        )
        raise SystemExit(1)
    logger.success(
        "[archive-event-shards] completed "
        + ",".join(completed or ["none"])
        + (f" cleaned={cleaned}" if cleaned else "")
    )


if __name__ == "__main__":
    asyncio.run(main())
