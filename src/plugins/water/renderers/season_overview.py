"""活动赛季文本渲染。"""

from __future__ import annotations

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.database.repo import WaterActivitySeasonRecord


def _season_window_text(season: WaterActivitySeasonRecord) -> str:
    return f"{season.start_date} ~ {season.end_date}"


def render_season_list(
    title: str,
    seasons: list[WaterActivitySeasonRecord],
    locale: LocaleCode,
) -> str:
    lines = [title]
    if not seasons:
        lines.append(tr(locale, "water.query.season_list.empty"))
        return "\n".join(lines)
    for season in seasons:
        lines.append(
            tr(
                locale,
                "water.query.season.status_line",
                season_id=season.season_id,
                name=season.name,
                window_text=_season_window_text(season),
                status=season.status,
            )
        )
    return "\n".join(lines)


def render_season_overview(
    season: WaterActivitySeasonRecord,
    body_lines: list[str],
    locale: LocaleCode,
) -> str:
    lines = [
        tr(locale, "water.query.season.overview.title", name=season.name),
        tr(locale, "water.query.season.overview.id", season_id=season.season_id),
        tr(
            locale,
            "water.query.season.overview.time",
            window_text=_season_window_text(season),
        ),
    ]
    if season.description:
        lines.append(
            tr(
                locale,
                "water.query.season.overview.description",
                description=season.description,
            )
        )
    lines.extend(body_lines)
    return "\n".join(lines)
