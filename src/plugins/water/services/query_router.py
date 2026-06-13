"""水王统一查询路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

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
from src.plugins.water.services.rank_query import water_rank_query_service
from src.plugins.water.services.rank_season import season_rank_service
from src.plugins.water.services.rank_types import (
    LEGACY_RANK_TOKENS,
    PERIOD_LABELS,
    PERIOD_TOKENS,
    SCOPE_LABELS,
    SCOPE_TOKENS,
    SUBJECT_LABELS,
    SUBJECT_TOKENS,
    WaterRankPeriod,
    WaterRankQuerySpec,
    WaterRankScope,
    WaterRankSubject,
    is_valid_rank_combo,
    suggest_scope_for_subject,
)
from src.plugins.water.services.season import SeasonLookupAmbiguous, season_service

WaterSubject = Literal["personal", "group", "matrix"]
WaterScopeType = Literal["activity", "history", "rank"]
WaterView = Literal[
    "overview",
    "score",
    "rank",
    "achievement",
    "profile",
    "ops",
    "menu",
]
WaterMode = Literal["simple", "full"]


@dataclass(frozen=True)
class WaterQuerySpec:
    subject: WaterSubject
    scope_type: WaterScopeType
    scope_value: str
    view: WaterView
    mode: WaterMode
    rank_spec: WaterRankQuerySpec | None = None
    errors: tuple[str, ...] = ()


class WaterQueryRouter:
    _CANCEL_TOKENS: tuple[str, ...] = ("取消", "退出", "算了", "q", "quit", "exit")

    @staticmethod
    def _subject_choices_text() -> str:
        return " / ".join(
            SUBJECT_LABELS[subject] for subject in ("user", "group", "matrix")
        )

    @staticmethod
    def _period_choices_text() -> str:
        return " / ".join(
            PERIOD_LABELS[period]
            for period in ("day", "week", "month", "season", "year", "total")
        )

    @staticmethod
    def should_send_working(spec: WaterQuerySpec) -> bool:
        if spec.scope_type == "activity":
            return True
        if spec.view in {"achievement", "profile"}:
            return True
        if spec.scope_type == "rank":
            return spec.rank_spec is not None and not spec.errors
        return False

    def parse(self, raw_text: str) -> WaterQuerySpec:
        text = raw_text.strip()
        tokens = text.split()
        if not tokens:
            return WaterQuerySpec(
                subject="personal",
                scope_type="rank",
                scope_value="menu",
                view="menu",
                mode="simple",
            )

        if self._looks_like_legacy_rank(tokens):
            return WaterQuerySpec(
                subject="personal",
                scope_type="rank",
                scope_value="legacy",
                view="menu",
                mode="simple",
                errors=("legacy_rank",),
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

        rank_spec, errors = self._parse_rank_spec(tokens)
        if rank_spec is not None:
            return WaterQuerySpec(
                subject="personal",
                scope_type="rank",
                scope_value=rank_spec.period,
                view="rank",
                mode="simple",
                rank_spec=rank_spec,
                errors=errors,
            )

        return WaterQuerySpec(
            subject="personal",
            scope_type="rank",
            scope_value="invalid",
            view="menu",
            mode="simple",
            errors=errors or ("invalid_rank",),
        )

    @classmethod
    def is_guided_cancel(cls, raw_text: str) -> bool:
        return raw_text.strip().casefold() in {
            token.casefold() for token in cls._CANCEL_TOKENS
        }

    @staticmethod
    def build_guided_footer(locale: LocaleCode) -> str:
        return tr(locale, "water.query.rank.guided.footer")

    def build_guided_intro(self, locale: LocaleCode) -> str:
        return "\n".join(
            [
                tr(
                    locale,
                    "water.query.rank.guided.subject_prompt",
                    choices=self._subject_choices_text(),
                ),
                self.build_guided_footer(locale),
            ]
        )

    def build_scope_prompt(
        self,
        locale: LocaleCode,
        subject: WaterRankSubject,
    ) -> str:
        labels = " / ".join(
            SCOPE_LABELS[scope] for scope in self.valid_scopes_for_subject(subject)
        )
        return "\n".join(
            [
                tr(locale, "water.query.rank.guided.scope_prompt", choices=labels),
                self.build_guided_footer(locale),
            ]
        )

    def build_period_prompt(self, locale: LocaleCode) -> str:
        return "\n".join(
            [
                tr(
                    locale,
                    "water.query.rank.guided.period_prompt",
                    choices=self._period_choices_text(),
                ),
                self.build_guided_footer(locale),
            ]
        )

    @staticmethod
    def build_guided_cancel_message(locale: LocaleCode) -> str:
        return tr(locale, "interaction.cancelled")

    @staticmethod
    def build_guided_summary(
        locale: LocaleCode,
        spec: WaterRankQuerySpec,
    ) -> str:
        return tr(
            locale,
            "water.query.rank.guided.summary",
            subject=SUBJECT_LABELS[spec.subject],
            scope=SCOPE_LABELS[spec.scope],
            period=PERIOD_LABELS[spec.period],
        )

    @staticmethod
    def parse_subject_choice(raw_text: str) -> WaterRankSubject | None:
        return SUBJECT_TOKENS.get(raw_text.strip())

    @staticmethod
    def parse_scope_choice(raw_text: str) -> WaterRankScope | None:
        return SCOPE_TOKENS.get(raw_text.strip())

    @staticmethod
    def parse_period_choice(raw_text: str) -> WaterRankPeriod | None:
        return PERIOD_TOKENS.get(raw_text.strip())

    @staticmethod
    def valid_scopes_for_subject(
        subject: WaterRankSubject,
    ) -> tuple[WaterRankScope, ...]:
        from src.plugins.water.services.rank_types import VALID_SCOPES_BY_SUBJECT

        return VALID_SCOPES_BY_SUBJECT[subject]

    def build_subject_retry_prompt(self, locale: LocaleCode) -> str:
        return "\n".join(
            [
                tr(
                    locale,
                    "water.query.rank.guided.subject_invalid",
                    choices=self._subject_choices_text(),
                ),
                self.build_guided_footer(locale),
            ]
        )

    def build_scope_retry_prompt(
        self,
        locale: LocaleCode,
        subject: WaterRankSubject,
        scope: WaterRankScope | None = None,
    ) -> str:
        lines = []
        if scope is not None and not is_valid_rank_combo(subject, scope):
            suggested_scope = suggest_scope_for_subject(subject)
            suggestion = (
                f"#水王 {SUBJECT_LABELS[subject]} "
                f"{SCOPE_LABELS[suggested_scope]} <时间>"
            )
            lines.append(
                tr(
                    locale,
                    "water.query.rank.error.invalid_combo",
                    suggestion=suggestion,
                )
            )
        else:
            lines.append(tr(locale, "water.query.rank.guided.scope_invalid"))
        lines.append(self.build_scope_prompt(locale, subject))
        return "\n".join(lines)

    def build_period_retry_prompt(self, locale: LocaleCode) -> str:
        return "\n".join(
            [
                tr(
                    locale,
                    "water.query.rank.guided.period_invalid",
                    choices=self._period_choices_text(),
                ),
                self.build_guided_footer(locale),
            ]
        )

    def _parse_rank_spec(
        self,
        tokens: list[str],
    ) -> tuple[WaterRankQuerySpec | None, tuple[str, ...]]:
        subject: WaterRankSubject | None = None
        scope: WaterRankScope | None = None
        period: WaterRankPeriod | None = None
        unknown_tokens: list[str] = []

        for token in tokens:
            if token in SUBJECT_TOKENS:
                if subject is not None and subject != SUBJECT_TOKENS[token]:
                    return None, ("duplicate_subject",)
                subject = SUBJECT_TOKENS[token]
                continue
            if token in SCOPE_TOKENS:
                if scope is not None and scope != SCOPE_TOKENS[token]:
                    return None, ("duplicate_scope",)
                scope = SCOPE_TOKENS[token]
                continue
            if token in PERIOD_TOKENS:
                if period is not None and period != PERIOD_TOKENS[token]:
                    return None, ("duplicate_period",)
                period = PERIOD_TOKENS[token]
                continue
            unknown_tokens.append(token)

        if unknown_tokens:
            return None, ("unknown_tokens", *unknown_tokens)

        missing: list[str] = []
        if subject is None:
            missing.append("subject")
        if scope is None:
            missing.append("scope")
        if period is None:
            missing.append("period")
        if missing:
            return None, ("missing_dimensions", *missing)

        subject = cast(WaterRankSubject, subject)
        scope = cast(WaterRankScope, scope)
        period = cast(WaterRankPeriod, period)

        if not is_valid_rank_combo(subject, scope):
            return (
                WaterRankQuerySpec(subject=subject, scope=scope, period=period),
                ("invalid_combo",),
            )
        return (
            WaterRankQuerySpec(subject=subject, scope=scope, period=period),
            (),
        )

    @staticmethod
    def _looks_like_legacy_rank(tokens: list[str]) -> bool:
        return any(token in LEGACY_RANK_TOKENS for token in tokens) and not any(
            token in SUBJECT_TOKENS or token in SCOPE_TOKENS for token in tokens
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
            return await self.build_profile_message(
                user_id=user_id,
                group_id=group_id,
                locale=locale,
                mode=spec.mode,
            )
        if spec.scope_type == "rank":
            if spec.rank_spec is None:
                return Message(self.build_rank_menu(locale, errors=spec.errors))
            if spec.errors:
                return Message(
                    self.build_rank_menu(
                        locale,
                        spec.rank_spec,
                        spec.errors,
                    )
                )
            return await water_rank_query_service.build_rank_message(
                subject=spec.rank_spec.subject,
                scope=spec.rank_spec.scope,
                period=spec.rank_spec.period,
                group_id=group_id,
                locale=locale,
            )
        return Message(tr(locale, "water.query.unsupported"))

    async def build_profile_message(
        self,
        *,
        user_id: str,
        group_id: str,
        locale: LocaleCode,
        mode: WaterMode = "simple",
        include_group_history_ranks: bool = False,
    ) -> Message:
        profile_data = await profile_service.build_profile_data(
            user_id=user_id,
            group_id=group_id,
            include_group_history_ranks=include_group_history_ranks,
        )
        if profile_data is None:
            return Message(tr(locale, "water.query.profile_not_enough"))
        if mode == "full":
            card = await build_my_water_image(profile_data, locale)
        else:
            card = await build_my_water_simple_image(profile_data, locale)
            if not card:
                card = await build_my_water_image(profile_data, locale)
        if card:
            return Message(MessageSegment.image(card))
        return Message(await build_my_water_fallback_text(profile_data, locale))

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

    def build_rank_menu(
        self,
        locale: LocaleCode,
        spec: WaterRankQuerySpec | None = None,
        errors: tuple[str, ...] = (),
    ) -> str:
        lines = [
            tr(locale, "water.query.rank.menu.intro"),
            tr(locale, "water.query.rank.menu.format"),
            tr(locale, "water.query.rank.menu.examples"),
            tr(locale, "water.query.rank.menu.example.user"),
            tr(locale, "water.query.rank.menu.example.group"),
            tr(locale, "water.query.rank.menu.example.matrix"),
            "",
            tr(locale, "water.query.rank.menu.legal"),
            tr(locale, "water.query.rank.menu.legal.user"),
            tr(locale, "water.query.rank.menu.legal.group"),
            tr(locale, "water.query.rank.menu.legal.matrix"),
        ]
        if errors:
            lines.insert(0, self._build_rank_error_text(locale, spec, errors))
            lines.insert(1, "")
        return "\n".join(lines)

    def _build_rank_error_text(
        self,
        locale: LocaleCode,
        spec: WaterRankQuerySpec | None,
        errors: tuple[str, ...],
    ) -> str:
        head = errors[0]
        if head == "legacy_rank":
            return tr(
                locale,
                "water.query.rank.error.legacy",
                example="#水王 用户榜 本群 周榜",
            )
        if head == "missing_dimensions":
            missing_labels = {
                "subject": "主体",
                "scope": "范围",
                "period": "时间",
            }
            missing = "、".join(missing_labels[item] for item in errors[1:])
            return tr(
                locale,
                "water.query.rank.error.missing_dimensions",
                dimensions=missing,
            )
        if head == "unknown_tokens":
            return tr(
                locale,
                "water.query.rank.error.unknown_tokens",
                tokens=" ".join(errors[1:]),
            )
        if head == "duplicate_subject":
            return tr(
                locale,
                "water.query.rank.error.duplicate_subject",
            )
        if head == "duplicate_scope":
            return tr(
                locale,
                "water.query.rank.error.duplicate_scope",
            )
        if head == "duplicate_period":
            return tr(
                locale,
                "water.query.rank.error.duplicate_period",
            )
        if head == "invalid_combo" and spec is not None:
            suggested_scope = suggest_scope_for_subject(spec.subject)
            suggestion = (
                f"#水王 {SUBJECT_LABELS[spec.subject]} "
                f"{SCOPE_LABELS[suggested_scope]} {PERIOD_LABELS[spec.period]}"
            )
            return tr(
                locale,
                "water.query.rank.error.invalid_combo",
                suggestion=suggestion,
            )
        return tr(locale, "water.query.rank.error.invalid")

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
