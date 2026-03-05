"""Render mock profile cards for water plugin UI review.

Usage:
  uv run python scripts/water/render_mock_profile.py
  uv run python scripts/water/render_mock_profile.py --scenario active_6_of_10
  uv run python scripts/water/render_mock_profile.py --out-dir /tmp/water-mock
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

import arrow
import nonebot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

nonebot.init()

from src.logger import logger
from src.plugins.water.img import WaterProfileCardData, build_my_water_image
from src.plugins.water.services.achievement import AchievementService

ScenarioName = str


def _ts(date_text: str) -> int:
    return int(arrow.get(date_text, "YYYY-MM-DD").timestamp())


def _base_items() -> list[tuple[str, str, str, int]]:
    return [
        ("FIRST_BLOOD", "permanent", "", _ts("2023-04-08")),
        ("MATRIX_PIONEER", "permanent", "", _ts("2024-09-17")),
        ("NIGHT_OWL", "seasonal", "2025S1", _ts("2025-02-20")),
        ("STEADY_COMPANION", "seasonal", "2025S2", _ts("2025-05-16")),
        ("SEASON_CLOCKWORK", "seasonal", "2025S3", _ts("2025-08-12")),
        ("SEASON_GOLDEN_WAVE", "seasonal", "2025S4", _ts("2025-11-25")),
    ]


def _current_season_items(
    season_id: str,
    unlocked_count: int,
) -> list[tuple[str, str, str, int]]:
    pool = [
        "NIGHT_OWL",
        "STEADY_COMPANION",
        "SEASON_CLOCKWORK",
        "SEASON_GOLDEN_WAVE",
        "SEASON_DAWN_RUNNER",
        "SEASON_STORM_CHASER",
        "SEASON_IRON_HEART",
        "SEASON_BREAKPOINT",
        "SEASON_MILESTONE",
        "SEASON_FOCUS_MASTER",
    ]
    normalized_count = max(0, unlocked_count)
    selected = pool[:normalized_count]
    if normalized_count > len(pool):
        selected.extend(
            [
                f"SEASON_EXTRA_{idx:02d}"
                for idx in range(len(pool) + 1, normalized_count + 1)
            ]
        )
    items: list[tuple[str, str, str, int]] = []
    for idx, achievement_id in enumerate(selected):
        day = (idx * 2) % 28 + 1
        items.append(
            (
                achievement_id,
                "seasonal",
                season_id,
                _ts(f"2026-02-{day:02d}"),
            )
        )
    return items


def _build_profile_data(scenario: ScenarioName) -> WaterProfileCardData:
    season_id = AchievementService.current_season_id()
    common = {
        "user_id": "1479559098",
        "group_id": "123456789",
        "matrix_id": "mtx_a1b2c3d4",
        "group_name": "樱花研发总群",
        "matrix_groups": [
            ("123456789", "樱花研发总群"),
            ("223456789", "樱花研发二群"),
            ("323456789", "樱花研发三群"),
            ("423456789", "樱花外包协同群"),
        ],
    }

    if scenario == "fresh_0_of_10":
        achievement_items = [("FIRST_BLOOD", "permanent", "", _ts("2026-02-03"))]
        return WaterProfileCardData(
            **common,
            username="刚入群的新朋友",
            global_level=(820, 820, 2),
            matrix_level=(620, 620, 2),
            global_rank=283,
            group_user_rank=57,
            matrix_user_rank=144,
            matrix_rank=63,
            group_rank=120,
            matrix_total_level=(632000, 632000, 17),
            achievement_items=achievement_items,
        )

    if scenario == "active_6_of_10":
        achievement_items = _base_items() + _current_season_items(season_id, 6)
        return WaterProfileCardData(
            **common,
            username="三年老水友·凛凛的头号捧场王",
            global_level=(242300, 31800, 49),
            matrix_level=(136800, 17600, 36),
            global_rank=15,
            group_user_rank=3,
            matrix_user_rank=4,
            matrix_rank=11,
            group_rank=27,
            matrix_total_level=(3982000, 622000, 44),
            achievement_items=achievement_items,
        )

    if scenario.startswith("season_") and scenario.endswith("_of_10"):
        unlocked_raw = scenario.removeprefix("season_").removesuffix("_of_10")
        unlocked_count = max(1, int(unlocked_raw))
        achievement_items = _base_items() + _current_season_items(
            season_id, unlocked_count
        )
        # 线性抬升一些关键数值，让 1~10 的图在视觉上更有进阶感。
        global_exp = 90000 + unlocked_count * 18000
        matrix_exp = 62000 + unlocked_count * 12000
        global_season_exp = 6000 + unlocked_count * 4200
        matrix_season_exp = 4500 + unlocked_count * 3200
        matrix_total_exp = 1800000 + unlocked_count * 340000
        matrix_total_season_exp = 220000 + unlocked_count * 66000
        return WaterProfileCardData(
            **common,
            username=f"赛季冲榜选手 · {unlocked_count}/10",
            global_level=(
                global_exp,
                global_season_exp,
                max(1, 24 + unlocked_count * 3),
            ),
            matrix_level=(
                matrix_exp,
                matrix_season_exp,
                max(1, 18 + unlocked_count * 2),
            ),
            global_rank=max(1, 120 - unlocked_count * 9),
            group_user_rank=max(1, 35 - unlocked_count * 2),
            matrix_user_rank=max(1, 56 - unlocked_count * 4),
            matrix_rank=max(1, 40 - unlocked_count * 3),
            group_rank=max(1, 65 - unlocked_count * 5),
            matrix_total_level=(
                matrix_total_exp,
                matrix_total_season_exp,
                max(1, 30 + unlocked_count * 2),
            ),
            achievement_items=achievement_items,
        )

    achievement_items = _base_items() + _current_season_items(season_id, 10)
    return WaterProfileCardData(
        **common,
        username="老兵水王·冲榜永动机",
        global_level=(402100, 66200, 63),
        matrix_level=(226400, 35100, 47),
        global_rank=3,
        group_user_rank=1,
        matrix_user_rank=1,
        matrix_rank=4,
        group_rank=9,
        matrix_total_level=(6580000, 938000, 57),
        achievement_items=achievement_items,
    )


def _write_image(out_dir: str, filename: str, img: bytes) -> str:
    os.makedirs(out_dir, exist_ok=True)
    output = os.path.join(out_dir, filename)
    with open(output, "wb") as fp:
        fp.write(img)
    return output


async def _render_one(scenario: ScenarioName, out_dir: str) -> str:
    data = _build_profile_data(scenario)
    img = await build_my_water_image(data)
    if not img:
        raise RuntimeError(f"render failed for scenario={scenario}")
    filename = f"water_mock_{scenario}.png"
    return await asyncio.to_thread(_write_image, out_dir, filename, img)


async def _main(args: argparse.Namespace, out_dir: str) -> None:
    scenarios: list[ScenarioName]
    if args.scenario == "all":
        scenarios = ["fresh_0_of_10", "active_6_of_10", "veteran_10_of_10"]
    elif args.scenario == "season_1_to_10":
        scenarios = [f"season_{i}_of_10" for i in range(1, 11)]
    else:
        scenarios = [args.scenario]

    for scenario in scenarios:
        path = await _render_one(scenario, out_dir)
        logger.info(f"[water-mock] rendered: {path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render water profile mock cards.")
    parser.add_argument(
        "--scenario",
        choices=[
            "all",
            "fresh_0_of_10",
            "active_6_of_10",
            "veteran_10_of_10",
            "season_1_to_10",
        ],
        default="all",
        help="Choose one scenario or render all.",
    )
    parser.add_argument(
        "--out-dir",
        default="/tmp",
        help="Output directory for rendered png files.",
    )
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    parsed = parser.parse_args()
    out_dir = os.path.abspath(os.path.expanduser(parsed.out_dir))
    asyncio.run(_main(parsed, out_dir))
