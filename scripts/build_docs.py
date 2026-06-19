"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache, partial
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import Literal, cast

from src.database.core.consts import Permission
from src.lib.consts import TriggerType
from src.lib.plugin_docs import (
    DemoCollectionJob,
    DemoCollectionTile,
    DocNode,
    DocsMeta,
    PluginDocBundle,
    audit_demo_layout,
    build_doc_tree,
    collection_demo_filename,
    create_docs_meta,
    load_doc_node,
    load_plugin_doc_bundle,
    render_collection_png,
    render_demo_png,
    resolve_help_entry_shape,
    should_prefer_collection_demo,
)
from src.lib.plugin_docs import (
    DemoCollectionRenderer as _DemoCollectionRenderer,
)
from src.lib.utils.common import get_current_time

DemoCollectionRenderer = _DemoCollectionRenderer

DOCS_ROOTS = (
    ROOT / "src" / "plugins",
    ROOT / "src" / "hooks",
)


@dataclass(slots=True, frozen=True)
class DemoRenderJob:
    bundle: PluginDocBundle
    feature_index: int
    output: Path


@dataclass(slots=True, frozen=True)
class DocBuildContext:
    path: Path
    bundle: PluginDocBundle
    docs_meta: DocsMeta
    node: DocNode


@lru_cache(maxsize=1)
def iter_readmes() -> list[Path]:
    readmes: list[Path] = []
    for root in DOCS_ROOTS:
        readmes.extend(sorted(root.glob("**/README.MD")))
    return [path for path in readmes if "/docs/" in path.as_posix()]


def _write_line(message: str) -> None:
    sys.stdout.write(f"{message}\n")


@lru_cache(maxsize=256)
def load_bundle(path: Path) -> PluginDocBundle:
    return load_plugin_doc_bundle(
        source=path,
        default_name=path.parent.name,
        default_description="",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )


@lru_cache(maxsize=256)
def _declared_docs_meta_for_path(path: Path) -> DocsMeta | None:
    target = path.resolve()
    try:
        rel = target.relative_to(ROOT / "src")
    except ValueError:
        rel = None
    if rel is not None and rel.parts[:3] == ("hooks", "docs", "processor"):
        return create_docs_meta(
            visible=True,
            category="system",
            order=10,
            source=path,
            slug="hook.processor",
            kind="overview",
        )
    if rel is not None and rel.parts[:3] == ("hooks", "docs", "plugin"):
        return create_docs_meta(
            visible=True,
            category="system",
            order=20,
            source=path,
            slug="hook.plugin",
            kind="overview",
        )
    candidates: list[Path] = []
    if rel is not None and len(rel.parts) >= 3:
        namespace = rel.parts[0]
        if namespace == "plugins":
            plugin_root = ROOT / "src" / "plugins" / rel.parts[1]
            candidates.extend(sorted(plugin_root.glob("*.py")))
        elif namespace == "hooks":
            hook_root = ROOT / "src" / "hooks"
            candidates.extend(sorted(hook_root.glob("*.py")))

    for module_path in candidates:
        try:
            raw = module_path.read_text(encoding="utf-8")
        except OSError:
            continue
        source_vars: dict[str, Path] = {}
        for match in re.finditer(
            r"([A-Z_]+DOCS_SOURCE)\s*=\s*(?P<expr>[^\n]+)",
            raw,
        ):
            var_name = match.group(1)
            expr = match.group("expr")
            parts = re.findall(r'"([^"]+)"', expr)
            base = module_path.parent
            if ".parent.parent" in expr:
                base = base.parent
            source_vars[var_name] = base.joinpath(*parts).resolve()

        for block in _extract_create_docs_meta_blocks(raw):
            source_match = re.search(r"source\s*=\s*([A-Z_]*DOCS_SOURCE)", block)
            if not source_match:
                continue
            source_var = source_match.group(1)
            if source_vars.get(source_var) != target:
                continue
            slug_match = re.search(r'slug\s*=\s*"([^"]+)"', block)
            if not slug_match:
                return None
            parent_match = re.search(r'parent_slug\s*=\s*"([^"]+)"', block)
            category_match = re.search(r'category\s*=\s*"([^"]+)"', block)
            order_match = re.search(r"order\s*=\s*(\d+)", block)
            kind_match = re.search(r'kind\s*=\s*"([^"]+)"', block)
            visible_match = re.search(r"visible\s*=\s*(True|False)", block)
            hidden_match = re.search(r"hidden\s*=\s*(True|False)", block)
            internal_match = re.search(r"internal\s*=\s*(True|False)", block)
            raw_kind = kind_match.group(1) if kind_match else "plugin"
            kind = cast(
                Literal["plugin", "overview", "internal", "static"],
                raw_kind
                if raw_kind in {"plugin", "overview", "internal", "static"}
                else "plugin",
            )
            return create_docs_meta(
                visible=(visible_match.group(1) == "True") if visible_match else True,
                hidden=(hidden_match.group(1) == "True") if hidden_match else False,
                internal=(
                    internal_match.group(1) == "True" if internal_match else False
                ),
                category=category_match.group(1) if category_match else "general",
                order=int(order_match.group(1)) if order_match else 100,
                source=path,
                slug=slug_match.group(1),
                parent_slug=parent_match.group(1) if parent_match else None,
                kind=kind,
            )
    return None


