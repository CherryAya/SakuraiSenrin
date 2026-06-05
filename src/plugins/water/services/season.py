"""活动赛季服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import arrow

from src.lib.utils.common import get_current_time
from src.plugins.water.database import water_repo
from src.plugins.water.database.repo import WaterActivitySeasonRecord

SeasonStatus = Literal["draft", "published", "archived"]


@dataclass(frozen=True)
class SeasonLookupAmbiguous:
    keyword: str
    candidates: list[WaterActivitySeasonRecord]


@dataclass(frozen=True)
class SeasonCreateInput:
    season_id: str
    start_date: int
    end_date: int
    name: str
    description: str = ""
    created_by: str = ""


class SeasonService:
    def normalize_name(self, name: str) -> str:
        return water_repo.normalize_season_name(name)

    def today_record_date(self) -> int:
        return int(arrow.get(get_current_time()).format("YYYYMMDD"))

    async def create(self, data: SeasonCreateInput) -> WaterActivitySeasonRecord:
        existing = await water_repo.get_activity_season(data.season_id)
        if existing is not None:
            raise ValueError(f"season_id 已存在: {data.season_id}")
        if data.start_date > data.end_date:
            raise ValueError("开始日期不能晚于结束日期")

        now_ts = get_current_time()
        await water_repo.create_activity_season(
            {
                "season_id": data.season_id,
                "name": data.name,
                "normalized_name": self.normalize_name(data.name),
                "description": data.description,
                "start_date": data.start_date,
                "end_date": data.end_date,
                "status": "draft",
                "published_at": None,
                "created_by": data.created_by,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        created = await water_repo.get_activity_season(data.season_id)
        if created is None:
            raise RuntimeError("赛季创建后读取失败")
        return created

    async def publish(self, season_id: str) -> WaterActivitySeasonRecord:
        await self.require(season_id)
        now_ts = get_current_time()
        ok = await water_repo.update_activity_season(
            season_id,
            status="published",
            published_at=now_ts,
            updated_at=now_ts,
        )
        if not ok:
            raise RuntimeError("赛季发布失败")
        published = await water_repo.get_activity_season(season_id)
        if published is None:
            raise RuntimeError("赛季发布后读取失败")
        return published

    async def archive(self, season_id: str) -> WaterActivitySeasonRecord:
        await self.require(season_id)
        now_ts = get_current_time()
        ok = await water_repo.update_activity_season(
            season_id,
            status="archived",
            updated_at=now_ts,
        )
        if not ok:
            raise RuntimeError("赛季归档失败")
        archived = await water_repo.get_activity_season(season_id)
        if archived is None:
            raise RuntimeError("赛季归档后读取失败")
        return archived

    async def delete_draft(self, season_id: str) -> bool:
        season = await self.require(season_id)
        if season.status != "draft":
            raise ValueError("仅 draft 赛季允许删除")
        return await water_repo.delete_activity_season(season_id)

    async def require(self, season_id: str) -> WaterActivitySeasonRecord:
        season = await water_repo.get_activity_season(season_id)
        if season is None:
            raise ValueError(f"赛季不存在: {season_id}")
        return season

    async def list(
        self, statuses: list[SeasonStatus] | None = None
    ) -> list[WaterActivitySeasonRecord]:
        return await water_repo.list_activity_seasons(
            list(statuses) if statuses is not None else None
        )

    async def list_current(self) -> list[WaterActivitySeasonRecord]:
        return await water_repo.list_current_activity_seasons(self.today_record_date())

    async def resolve_current(self) -> list[WaterActivitySeasonRecord]:
        return await self.list_current()

    async def resolve_one_or_many(
        self,
        keyword: str,
    ) -> list[WaterActivitySeasonRecord] | SeasonLookupAmbiguous:
        text = keyword.strip()
        if text == "当前":
            return await self.resolve_current()

        by_id = await water_repo.get_activity_season(text)
        if by_id is not None and by_id.status == "published":
            return [by_id]

        normalized = self.normalize_name(text)
        candidates = await water_repo.search_published_activity_seasons(normalized)
        exact_name = [item for item in candidates if item.name == text]
        if exact_name:
            return exact_name
        exact_normalized = [
            item for item in candidates if item.normalized_name == normalized
        ]
        if exact_normalized:
            return exact_normalized
        if len(candidates) == 1:
            return candidates
        return SeasonLookupAmbiguous(keyword=text, candidates=candidates)


season_service = SeasonService()
