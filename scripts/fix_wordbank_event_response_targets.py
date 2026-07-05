"""Bind sender targets for legacy wordbank event responses."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "db" / "wordbank_db" / "wordbank_main.db"
RESPONSE_TARGET_SENDER = "__sender__"

_SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True, frozen=True)
class FixStats:
    scanned: int = 0
    updated: int = 0
    at_updated: int = 0
    poke_updated: int = 0


def normalize_text(text: str, *, casefold: bool = True) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.casefold() if casefold else normalized


def _join_shape_text_parts(parts: list[str]) -> str:
    joined = ""
    for part in parts:
        if not part:
            continue
        if not joined:
            joined = part
            continue
        if joined[-1].isspace() or part[0].isspace():
            joined += part
            continue
        joined += f" {part}"
    if not joined:
        return ""
    stripped = joined.strip()
    return stripped or " "


def format_at_fallback_text(target_id: str) -> str:
    if target_id == "all":
        return "@全体成员"
    return f"@用户({target_id})"


def format_at_summary_text(target_id: str) -> str:
    if target_id == RESPONSE_TARGET_SENDER:
        return "@触发者"
    return format_at_fallback_text(target_id)


def shape_to_summary_text(shape: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for atom in shape:
        kind = str(atom.get("kind", "") or "")
        if kind == "text":
            text = str(atom.get("text", "") or "")
            if text:
                parts.append(text)
        elif kind == "image":
            canonical_image_id = atom.get("canonical_image_id")
            if canonical_image_id is not None:
                parts.append(f"[图片:{int(str(canonical_image_id))}]")
        elif kind == "at":
            target_id = str(atom.get("target_id", "") or "")
            if target_id:
                parts.append(format_at_summary_text(target_id))
        elif kind == "event":
            event_name = str(atom.get("event_name", "") or "")
            target_id = str(atom.get("target_id", "") or "")
            if event_name == "event:poke":
                if target_id == RESPONSE_TARGET_SENDER:
                    parts.append("戳一戳触发者")
                elif target_id:
                    parts.append(f"戳一戳用户({target_id})")
                else:
                    parts.append("[事件:event:poke]")
            elif event_name:
                parts.append(f"[事件:{event_name}]")
    return _join_shape_text_parts(parts)


def shape_to_search_text(shape: list[dict[str, object]]) -> str:
    texts: list[str] = []
    for atom in shape:
        kind = str(atom.get("kind", "") or "")
        if kind == "text":
            text = str(atom.get("text", "") or "")
            if text:
                texts.append(text)
        elif kind == "at":
            target_id = str(atom.get("target_id", "") or "")
            if target_id:
                texts.append(f"at {target_id}")
        elif kind == "event":
            event_name = str(atom.get("event_name", "") or "")
            target_id = str(atom.get("target_id", "") or "")
            if event_name:
                texts.append(f"{event_name} {target_id}".strip())
    return _join_shape_text_parts(texts)


def build_ngram_tokens(text_value: str, *, max_gram_size: int = 3) -> str:
    condensed = normalize_text(text_value).replace(" ", "")
    if not condensed:
        return ""
    tokens: list[str] = []
    for gram_size in range(1, min(max_gram_size, len(condensed)) + 1):
        for index in range(0, len(condensed) - gram_size + 1):
            tokens.append(condensed[index : index + gram_size])
    return " ".join(dict.fromkeys(tokens))


def image_keys(shape: list[dict[str, object]]) -> str:
    ids: list[str] = []
    for atom in shape:
        if str(atom.get("kind", "") or "") != "image":
            continue
        canonical_image_id = atom.get("canonical_image_id")
        if canonical_image_id is None:
            continue
        ids.append(str(int(str(canonical_image_id))))
    if not ids:
        return ""
    return "|" + "|".join(ids) + "|"


def payload_fingerprint(shape: list[dict[str, object]]) -> dict[str, str]:
    payload = json.dumps(shape, ensure_ascii=False, separators=(",", ":"))
    search_text = shape_to_search_text(shape)
    return {
        "payload": payload,
        "exact_md5": hashlib.md5(payload.encode("utf-8")).hexdigest(),
        "structure_key": "|".join(str(atom.get("kind", "") or "") for atom in shape),
        "summary_text": shape_to_summary_text(shape),
        "search_text": search_text,
        "search_tokens": build_ngram_tokens(search_text),
        "image_keys": image_keys(shape),
    }


def has_sender_at(shape: list[dict[str, object]]) -> bool:
    return any(
        str(atom.get("kind", "") or "") == "at"
        and str(atom.get("target_id", "") or "") == RESPONSE_TARGET_SENDER
        for atom in shape
    )


def has_sender_poke(shape: list[dict[str, object]]) -> bool:
    return any(
        str(atom.get("kind", "") or "") == "event"
        and str(atom.get("event_name", "") or "") == "event:poke"
        and str(atom.get("target_id", "") or "") == RESPONSE_TARGET_SENDER
        for atom in shape
    )


def remove_sender_at(shape: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        atom
        for atom in shape
        if not (
            str(atom.get("kind", "") or "") == "at"
            and str(atom.get("target_id", "") or "") == RESPONSE_TARGET_SENDER
        )
    ]


def prepend_sender_at(shape: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{"kind": "at", "target_id": RESPONSE_TARGET_SENDER}, *shape]


def prepend_sender_poke(shape: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "kind": "event",
            "event_name": "event:poke",
            "target_id": RESPONSE_TARGET_SENDER,
        },
        *shape,
    ]


def event_names(shape: list[dict[str, object]]) -> set[str]:
    return {
        str(atom.get("event_name", "") or "")
        for atom in shape
        if str(atom.get("kind", "") or "") == "event" and atom.get("event_name")
    }


def transform_response_shape(
    trigger_shape: list[dict[str, object]],
    response_shape: list[dict[str, object]],
) -> tuple[list[dict[str, object]], str | None]:
    events = event_names(trigger_shape)
    if "event:at" in events:
        if has_sender_at(response_shape):
            return response_shape, None
        return prepend_sender_at(response_shape), "event:at"
    if "event:poke" in events:
        cleaned_shape = remove_sender_at(response_shape)
        if has_sender_poke(cleaned_shape):
            if cleaned_shape == response_shape:
                return response_shape, None
            return cleaned_shape, "event:poke"
        return prepend_sender_poke(cleaned_shape), "event:poke"
    return response_shape, None


def fix_wordbank_event_response_targets(
    db_path: Path,
    *,
    dry_run: bool = False,
) -> FixStats:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                ri.id,
                ri.message_json,
                tv.message_json AS trigger_message_json
            FROM wordbank_response_item ri
            JOIN wordbank_trigger_variant tv
              ON tv.trigger_group_id = ri.trigger_group_id
            WHERE ri.deleted_at = 0
            """
        ).fetchall()
        updated = 0
        at_updated = 0
        poke_updated = 0
        for row in rows:
            response_shape = json.loads(str(row["message_json"] or "[]"))
            trigger_shape = json.loads(str(row["trigger_message_json"] or "[]"))
            new_shape, reason = transform_response_shape(trigger_shape, response_shape)
            if reason is None or new_shape == response_shape:
                continue
            fingerprint = payload_fingerprint(new_shape)
            if not dry_run:
                connection.execute(
                    """
                    UPDATE wordbank_response_item
                    SET
                        text = ?,
                        message_json = ?,
                        exact_md5 = ?,
                        structure_key = ?,
                        search_text = ?,
                        search_tokens = ?,
                        image_keys = ?
                    WHERE id = ?
                    """,
                    (
                        fingerprint["summary_text"],
                        fingerprint["payload"],
                        fingerprint["exact_md5"],
                        fingerprint["structure_key"],
                        fingerprint["search_text"],
                        fingerprint["search_tokens"],
                        fingerprint["image_keys"],
                        int(row["id"]),
                    ),
                )
            updated += 1
            if reason == "event:at":
                at_updated += 1
            else:
                poke_updated += 1
        if not dry_run:
            connection.execute("DELETE FROM wordbank_search_document")
            connection.execute("DELETE FROM wordbank_search_image_map")
            connection.execute("DELETE FROM wordbank_search_trigger_fts")
            connection.execute("DELETE FROM wordbank_search_response_fts")
            connection.commit()
        return FixStats(
            scanned=len(rows),
            updated=updated,
            at_updated=at_updated,
            poke_updated=poke_updated,
        )
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind sender targets for legacy wordbank event responses"
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="path to wordbank_main.db",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and report without writing changes",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    stats = fix_wordbank_event_response_targets(
        Path(args.db_path).resolve(),
        dry_run=args.dry_run,
    )
    print(  # noqa: T201
        "wordbank-event-response-fix",
        f"db_path={Path(args.db_path).resolve()}",
        f"scanned={stats.scanned}",
        f"updated={stats.updated}",
        f"at_updated={stats.at_updated}",
        f"poke_updated={stats.poke_updated}",
        f"dry_run={args.dry_run}",
    )


if __name__ == "__main__":
    main()
