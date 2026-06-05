from unittest.mock import AsyncMock

import pytest

from src.plugins.water.database.repo import WaterActivitySeasonRecord
from src.plugins.water.services.season import (
    SeasonCreateInput,
    SeasonLookupAmbiguous,
    SeasonService,
)


@pytest.mark.asyncio
async def test_create_publish_archive_delete_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SeasonService()
    created = WaterActivitySeasonRecord(
        season_id="spring_2026",
        name="2026 春日特别季",
        normalized_name="2026 春日特别季",
        description="",
        start_date=20260301,
        end_date=20260331,
        status="draft",
        published_at=None,
        created_by="",
        created_at=1,
        updated_at=1,
    )

    from src.plugins.water.services import season as season_module

    published = WaterActivitySeasonRecord(**{**created.__dict__, "status": "published"})
    archived = WaterActivitySeasonRecord(**{**created.__dict__, "status": "archived"})

    monkeypatch.setattr(
        season_module.water_repo,
        "get_activity_season",
        AsyncMock(
            side_effect=[
                None,
                created,
                created,
                published,
                published,
                archived,
                created,
            ]
        ),
    )
    monkeypatch.setattr(
        season_module.water_repo, "create_activity_season", AsyncMock(return_value=1)
    )
    monkeypatch.setattr(
        season_module.water_repo, "update_activity_season", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        season_module.water_repo, "delete_activity_season", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(season_module, "get_current_time", lambda: 1)

    draft = await service.create(
        SeasonCreateInput(
            season_id="spring_2026",
            start_date=20260301,
            end_date=20260331,
            name="2026 春日特别季",
        )
    )
    assert draft.season_id == "spring_2026"

    published_result = await service.publish("spring_2026")
    assert published_result.status == "published"

    archived_result = await service.archive("spring_2026")
    assert archived_result.status == "archived"

    deleted = await service.delete_draft("spring_2026")
    assert deleted is True


@pytest.mark.asyncio
async def test_resolve_one_or_many_by_id_name_and_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SeasonService()
    item_a = WaterActivitySeasonRecord(
        season_id="newyear_2026",
        name="2026 新年特别季",
        normalized_name="2026 新年特别季".casefold(),
        description="",
        start_date=20260101,
        end_date=20260115,
        status="published",
        published_at=1,
        created_by="1",
        created_at=1,
        updated_at=1,
    )
    item_b = WaterActivitySeasonRecord(
        season_id="newyear_2026_b",
        name="2026 新年庆典季",
        normalized_name="2026 新年庆典季".casefold(),
        description="",
        start_date=20260116,
        end_date=20260131,
        status="published",
        published_at=1,
        created_by="1",
        created_at=1,
        updated_at=1,
    )

    from src.plugins.water.services import season as season_module

    monkeypatch.setattr(
        season_module.water_repo, "get_activity_season", AsyncMock(return_value=item_a)
    )
    result = await service.resolve_one_or_many("newyear_2026")
    assert isinstance(result, list)
    assert result[0].season_id == "newyear_2026"

    monkeypatch.setattr(
        season_module.water_repo, "get_activity_season", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        season_module.water_repo,
        "search_published_activity_seasons",
        AsyncMock(return_value=[item_a]),
    )
    normalized = await service.resolve_one_or_many("2026　新年特别季")
    assert isinstance(normalized, list)
    assert normalized[0].season_id == "newyear_2026"

    monkeypatch.setattr(
        season_module.water_repo,
        "search_published_activity_seasons",
        AsyncMock(return_value=[item_a, item_b]),
    )
    ambiguous = await service.resolve_one_or_many("2026 新年")
    assert isinstance(ambiguous, SeasonLookupAmbiguous)
    assert len(ambiguous.candidates) == 2


@pytest.mark.asyncio
async def test_resolve_current_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SeasonService()

    from src.plugins.water.services import season as season_module

    monkeypatch.setattr(
        season_module.water_repo,
        "list_current_activity_seasons",
        AsyncMock(
            return_value=[
                WaterActivitySeasonRecord(
                    season_id="a",
                    name="A",
                    normalized_name="a",
                    description="",
                    start_date=20260501,
                    end_date=20260531,
                    status="published",
                    published_at=1,
                    created_by="1",
                    created_at=1,
                    updated_at=1,
                ),
                WaterActivitySeasonRecord(
                    season_id="b",
                    name="B",
                    normalized_name="b",
                    description="",
                    start_date=20260515,
                    end_date=20260615,
                    status="published",
                    published_at=1,
                    created_by="1",
                    created_at=1,
                    updated_at=1,
                ),
            ]
        ),
    )
    monkeypatch.setattr(service, "today_record_date", lambda: 20260524)

    current = await service.resolve_one_or_many("当前")

    assert isinstance(current, list)
    assert [item.season_id for item in current] == ["a", "b"]
