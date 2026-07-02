"""Emit the current LongTask migration progress as a machine-readable endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lib.long_task_progress import (
    DEFAULT_ENDPOINT_PATH,
    write_long_task_progress_endpoint,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the LongTask migration progress endpoint",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ENDPOINT_PATH,
        help="path to the JSON endpoint file",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the full JSON payload to stdout",
    )
    parser.add_argument(
        "--fail-on-candidates",
        action="store_true",
        help="exit with code 1 when legacy wait-message candidates still exist",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = write_long_task_progress_endpoint(args.output)
    if args.stdout:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        summary = payload["summary"]
        sys.stdout.write(
            "long-task-progress "
            f"targets={summary['total_targets']} "
            f"complete={summary['complete_targets']} "
            f"partial={summary['partial_targets']} "
            f"legacy_candidates={summary['legacy_wait_candidates']} "
            f"path={args.output}\n"
        )
    if args.fail_on_candidates and payload["summary"]["legacy_wait_candidates"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
