"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import partial
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DemoCollectionJob,
    DemoCollectionRenderer,
    DemoCollectionTile,
    DocNode,
    PluginDocBundle,
    audit_demo_layout,
    build_doc_tree,
    collection_demo_filename,
    create_docs_meta,
    load_doc_node,
    load_plugin_doc_bundle,
    render_demo_png,
    render_collection_png,
    resolve_help_entry_shape,
    should_prefer_collection_demo,
)
from src.lib.utils.common import get_current_time

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


def render_demo_job(
    job: DemoRenderJob,
    *,
    generated_at: datetime | None = None,
) -> tuple[Path, bytes]:
    feature = job.bundle.index[job.feature_index]
    return job.output, render_demo_png(job.bundle, feature, generated_at=generated_at)


def write_demo_result(result: tuple[Path, bytes]) -> Path:
    output, demo_bytes = result
    output.write_bytes(demo_bytes)
    return output


def collect_collection_jobs(
    *, columns: int
) -> tuple[int, tuple[DemoCollectionJob, ...]]:
    total_files = 0
    jobs: list[DemoCollectionJob] = []
    for path in iter_readmes():
        bundle = load_bundle(path)
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        total_files += 1
        if not bundle.index:
            continue
        docs_meta = create_docs_meta(
            visible=True,
            category="general",
            order=100,
            source=path,
        )
        node = load_doc_node(
            source=path,
            default_name=bundle.title,
            default_description=bundle.description,
            trigger=TriggerType.COMMAND,
            permission=Permission.NORMAL,
            docs_meta=docs_meta,
        )
        if not should_prefer_collection_demo(node):
            continue
        tiles = tuple(
            DemoCollectionTile(
                index=feature_index + 1,
                title=feature.title,
                slug=feature.slug,
                summary=feature.summary,
                trigger=feature.trigger,
                demo_help=f"#help {bundle.title} {feature.slug}",
            )
            for feature_index, feature in enumerate(bundle.index)
        )
        jobs.append(
            DemoCollectionJob(
                bundle=bundle,
                output=demos_dir / collection_demo_filename(path),
                tiles=tiles,
                columns=columns,
            )
        )
    return total_files, tuple(jobs)


def render_collection_job(job: DemoCollectionJob) -> tuple[Path, bytes]:
    return job.output, render_collection_png(job)


