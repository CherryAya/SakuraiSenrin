"""Export real legacy PostgreSQL wordbank rows into treemap fixtures and PNGs."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
import json
from pathlib import Path
import re
import sys
import types
from typing import Any, cast

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
_ensure_pkg(
    "src.plugins.wordbank.services",
    ROOT / "src" / "plugins" / "wordbank" / "services",
)
_ensure_pkg(
    "src.plugins.wordbank.database",
    ROOT / "src" / "plugins" / "wordbank" / "database",
)
_ensure_pkg("src.lib.object_storage", ROOT / "src" / "lib" / "object_storage")

from src.lib.i18n.types import LocaleCode
from src.lib.wordbank_search_treemap import (
    SearchTreemapItem,
    SearchTreemapPage,
    SearchTreemapQuery,
    SearchTreemapResponseCard,
    SearchTreemapResponseSegment,
    render_search_results_treemap_bytes,
)
from src.plugins.wordbank.migration import LegacyPgConfig, load_legacy_pg_config

_NON_FILENAME_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")
_MEDIA_CACHE_ROOT = ROOT / "data" / "wordbank" / "media_cache"
_IMAGE_PLACEHOLDER_RE = re.compile(r"\s*\[图片x\d+\]\s*")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-repo",
        default="../sakuraisenrin-old",
        help="path to the legacy repository root",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        required=True,
        help="search keyword, repeatable",
    )
    parser.add_argument(
        "--field",
        choices=("all", "trigger", "response"),
        default="trigger",
        help="text search scope",
    )
    parser.add_argument(
        "--creator",
        default="",
        help="optional creator_id filter",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="1-based page number",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="items per page",
    )
    parser.add_argument(
        "--preview-responses",
        type=int,
        default=8,
        help="how many responses to keep in fixture per trigger",
    )
    parser.add_argument(
        "--locale",
        default="zh-CN",
        help="treemap locale",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "output" / "wordbank-pg-treemap",
        help="directory for generated fixtures and PNGs",
    )
    parser.add_argument("--pg-host", help="legacy PostgreSQL host override")
    parser.add_argument("--pg-port", type=int, help="legacy PostgreSQL port override")
    parser.add_argument("--pg-user", help="legacy PostgreSQL user override")
    parser.add_argument("--pg-password", help="legacy PostgreSQL password override")
    parser.add_argument(
        "--pg-database",
        default="senrin_wordbank",
        help="legacy PostgreSQL database name",
    )
    return parser.parse_args()


def build_pg_config(args: argparse.Namespace) -> LegacyPgConfig:
    defaults = load_legacy_pg_config(Path(args.old_repo))
    return LegacyPgConfig(
        host=args.pg_host or defaults.host,
        port=args.pg_port or defaults.port,
        user=args.pg_user or defaults.user,
        password=args.pg_password or defaults.password,
        database=args.pg_database or defaults.database,
    )


def _connect(config: LegacyPgConfig) -> Any:
    import psycopg2

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=config.database,
    )


def _fetch_rows_sync(
    config: LegacyPgConfig,
    *,
    sql: str,
    params: Sequence[object] | None = None,
) -> list[dict[str, object]]:
    from psycopg2.extras import RealDictCursor

    with closing(_connect(config)) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    return [dict(row) for row in rows]


async def _fetch_rows(
    config: LegacyPgConfig,
    *,
    sql: str,
    params: Sequence[object] | None = None,
) -> list[dict[str, object]]:
    return await asyncio.to_thread(_fetch_rows_sync, config, sql=sql, params=params)


def _search_clause(field: str) -> str:
    if field == "trigger":
        return "t.trigger_text::text ILIKE %s"
    if field == "response":
        return "r.response_text::text ILIKE %s"
    return "(t.trigger_text::text ILIKE %s OR r.response_text::text ILIKE %s)"


def _search_params(keyword: str, field: str, creator_id: str) -> list[object]:
    pattern = f"%{keyword}%"
    params: list[object]
    if field == "all":
        params = [pattern, pattern]
    else:
        params = [pattern]
    if creator_id:
        params.append(creator_id)
    return params


def _coerce_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        return int(stripped)
    return default


async def fetch_matching_group_counts(
    config: LegacyPgConfig,
    *,
    keyword: str,
    field: str,
    creator_id: str,
) -> list[dict[str, object]]:
    creator_sql = " AND r.created_by::text = %s" if creator_id else ""
    rows = await _fetch_rows(
        config,
        sql=f"""
        WITH matched_groups AS (
            SELECT DISTINCT t.trigger_id
            FROM trigger AS t
            JOIN response AS r
                ON r.trigger_id = t.trigger_id
            JOIN approval AS a
                ON a.response_id = r.response_id
            WHERE a.current_status = 'APPROVED'
              AND r.availability = TRUE
              AND {_search_clause(field)}
              {creator_sql}
        )
        SELECT
            t.trigger_id,
            COUNT(r.response_id)::int AS response_count
        FROM matched_groups AS mg
        JOIN trigger AS t
            ON t.trigger_id = mg.trigger_id
        JOIN response AS r
            ON r.trigger_id = t.trigger_id
        JOIN approval AS a
            ON a.response_id = r.response_id
        WHERE a.current_status = 'APPROVED'
          AND r.availability = TRUE
        GROUP BY t.trigger_id
        ORDER BY response_count DESC, t.trigger_id ASC
        """,
        params=_search_params(keyword, field, creator_id),
    )
    return rows


async def fetch_group_rows(
    config: LegacyPgConfig,
    *,
    trigger_ids: Sequence[int],
) -> list[dict[str, object]]:
    if not trigger_ids:
        return []
    return await _fetch_rows(
        config,
        sql="""
        SELECT
            t.trigger_id,
            t.trigger_text,
            t.extra_info,
            t.trigger_config,
            r.response_id,
            r.response_text,
            r.response_rule_conditions,
            r.weight,
            r.priority,
            r.created_by
        FROM trigger AS t
        JOIN response AS r
            ON r.trigger_id = t.trigger_id
        JOIN approval AS a
            ON a.response_id = r.response_id
        WHERE t.trigger_id = ANY(%s)
          AND a.current_status = 'APPROVED'
          AND r.availability = TRUE
        ORDER BY t.trigger_id ASC, r.priority ASC, r.response_id ASC
        """,
        params=[list(trigger_ids)],
    )


def _load_json_payload(value: object) -> Any:
    if value in (None, ""):
        return []
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return []


def summarize_legacy_message_payload(value: object) -> str:
    payload = _load_json_payload(value)
    if not isinstance(payload, list):
        return str(value or "").strip()
    text_parts: list[str] = []
    image_count = 0
    extra_segments: list[str] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind == "text":
            text = str(item.get("text") or "").replace("\r", "\n").strip()
            if text:
                text_parts.append(" ".join(part for part in text.splitlines() if part))
            continue
        if kind == "image":
            image_count += 1
            continue
        if kind:
            extra_segments.append(kind)
    summary = " ".join(part for part in text_parts if part).strip()
    if image_count > 0:
        image_label = f"[图片x{image_count}]"
        summary = f"{summary} {image_label}".strip() if summary else image_label
    if not summary and extra_segments:
        summary = " ".join(f"[{kind}]" for kind in extra_segments)
    return summary or "无文本内容"


def strip_image_placeholder(text: str) -> str:
    cleaned = _IMAGE_PLACEHOLDER_RE.sub(" ", text)
    cleaned = " ".join(part for part in cleaned.split() if part)
    return cleaned.strip()


def build_legacy_message_segments(
    value: object,
    *,
    media_samples: Sequence[str],
    sample_index: int,
) -> tuple[tuple[SearchTreemapResponseSegment, ...], int]:
    payload = _load_json_payload(value)
    if not isinstance(payload, list):
        text = str(value or "").strip()
        return (
            (SearchTreemapResponseSegment(kind="text", text=text),) if text else (),
            sample_index,
        )
    segments: list[SearchTreemapResponseSegment] = []
    next_index = sample_index
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind == "text":
            text = str(item.get("text") or "").replace("\r", "\n").strip()
            normalized = " ".join(part for part in text.splitlines() if part).strip()
            if normalized:
                segments.append(
                    SearchTreemapResponseSegment(kind="text", text=normalized)
                )
            continue
        if kind == "image":
            image_path = ""
            if media_samples:
                image_path = media_samples[next_index % len(media_samples)]
                next_index += 1
            if image_path:
                segments.append(
                    SearchTreemapResponseSegment(
                        kind="image",
                        image_path=image_path,
                    )
                )
    return (tuple(segments), next_index)


def legacy_payload_contains_image(value: object) -> bool:
    payload = _load_json_payload(value)
    if not isinstance(payload, list):
        return False
    return any(
        isinstance(item, Mapping)
        and str(item.get("type") or "").strip().lower() == "image"
        for item in payload
    )


def summarize_legacy_rule(value: object) -> str:
    payload = _load_json_payload(value)
    if not isinstance(payload, Mapping) or not payload:
        return "默认"
    parts: list[str] = []
    group_id = _extract_rule_value(payload.get("group_id"))
    user_id = _extract_rule_value(payload.get("user_id"))
    role = str(payload.get("role") or "").strip()
    call_count = payload.get("call_count")
    if group_id:
        parts.append(f"群 {group_id}")
    if user_id:
        parts.append(f"用户 {user_id}")
    if role:
        parts.append(f"角色 {role}")
    if isinstance(call_count, Mapping):
        window = _coerce_int(
            call_count.get("window") or call_count.get("window_seconds") or 0
        )
        min_count = _coerce_int(call_count.get("min") or 0)
        max_count = _coerce_int(call_count.get("max") or 0)
        parts.append(f"频次 {window}:{min_count}:{max_count}")
    if "$or" in payload:
        parts.append("OR")
    if "$and" in payload:
        parts.append("AND")
    if not parts:
        parts.extend(str(key) for key in payload.keys())
    return " / ".join(parts)


def _extract_rule_value(value: object) -> str:
    if isinstance(value, Mapping):
        for key in ("$eq", "eq", "value"):
            nested = value.get(key)
            if nested not in (None, ""):
                return str(nested).strip()
    return str(value or "").strip()


def detect_match_source(
    *,
    keyword: str,
    trigger_text: str,
    response_texts: Sequence[str],
) -> str:
    lowered = keyword.casefold()
    trigger_hit = lowered in trigger_text.casefold()
    response_hit = any(lowered in text.casefold() for text in response_texts)
    if trigger_hit and response_hit:
        return "text:mixed"
    if trigger_hit:
        return "text:trigger"
    if response_hit:
        return "text:response"
    return "text:group"


def sanitize_filename(value: str) -> str:
    cleaned = _NON_FILENAME_RE.sub("-", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:80] or "keyword"


def load_media_cache_samples(limit: int = 512) -> tuple[str, ...]:
    if not _MEDIA_CACHE_ROOT.is_dir():
        return ()
    samples = sorted(
        str(path.resolve())
        for path in _MEDIA_CACHE_ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg"}
    )
    return tuple(samples[:limit])


def build_page_from_rows(
    *,
    keyword: str,
    field: str,
    creator_id: str,
    page: int,
    limit: int,
    preview_responses: int,
    counts: Sequence[dict[str, object]],
    rows: Sequence[dict[str, object]],
    media_samples: Sequence[str],
) -> SearchTreemapPage:
    total_count = len(counts)
    order = [_coerce_int(item["trigger_id"]) for item in counts]
    response_count_by_trigger_id = {
        _coerce_int(item["trigger_id"]): _coerce_int(item["response_count"])
        for item in counts
    }
    rows_by_trigger: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        rows_by_trigger[_coerce_int(row["trigger_id"])].append(row)

    items: list[SearchTreemapItem] = []
    sample_index = 0
    for trigger_id in order:
        group_rows = rows_by_trigger.get(trigger_id)
        if not group_rows:
            continue
        trigger_text = summarize_legacy_message_payload(group_rows[0]["trigger_text"])
        response_cards_list: list[SearchTreemapResponseCard] = []
        for row in group_rows[:preview_responses]:
            text = summarize_legacy_message_payload(row["response_text"])
            segments, sample_index = build_legacy_message_segments(
                row["response_text"],
                media_samples=media_samples,
                sample_index=sample_index,
            )
            image_path = next(
                (
                    segment.image_path
                    for segment in segments
                    if segment.kind == "image" and segment.image_path
                ),
                "",
            )
            if image_path:
                text = strip_image_placeholder(text)
            response_cards_list.append(
                SearchTreemapResponseCard(
                    text=text,
                    created_by=str(row.get("created_by") or ""),
                    weight=_coerce_int(row.get("weight") or 0),
                    rule=summarize_legacy_rule(row.get("response_rule_conditions")),
                    image_path=image_path,
                    segments=segments,
                )
            )
        response_cards = tuple(response_cards_list)
        response_texts = [card.text for card in response_cards]
        response_count = response_count_by_trigger_id[trigger_id]
        items.append(
            SearchTreemapItem(
                trigger_group_id=trigger_id,
                trigger_text=trigger_text,
                status="approved",
                created_by=str(group_rows[0].get("created_by") or ""),
                response_count=response_count,
                responses=response_cards,
                remaining_response_count=max(0, response_count - len(response_cards)),
                matched_by=detect_match_source(
                    keyword=keyword,
                    trigger_text=trigger_text,
                    response_texts=response_texts,
                ),
            )
        )

    return SearchTreemapPage(
        query=SearchTreemapQuery(
            keyword=keyword,
            field=field,
            creator_id=creator_id,
            has_image=False,
            page=page,
            total_count=total_count,
            limit=limit,
        ),
        items=tuple(items),
    )


def page_to_fixture_dict(page: SearchTreemapPage) -> dict[str, object]:
    return {
        "query": {
            "keyword": page.query.keyword,
            "field": page.query.field,
            "creator_id": page.query.creator_id,
            "has_image": page.query.has_image,
            "page": page.query.page,
            "total_count": page.query.total_count,
            "limit": page.query.limit,
        },
        "items": [
            {
                "trigger_group_id": item.trigger_group_id,
                "trigger_text": item.trigger_text,
                "status": item.status,
                "created_by": item.created_by,
                "response_count": item.response_count,
                "responses": [
                    {
                        "text": response.text,
                        "created_by": response.created_by,
                        "weight": response.weight,
                        "rule": response.rule,
                        "image_path": response.image_path,
                        "segments": [
                            {
                                "kind": segment.kind,
                                "text": segment.text,
                                "image_path": segment.image_path,
                            }
                            for segment in response.segments
                        ],
                    }
                    for response in item.responses
                ],
                "remaining_response_count": item.remaining_response_count,
                "matched_by": item.matched_by,
            }
            for item in page.items
        ],
    }


async def export_keyword(
    config: LegacyPgConfig,
    *,
    keyword: str,
    field: str,
    creator_id: str,
    page: int,
    limit: int,
    preview_responses: int,
    locale: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    media_samples = load_media_cache_samples()
    counts = await fetch_matching_group_counts(
        config,
        keyword=keyword,
        field=field,
        creator_id=creator_id,
    )
    offset = (page - 1) * limit
    selected_counts = counts[offset : offset + limit]
    trigger_ids = [_coerce_int(item["trigger_id"]) for item in selected_counts]
    rows = await fetch_group_rows(config, trigger_ids=trigger_ids)
    page_data = build_page_from_rows(
        keyword=keyword,
        field=field,
        creator_id=creator_id,
        page=page,
        limit=limit,
        preview_responses=preview_responses,
        counts=counts,
        rows=rows,
        media_samples=media_samples,
    )
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    slug = sanitize_filename(keyword)
    base_name = f"treemap-real-{slug}-p{page}"
    fixture_path = output_dir / f"{base_name}.json"
    image_path = output_dir / f"{base_name}.png"
    await asyncio.to_thread(
        fixture_path.write_text,
        json.dumps(page_to_fixture_dict(page_data), ensure_ascii=False, indent=2),
        "utf-8",
    )
    await asyncio.to_thread(
        image_path.write_bytes,
        render_search_results_treemap_bytes(
            page=page_data,
            locale=cast(LocaleCode, locale),
        ),
    )
    return fixture_path, image_path


async def main() -> None:
    args = parse_args()
    config = build_pg_config(args)
    for keyword in args.keyword:
        fixture_path, image_path = await export_keyword(
            config,
            keyword=keyword,
            field=args.field,
            creator_id=args.creator,
            page=max(1, args.page),
            limit=max(1, args.limit),
            preview_responses=max(1, args.preview_responses),
            locale=args.locale,
            output_dir=args.output_dir,
        )
        sys.stdout.write(f"{keyword}\t{fixture_path}\t{image_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
