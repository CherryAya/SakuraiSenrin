"""活动赛季文本渲染。"""

from __future__ import annotations

from src.plugins.water.database.repo import WaterActivitySeasonRecord


def _season_window_text(season: WaterActivitySeasonRecord) -> str:
    return f"{season.start_date} ~ {season.end_date}"


def render_season_list(
    title: str,
    seasons: list[WaterActivitySeasonRecord],
) -> str:
    lines = [title]
    if not seasons:
        lines.append("暂无赛季。")
        return "\n".join(lines)
    for season in seasons:
        lines.append(
            f"- {season.season_id} | {season.name} | "
            f"{_season_window_text(season)} | {season.status}"
        )
    return "\n".join(lines)


def render_season_overview(
    season: WaterActivitySeasonRecord,
    body_lines: list[str],
) -> str:
    lines = [
        f"===== 水王赛季概览 · {season.name} =====",
        f"season_id: {season.season_id}",
        f"时间: {_season_window_text(season)}",
    ]
    if season.description:
        lines.append(f"说明: {season.description}")
    lines.extend(body_lines)
    return "\n".join(lines)