def _extract_create_docs_meta_blocks(raw: str) -> list[str]:
    blocks: list[str] = []
    needle = "create_docs_meta("
    cursor = 0
    while True:
        start = raw.find(needle, cursor)
        if start < 0:
            break
        index = start + len(needle)
        depth = 1
        while index < len(raw) and depth > 0:
            char = raw[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth == 0:
            blocks.append(raw[start + len(needle) : index - 1])
        cursor = index
    return blocks


def default_worker_count() -> int:
    return max(1, os.cpu_count() or 1)


@lru_cache(maxsize=256)
def load_doc_context(path: Path) -> DocBuildContext:
    bundle = load_bundle(path)
    docs_meta = _declared_docs_meta_for_path(path) or create_docs_meta(
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
    return DocBuildContext(path=path, bundle=bundle, docs_meta=docs_meta, node=node)


def _progress(stage: str, index: int, total: int, path: Path) -> None:
    _write_line(f"[{stage} {index}/{total}] {path.relative_to(ROOT)}")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def collect_demo_jobs() -> tuple[int, tuple[DemoRenderJob, ...]]:
    readmes = iter_readmes()
    jobs: list[DemoRenderJob] = []
    for path in readmes:
        bundle = load_doc_context(path).bundle
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
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
    return len(readmes), tuple(jobs)


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
    readmes = iter_readmes()
    jobs: list[DemoCollectionJob] = []
    for path in readmes:
        context = load_doc_context(path)
        bundle = context.bundle
        demos_dir = path.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        if not bundle.index:
            continue
        node = context.node
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
    return len(readmes), tuple(jobs)


def render_collection_job(job: DemoCollectionJob) -> tuple[Path, bytes]:
    return job.output, render_collection_png(job)


def compose(*, workers: int | None = None, columns: int = 2) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_collection_jobs(columns=columns)
    _write_line(
        f"compose: discovered {total_files} README files, {len(jobs)} collection jobs"
    )
    if worker_count == 1 or len(jobs) <= 1:
        for index, job in enumerate(jobs, start=1):
            _progress("compose", index, len(jobs), job.output)
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
    return 0
    return validate()


def generate(*, workers: int | None = None) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    total_files, jobs = collect_demo_jobs()
    build_time = datetime.fromtimestamp(get_current_time()).replace(microsecond=0)
    render_job = partial(render_demo_job, generated_at=build_time)
    _write_line(
        f"generate: discovered {total_files} README files, {len(jobs)} demo jobs"
    )
    if worker_count == 1 or len(jobs) <= 1:
        for index, job in enumerate(jobs, start=1):
            _progress("generate", index, len(jobs), job.output)
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
    readmes = iter_readmes()
    _write_line(f"validate: checking {len(readmes)} README files")
    for index, path in enumerate(readmes, start=1):
        _progress("validate", index, len(readmes), path)
        try:
            context = load_doc_context(path)
        except Exception as exc:
            errors.append(
                f"{path.relative_to(ROOT)}: parse failed: {type(exc).__name__}: {exc}"
            )
            continue

        bundle = context.bundle
        node = context.node
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
        if node.kind != "static" and not bundle.index:
            errors.append(f"{path.relative_to(ROOT)}: missing feature entries")

        if node.kind == "static":
            continue

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

    _write_line(f"validated {len(readmes)} README files")
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
