"""pyright 类型债基线校验（ratchet / 只允许下降）。

诊断数固化成基线：允许减少、拦下增加，strict 收敛按批次推进。
用法：uv run python scripts/check_pyright_baseline.py [--update]
基线文件：pyright-baseline.json（按 file::rule 记录计数，忽略行号漂移）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "pyright-baseline.json"


def _norm(path: str) -> str:
    """把 pyright 返回的绝对路径归一成仓库相对 POSIX 路径。"""
    return Path(path).resolve().relative_to(ROOT).as_posix()


def collect() -> dict[str, int]:
    """运行 pyright，返回 ``{相对路径::规则: 计数}``。"""
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    counts: dict[str, int] = {}
    for item in payload["generalDiagnostics"]:
        key = f"{_norm(item['file'])}::{item['rule']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _load_baseline() -> dict[str, int]:
    raw: dict[str, Any] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in raw["counts"].items()}


def _write_baseline(counts: dict[str, int]) -> None:
    BASELINE_PATH.write_text(
        json.dumps(
            {"total": sum(counts.values()), "counts": dict(sorted(counts.items()))},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="pyright 类型债基线校验")
    parser.add_argument(
        "--update",
        action="store_true",
        help="把当前诊断写回基线（仅在确认收敛后使用）",
    )
    args = parser.parse_args(argv[1:])

    current = collect()
    if args.update:
        _write_baseline(current)
        sys.stdout.write(f"已刷新基线：{sum(current.values())} 条诊断\n")
        return 0

    if not BASELINE_PATH.exists():
        sys.stdout.write(f"缺少基线文件 {BASELINE_PATH.name}；先执行 --update 生成。\n")
        return 1

    baseline = _load_baseline()
    regressions = {
        key: (baseline.get(key, 0), count)
        for key, count in current.items()
        if count > baseline.get(key, 0)
    }
    improvements = {
        key: (baseline[key], current.get(key, 0))
        for key in baseline
        if current.get(key, 0) < baseline[key]
    }

    if regressions:
        for key, (was, now) in sorted(regressions.items()):
            sys.stdout.write(f"类型债新增：{key}  {was} -> {now}\n")
        sys.stdout.write(
            f"\n共 {len(regressions)} 处类型债增加。"
            "请修掉新增诊断；确属合理放宽时用 --update 刷新基线。\n"
        )
        return 1

    sys.stdout.write(
        f"类型债未增加：当前 {sum(current.values())}，基线 {sum(baseline.values())}"
    )
    if improvements:
        sys.stdout.write(f"，已减少 {len(improvements)} 处（可 --update 收敛基线）")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
