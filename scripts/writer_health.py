"""Inspect buffered writer runtime state and dead-letter queues.

对标 Elasticsearch 的 _cluster/health：列出所有 BatchWriter 的落盘量、失败量、
死信批次与连续失败次数。死信此前只存在内存里，任何持续性写入失败都会静默丢
数据直到进程重启；本脚本让这种情况可见。

退出码：有降级 writer 时返回 1，可直接用于监控探针。

用法:
    uv run python scripts/writer_health.py
    uv run python scripts/writer_health.py --json
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lib.types import JsonValue


def _stub_plugin_packages() -> None:
    """避免触发插件 bootstrap 的 NoneBot 副作用。"""
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


async def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect buffered writer health")
    parser.add_argument("--json", action="store_true", help="emit raw json")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only report when degraded",
    )
    args = parser.parse_args()
    _stub_plugin_packages()

    from src.services.writer_health import (
        build_health_report,
        render_health_table,
        report_writer_health,
    )

    if args.json:
        report = build_health_report()
        payload: dict[str, JsonValue] = {
            "is_degraded": report.is_degraded,
            "degraded_writers": list(report.degraded_writers),
            "dead_letter_batches": report.dead_letter_batches,
            "dead_letter_items": report.dead_letter_items,
            "writers": {name: asdict(health) for name, health in report.detail},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if report.is_degraded else 0

    report = build_health_report()
    if not args.quiet or report.is_degraded:
        print(render_health_table())
    await report_writer_health()
    return 1 if report.is_degraded else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
