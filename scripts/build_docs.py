"""Generate and validate plugin documentation demo assets."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import argparse
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter

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
    FeatureDoc,
    HelpDashboardSection,
    PluginDocBundle,
    build_doc_tree,
    build_help_home_sections,
    collection_demo_filename,
    create_docs_meta,
    dashboard_signature,
    dashboard_target_key,
    feature_signature,
    feature_target_key,
    guide_signature,
    guide_target_key,
    load_doc_node,
    load_plugin_doc_bundle,
    render_collection_png,
    render_demo_png,
    render_demo_png_with_audit,
    render_feature_deep_dive,
    render_help_dashboard,
    render_plugin_guide,
    render_static_entry,
    resolve_help_entry_shape,
    should_prefer_collection_demo,
    static_signature,
    static_target_key,
)
from src.lib.plugin_docs import (
    DemoCollectionRenderer as _DemoCollectionRenderer,
)
from src.lib.plugin_docs.query import can_view_node, filter_features_by_permission
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


@dataclass(slots=True, frozen=True)
class DeclaredDocContext:
    docs_meta: DocsMeta
    permission: Permission
    module_name: str = ""
    plugin_name: str = ""


type HelpAssetKind = Literal["dashboard", "guide", "static", "feature"]


@dataclass(slots=True, frozen=True)
class HelpAssetRenderPlan:
    kind: HelpAssetKind
    render_key: str
    signature: str
    profile: str
    source_path: Path
    target_key: str
    actor_permission: Permission
    generated_at: datetime
    node: DocNode | None = None
    feature: FeatureDoc | None = None
    sections: tuple[HelpDashboardSection, ...] = ()


@dataclass(slots=True, frozen=True)
class HelpAssetRenderResult:
    plan: HelpAssetRenderPlan
    data: bytes
    elapsed_ms: float


@dataclass(slots=True, frozen=True)
class ProfileRecord:
    stage: str
    label: str
    elapsed_ms: float


@dataclass(slots=True, frozen=True)
class ValidateReadmeResult:
    path: Path
    node: DocNode | None
    errors: tuple[str, ...]
    elapsed_ms: float


@dataclass(slots=True)
class Profiler:
    enabled: bool = False
    top_n: int = 10
    records: list[ProfileRecord] = field(default_factory=list)

    def record(self, stage: str, label: str, elapsed_ms: float) -> None:
        if not self.enabled:
            return
        self.records.append(
            ProfileRecord(stage=stage, label=label, elapsed_ms=elapsed_ms)
        )
        _write_line(f"[profile:{stage}] {elapsed_ms:.1f} ms {label}")

    def report_stage(self, stage: str) -> None:
        if not self.enabled:
            return
        stage_records = [record for record in self.records if record.stage == stage]
        if not stage_records:
            return
        total_ms = sum(record.elapsed_ms for record in stage_records)
        avg_ms = total_ms / len(stage_records)
        _write_line(
            f"[profile:{stage}:summary] count={len(stage_records)} "
            f"total={total_ms:.1f} ms avg={avg_ms:.1f} ms"
        )
        for record in sorted(
            stage_records,
            key=lambda item: item.elapsed_ms,
            reverse=True,
        )[: self.top_n]:
            _write_line(
                f"[profile:{stage}:top] {record.elapsed_ms:.1f} ms {record.label}"
            )


PERMISSION_VARIANTS: tuple[tuple[str, Permission], ...] = (
    ("normal", Permission.NORMAL),
    ("group_admin", Permission.GROUP_ADMIN),
    ("group_owner", Permission.GROUP_OWNER),
    ("superuser", Permission.SUPERUSER),
)


@lru_cache(maxsize=1)
def iter_readmes() -> list[Path]:
    readmes: list[Path] = []
    for root in DOCS_ROOTS:
        readmes.extend(sorted(root.glob("**/README.MD")))
    return [path for path in readmes if "/docs/" in path.as_posix()]


def _write_line(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _relative_display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _timed_call[T](
    func: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> tuple[T, float]:
    started_at = perf_counter()
    result = func(*args, **kwargs)
    return result, (perf_counter() - started_at) * 1000


def _relative_root(path: Path) -> str:
    return str(path.relative_to(ROOT))


@lru_cache(maxsize=256)
def load_bundle(path: Path) -> PluginDocBundle:
    return load_plugin_doc_bundle(
        source=path,
        default_name=path.parent.name,
        default_description="",
        trigger=TriggerType.COMMAND,
        permission=Permission.NORMAL,
    )


def _coerce_permission_literal(raw: str) -> Permission:
    normalized = raw.strip()
    if not normalized:
        return Permission.NORMAL
    try:
        return Permission[normalized]
    except KeyError:
        pass
    try:
        return Permission(int(normalized))
    except ValueError:
        return Permission.NORMAL


def _module_name_for_path(module_path: Path) -> str:
    try:
        rel = module_path.resolve().relative_to(ROOT / "src")
    except ValueError:
        return ""
    parts = list(rel.parts)
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = module_path.stem
    return f"src.{'.'.join(parts)}" if parts else ""


def _plugin_name_for_path(module_path: Path) -> str:
    module_name = _module_name_for_path(module_path)
    if not module_name:
        return ""
    parts = module_name.split(".")
    if len(parts) >= 3 and parts[1] in {"plugins", "hooks"}:
        return parts[2]
    return ""


def _permission_for_module_raw(raw: str) -> Permission:
    patterns = (
        r'"permission"\s*:\s*Permission\.([A-Z_]+)',
        r'"permission"\s*:\s*"([A-Z_]+)"',
        r'"permission"\s*:\s*(\d+)',
    )
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match is None:
            continue
        return _coerce_permission_literal(match.group(1))
    return Permission.NORMAL


@lru_cache(maxsize=256)
def _declared_doc_context_for_path(path: Path) -> DeclaredDocContext | None:
    target = path.resolve()
    try:
        rel = target.relative_to(ROOT / "src")
    except ValueError:
        rel = None
    if rel is not None and rel.parts[:3] == ("hooks", "docs", "processor"):
        docs_meta = create_docs_meta(
            visible=True,
            category="system",
            order=10,
            source=path,
            slug="hook.processor",
            kind="overview",
        )
        return DeclaredDocContext(
            docs_meta=docs_meta,
            permission=Permission.NORMAL,
            module_name="src.hooks.processor",
            plugin_name="processor",
        )
    if rel is not None and rel.parts[:3] == ("hooks", "docs", "plugin"):
        docs_meta = create_docs_meta(
            visible=True,
            category="system",
            order=20,
            source=path,
            slug="hook.plugin",
            kind="overview",
        )
        return DeclaredDocContext(
            docs_meta=docs_meta,
            permission=Permission.NORMAL,
            module_name="src.hooks.plugin",
            plugin_name="plugin",
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
            r"([A-Z_]*DOCS_SOURCE)\s*=\s*(?P<expr>[^\n]+)",
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
            docs_meta = create_docs_meta(
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
            return DeclaredDocContext(
                docs_meta=docs_meta,
                permission=_permission_for_module_raw(raw),
                module_name=_module_name_for_path(module_path),
                plugin_name=_plugin_name_for_path(module_path),
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
    declared = _declared_doc_context_for_path(path)
    if declared is None:
        docs_meta = create_docs_meta(
            visible=True,
            category="general",
            order=100,
            source=path,
        )
        permission = Permission.NORMAL
        module_name = ""
        plugin_name = ""
    else:
        docs_meta = declared.docs_meta
        permission = declared.permission
        module_name = declared.module_name
        plugin_name = declared.plugin_name
    node = load_doc_node(
        source=path,
        default_name=bundle.title,
        default_description=bundle.description,
        trigger=TriggerType.COMMAND,
        permission=permission,
        docs_meta=docs_meta,
        impression_color=bundle.impression_color,
        module_name=module_name,
        plugin_name=plugin_name,
    )
    return DocBuildContext(path=path, bundle=bundle, docs_meta=docs_meta, node=node)


def _progress(stage: str, index: int, total: int, path: Path) -> None:
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        relative = path
    _write_line(f"[{stage} {index}/{total}] {relative}")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _add_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        action="store_true",
        help="emit detailed stage timing and slow-task summaries",
    )
    parser.add_argument(
        "--profile-top",
        type=positive_int,
        default=10,
        help=(
            "number of slowest timing records to print per stage (default: %(default)s)"
        ),
    )


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
    rendered = render_demo_png(job.bundle, feature, generated_at=generated_at)
    suffix = _encoded_image_suffix(rendered)
    output = (
        job.output
        if job.output.suffix.lower() == suffix
        else job.output.with_suffix(suffix)
    )
    return output, rendered


def _timed_render_demo_job(
    job: DemoRenderJob,
    *,
    generated_at: datetime | None = None,
) -> tuple[tuple[Path, bytes], float]:
    return _timed_call(render_demo_job, job, generated_at=generated_at)


def write_demo_result(result: tuple[Path, bytes]) -> Path:
    output, demo_bytes = result
    output.write_bytes(demo_bytes)
    return output


def _encoded_image_suffix(data: bytes) -> str:
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    return ".png"


def _hash_filename(base_name: str, signature: str) -> str:
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix or ".png"
    return f"{stem}--{signature}{suffix}"


def _with_suffix(base_name: str, suffix: str) -> str:
    return str(Path(base_name).with_suffix(suffix))


def _output_static_asset(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _render_help_asset_plan(plan: HelpAssetRenderPlan) -> HelpAssetRenderResult:
    started_at = perf_counter()
    if plan.kind == "dashboard":
        data = render_help_dashboard(
            plan.sections,
            locale="zh-CN",
            generated_at=plan.generated_at,
            actor_permission=plan.actor_permission,
            prefer_static=False,
            source_path=plan.source_path,
        )
    elif plan.kind == "guide":
        assert plan.node is not None
        data = render_plugin_guide(
            plan.node,
            actor_permission=plan.actor_permission,
            locale="zh-CN",
            generated_at=plan.generated_at,
            prefer_static=False,
        )
    elif plan.kind == "static":
        assert plan.node is not None
        data = render_static_entry(
            plan.node,
            actor_permission=plan.actor_permission,
            locale="zh-CN",
            generated_at=plan.generated_at,
            prefer_static=False,
        )
    else:
        assert plan.node is not None
        assert plan.feature is not None
        data = render_feature_deep_dive(
            plan.node,
            plan.feature,
            locale="zh-CN",
            generated_at=plan.generated_at,
            actor_permission=plan.actor_permission,
            prefer_static=False,
        )
    return HelpAssetRenderResult(
        plan=plan,
        data=data,
        elapsed_ms=(perf_counter() - started_at) * 1000,
    )


def _cleanup_generated_assets(
    *,
    context: DocBuildContext,
    target_map: dict[str, dict[str, str]],
) -> None:
    demos_dir = context.path.parent / "demos"
    if not demos_dir.is_dir():
        return
    keep_files = {"manifest.json"}
    keep_files.update(feature.demo_filename for feature in context.bundle.index)
    if should_prefer_collection_demo(
        context.node,
        actor_permission=Permission.NORMAL,
    ):
        keep_files.add(collection_demo_filename(context.path))
    for variants in target_map.values():
        keep_files.update(variants.values())
    for asset in demos_dir.iterdir():
        if not asset.is_file():
            continue
        if asset.name in keep_files:
            continue
        if asset.suffix not in {".png", ".webp", ".json"}:
            continue
        if asset.name == "manifest.json":
            continue
        asset.unlink()


def _all_doc_contexts() -> tuple[DocBuildContext, ...]:
    return tuple(load_doc_context(path) for path in iter_readmes())


def _reset_caches() -> None:
    iter_readmes.cache_clear()
    load_bundle.cache_clear()
    _declared_doc_context_for_path.cache_clear()
    load_doc_context.cache_clear()


def _build_tree(contexts: tuple[DocBuildContext, ...]) -> tuple[DocNode, ...]:
    return tuple(context.node for context in contexts)


def _help_asset_label(plan: HelpAssetRenderPlan) -> str:
    base = f"{plan.kind}:{plan.profile}"
    if plan.kind == "dashboard":
        return f"{base} dashboard:index"
    if plan.node is None:
        return base
    owner = _relative_display(plan.source_path)
    if plan.kind == "feature" and plan.feature is not None:
        return f"{base} {owner}#{plan.feature.slug}"
    return f"{base} {owner}"


def _render_plans(
    plans: list[HelpAssetRenderPlan],
    *,
    workers: int,
    profiler: Profiler,
) -> dict[str, HelpAssetRenderResult]:
    if not plans:
        return {}
    unique_plans = list({plan.render_key: plan for plan in plans}.values())
    results: dict[str, HelpAssetRenderResult] = {}
    if workers <= 1 or len(unique_plans) <= 1:
        for plan in unique_plans:
            result = _render_help_asset_plan(plan)
            profiler.record(
                "help-assets.render",
                _help_asset_label(plan),
                result.elapsed_ms,
            )
            results[plan.render_key] = result
        return results

    with ThreadPoolExecutor(max_workers=min(workers, len(unique_plans))) as executor:
        future_map: dict[Future[HelpAssetRenderResult], HelpAssetRenderPlan] = {
            executor.submit(_render_help_asset_plan, plan): plan
            for plan in unique_plans
        }
        for future in as_completed(future_map):
            plan = future_map[future]
            result = future.result()
            profiler.record(
                "help-assets.render",
                _help_asset_label(plan),
                result.elapsed_ms,
            )
            results[plan.render_key] = result
    return results


def build_help_assets(
    *,
    workers: int | None = None,
    profile: bool = False,
    profile_top: int = 10,
    reset_caches: bool = True,
) -> int:
    if reset_caches:
        _reset_caches()
    worker_count = workers if workers is not None else default_worker_count()
    profiler = Profiler(enabled=profile, top_n=profile_top)
    contexts = _all_doc_contexts()
    nodes = _build_tree(contexts)
    tree = build_doc_tree(nodes)
    build_time = datetime.fromtimestamp(get_current_time()).replace(microsecond=0)
    dashboard_outputs: dict[str, tuple[str, bytes]] = {}
    manifest_by_source: dict[Path, dict[str, dict[str, str]]] = {}
    target_filenames: dict[tuple[Path, str, str], dict[str, str]] = {}
    first_dashboard_signature: str | None = None

    help_context = next(
        (context for context in contexts if context.node.slug == "help"),
        None,
    )
    if help_context is None:
        _write_line("help-assets: help docs not found, skipping")
        return 0

    plans: list[HelpAssetRenderPlan] = []
    for profile_name, permission in PERMISSION_VARIANTS:
        sections = build_help_home_sections(
            nodes,
            locale="zh-CN",
            actor_permission=permission,
        )
        if not sections:
            continue
        signature = dashboard_signature(sections)
        if first_dashboard_signature is None:
            first_dashboard_signature = signature
        plans.append(
            HelpAssetRenderPlan(
                kind="dashboard",
                render_key=f"dashboard:{signature}",
                signature=signature,
                profile=profile_name,
                source_path=help_context.path,
                target_key=dashboard_target_key(),
                actor_permission=permission,
                generated_at=build_time,
                sections=sections,
            )
        )

    for context in contexts:
        source = context.path
        children = tuple(tree.children_of(context.node.slug))
        for profile_name, permission in PERMISSION_VARIANTS:
            features = filter_features_by_permission(context.node.features, permission)
            visible_children = tuple(
                child for child in children if can_view_node(child, permission)
            )
            shape = resolve_help_entry_shape(
                context.node,
                actor_permission=permission,
                children=visible_children,
            )
            if shape == "plugin_guide" or shape == "overview_group":
                signature = guide_signature(
                    context.node,
                    feature_slugs=tuple(feature.slug for feature in features),
                    child_slugs=tuple(child.slug for child in visible_children),
                )
                plans.append(
                    HelpAssetRenderPlan(
                        kind="guide",
                        render_key=f"guide:{signature}",
                        signature=signature,
                        profile=profile_name,
                        source_path=source,
                        target_key=guide_target_key(context.node),
                        actor_permission=permission,
                        generated_at=build_time,
                        node=context.node,
                    )
                )
            elif shape == "static_entry":
                signature = static_signature(context.node)
                plans.append(
                    HelpAssetRenderPlan(
                        kind="static",
                        render_key=f"static:{signature}",
                        signature=signature,
                        profile=profile_name,
                        source_path=source,
                        target_key=static_target_key(context.node),
                        actor_permission=permission,
                        generated_at=build_time,
                        node=context.node,
                    )
                )

            for feature in features:
                signature = feature_signature(context.node, feature)
                plans.append(
                    HelpAssetRenderPlan(
                        kind="feature",
                        render_key=f"feature:{signature}",
                        signature=signature,
                        profile=profile_name,
                        source_path=source,
                        target_key=feature_target_key(context.node, feature),
                        actor_permission=permission,
                        generated_at=build_time,
                        node=context.node,
                        feature=feature,
                    )
                )

    unique_render_count = len({plan.render_key for plan in plans})
    _write_line(
        "help-assets: "
        f"{len(contexts)} contexts, {len(plans)} manifest mappings, "
        f"{unique_render_count} unique renders, "
        f"{min(worker_count, unique_render_count) if unique_render_count else 1} "
        "workers"
    )
    rendered_by_key = _render_plans(
        plans,
        workers=worker_count,
        profiler=profiler,
    )

    for plan in plans:
        result = rendered_by_key[plan.render_key]
        suffix = _encoded_image_suffix(result.data)
        target_map = manifest_by_source.setdefault(plan.source_path, {})
        signature_to_filename = target_filenames.setdefault(
            (plan.source_path, plan.target_key, plan.kind),
            {},
        )
        if plan.kind == "dashboard":
            filename = signature_to_filename.get(plan.signature)
            if filename is None:
                filename = (
                    f"help-index{suffix}"
                    if plan.signature == first_dashboard_signature
                    else _hash_filename(f"help-index{suffix}", plan.signature)
                )
                signature_to_filename[plan.signature] = filename
            dashboard_outputs.setdefault(plan.signature, (filename, result.data))
        elif plan.kind == "guide":
            filename = signature_to_filename.get(plan.signature)
            if filename is None:
                filename = _hash_filename(
                    f"{plan.source_path.stem}-guide{suffix}",
                    plan.signature,
                )
                signature_to_filename[plan.signature] = filename
            path = plan.source_path.parent / "demos" / filename
            _output_static_asset(path, result.data)
        elif plan.kind == "static":
            filename = signature_to_filename.get(plan.signature)
            if filename is None:
                filename = _hash_filename(
                    f"{plan.source_path.stem}-static{suffix}",
                    plan.signature,
                )
                signature_to_filename[plan.signature] = filename
            path = plan.source_path.parent / "demos" / filename
            _output_static_asset(path, result.data)
        else:
            assert plan.feature is not None
            base_filename = _with_suffix(plan.feature.demo_filename, suffix)
            filename = signature_to_filename.get(plan.signature)
            if filename is None:
                filename = (
                    base_filename
                    if not signature_to_filename
                    else _hash_filename(base_filename, plan.signature)
                )
                signature_to_filename[plan.signature] = filename
            path = plan.source_path.parent / "demos" / filename
            _output_static_asset(path, result.data)

        target_map.setdefault(plan.target_key, {})[plan.profile] = filename

    for _, (filename, data) in dashboard_outputs.items():
        _output_static_asset(help_context.path.parent / "demos" / filename, data)
        manifest_by_source.setdefault(help_context.path, {}).setdefault(
            dashboard_target_key(),
            {},
        )

    for context in contexts:
        source = context.path
        demos_dir = source.parent / "demos"
        demos_dir.mkdir(parents=True, exist_ok=True)
        target_map = manifest_by_source.setdefault(source, {})
        manifest_payload = {
            "version": 1,
            "locale": "zh-CN",
            "targets": target_map,
        }
        (demos_dir / "manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _cleanup_generated_assets(context=context, target_map=target_map)
        _write_line(f"help-assets {_relative_display(source)}")
    profiler.report_stage("help-assets.render")
    return 0


def collect_collection_jobs(
    *,
    columns: int,
    reset_caches: bool = True,
) -> tuple[int, tuple[DemoCollectionJob, ...]]:
    if reset_caches:
        _reset_caches()
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
        if not should_prefer_collection_demo(
            node,
            actor_permission=Permission.NORMAL,
        ):
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


def _timed_render_collection_job(
    job: DemoCollectionJob,
) -> tuple[tuple[Path, bytes], float]:
    return _timed_call(render_collection_job, job)


def compose(
    *,
    workers: int | None = None,
    columns: int = 2,
    profile: bool = False,
    profile_top: int = 10,
    reset_caches: bool = True,
) -> int:
    worker_count = workers if workers is not None else default_worker_count()
    profiler = Profiler(enabled=profile, top_n=profile_top)
    total_files, jobs = collect_collection_jobs(
        columns=columns,
        reset_caches=reset_caches,
    )
    _write_line(
        f"compose: discovered {total_files} README files, {len(jobs)} collection jobs"
    )
    if worker_count == 1 or len(jobs) <= 1:
        for index, job in enumerate(jobs, start=1):
            _progress("compose", index, len(jobs), job.output)
            result, render_ms = _timed_render_collection_job(job)
            profiler.record("compose.render", _relative_display(job.output), render_ms)
            output, write_ms = _timed_call(write_demo_result, result)
            profiler.record("compose.write", _relative_display(output), write_ms)
            _write_line(f"composed {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map: dict[
                Future[tuple[tuple[Path, bytes], float]],
                DemoCollectionJob,
            ] = {
                executor.submit(_timed_render_collection_job, job): job for job in jobs
            }
            for future in as_completed(future_map):
                job = future_map[future]
                result, render_ms = future.result()
                profiler.record(
                    "compose.render",
                    _relative_display(job.output),
                    render_ms,
                )
                output, write_ms = _timed_call(write_demo_result, result)
                profiler.record("compose.write", _relative_display(output), write_ms)
                _write_line(f"composed {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, composed {len(jobs)} collection images"
    )
    profiler.report_stage("compose.render")
    profiler.report_stage("compose.write")
    return 0


def build(
    *,
    workers: int | None = None,
    columns: int = 2,
    profile: bool = False,
    profile_top: int = 10,
) -> int:
    phase_profiler = Profiler(enabled=profile, top_n=profile_top)
    _reset_caches()
    generated, generated_ms = _timed_call(
        generate,
        workers=workers,
        profile=profile,
        profile_top=profile_top,
        reset_caches=False,
    )
    phase_profiler.record("build.phase", "generate", generated_ms)
    if generated != 0:
        return generated
    composed, composed_ms = _timed_call(
        compose,
        workers=workers,
        columns=columns,
        profile=profile,
        profile_top=profile_top,
        reset_caches=False,
    )
    phase_profiler.record("build.phase", "compose", composed_ms)
    if composed != 0:
        return composed
    assets, assets_ms = _timed_call(
        build_help_assets,
        workers=workers,
        profile=profile,
        profile_top=profile_top,
        reset_caches=False,
    )
    phase_profiler.record("build.phase", "help-assets", assets_ms)
    if assets != 0:
        return assets
    validated, validate_ms = _timed_call(
        validate,
        workers=workers,
        profile=profile,
        profile_top=profile_top,
        reset_caches=False,
    )
    phase_profiler.record("build.phase", "validate", validate_ms)
    phase_profiler.report_stage("build.phase")
    return validated


def generate(
    *,
    workers: int | None = None,
    profile: bool = False,
    profile_top: int = 10,
    reset_caches: bool = True,
) -> int:
    if reset_caches:
        _reset_caches()
    worker_count = workers if workers is not None else default_worker_count()
    profiler = Profiler(enabled=profile, top_n=profile_top)
    total_files, jobs = collect_demo_jobs()
    build_time = datetime.fromtimestamp(get_current_time()).replace(microsecond=0)
    _write_line(
        f"generate: discovered {total_files} README files, {len(jobs)} demo jobs"
    )
    if worker_count == 1 or len(jobs) <= 1:
        for index, job in enumerate(jobs, start=1):
            _progress("generate", index, len(jobs), job.output)
            result, render_ms = _timed_render_demo_job(job, generated_at=build_time)
            profiler.record("generate.render", _relative_display(job.output), render_ms)
            output, write_ms = _timed_call(write_demo_result, result)
            profiler.record("generate.write", _relative_display(output), write_ms)
            _write_line(f"generated {output.relative_to(ROOT)}")
    else:
        max_workers = min(worker_count, len(jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map: dict[
                Future[tuple[tuple[Path, bytes], float]],
                DemoRenderJob,
            ] = {
                executor.submit(
                    _timed_render_demo_job,
                    job,
                    generated_at=build_time,
                ): job
                for job in jobs
            }
            for future in as_completed(future_map):
                job = future_map[future]
                result, render_ms = future.result()
                profiler.record(
                    "generate.render",
                    _relative_display(job.output),
                    render_ms,
                )
                output, write_ms = _timed_call(write_demo_result, result)
                profiler.record("generate.write", _relative_display(output), write_ms)
                _write_line(f"generated {output.relative_to(ROOT)}")

    _write_line(
        f"processed {total_files} README files, generated {len(jobs)} demo images"
    )
    profiler.report_stage("generate.render")
    profiler.report_stage("generate.write")
    return 0


def _validate_readme(path: Path) -> ValidateReadmeResult:
    started_at = perf_counter()
    errors: list[str] = []
    try:
        context = load_doc_context(path)
    except Exception as exc:
        return ValidateReadmeResult(
            path=path,
            node=None,
            errors=(
                f"{_relative_root(path)}: parse failed: {type(exc).__name__}: {exc}",
            ),
            elapsed_ms=(perf_counter() - started_at) * 1000,
        )

    bundle = context.bundle
    node = context.node

    if not bundle.summary.strip():
        errors.append(f"{_relative_root(path)}: missing 概览 section content")
    if node.kind != "static" and not bundle.index:
        errors.append(f"{_relative_root(path)}: missing feature entries")

    if node.kind != "static":
        for feature in bundle.index:
            if not feature.overview.strip():
                errors.append(
                    f"{_relative_root(path)}: feature {feature.slug} "
                    "missing 说明 section"
                )
            if not feature.preconditions.strip():
                errors.append(
                    f"{_relative_root(path)}: feature {feature.slug} "
                    "missing 前置条件 section"
                )
            if not feature.failures.strip():
                errors.append(
                    f"{_relative_root(path)}: feature {feature.slug} "
                    "missing 失败情况 section"
                )
            if not feature.demo_turns:
                errors.append(
                    f"{_relative_root(path)}: feature {feature.slug} missing demo turns"
                )
                continue
            try:
                _, layout_errors = render_demo_png_with_audit(bundle, feature)
            except Exception as exc:
                errors.append(
                    f"{_relative_root(path)}: feature {feature.slug} "
                    f"demo render failed: {type(exc).__name__}: {exc}"
                )
                continue
            if layout_errors:
                errors.extend(
                    f"{_relative_root(path)}: feature {feature.slug} {message}"
                    for message in layout_errors
                )

        if bundle.index and (
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
                        output=path.parent / "demos" / collection_demo_filename(path),
                        tiles=tiles,
                        columns=2,
                    )
                )
            except Exception as exc:
                errors.append(
                    f"{_relative_root(path)}: collection demo render failed: "
                    f"{type(exc).__name__}: {exc}"
                )

    return ValidateReadmeResult(
        path=path,
        node=node,
        errors=tuple(errors),
        elapsed_ms=(perf_counter() - started_at) * 1000,
    )


def validate(
    *,
    workers: int | None = None,
    profile: bool = False,
    profile_top: int = 10,
    reset_caches: bool = True,
) -> int:
    if reset_caches:
        _reset_caches()
    worker_count = workers if workers is not None else default_worker_count()
    profiler = Profiler(enabled=profile, top_n=profile_top)
    errors: list[str] = []
    nodes: list[DocNode] = []
    slugs_seen: dict[str, Path] = {}
    readmes = iter_readmes()
    _write_line(f"validate: checking {len(readmes)} README files")
    ordered_results: list[ValidateReadmeResult]
    if worker_count <= 1 or len(readmes) <= 1:
        ordered_results = []
        for index, path in enumerate(readmes, start=1):
            _progress("validate", index, len(readmes), path)
            ordered_results.append(_validate_readme(path))
    else:
        ordered_results = [None] * len(readmes)  # type: ignore[list-item]
        with ThreadPoolExecutor(
            max_workers=min(worker_count, len(readmes))
        ) as executor:
            future_map: dict[Future[ValidateReadmeResult], tuple[int, Path]] = {}
            for index, path in enumerate(readmes):
                _progress("validate", index + 1, len(readmes), path)
                future_map[executor.submit(_validate_readme, path)] = (index, path)
            for future in as_completed(future_map):
                index, _ = future_map[future]
                ordered_results[index] = future.result()

    for result in ordered_results:
        profiler.record(
            "validate.readme",
            _relative_display(result.path),
            result.elapsed_ms,
        )
        errors.extend(result.errors)
        if result.node is None:
            continue
        nodes.append(result.node)
        prior = slugs_seen.get(result.node.slug)
        if prior is not None:
            errors.append(
                f"{result.path.relative_to(ROOT)}: duplicate doc slug "
                f"{result.node.slug} "
                f"(first seen in {prior.relative_to(ROOT)})"
            )
        else:
            slugs_seen[result.node.slug] = result.path

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
    profiler.report_stage("validate.readme")
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
    _add_profile_args(build_parser)
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
    _add_profile_args(compose_parser)
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
    _add_profile_args(generate_parser)
    validate_parser = subparsers.add_parser(
        "validate",
        help="validate README structure and demo assets",
    )
    validate_parser.add_argument(
        "-j",
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help=(
            "parallel validation workers; use 1 for serial validation "
            "(default: %(default)s)"
        ),
    )
    _add_profile_args(validate_parser)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    match args.action:
        case "build":
            return build(
                workers=args.workers,
                columns=args.columns,
                profile=args.profile,
                profile_top=args.profile_top,
            )
        case "compose":
            return compose(
                workers=args.workers,
                columns=args.columns,
                profile=args.profile,
                profile_top=args.profile_top,
            )
        case "generate":
            return generate(
                workers=args.workers,
                profile=args.profile,
                profile_top=args.profile_top,
            )
        case "validate":
            return validate(
                workers=args.workers,
                profile=args.profile,
                profile_top=args.profile_top,
            )
        case _:
            parser.error("unknown action")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
