"""Export offline wordbank trigger-response analysis for selected triggers."""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import arrow
import nonebot
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.core.tables import User
from src.database.instances import core_db
from src.lib.utils.common import get_current_time

DEFAULT_OUTPUT_DIR = ROOT / "output" / "wordbank-trigger-analysis"
DEFAULT_PREFIX = "wordbank-trigger-analysis"
DEFAULT_DECORATION_IMAGE = ROOT / ".devtest" / "senrin-v3-transparent.png"
DEFAULT_TRIGGERS = (
    "真心话",
    "大冒险",
    "我的自拍",
    "摸金",
    "随机仙尊语录",
    "运势",
    "jrlp",
    "猜拳",
    "漂流瓶",
    "打卡",
)
SCOPE_SCORES = {
    "all_groups": 1.0,
    "current_group": 0.7,
    "self": 0.5,
    "private_only": 0.5,
    "self_in_current_group": 0.5,
}

wordbank_repo: Any = None
wordbank_main_db: Any = None
WordbankResponseItem: Any = None
WordbankTriggerVariant: Any = None


@dataclass(slots=True)
class ResponseRecord:
    trigger_name: str
    trigger_group_id: int
    response_item_id: int
    created_by: str
    creator_name: str
    score: float
    scope: str
    group_id: str
    status: str
    enabled: int
    created_at: int
    updated_at: int
    approved_by: str
    text_preview: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export selected wordbank trigger analysis from offline DB",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="output directory",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help="output filename prefix",
    )
    parser.add_argument(
        "--triggers",
        nargs="*",
        default=list(DEFAULT_TRIGGERS),
        help="trigger texts to analyze",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit response-level detail exports and detailed HTML tables",
    )
    parser.add_argument(
        "--screenshot",
        action="store_true",
        help="export a full-page screenshot next to the HTML",
    )
    return parser.parse_args()


