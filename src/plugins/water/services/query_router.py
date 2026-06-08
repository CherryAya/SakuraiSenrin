"""水王统一查询路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.water.img import (
    build_my_water_fallback_text,
    build_my_water_image,
    build_my_water_simple_image,
)
from src.plugins.water.renderers import render_season_list
from src.plugins.water.services.profile import profile_service
from src.plugins.water.services.rank_absolute import absolute_rank_service
from src.plugins.water.services.rank_season import season_rank_service
from src.plugins.water.services.season import SeasonLookupAmbiguous, season_service

WaterSubject = Literal["personal", "group", "matrix"]
WaterScopeType = Literal["absolute", "activity", "history"]
WaterView = Literal["overview", "score", "rank", "achievement", "profile", "ops"]
WaterMode = Literal["simple", "full"]


@dataclass(frozen=True)
class WaterQuerySpec:
    subject: WaterSubject
    scope_type: WaterScopeType
    scope_value: str
    view: WaterView
    mode: WaterMode


class WaterQueryRouter:
    def parse(self, raw_text: str) -> WaterQuerySpec:
        text = raw_text.strip()
        tokens = text.split()
        if not tokens:
            return WaterQuerySpec(
                subject="personal",
                scope_type="history",
                scope_value="all",
                view="profile",
                mode="simple",
            )

        if tokens[0] == "赛季":
            return self._parse_season(tokens)

        joined = "".join(tokens)
        if joined in {"完整", "详细", "完整版"}:
            return WaterQuerySpec(
                subject="personal",
                scope_type="history",
                scope_value="all",
                view="profile",
                mode="full",
            )
        if joined in {"成就"}:
            return WaterQuerySpec(
                subject="personal",
                scope_type="history",
                scope_value="all",
                view="achievement",
                mode="simple",
            )
        scope_map = {
            "日榜": "day",
            "月榜": "month",
            "季榜": "season",
            "年榜": "year",
            "总榜": "total",
        }
        if joined in scope_map:
            return WaterQuerySpec(
                subject="group" if joined != "总榜" else "personal",
                scope_type="absolute",
                scope_value=scope_map[joined],
                view="rank",
                mode="simple",
            )
        return WaterQuerySpec(
            subject="personal",
            scope_type="history",
            scope_value="all",
            view="profile",
            mode="simple",
        )

    def _parse_season(self, tokens: list[str]) -> WaterQuerySpec:
        if len(tokens) == 1:
            return WaterQuerySpec(
                subject="personal",
                scope_type="activity",
                scope_value="当前",
                view="overview",
                mode="simple",
            )
        second = tokens[1]
        if second in {"列表"}:
            return WaterQuerySpec(
                subject="personal",
                scope_type="activity",
                scope_value="列表",
                view="overview",
                mode="simple",
            )
        if second in {"当前列表"}:
            return WaterQuerySpec(
                subject="personal",
                scope_type="activity",
                scope_value="当前列表",
                view="overview",
                mode="simple",
            )

        scope_value = second
        subject = "personal"
        view: WaterView = "overview"
        for token in tokens[2:]:
            if token in {"个人"}:
                subject = "personal"
            elif token in {"群聊"}:
                subject = "group"
            elif token in {"矩阵"}:
                subject = "matrix"
            elif token in {"概览"}:
                view = "overview"
            elif token in {"积分"}:
                view = "score"
            elif token in {"排名"}:
                view = "rank"
            elif token in {"成就"}:
                view = "achievement"
        return WaterQuerySpec(
            subject=subject,
            scope_type="activity",
            scope_value=scope_value,
            view=view,
            mode="simple",
        )

    async def execute(
        self,
        *,
        spec: WaterQuerySpec,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> Message:
        if spec.scope_type == "activity":
            return await self._execute_activity(
                spec=spec,
                user_id=user_id,
                group_id=group_id,
                locale=locale,
            )
        if spec.view == "achievement":
            from src.plugins.water.services.achievement import achievement_service

            text = await achievement_service.build_user_achievement_message(
                user_id=user_id,
                matrix_id=await self._matrix_id(group_id),
                record_date=season_service.today_record_date(),
                locale=locale,
            )
            return Message(text)
        if spec.view == "profile":
            profile_data = await profile_service.build_profile_data(
                user_id=user_id,
                group_id=group_id,
            )
            if profile_data is None:
                return Message(tr(locale, "water.query.profile_not_enough"))
            if spec.mode == "full":
                card = await build_my_water_image(profile_data, locale)
            else:
                card = await build_my_water_simple_image(profile_data, locale)
                if not card:
                    card = await build_my_water_image(profile_data, locale)
            if card:
                return Message(MessageSegment.image(card))
            return Message(await build_my_water_fallback_text(profile_data, locale))
        if spec.scope_value == "day":
            return await absolute_rank_service.build_group_day_rank(group_id, locale)
        if spec.scope_value == "month":
            return await absolute_rank_service.build_period_rank("month", locale)
        if spec.scope_value == "season":
            return await absolute_rank_service.build_period_rank("season", locale)
        if spec.scope_value == "year":
            return await absolute_rank_service.build_period_rank("year", locale)
        if spec.scope_value == "total":
            return await absolute_rank_service.build_total_rank(locale)
        return Message(tr(locale, "water.query.unsupported"))

    async def _execute_activity(
        self,
        *,
        spec: WaterQuerySpec,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
    ) -> Message:
        if spec.scope_value == "列表":
            seasons = await season_service.list(["published"])
            return Message(
                render_season_list(
                    tr(locale, "water.query.season_list.published"),
                    seasons,
                    locale,
                )
            )
        if spec.scope_value == "当前列表":
            seasons = await season_service.list_current()
            return Message(
                render_season_list(
                    tr(locale, "water.query.season_list.current"),
                    seasons,
                    locale,
                )
            )

        resolved = await season_service.resolve_one_or_many(spec.scope_value)
        if isinstance(resolved, SeasonLookupAmbiguous):
            if not resolved.candidates:
                return Message(tr(locale, "water.query.season_not_found"))
            return Message(
                tr(
                    locale,
                    "water.query.season_ambiguous",
                    items="\n".join(
                        f"- {item.season_id} | {item.name} | "
                        f"{item.start_date}~{item.end_date}"
                        for item in resolved.candidates
                    ),
                )
            )
        if not resolved:
            return Message(tr(locale, "water.query.season_empty"))

        messages: list[str] = []
        if len(resolved) > 1:
            messages.append(
                render_season_list(
                    tr(locale, "water.query.season_list.current"),
                    resolved,
                    locale,
                )
            )
        for season in resolved:
            messages.append(
                await season_rank_service.build_message(
                    season=season,
                    subject=spec.subject,
                    view=self._season_view(spec.view),
                    user_id=user_id,
                    group_id=group_id,
                    locale=locale,
                )
            )
        return Message("\n\n".join(messages))

    @staticmethod
    async def _matrix_id(group_id: str) -> str:
        from src.plugins.water.database import water_repo

        return await water_repo.get_or_create_group_matrix_id(group_id)

    @staticmethod
    def _season_view(
        view: WaterView,
    ) -> Literal["overview", "score", "rank", "achievement"]:
        if view == "score":
            return "score"
        if view == "rank":
            return "rank"
        if view == "achievement":
            return "achievement"
        return "overview"


water_query_router = WaterQueryRouter()
