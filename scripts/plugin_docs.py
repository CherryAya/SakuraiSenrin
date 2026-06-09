"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    PluginDocBundle,
    audit_demo_layout,
    load_plugin_doc_bundle,
    render_demo_png,
)

DOCS_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)


@dataclass(slots=True, frozen=True)
class DemoRenderJob:
    bundle: PluginDocBundle
    feature_index: int
    output: Path


def iter_readmes() -> list[Path]:
    readmes: list[Path] = []
    for root in DOCS_ROOTS:
        readmes.extend(sorted(root.glob("**/README.MD")))
    return [path for path in readmes if "/docs/" in path.as_posix()]


def _write_line(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def load_bundle(path: Path) -> PluginDocBundle:
    return load_plugin_doc_bundle(
        source=path,
        default_name=path.parent.name,
        default_description="",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )


def default_worker_count() -> int:
    return max(1, os.cpu_count() or 1)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def collect_demo_jobs() -> tuple[int, tuple[DemoRenderJob, ...]]:
    total_files = 0
    jobs: list[DemoRenderJob] = []
    for path in iter_readmes():
        bundle = load_bundle(path)
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        total_files += 1
        for feature_index, feature in enumerate(bundle.index):
            if not feature.demo_turns or not feature.demo_filename:
                continue
            jobs.append(
                DemoRenderJob(
                    bundle=bundle,
                    feature_index=feature_index,
                    output=demos_dir / feature.demo_filename,
                )
            )
    return total_files, tuple(jobs)


def render_demo_job(job: DemoRenderJob) -> tuple[Path, bytes]:
    feature = job.bundle.index[job.feature_index]
    return job.output, render_demo_png(job.bundle, feature)


def write_demo_result(result: tuple[Path, bytes]) -> Path:
    output, demo_bytes = result
    output.write_bytes(demo_bytes)
    return output


def generate(*, workers: int | None = None) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_demo_jobs()
    if worker_count == 1 or len(jobs) <= 1:
        for job in jobs:
            output = write_demo_result(render_demo_job(job))
            _write_line(f"generated {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(render_demo_job, jobs):
                output = write_demo_result(result)
                _write_line(f"generated {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, generated {len(jobs)} demo images"
    )
    return 0


def validate() -> int:
    errors: list[str] = []
    for path in iter_readmes():
        try:
            bundle = load_bundle(path)
        except Exception as exc:
            errors.append(
                f"{path.relative_to(ROOT)}: parse failed: {type(exc).__name__}: {exc}"
            )
            continue

        if not bundle.summary.strip():
            errors.append(f"{path.relative_to(ROOT)}: missing 概览 section content")
        if not bundle.index:
            errors.append(f"{path.relative_to(ROOT)}: missing feature entries")

        for feature in bundle.index:
            if not feature.overview.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 说明 section"
                )
            if not feature.preconditions.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 前置条件 section"
                )
            if not feature.failures.strip():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing 失败情况 section"
                )
            if not feature.demo_turns:
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    "missing demo turns"
                )
            demo_path = path.parent / "demos" / feature.demo_filename
            if not demo_path.exists():
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    f"missing demo file {feature.demo_filename}"
                )
            layout_errors = audit_demo_layout(bundle, feature)
            if layout_errors:
                errors.extend(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} {message}"
                    for message in layout_errors
                )

    if errors:
        _write_line("\n".join(errors))
        return 1

    _write_line(f"validated {len(iter_readmes())} README files")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plugin docs helper")
    subparsers = parser.add_subparsers(dest="action", required=True)
    generate_parser = subparsers.add_parser(
        "generate",
        help="generate demo PNG assets from README specs",
    )
    generate_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel render workers; use 1 for serial rendering (default: %(default)s)"
        ),
    )
    subparsers.add_parser("validate", help="validate README structure and demo assets")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    match args.action:
        case "generate":
            return generate(workers=args.workers)
        case "validate":
            return validate()
        case _:
            parser.error("unknown action")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
