"""列出 BatchWriter 运行态与死信队列。

对标 ES 的 _cluster/health：覆盖落盘量、失败量、死信批次与连续失败次数，
让持续写入失败可见。退出码：有降级 writer 时返回 1，可作监控探针。
用法：uv run python scripts/writer_health.py [--json]
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import ModuleType

import nonebot

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lib.types import JsonValue


def _stub_plugin_packages() -> None:
    """绕开插件 bootstrap 的 NoneBot 副作用。"""
    project_root = Path(__file__).resolve().parents[1]
    for name, path in (
        ("src.plugins.water", project_root / "src" / "plugins" / "water"),
        ("src.plugins.wordbank", project_root / "src" / "plugins" / "wordbank"),
    ):
        if name not in sys.modules:
            module = ModuleType(name)
            module.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[name] = module


async def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect buffered writer health")
    parser.add_argument("--json", action="store_true", help="emit raw json")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only report when degraded",
    )
    args = parser.parse_args()
    nonebot.init()
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
