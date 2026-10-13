"""离线水王全局总榜调试板：缓存与渲染。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

import nonebot
from pil_utils import BuildImage

if TYPE_CHECKING:
    # 类型标注专用：绕开 water 包 nonebot 初始化
    from src.lib.i18n.types import LocaleCode
    from src.plugins.water.services.rank_types import (
        WaterRankPeriod,
        WaterRankScope,
        WaterRankSubject,
    )

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT_DIR = ROOT / "output" / "water-rank-debug"
# 与 LocaleCode 一致；就地声明以避开运行期导入
_LOCALE_CHOICES: tuple[LocaleCode, ...] = ("zh-CN", "lzh", "x-meme")
DEFAULT_LOCALE: LocaleCode = "zh-CN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cache and render water global total-rank debug images",
    )
    parser.add_argument(
        "--mode",
        choices=("all", "cache", "render"),
        default="all",
        help="cache data only, render from cache only, or do both",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="top-N rows to include in the leaderboard",
    )
    parser.add_argument(
        "--locale",
        default=DEFAULT_LOCALE,
        choices=_LOCALE_CHOICES,
        help="locale passed into water rank builders",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="directory for cache JSON and rendered PNG files",
    )
    parser.add_argument(
        "--cache-prefix",
        default="water-global-total-rank",
        help="cache filename prefix",
    )
    parser.add_argument(
        "--skip-avatar-hydration",
        action="store_true",
        help="render from cache without refetching QQ avatars",
    )
    return parser.parse_args()


async def _init_runtime() -> dict[str, Any]:
    nonebot.init()

    from src.plugins.water.database import water_repo
    from src.plugins.water.renderers.models import (
        WaterPeriodRankCardData,
        WaterRankCardItem,
    )
    from src.plugins.water.renderers.report import build_water_period_rank_image
    from src.plugins.water.services.rank import water_rank_service

    await water_repo.init_all_tables()
    return {
        "water_repo": water_repo,
        "WaterPeriodRankCardData": WaterPeriodRankCardData,
        "WaterRankCardItem": WaterRankCardItem,
        "build_water_period_rank_image": build_water_period_rank_image,
        "water_rank_service": water_rank_service,
    }


def _cache_path(output_dir: Path, prefix: str, subject: str) -> Path:
    return output_dir / f"{prefix}.{subject}.json"


def _image_path(output_dir: Path, prefix: str, subject: str) -> Path:
    return output_dir / f"{prefix}.{subject}.png"


def _serialize_card_data(
    data: Any,
    *,
    subject: WaterRankSubject,
    scope: WaterRankScope,
    period: WaterRankPeriod,
) -> dict[str, Any]:
    payload = asdict(data)
    for item in payload.get("top_items", []):
        item["avatar"] = None
    payload["subject"] = subject
    payload["scope"] = scope
    payload["source_period"] = period
    payload["cache_version"] = 1
    return payload


def _as_locale(value: object) -> LocaleCode:
    """命令行 locale 收窄成 LocaleCode，非法值回落默认语言。"""
    for candidate in _LOCALE_CHOICES:
        if value == candidate:
            return candidate
    return DEFAULT_LOCALE


def _as_rank_subject(value: object) -> WaterRankSubject:
    """缓存 JSON 回读的 subject 收窄成字面量，非法值回落 user。"""
    if value in ("user", "group", "matrix"):
        return value
    return "user"


async def _build_cache_payload(
    runtime: dict[str, Any],
    *,
    subject: WaterRankSubject,
    scope: WaterRankScope,
    period: WaterRankPeriod,
    locale: LocaleCode,
    limit: int,
) -> dict[str, Any]:
    water_rank_service = runtime["water_rank_service"]
    data = await water_rank_service.build_natural_period_rank_data(
        subject=subject,
        scope=scope,
        period=period,
        group_id="",
        locale=locale,
        limit=limit,
    )
    if data is None:
        raise RuntimeError(
            f"No water rank data for subject={subject} scope={scope} period={period}"
        )
    return _serialize_card_data(data, subject=subject, scope=scope, period=period)


async def _write_cache_files(
    runtime: dict[str, Any],
    *,
    output_dir: Path,
    prefix: str,
    locale: LocaleCode,
    limit: int,
) -> list[Path]:
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    cache_specs: tuple[
        tuple[WaterRankSubject, WaterRankScope, WaterRankPeriod], ...
    ] = (
        ("user", "global", "total"),
        ("group", "global", "total"),
    )
    written: list[Path] = []
    for subject, scope, period in cache_specs:
        payload = await _build_cache_payload(
            runtime,
            subject=subject,
            scope=scope,
            period=period,
            locale=locale,
            limit=limit,
        )
        path = _cache_path(output_dir, prefix, subject)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(path)
    return written


async def _hydrate_avatar(
    water_rank_service: Any,
    *,
    subject: WaterRankSubject,
    entity_id: str,
) -> BuildImage | None:
    try:
        return await water_rank_service._resolve_avatar(subject, entity_id)
    except Exception:
        return None


async def _load_card_data_from_cache(
    runtime: dict[str, Any],
    *,
    cache_file: Path,
    hydrate_avatars: bool,
) -> Any:
    payload = json.loads(
        await asyncio.to_thread(cache_file.read_text, encoding="utf-8")
    )
    WaterPeriodRankCardData = runtime["WaterPeriodRankCardData"]
    WaterRankCardItem = runtime["WaterRankCardItem"]
    water_rank_service = runtime["water_rank_service"]

    subject = _as_rank_subject(payload.get("subject"))
    top_items_payload = payload.pop("top_items", [])
    payload.pop("cache_version", None)
    payload.pop("subject", None)
    payload.pop("scope", None)
    payload.pop("source_period", None)

    avatars: list[BuildImage | None]
    if hydrate_avatars:
        avatars = await asyncio.gather(
            *(
                _hydrate_avatar(
                    water_rank_service,
                    subject=subject,
                    entity_id=str(item["entity_id"]),
                )
                for item in top_items_payload
            )
        )
    else:
        avatars = [None] * len(top_items_payload)

    top_items = [
        WaterRankCardItem(
            entity_id=str(item["entity_id"]),
            display_name=str(item["display_name"]),
            secondary_label=str(item["secondary_label"]),
            avatar=avatars[index],
            msg_count=int(item["msg_count"]),
            active_days=int(item["active_days"]),
            active_hours=int(item["active_hours"]),
            hourly_counts=[int(value) for value in item["hourly_counts"]],
            daily_msg_counts=[int(value) for value in item.get("daily_msg_counts", [])],
            current_rank=int(item["current_rank"]),
            trend=int(item["trend"]) if item["trend"] is not None else None,
            group_count=int(item.get("group_count", 0)),
        )
        for index, item in enumerate(top_items_payload)
    ]
    payload["top_items"] = top_items
    return WaterPeriodRankCardData(**payload)


async def _render_images_from_cache(
    runtime: dict[str, Any],
    *,
    output_dir: Path,
    prefix: str,
    hydrate_avatars: bool,
    locale: LocaleCode,
) -> list[Path]:
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    build_water_period_rank_image = runtime["build_water_period_rank_image"]
    rendered: list[Path] = []
    for subject in ("user", "group"):
        cache_file = _cache_path(output_dir, prefix, subject)
        if not cache_file.exists():
            raise FileNotFoundError(f"Missing cache file: {cache_file}")
        data = await _load_card_data_from_cache(
            runtime,
            cache_file=cache_file,
            hydrate_avatars=hydrate_avatars,
        )
        image = await build_water_period_rank_image(data, locale)
        if image is None:
            raise RuntimeError(f"Renderer returned empty image for {cache_file.name}")
        image_path = _image_path(output_dir, prefix, subject)
        await asyncio.to_thread(image_path.write_bytes, image)
        rendered.append(image_path)
    return rendered


async def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    # argparse 出 str，收窄成 LocaleCode
    locale: LocaleCode = _as_locale(args.locale)
    runtime = await _init_runtime()

    cache_files: list[Path] = []
    image_files: list[Path] = []

    if args.mode in {"all", "cache"}:
        cache_files = await _write_cache_files(
            runtime,
            output_dir=output_dir,
            prefix=args.cache_prefix,
            locale=locale,
            limit=max(1, int(args.limit)),
        )

    if args.mode in {"all", "render"}:
        image_files = await _render_images_from_cache(
            runtime,
            output_dir=output_dir,
            prefix=args.cache_prefix,
            hydrate_avatars=not args.skip_avatar_hydration,
            locale=locale,
        )

    result = {
        "mode": args.mode,
        "output_dir": str(output_dir),
        "cache_files": [str(path) for path in cache_files],
        "image_files": [str(path) for path in image_files],
    }
    sys.stdout.write(f"{json.dumps(result, ensure_ascii=False, indent=2)}\n")


if __name__ == "__main__":
    asyncio.run(main())
