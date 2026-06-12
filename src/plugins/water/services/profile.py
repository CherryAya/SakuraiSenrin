"""画像资料组装服务。"""

from __future__ import annotations

import asyncio

from src.plugins.water.database import water_repo
from src.plugins.water.img import WaterProfileCardData
from src.services.info import resolve_group_card, resolve_group_name


class WaterProfileService:
    async def build_profile_data(
        self,
        *,
        user_id: str,
        group_id: str,
        include_group_history_ranks: bool = False,
    ) -> WaterProfileCardData | None:
        matrix_id = await water_repo.get_or_create_group_matrix_id(group_id)
        matrix_group_ids = await water_repo.get_groups_by_matrix_id(matrix_id)
        if not matrix_group_ids:
            matrix_group_ids = [group_id]
        matrix_group_names = await asyncio.gather(
            *(resolve_group_name(None, gid) for gid in matrix_group_ids)
        )
        matrix_groups = list(
            zip(
                matrix_group_ids,
                [
                    name or f"群聊_{gid[-4:]}"
                    for gid, name in zip(
                        matrix_group_ids, matrix_group_names, strict=False
                    )
                ],
                strict=False,
            )
        )
        global_level, matrix_level, matrix_total_level = await asyncio.gather(
            water_repo.get_user_global_level(user_id),
            water_repo.get_user_matrix_level(user_id, matrix_id),
            water_repo.get_matrix_total_level(matrix_id),
        )
        if global_level is None and matrix_level is None:
            return None
        (
            global_rank,
            matrix_user_rank,
            matrix_rank,
            achievement_items,
        ) = await asyncio.gather(
            water_repo.get_user_global_rank(user_id),
            water_repo.get_user_matrix_rank(user_id, matrix_id),
            water_repo.get_matrix_rank(matrix_id),
            water_repo.get_user_achievement_items(user_id),
        )
        group_user_rank: int | None = None
        group_activity_rank: int | None = None
        if include_group_history_ranks:
            group_user_rank, group_activity_rank = await asyncio.gather(
                water_repo.get_group_user_rank(group_id, user_id),
                water_repo.get_group_activity_rank(group_id),
            )
        return WaterProfileCardData(
            user_id=user_id,
            group_id=group_id,
            matrix_id=matrix_id,
            group_name=await resolve_group_name(None, group_id),
            username=await resolve_group_card(None, user_id, group_id),
            global_level=global_level,
            matrix_level=matrix_level,
            global_rank=global_rank,
            group_user_rank=group_user_rank,
            matrix_user_rank=matrix_user_rank,
            matrix_rank=matrix_rank,
            group_rank=group_activity_rank,
            matrix_total_level=matrix_total_level,
            matrix_groups=matrix_groups,
            achievement_items=achievement_items,
        )


profile_service = WaterProfileService()
