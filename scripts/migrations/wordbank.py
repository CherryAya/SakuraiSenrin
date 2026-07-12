"""Legacy wordbank migration helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
import shutil

from src.database.consts import WritePolicy
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.database.instances import wordbank_main_db
from src.plugins.wordbank.database.repo import WordbankRepository
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    shape_from_event,
    shape_from_text,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import SCOPE_PRIORITY

from .wordbank_legacy_source import (
    _default_legacy_image_mapping_path,
    _default_legacy_image_root,
    build_legacy_image_catalog,
    fetch_legacy_addition_log_rows,
    fetch_legacy_message_approval_rows,
    fetch_legacy_response_log_rows,
    fetch_legacy_response_rows,
    fetch_legacy_trigger_log_rows,
    load_legacy_pg_config,
    parse_legacy_env_file,
)
from .wordbank_rules import (
    MigrationError,
    _coerce_int,
    _optional_coerce_int,
    _resolve_legacy_call_window_seconds,
    extract_failure_details_from_categorized_report,
    infer_report_response_available,
    load_legacy_json,
    message_ref_shard_key,
    normalize_legacy_message_text_preserving_newlines,
    normalize_legacy_probability,
    normalize_legacy_rules,
    normalize_legacy_scope,
    normalize_legacy_state,
    normalize_legacy_timestamp,
    rebuild_legacy_row_from_failure_detail,
    rebuild_legacy_rows_from_failure_details,
    shape_from_legacy_extra_info,
    validate_legacy_image_bytes,
)
from .wordbank_types import (
    LegacyImageCatalog,
    LegacyImportedLogTarget,
    LegacyImportTarget,
    LegacyMigrationProgressCallback,
    LegacyPgConfig,
    WordbankMigrationReport,
)


async def legacy_message_to_shape(
    payload: object,
    *,
    extra_info: object = None,
    image_catalog: LegacyImageCatalog,
    media_service: WordbankMediaService,
    report: WordbankMigrationReport | None = None,
    preserve_text_newlines: bool = False,
) -> MessageShape:
    from src.plugins.wordbank.message_model import normalize_message_text

    event_shape = shape_from_legacy_extra_info(extra_info)
    if event_shape is not None:
        if isinstance(event_shape, MessageShape):
            return event_shape
        if isinstance(event_shape, str):
            return shape_from_event(event_shape)
        raise MigrationError("legacy extra_info produced unsupported message shape")

    segments = load_legacy_json(payload)
    if not isinstance(segments, list):
        raise MigrationError("legacy message payload must be a list")

    atoms: list[MessageAtom] = []
    for item in segments:
        if not isinstance(item, Mapping):
            continue
        segment_type = str(item.get("type", "") or "").strip().lower()
        if segment_type == "text":
            raw_text = str(item.get("text", "") or "")
            if preserve_text_newlines:
                text_value = normalize_legacy_message_text_preserving_newlines(raw_text)
            else:
                text_value = normalize_message_text(
                    raw_text,
                    preserve_blank_text=True,
                )
            if text_value:
                atoms.append(MessageAtom(kind="text", text=text_value))
            continue
        if segment_type == "image":
            file_name = str(item.get("file", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            image_path, resolution_source = image_catalog.resolve_with_source(
                file_name,
                url=url,
            )
            if image_path is None:
                raise MigrationError(f"image file not found: {file_name or url}")
            data = await asyncio.to_thread(image_path.read_bytes)
            source_name = file_name or url or image_path.name
            validate_legacy_image_bytes(data, source=source_name)
            image = await media_service.ingest_image_bytes(data)
            if report is not None:
                report.image_counts[image_path.suffix.lower()] += 1
                report.image_resolution_counts[resolution_source] += 1
            atoms.append(
                MessageAtom(kind="image", canonical_image_id=image.canonical_id)
            )
            continue
        if segment_type == "at":
            target_id = str(item.get("qq", "") or "").strip()
            if target_id:
                atoms.append(MessageAtom(kind="at", target_id=target_id))
            continue
        if segment_type == "face":
            face_id = str(item.get("id", "") or "").strip()
            atoms.append(MessageAtom(kind="text", text=f"[face:{face_id}]"))
            continue

    return MessageShape(tuple(atoms))


def _emit_progress(
    callback: LegacyMigrationProgressCallback | None,
    *,
    phase: str,
    current: int,
    total: int,
    detail: Mapping[str, object],
) -> None:
    if callback is None:
        return
    callback(phase, current, total, detail)


def _emit_progress_if_needed(
    callback: LegacyMigrationProgressCallback | None,
    *,
    phase: str,
    current: int,
    total: int,
    every: int,
    detail: Mapping[str, object],
) -> None:
    if callback is None:
        return
    if total <= 0:
        callback(phase, current, total, detail)
        return
    step = max(1, every)
    if current == 1 or current == total or current % step == 0:
        callback(phase, current, total, detail)


async def _recreate_wordbank_target_namespace() -> None:
    namespace_dir = wordbank_main_db.base_dir
    if namespace_dir.exists():
        await asyncio.to_thread(shutil.rmtree, namespace_dir)
    await asyncio.to_thread(namespace_dir.mkdir, parents=True, exist_ok=True)


async def migrate_legacy_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    media_service: WordbankMediaService,
    image_catalog: LegacyImageCatalog,
    response_log_rows: Sequence[Mapping[str, object]] = (),
    trigger_log_rows: Sequence[Mapping[str, object]] = (),
    addition_log_rows: Sequence[Mapping[str, object]] = (),
    message_approval_rows: Sequence[Mapping[str, object]] = (),
    reset_target: bool = True,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> WordbankMigrationReport:
    if reset_target:
        await _recreate_wordbank_target_namespace()
    await repository.init_all_tables()
    await media_service.rebuild_cache()

    report = WordbankMigrationReport(total_rows=len(rows))
    migration_time = get_current_time()
    imported_log_targets: dict[int, LegacyImportedLogTarget] = {}
    imported_trigger_targets: dict[int, LegacyImportedLogTarget] = {}
    _emit_progress(
        progress,
        phase="entries",
        current=0,
        total=len(rows),
        detail={"reset_target": reset_target},
    )

    for index, row in enumerate(rows, start=1):
        response_id = _coerce_int(row["response_id"], field="response_id")
        try:
            trigger_shape = await legacy_message_to_shape(
                row.get("trigger_text"),
                extra_info=row.get("extra_info"),
                image_catalog=image_catalog,
                media_service=media_service,
                report=report,
            )
            response_shape = await legacy_message_to_shape(
                row.get("response_text"),
                image_catalog=image_catalog,
                media_service=media_service,
                report=report,
                preserve_text_newlines=True,
            )
            if trigger_shape.is_empty():
                trigger_shape = shape_from_text(" ", preserve_blank_text=True)
            if response_shape.is_empty():
                raise MigrationError("empty response shape")

            targets = normalize_legacy_rules(
                priority=_coerce_int(row["priority"], field="priority"),
                response_rule_conditions=load_legacy_json(
                    row.get("response_rule_conditions")
                ),
                trigger_config=row.get("trigger_config"),
            )
            state = normalize_legacy_state(
                approval_status=str(row.get("approval_status", "PENDING")),
                response_available=bool(row.get("response_available", False)),
                migration_time=migration_time,
            )
            created_at = normalize_legacy_timestamp(
                row.get("created_at"),
                fallback=migration_time,
            )
            updated_at = max(created_at, state.deleted_at or created_at)
            probability = normalize_legacy_probability(row.get("trigger_config"))
            weight = _coerce_int(row.get("weight", 3), field="weight")
            created_by = str(row.get("created_by") or "")
            imported_ids: list[int] = []
            primary_log_target: LegacyImportedLogTarget | None = None
            for target in targets:
                entry = await repository.import_message_entry(
                    trigger_shape=trigger_shape,
                    response_shape=response_shape,
                    rule=target.rule,
                    scope=target.scope,
                    priority=SCOPE_PRIORITY[target.scope],
                    trigger_probability=probability,
                    weight=weight,
                    group_id=target.group_id,
                    created_by=created_by,
                    status=state.status,
                    enabled=state.enabled,
                    approved_by="",
                    deleted_at=state.deleted_at,
                    created_at=created_at,
                    updated_at=updated_at,
                )
                imported_ids.append(entry.response_item_id)
                if primary_log_target is None:
                    trigger_variants = entry.trigger_group.trigger_variants
                    if not trigger_variants:
                        raise MigrationError("imported trigger group has no variants")
                    primary_log_target = LegacyImportedLogTarget(
                        trigger_group_id=entry.trigger_group_id,
                        trigger_variant_id=trigger_variants[0].id,
                        response_item_id=entry.response_item_id,
                    )
                report.imported_group_ids[response_id] = entry.trigger_group_id
                report.response_count_by_group_id[entry.trigger_group_id] = (
                    report.response_count_by_group_id.get(entry.trigger_group_id, 0) + 1
                )
                if entry.created_group:
                    report.imported_groups += 1
                report.imported_response_items += 1
                report.imported_entries += 1
                report.status_counts[entry.status] += 1
            report.imported_entry_ids[response_id] = imported_ids
            if primary_log_target is not None:
                imported_log_targets[response_id] = primary_log_target
                trigger_id = row.get("trigger_id")
                if isinstance(trigger_id, int):
                    imported_trigger_targets.setdefault(trigger_id, primary_log_target)
        except Exception as exc:
            report.add_failure(response_id, str(exc), row=row)
        else:
            report.imported_rows += 1
        _emit_progress_if_needed(
            progress,
            phase="entries",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_rows": report.imported_rows,
                "skipped_rows": report.skipped_rows,
                "last_response_id": response_id,
            },
        )

    if response_log_rows:
        imported_message_ids = await migrate_legacy_response_logs(
            response_log_rows,
            repository=repository,
            imported_targets=imported_log_targets,
            report=report,
            progress=progress,
            progress_every=progress_every,
        )
    else:
        imported_message_ids = set()
    if trigger_log_rows:
        await migrate_legacy_trigger_logs(
            trigger_log_rows,
            repository=repository,
            imported_targets=imported_trigger_targets,
            imported_message_ids=imported_message_ids,
            report=report,
            progress=progress,
            progress_every=progress_every,
        )
    if message_approval_rows:
        await migrate_legacy_approval_message_refs(
            message_approval_rows,
            repository=repository,
            imported_targets=imported_log_targets,
            addition_log_rows=addition_log_rows,
            report=report,
            progress=progress,
            progress_every=progress_every,
        )

    _emit_progress(progress, phase="search_index", current=0, total=1, detail={})
    await repository.rebuild_search_index()
    _emit_progress(progress, phase="search_index", current=1, total=1, detail={})
    return report


async def migrate_legacy_response_logs(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    imported_targets: Mapping[int, LegacyImportedLogTarget],
    report: WordbankMigrationReport | None = None,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> set[str]:
    imported_message_ids: set[str] = set()
    if report is not None:
        report.total_log_rows += len(rows)
    _emit_progress(
        progress, phase="response_logs", current=0, total=len(rows), detail={}
    )
    for index, row in enumerate(rows, start=1):
        log_id = row.get("log_id")
        try:
            response_id = _coerce_int(row["response_id"], field="response_id")
            imported_target = imported_targets.get(response_id)
            if imported_target is None:
                raise MigrationError(
                    f"missing imported response mapping for response_id={response_id}"
                )
            created_at = normalize_legacy_timestamp(
                row.get("call_time"),
                fallback=get_current_time(),
            )
            await repository.save_log(
                {
                    "trigger_group_id": imported_target.trigger_group_id,
                    "trigger_variant_id": imported_target.trigger_variant_id,
                    "response_item_id": imported_target.response_item_id,
                    "group_id": str(row.get("group_id") or "").strip(),
                    "user_id": str(row.get("user_id") or ""),
                    "message_type": "group" if row.get("group_id") else "unknown",
                    "created_at": created_at,
                },
                policy=WritePolicy.IMMEDIATE,
            )
            message_id = str(row.get("message_id") or "").strip()
            if message_id:
                imported_message_ids.add(message_id)
            if report is not None:
                report.imported_log_rows += 1
        except Exception as exc:
            if report is not None:
                report.add_log_failure(
                    _optional_coerce_int(log_id, field="log_id"),
                    str(exc),
                    row=row,
                )
        _emit_progress_if_needed(
            progress,
            phase="response_logs",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_log_rows": report.imported_log_rows if report else 0,
                "skipped_log_rows": report.skipped_log_rows if report else 0,
                "last_log_id": _optional_coerce_int(log_id, field="log_id") or 0,
            },
        )
    await repository.drain_logs()
    return imported_message_ids


async def migrate_legacy_trigger_logs(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    imported_targets: Mapping[int, LegacyImportedLogTarget],
    imported_message_ids: set[str],
    report: WordbankMigrationReport | None = None,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> None:
    if report is not None:
        report.total_trigger_log_rows += len(rows)
    _emit_progress(
        progress, phase="trigger_logs", current=0, total=len(rows), detail={}
    )
    for index, row in enumerate(rows, start=1):
        log_id = row.get("log_id")
        message_id = str(row.get("message_id") or "").strip()
        try:
            if message_id and message_id in imported_message_ids:
                continue
            trigger_id = _coerce_int(row["trigger_id"], field="trigger_id")
            imported_target = imported_targets.get(trigger_id)
            if imported_target is None:
                raise MigrationError(
                    f"missing imported trigger mapping for trigger_id={trigger_id}"
                )
            created_at = normalize_legacy_timestamp(
                row.get("call_time"),
                fallback=get_current_time(),
            )
            await repository.save_log(
                {
                    "trigger_group_id": imported_target.trigger_group_id,
                    "trigger_variant_id": imported_target.trigger_variant_id,
                    "response_item_id": imported_target.response_item_id,
                    "group_id": "",
                    "user_id": str(row.get("user_id") or ""),
                    "message_type": "unknown",
                    "created_at": created_at,
                },
                policy=WritePolicy.IMMEDIATE,
            )
            if message_id:
                imported_message_ids.add(message_id)
            if report is not None:
                report.imported_trigger_log_rows += 1
        except Exception as exc:
            if report is not None:
                report.add_trigger_log_failure(
                    _optional_coerce_int(log_id, field="log_id"),
                    str(exc),
                    row=row,
                )
        _emit_progress_if_needed(
            progress,
            phase="trigger_logs",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_trigger_log_rows": (
                    report.imported_trigger_log_rows if report else 0
                ),
                "skipped_trigger_log_rows": (
                    report.skipped_trigger_log_rows if report else 0
                ),
                "last_log_id": _optional_coerce_int(log_id, field="log_id") or 0,
            },
        )
    await repository.drain_logs()


async def migrate_legacy_approval_message_refs(
    rows: Sequence[Mapping[str, object]],
    *,
    repository: WordbankRepository,
    imported_targets: Mapping[int, LegacyImportedLogTarget],
    addition_log_rows: Sequence[Mapping[str, object]] = (),
    report: WordbankMigrationReport | None = None,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> None:
    addition_by_approval_id: dict[int, Mapping[str, object]] = {}
    addition_by_response_id: dict[int, Mapping[str, object]] = {}
    for addition_row in addition_log_rows:
        approval_id = addition_row.get("approval_id")
        response_id = addition_row.get("response_id")
        if isinstance(approval_id, int):
            addition_by_approval_id.setdefault(approval_id, addition_row)
        if isinstance(response_id, int):
            addition_by_response_id.setdefault(response_id, addition_row)

    if report is not None:
        report.total_approval_ref_rows += len(rows)
    _emit_progress(
        progress, phase="approval_refs", current=0, total=len(rows), detail={}
    )

    for index, row in enumerate(rows, start=1):
        message_id = str(row.get("message_id") or "").strip()
        try:
            if not message_id:
                raise MigrationError("approval message_id is empty")
            response_id = _coerce_int(row["response_id"], field="response_id")
            imported_target = imported_targets.get(response_id)
            if imported_target is None:
                raise MigrationError(
                    f"missing imported response mapping for response_id={response_id}"
                )
            approval_id = row.get("approval_id")
            addition_row = (
                addition_by_approval_id.get(approval_id)
                if isinstance(approval_id, int)
                else None
            ) or addition_by_response_id.get(response_id)
            created_at = normalize_legacy_timestamp(
                row.get("approval_created_at")
                or (addition_row.get("add_time") if addition_row is not None else None),
                fallback=get_current_time(),
            )
            add_source = (
                load_legacy_json(addition_row.get("add_source"))
                if addition_row is not None
                else {}
            )
            if not isinstance(add_source, Mapping):
                add_source = {}
            await repository.record_message_ref(
                {
                    "message_id": message_id,
                    "ref_kind": "approval",
                    "shard_key": message_ref_shard_key(created_at),
                    "trigger_group_id": imported_target.trigger_group_id,
                    "trigger_variant_id": imported_target.trigger_variant_id,
                    "response_item_id": imported_target.response_item_id,
                    "group_id": str(add_source.get("group_id") or "").strip(),
                    "user_id": str(
                        add_source.get("user_id")
                        or (
                            addition_row.get("user_id")
                            if addition_row is not None
                            else row.get("approval_user_id")
                        )
                        or ""
                    ).strip(),
                    "message_type": (
                        "group"
                        if str(add_source.get("group_id") or "").strip()
                        else "private"
                    ),
                    "source_message_id": str(
                        addition_row.get("created_message_id")
                        if addition_row is not None
                        else ""
                    ).strip(),
                    "context_type": "",
                    "current_page": 1,
                    "keyword": "",
                    "field": "",
                    "creator_id": "",
                    "has_image": 0,
                    "group_ids_json": "[]",
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
            if report is not None:
                report.imported_approval_ref_rows += 1
        except Exception as exc:
            if report is not None:
                report.add_approval_ref_failure(message_id, str(exc), row=row)
        _emit_progress_if_needed(
            progress,
            phase="approval_refs",
            current=index,
            total=len(rows),
            every=progress_every,
            detail={
                "imported_approval_ref_rows": (
                    report.imported_approval_ref_rows if report else 0
                ),
                "skipped_approval_ref_rows": (
                    report.skipped_approval_ref_rows if report else 0
                ),
                "last_message_id": message_id,
            },
        )


async def migrate_legacy_wordbank(
    old_repo_root: Path,
    *,
    repository: WordbankRepository,
    media_service: WordbankMediaService,
    image_root: Path | None = None,
    mapping_path: Path | None = None,
    pg_config: LegacyPgConfig | None = None,
    reset_target: bool = True,
    import_logs: bool = True,
    progress: LegacyMigrationProgressCallback | None = None,
    progress_every: int = 100,
) -> WordbankMigrationReport:
    resolved_pg_config = pg_config or load_legacy_pg_config(old_repo_root)
    rows = await fetch_legacy_response_rows(resolved_pg_config)
    response_log_rows: Sequence[Mapping[str, object]] = ()
    trigger_log_rows: Sequence[Mapping[str, object]] = ()
    addition_log_rows: Sequence[Mapping[str, object]] = ()
    message_approval_rows: Sequence[Mapping[str, object]] = ()
    if import_logs:
        response_log_rows = await fetch_legacy_response_log_rows(resolved_pg_config)
        trigger_log_rows = await fetch_legacy_trigger_log_rows(resolved_pg_config)
        addition_log_rows = await fetch_legacy_addition_log_rows(resolved_pg_config)
        message_approval_rows = await fetch_legacy_message_approval_rows(
            resolved_pg_config
        )
    resolved_image_root = image_root or _default_legacy_image_root(old_repo_root)
    resolved_mapping_path = (
        mapping_path
        if mapping_path is not None
        else _default_legacy_image_mapping_path(old_repo_root)
    )
    return await migrate_legacy_rows(
        rows,
        repository=repository,
        media_service=media_service,
        image_catalog=build_legacy_image_catalog(
            resolved_image_root,
            resolved_mapping_path,
        ),
        response_log_rows=response_log_rows,
        trigger_log_rows=trigger_log_rows,
        addition_log_rows=addition_log_rows,
        message_approval_rows=message_approval_rows,
        reset_target=reset_target,
        progress=progress,
        progress_every=progress_every,
    )


__all__ = [
    "LegacyImageCatalog",
    "LegacyImportTarget",
    "LegacyPgConfig",
    "MigrationError",
    "WordbankMigrationReport",
    "_resolve_legacy_call_window_seconds",
    "build_legacy_image_catalog",
    "extract_failure_details_from_categorized_report",
    "fetch_legacy_addition_log_rows",
    "fetch_legacy_message_approval_rows",
    "fetch_legacy_response_log_rows",
    "fetch_legacy_response_rows",
    "fetch_legacy_trigger_log_rows",
    "infer_report_response_available",
    "legacy_message_to_shape",
    "load_legacy_pg_config",
    "migrate_legacy_approval_message_refs",
    "migrate_legacy_response_logs",
    "migrate_legacy_rows",
    "migrate_legacy_trigger_logs",
    "migrate_legacy_wordbank",
    "normalize_legacy_rules",
    "normalize_legacy_scope",
    "normalize_legacy_state",
    "parse_legacy_env_file",
    "rebuild_legacy_row_from_failure_detail",
    "rebuild_legacy_rows_from_failure_details",
]