def _normalize_triggers(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _score_scope(scope: str) -> float:
    return float(SCOPE_SCORES.get(scope, 0.0))


def _preview_text(value: str, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _json_path(output_dir: Path, prefix: str) -> Path:
    return output_dir / f"{prefix}.json"


def _summary_csv_path(output_dir: Path, prefix: str) -> Path:
    return output_dir / f"{prefix}.summary.csv"


def _creators_csv_path(output_dir: Path, prefix: str) -> Path:
    return output_dir / f"{prefix}.creators.csv"


def _responses_csv_path(output_dir: Path, prefix: str) -> Path:
    return output_dir / f"{prefix}.responses.csv"


def _html_path(output_dir: Path, prefix: str) -> Path:
    return output_dir / f"{prefix}.html"


def _png_path(output_dir: Path, prefix: str) -> Path:
    return output_dir / f"{prefix}.png"


def _decoration_image_url() -> str:
    return DEFAULT_DECORATION_IMAGE.resolve().as_uri()


def _export_screenshot(html_path: Path, png_path: Path) -> None:
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError("npx not found")
    subprocess.run(
        [
            npx,
            "playwright",
            "screenshot",
            "--full-page",
            html_path.resolve().as_uri(),
            str(png_path),
        ],
        check=True,
    )


async def _load_user_names(user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    async with core_db.session(commit=False) as session:
        rows = (
            await session.execute(
                select(User.user_id, User.user_name).where(
                    User.user_id.in_(tuple(user_ids))
                )
            )
        ).all()
    result = {str(user_id): str(user_name or "").strip() for user_id, user_name in rows}
    for user_id in user_ids:
        result.setdefault(user_id, user_id)
    return result


async def _load_responses(
    trigger_names: list[str],
) -> tuple[dict[str, list[int]], list[tuple[Any, Any]]]:
    async with wordbank_main_db.read_session() as session:
        variant_rows = (
            await session.execute(
                select(
                    WordbankTriggerVariant.trigger_text,
                    WordbankTriggerVariant.trigger_group_id,
                ).where(WordbankTriggerVariant.trigger_text.in_(tuple(trigger_names)))
            )
        ).all()
        group_ids = sorted({int(row.trigger_group_id) for row in variant_rows})
        if not group_ids:
            return {name: [] for name in trigger_names}, []
        response_rows = (
            await session.execute(
                select(WordbankTriggerVariant, WordbankResponseItem)
                .join(
                    WordbankResponseItem,
                    WordbankResponseItem.trigger_group_id
                    == WordbankTriggerVariant.trigger_group_id,
                )
                .where(
                    WordbankTriggerVariant.trigger_group_id.in_(tuple(group_ids)),
                    WordbankTriggerVariant.trigger_text.in_(tuple(trigger_names)),
                    WordbankResponseItem.status == "approved",
                    WordbankResponseItem.deleted_at == 0,
                )
                .order_by(
                    WordbankTriggerVariant.trigger_text.asc(),
                    WordbankResponseItem.created_at.asc(),
                    WordbankResponseItem.id.asc(),
                )
            )
        ).all()
    groups_by_trigger: dict[str, list[int]] = {name: [] for name in trigger_names}
    for row in variant_rows:
        trigger_text = str(row.trigger_text)
        trigger_group_id = int(row.trigger_group_id)
        if trigger_group_id not in groups_by_trigger[trigger_text]:
            groups_by_trigger[trigger_text].append(trigger_group_id)
    return groups_by_trigger, list(response_rows)


def _build_payload(
    trigger_names: list[str],
    groups_by_trigger: dict[str, list[int]],
    response_rows: list[tuple[Any, Any]],
    user_names: dict[str, str],
) -> dict[str, Any]:
    rows_by_trigger: dict[str, dict[int, list[ResponseRecord]]] = {
        trigger_name: {} for trigger_name in trigger_names
    }
    for variant, response in response_rows:
        trigger_name = str(variant.trigger_text)
        group_id = int(response.trigger_group_id)
        by_group = rows_by_trigger.setdefault(trigger_name, {})
        items = by_group.setdefault(group_id, [])
        items.append(
            ResponseRecord(
                trigger_name=trigger_name,
                trigger_group_id=group_id,
                response_item_id=int(response.id),
                created_by=str(response.created_by),
                creator_name=user_names.get(
                    str(response.created_by), str(response.created_by)
                ),
                score=_score_scope(str(response.scope)),
                scope=str(response.scope),
                group_id=str(response.group_id or ""),
                status=str(response.status),
                enabled=int(response.enabled),
                created_at=int(response.created_at),
                updated_at=int(response.updated_at),
                approved_by=str(response.approved_by or ""),
                text_preview=_preview_text(str(response.text or "")),
            )
        )

    trigger_summaries: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    creator_rows: list[dict[str, Any]] = []
    response_detail_rows: list[dict[str, Any]] = []

    for trigger_name in trigger_names:
        group_map = rows_by_trigger.get(trigger_name, {})
        deduped: dict[int, ResponseRecord] = {}
        for items in group_map.values():
            for item in items:
                deduped[item.response_item_id] = item
        responses = sorted(
            deduped.values(),
            key=lambda item: (-item.score, item.created_at, item.response_item_id),
        )

        creator_stats: dict[str, dict[str, Any]] = {}
        for item in responses:
            stat = creator_stats.setdefault(
                item.created_by,
                {
                    "user_id": item.created_by,
                    "creator_name": item.creator_name,
                    "response_count": 0,
                    "score_total": 0.0,
                    "first_created_at": item.created_at,
                    "last_created_at": item.created_at,
                    "scope_breakdown": {
                        "all_groups": 0,
                        "current_group": 0,
                        "self": 0,
                        "private_only": 0,
                        "self_in_current_group": 0,
                    },
                    "responses": [],
                },
            )
            stat["response_count"] += 1
            stat["score_total"] += item.score
            stat["first_created_at"] = min(
                int(stat["first_created_at"]), item.created_at
            )
            stat["last_created_at"] = max(int(stat["last_created_at"]), item.created_at)
            stat["scope_breakdown"][item.scope] = (
                int(stat["scope_breakdown"].get(item.scope, 0)) + 1
            )
            stat["responses"].append(
                {
                    "response_item_id": item.response_item_id,
                    "trigger_group_id": item.trigger_group_id,
                    "scope": item.scope,
                    "score": item.score,
                    "created_at": item.created_at,
                    "group_id": item.group_id,
                    "text_preview": item.text_preview,
                }
            )

        creators_by_count = sorted(
            creator_stats.values(),
            key=lambda stat: (
                -int(stat["response_count"]),
                -float(stat["score_total"]),
                int(stat["first_created_at"]),
                str(stat["user_id"]),
            ),
        )
        creators_by_first_created = sorted(
            creator_stats.values(),
            key=lambda stat: (
                int(stat["first_created_at"]),
                -int(stat["response_count"]),
                str(stat["user_id"]),
            ),
        )

        total_score = round(sum(item.score for item in responses), 2)
        creator_names = [str(item["creator_name"]) for item in creators_by_count]
        summary = {
            "trigger_name": trigger_name,
            "trigger_group_count": len(groups_by_trigger.get(trigger_name, [])),
            "response_count": len(responses),
            "creator_count": len(creator_stats),
            "total_score": total_score,
            "creator_names": creator_names,
            "creators_by_count": creators_by_count,
            "creators_by_first_created": creators_by_first_created,
            "responses": [
                {
                    "response_item_id": item.response_item_id,
                    "trigger_group_id": item.trigger_group_id,
                    "created_by": item.created_by,
                    "creator_name": item.creator_name,
                    "score": item.score,
                    "scope": item.scope,
                    "group_id": item.group_id,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "approved_by": item.approved_by,
                    "text_preview": item.text_preview,
                }
                for item in responses
            ],
        }
        trigger_summaries.append(summary)
        summary_rows.append(
            {
                "trigger_name": trigger_name,
                "trigger_group_count": len(groups_by_trigger.get(trigger_name, [])),
                "response_count": len(responses),
                "creator_count": len(creator_stats),
                "total_score": total_score,
                "top_creator_name": creator_names[0] if creator_names else "",
                "top_creator_response_count": (
                    int(creators_by_count[0]["response_count"])
                    if creators_by_count
                    else 0
                ),
                "earliest_creator_name": (
                    str(creators_by_first_created[0]["creator_name"])
                    if creators_by_first_created
                    else ""
                ),
                "earliest_created_at": (
                    int(creators_by_first_created[0]["first_created_at"])
                    if creators_by_first_created
                    else 0
                ),
            }
        )
        for rank, creator in enumerate(creators_by_count, start=1):
            creator_rows.append(
                {
                    "trigger_name": trigger_name,
                    "creator_rank_by_count": rank,
                    "user_id": str(creator["user_id"]),
                    "creator_name": str(creator["creator_name"]),
                    "response_count": int(creator["response_count"]),
                    "score_total": round(float(creator["score_total"]), 2),
                    "first_created_at": int(creator["first_created_at"]),
                    "last_created_at": int(creator["last_created_at"]),
                    "all_groups_count": int(creator["scope_breakdown"]["all_groups"]),
                    "current_group_count": int(
                        creator["scope_breakdown"]["current_group"]
                    ),
                    "self_count": int(creator["scope_breakdown"]["self"]),
                    "private_only_count": int(
                        creator["scope_breakdown"]["private_only"]
                    ),
                    "self_in_current_group_count": int(
                        creator["scope_breakdown"]["self_in_current_group"]
                    ),
                }
            )
        for score_rank, item in enumerate(responses, start=1):
            response_detail_rows.append(
                {
                    "trigger_name": trigger_name,
                    "score_rank": score_rank,
                    "response_item_id": item.response_item_id,
                    "trigger_group_id": item.trigger_group_id,
                    "created_by": item.created_by,
                    "creator_name": item.creator_name,
                    "score": item.score,
                    "scope": item.scope,
                    "group_id": item.group_id,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "approved_by": item.approved_by,
                    "text_preview": item.text_preview,
                }
            )

    trigger_summaries.sort(
        key=lambda item: (
            -float(item["total_score"]),
            -int(item["response_count"]),
            item["trigger_name"],
        )
    )
    summary_rows.sort(
        key=lambda item: (
            -float(item["total_score"]),
            -int(item["response_count"]),
            item["trigger_name"],
        )
    )
    return {
        "generated_at": get_current_time(),
        "score_rules": dict(SCOPE_SCORES),
        "triggers": trigger_summaries,
        "summary_rows": summary_rows,
        "creator_rows": creator_rows,
        "response_rows": response_detail_rows,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _format_time(timestamp: int) -> str:
    if timestamp <= 0:
        return ""
    return arrow.get(timestamp).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")


def _build_html(payload: dict[str, Any], *, summary_only: bool) -> str:
    decoration_url = _decoration_image_url()
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>Wordbank Trigger Analysis</title>",
        "<style>",
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #2c3440; background: #f7f9fc; line-height: 1.5; position: relative; }",  # noqa: E501
        "h1, h2, h3 { margin: 0 0 12px; }",
        "p { margin: 8px 0 12px; }",
        "table { border-collapse: collapse; width: 100%; background: rgba(255,255,255,0.7); margin: 12px 0 20px; }",  # noqa: E501
        "th, td { border: 1px solid #e4e8f1; padding: 8px 10px; text-align: left; vertical-align: top; }",  # noqa: E501
        "th { background: #edf3fb; color: #617796; }",
        ".page-head { margin-bottom: 24px; padding-right: 240px; position: relative; z-index: 1; }",  # noqa: E501
        ".page-head p { color: #748297; }",
        ".page-deco { position: fixed; top: 12px; right: 24px; width: 188px; max-width: 18vw; opacity: 0.92; pointer-events: none; user-select: none; }",  # noqa: E501
        ".trigger-block { border-top: 1px solid #dde5f1; padding: 16px 0 4px; margin: 22px 0; }",  # noqa: E501
        ".trigger-block h2 { color: #667fa3; }",
        ".trigger-block h3 { color: #8a738e; }",
        ".trigger-meta { color: #758499; }",
        ".summary-table { margin-bottom: 28px; }",
        "ol { margin: 8px 0 20px 20px; padding: 0; }",
        "li { margin: 6px 0; }",
        ".summary-table tr:nth-child(even) td { background: rgba(241, 246, 252, 0.45); }",  # noqa: E501
        ".trigger-block table tr:nth-child(even) td { background: rgba(251, 243, 248, 0.28); }",  # noqa: E501
        "</style>",
        "</head>",
        "<body>",
        f'<img class="page-deco" src="{decoration_url}" alt="senrin decoration">',
        '<div class="page-head">',
        "<h1>Wordbank Trigger Analysis</h1>",
        f"<p>生成时间：{_format_time(int(payload['generated_at']))}</p>",
        "<p>积分规则：all_groups=1.0, current_group=0.7, self/private_only/self_in_current_group=0.5</p>",  # noqa: E501
        "</div>",
        "<h2>汇总</h2>",
        '<table class="summary-table">',
        "<tr><th>Trigger</th><th>Response 总数</th><th>创建者总数</th><th>总积分</th><th>贡献最大者</th><th>最早创建者</th></tr>",  # noqa: E501
    ]
    for row in payload["summary_rows"]:
        parts.append(
            "<tr>"
            f"<td>{row['trigger_name']}</td>"
            f"<td>{row['response_count']}</td>"
            f"<td>{row['creator_count']}</td>"
            f"<td>{row['total_score']}</td>"
            f"<td>{row['top_creator_name']} ({row['top_creator_response_count']})</td>"
            f"<td>{row['earliest_creator_name']} ({_format_time(int(row['earliest_created_at']))})</td>"  # noqa: E501
            "</tr>"
        )
    parts.append("</table>")

    for trigger in payload["triggers"]:
        parts.append('<section class="trigger-block">')
        parts.append(f"<h2>{trigger['trigger_name']}</h2>")
        parts.append(
            '<p class="trigger-meta">'
            f"trigger group 数：{trigger['trigger_group_count']} | "
            f"response 总数：{trigger['response_count']} | "
            f"创建者总数：{trigger['creator_count']} | "
            f"总积分：{trigger['total_score']}"
            "</p>"
        )
        parts.append(f"<p>创建者：{', '.join(trigger['creator_names'])}</p>")

        parts.append("<h3>按创建数量排序</h3>")
        parts.append("<table>")
        parts.append(
            "<tr><th>排名</th><th>创建者</th><th>数量</th><th>积分</th><th>最早创建</th><th>最晚创建</th><th>Scope 拆分</th></tr>"  # noqa: E501
        )
        for index, creator in enumerate(trigger["creators_by_count"], start=1):
            scope_breakdown = creator["scope_breakdown"]
            parts.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td>{creator['creator_name']} ({creator['user_id']})</td>"
                f"<td>{creator['response_count']}</td>"
                f"<td>{round(float(creator['score_total']), 2)}</td>"
                f"<td>{_format_time(int(creator['first_created_at']))}</td>"
                f"<td>{_format_time(int(creator['last_created_at']))}</td>"
                f"<td>all={scope_breakdown['all_groups']}, current={scope_breakdown['current_group']}, self={scope_breakdown['self']}, private={scope_breakdown['private_only']}, self+group={scope_breakdown['self_in_current_group']}</td>"  # noqa: E501
                "</tr>"
            )
        parts.append("</table>")

        parts.append("<h3>按最早创建时间排序</h3>")
        parts.append("<ol>")
        for creator in trigger["creators_by_first_created"]:
            parts.append(
                f"<li>{creator['creator_name']} ({creator['user_id']}) - "
                f"{_format_time(int(creator['first_created_at']))} - "
                f"{creator['response_count']} 条</li>"
            )
        parts.append("</ol>")

        if not summary_only:
            parts.append("<h3>Response 明细（按积分排序）</h3>")
            parts.append("<table>")
            parts.append(
                "<tr><th>排名</th><th>Response ID</th><th>创建者</th><th>积分</th><th>Scope</th><th>创建时间</th><th>文本预览</th></tr>"  # noqa: E501
            )
            for index, response in enumerate(trigger["responses"], start=1):
                parts.append(
                    "<tr>"
                    f"<td>{index}</td>"
                    f"<td>{response['response_item_id']}</td>"
                    f"<td>{response['creator_name']} ({response['created_by']})</td>"
                    f"<td>{response['score']}</td>"
                    f"<td>{response['scope']}</td>"
                    f"<td>{_format_time(int(response['created_at']))}</td>"
                    f"<td>{response['text_preview']}</td>"
                    "</tr>"
                )
            parts.append("</table>")
        parts.append("</section>")
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts)


async def main() -> None:
    args = parse_args()
    trigger_names = _normalize_triggers(list(args.triggers))
    output_dir = Path(args.output_dir)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

    nonebot.init()
    global wordbank_repo
    global wordbank_main_db
    global WordbankResponseItem
    global WordbankTriggerVariant
    from src.plugins.wordbank.database import wordbank_repo as loaded_wordbank_repo
    from src.plugins.wordbank.database.instances import (
        wordbank_main_db as loaded_wordbank_main_db,
    )
    from src.plugins.wordbank.database.tables import (
        WordbankResponseItem as loaded_wordbank_response_item,
    )
    from src.plugins.wordbank.database.tables import (
        WordbankTriggerVariant as loaded_wordbank_trigger_variant,
    )

    wordbank_repo = loaded_wordbank_repo
    wordbank_main_db = loaded_wordbank_main_db
    WordbankResponseItem = loaded_wordbank_response_item
    WordbankTriggerVariant = loaded_wordbank_trigger_variant

    await wordbank_repo.init_all_tables()

    groups_by_trigger, response_rows = await _load_responses(trigger_names)
    user_ids = {str(response.created_by) for _, response in response_rows}
    user_names = await _load_user_names(user_ids)
    payload = _build_payload(
        trigger_names, groups_by_trigger, response_rows, user_names
    )

    json_path = _json_path(output_dir, args.prefix)
    summary_csv_path = _summary_csv_path(output_dir, args.prefix)
    creators_csv_path = _creators_csv_path(output_dir, args.prefix)
    responses_csv_path = _responses_csv_path(output_dir, args.prefix)
    html_path = _html_path(output_dir, args.prefix)
    png_path = _png_path(output_dir, args.prefix)

    export_payload = dict(payload)
    if args.summary_only:
        export_payload = {
            **payload,
            "triggers": [
                {key: value for key, value in trigger.items() if key != "responses"}
                for trigger in payload["triggers"]
            ],
        }

    json_path.write_text(
        json.dumps(export_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(
        summary_csv_path,
        list(payload["summary_rows"]),
        [
            "trigger_name",
            "trigger_group_count",
            "response_count",
            "creator_count",
            "total_score",
            "top_creator_name",
            "top_creator_response_count",
            "earliest_creator_name",
            "earliest_created_at",
        ],
    )
    _write_csv(
        creators_csv_path,
        list(payload["creator_rows"]),
        [
            "trigger_name",
            "creator_rank_by_count",
            "user_id",
            "creator_name",
            "response_count",
            "score_total",
            "first_created_at",
            "last_created_at",
            "all_groups_count",
            "current_group_count",
            "self_count",
            "private_only_count",
            "self_in_current_group_count",
        ],
    )
    if not args.summary_only:
        _write_csv(
            responses_csv_path,
            list(payload["response_rows"]),
            [
                "trigger_name",
                "score_rank",
                "response_item_id",
                "trigger_group_id",
                "created_by",
                "creator_name",
                "score",
                "scope",
                "group_id",
                "created_at",
                "updated_at",
                "approved_by",
                "text_preview",
            ],
        )
    html_path.write_text(
        _build_html(export_payload, summary_only=args.summary_only),
        encoding="utf-8",
    )
    if args.screenshot:
        _export_screenshot(html_path, png_path)

    result = {
        "json": str(json_path),
        "summary_csv": str(summary_csv_path),
        "creators_csv": str(creators_csv_path),
        "responses_csv": "" if args.summary_only else str(responses_csv_path),
        "html": str(html_path),
        "png": str(png_path) if args.screenshot else "",
        "trigger_count": len(trigger_names),
        "response_count": len(payload["response_rows"]),
        "summary_only": bool(args.summary_only),
        "screenshot": bool(args.screenshot),
    }
    sys.stdout.write(f"{json.dumps(result, ensure_ascii=False, indent=2)}\n")


if __name__ == "__main__":
    asyncio.run(main())