def compose(*, workers: int | None = None, columns: int = 2) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_collection_jobs(columns=columns)
    if worker_count == 1 or len(jobs) <= 1:
        for job in jobs:
            output = write_demo_result(render_collection_job(job))
            _write_line(f"composed {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(render_collection_job, jobs):
                output = write_demo_result(result)
                _write_line(f"composed {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, composed {len(jobs)} collection images"
    )
    return 0


def build(*, workers: int | None = None, columns: int = 2) -> int:
    generated = generate(workers=workers)
    if generated != 0:
        return generated
    composed = compose(
        workers=workers,
        columns=columns,
    )
    if composed != 0:
        return composed
    return validate()


def generate(*, workers: int | None = None) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_demo_jobs()
    build_time = datetime.fromtimestamp(get_current_time()).replace(microsecond=0)
    render_job = partial(render_demo_job, generated_at=build_time)
    if worker_count == 1 or len(jobs) <= 1:
        for job in jobs:
            output = write_demo_result(render_job(job))
            _write_line(f"generated {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(render_job, jobs):
                output = write_demo_result(result)
                _write_line(f"generated {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, generated {len(jobs)} demo images"
    )
    return 0


def validate() -> int:
    errors: list[str] = []
    nodes: list[DocNode] = []
    slugs_seen: dict[str, Path] = {}
    for path in iter_readmes():
        try:
            bundle = load_bundle(path)
        except Exception as exc:
            errors.append(
                f"{path.relative_to(ROOT)}: parse failed: {type(exc).__name__}: {exc}"
            )
            continue

        docs_meta = create_docs_meta(
            visible=True,
            category="general",
            order=100,
            source=path,
        )
        node = load_doc_node(
            source=path,
            default_name=bundle.title,
            default_description=bundle.description,
            trigger=TriggerType.COMMAND,
            permission=Permission.NORMAL,
            docs_meta=docs_meta,
            impression_color=bundle.impression_color,
        )
        nodes.append(node)
        prior = slugs_seen.get(node.slug)
        if prior is not None:
            errors.append(
                f"{path.relative_to(ROOT)}: duplicate doc slug {node.slug} "
                f"(first seen in {prior.relative_to(ROOT)})"
            )
        else:
            slugs_seen[node.slug] = path

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
                continue
            try:
                render_demo_png(bundle, feature)
            except Exception as exc:
                errors.append(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} "
                    f"demo render failed: {type(exc).__name__}: {exc}"
                )
            layout_errors = audit_demo_layout(bundle, feature)
            if layout_errors:
                errors.extend(
                    f"{path.relative_to(ROOT)}: feature {feature.slug} {message}"
                    for message in layout_errors
                )

        if bundle.index:
            node = load_doc_node(
                source=path,
                default_name=bundle.title,
                default_description=bundle.description,
                trigger=TriggerType.COMMAND,
                permission=Permission.NORMAL,
                impression_color=bundle.impression_color,
            )
            if (
                resolve_help_entry_shape(
                    node,
                    actor_permission=Permission.NORMAL,
                )
                != "simple_leaf"
            ):
                tiles = tuple(
                    DemoCollectionTile(
                        index=feature_index + 1,
                        title=feature.title,
                        slug=feature.slug,
                        summary=feature.summary,
                        trigger=feature.trigger,
                        demo_help=f"#help {bundle.title} {feature.slug}",
                    )
                    for feature_index, feature in enumerate(bundle.index)
                )
                try:
                    render_collection_png(
                        DemoCollectionJob(
                            bundle=bundle,
                            output=path.parent
                            / "demos"
                            / collection_demo_filename(path),
                            tiles=tiles,
                            columns=2,
                        )
                    )
                except Exception as exc:
                    errors.append(
                        f"{path.relative_to(ROOT)}: collection demo render failed: "
                        f"{type(exc).__name__}: {exc}"
                    )

    if not errors:
        tree = build_doc_tree(nodes)
        known_slugs = {node.slug for node in tree.nodes}
        for node in tree.nodes:
            if node.parent_slug is not None and node.parent_slug not in known_slugs:
                errors.append(
                    f"{node.source_path.relative_to(ROOT)}: missing parent node "
                    f"{node.parent_slug} for slug {node.slug}"
                )

    if errors:
        _write_line("\n".join(errors))
        return 1

    _write_line(f"validated {len(iter_readmes())} README files")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plugin docs helper")
    subparsers = parser.add_subparsers(dest="action", required=True)
    build_parser = subparsers.add_parser(
        "build",
        help=(
            "generate feature demos, compose collection images, "
            "then validate all docs assets"
        ),
    )
    build_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel workers shared by generate/compose; use 1 for serial execution "
            "(default: %(default)s)"
        ),
    )
    build_parser.add_argument(
        "--columns",
        type=positive_int,
        default=2,
        help="number of columns in each collection image (default: %(default)s)",
    )
    compose_parser = subparsers.add_parser(
        "compose",
        help="compose per-README collection PNG assets from README feature data",
    )
    compose_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel compose workers; use 1 for serial rendering "
            "(default: %(default)s)"
        ),
    )
    compose_parser.add_argument(
        "--columns",
        type=positive_int,
        default=2,
        help="number of columns in each collection image (default: %(default)s)",
    )
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
        case "build":
            return build(
                workers=args.workers,
                columns=args.columns,
            )
        case "compose":
            return compose(
                workers=args.workers,
                columns=args.columns,
            )
        case "generate":
            return generate(workers=args.workers)
        case "validate":
            return validate()
        case _:
            parser.error("unknown action")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
