"""Wordbank command parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import WordbankRankPeriod
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.rules import (
    RuleError,
    normalize_role_alias,
    normalize_scope_alias,
)
from src.plugins.wordbank.text_parsing import (
    TokenSpan,
    has_meaningful_text,
    join_tokens_with_original_spacing,
    rest_after_token,
    split_command_text,
    tokenize_shell_like,
)

ADD_SEPARATORS = ("=>", "->", "回答", "回复")
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 20
SEARCH_FIELD_ALIASES = {
    "all": "all",
    "a": "all",
    "全部": "all",
    "全量": "all",
    "trigger": "trigger",
    "t": "trigger",
    "触发": "trigger",
    "触发词": "trigger",
    "response": "response",
    "r": "response",
    "响应": "response",
    "响应词": "response",
}
RANK_PERIOD_ALIASES: dict[str, WordbankRankPeriod] = {
    "week": "week",
    "w": "week",
    "周": "week",
    "周榜": "week",
    "本周": "week",
    "month": "month",
    "m": "month",
    "月": "month",
    "月榜": "month",
    "本月": "month",
    "season": "season",
    "quarter": "season",
    "q": "season",
    "季": "season",
    "季榜": "season",
    "本季": "season",
    "quarterly": "season",
    "total": "total",
    "all": "total",
    "总": "total",
    "总榜": "total",
    "累计": "total",
}
_COMPACT_GROUP_VIEW_RE = re.compile(
    r"^(?P<action>详情|展开|group|grp)(?P<group_id>\d+)(?:\s+(?P<page>\d+))?$",
    re.IGNORECASE,
)


def _default_i18n_text(key: MessageKey, **params: object) -> str:
    return tr("zh-CN", key, **params)


def _label(name: str) -> str:
    labels: dict[str, MessageKey] = {
        "scope": "wordbank.label.scope",
        "probability": "wordbank.label.probability",
        "weight": "wordbank.label.weight",
        "role": "wordbank.label.role",
        "page": "wordbank.label.page",
        "limit": "wordbank.label.limit",
        "search_field": "wordbank.label.search_field",
        "creator_id": "wordbank.label.creator_id",
    }
    return tr("zh-CN", labels[name])


def _normalize_inline_scope_value(value: str) -> str:
    normalized = normalize_scope_alias(value)
    if normalized is None or value.strip() in {"1", "2", "3", "4"}:
        raise RuleError(
            _default_i18n_text("wordbank.error.scope_unsupported", scope=value),
            key="wordbank.error.scope_unsupported",
            scope=value,
        )
    return normalized


def _normalize_inline_role_value(value: str) -> str:
    normalized = normalize_role_alias(value)
    if normalized is None:
        raise RuleError(
            _default_i18n_text("wordbank.error.role_unsupported", role=value),
            key="wordbank.error.role_unsupported",
            role=value,
        )
    return normalized


@dataclass(slots=True, frozen=True)
class ParsedTextAdd:
    trigger_text: str
    response_text: str
    raw_rule: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ParsedSearch:
    keyword: str
    page: int
    limit: int
    field: str
    creator_id: str


@dataclass(slots=True, frozen=True)
class ParsedGroupView:
    trigger_group_id: int
    page: int


@dataclass(slots=True, frozen=True)
class ParsedTriggerProbability:
    trigger_group_id: int
    probability: float


@dataclass(slots=True, frozen=True)
class ParsedResponseWeight:
    response_item_id: int
    weight: int


@dataclass(slots=True, frozen=True)
class ParsedTriggerSet:
    trigger_group_id: int
    text: str


@dataclass(slots=True, frozen=True)
class ParsedResponseSet:
    response_item_id: int
    text: str


@dataclass(slots=True, frozen=True)
class GuidedAdvancedOptions:
    raw_rule: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ParsedStudyMediaPrefix:
    source: str
    raw_rule: dict[str, Any]


@dataclass(slots=True, frozen=True)
class GuidedSearchSelection:
    field: str
    requires_query: bool = True
    requires_creator: bool = False


@dataclass(slots=True, frozen=True)
class ParsedSearchSessionCommand:
    action: str
    page: int | None = None
    trigger_group_id: int | None = None
    delete_indexes: tuple[int, ...] = ()


@dataclass(slots=True, frozen=True)
class ParsedAddMedia:
    source: str
    raw_rule: dict[str, Any]
    pair: tuple[str, str] | None


@dataclass(slots=True, frozen=True)
class MutationActor:
    user_id: str
    group_id: str
    can_moderate_group: bool
    is_superuser: bool


def actor_can_review(actor: MutationActor) -> bool:
    return actor.is_superuser or actor.can_moderate_group


def parse_rank_period(text: str) -> WordbankRankPeriod:
    normalized = text.strip()
    if not normalized:
        return "month"
    token, rest = split_command_text(normalized)
    if rest.strip():
        raise RuleError(
            _default_i18n_text("wordbank.rank.invalid_period", value=normalized),
            key="wordbank.rank.invalid_period",
            value=normalized,
        )
    period = RANK_PERIOD_ALIASES.get(token.casefold()) or RANK_PERIOD_ALIASES.get(token)
    if period is None:
        raise RuleError(
            _default_i18n_text("wordbank.rank.invalid_period", value=normalized),
            key="wordbank.rank.invalid_period",
            value=normalized,
        )
    return period


def split_add_pair(source: str) -> tuple[str, str] | None:
    for sep in ADD_SEPARATORS:
        if sep in source:
            trigger, response = source.split(sep, 1)
            return trigger.rstrip(), response.lstrip()
    return None


def _parse_positive_int(
    value: str, *, fallback: str, key: MessageKey, **params: object
) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuleError(fallback, key=key, **params) from exc
    if parsed <= 0:
        raise RuleError(fallback, key=key, **params)
    return parsed


def _parse_probability_value(value: str) -> float:
    try:
        probability = float(value)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.probability_invalid"),
            key="wordbank.error.probability_invalid",
        ) from exc
    if probability < 0 or probability > 1:
        raise RuleError(
            _default_i18n_text("wordbank.error.probability_invalid"),
            key="wordbank.error.probability_invalid",
        )
    return probability


def _parse_weight_value(value: str) -> int:
    try:
        weight = int(value)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.weight_invalid"),
            key="wordbank.error.weight_invalid",
        ) from exc
    if weight < 1 or weight > 5:
        raise RuleError(
            _default_i18n_text("wordbank.error.weight_invalid"),
            key="wordbank.error.weight_invalid",
        )
    return weight


def parse_trigger_probability_args(text: str) -> ParsedTriggerProbability:
    tokens = tokenize_shell_like(text)
    if len(tokens) != 2:
        raise RuleError(
            _default_i18n_text("wordbank.error.group_id_numeric"),
            key="wordbank.error.group_id_numeric",
        )
    return ParsedTriggerProbability(
        trigger_group_id=_parse_positive_int(
            tokens[0].value,
            fallback=_default_i18n_text("wordbank.error.group_id_numeric"),
            key="wordbank.error.group_id_numeric",
        ),
        probability=_parse_probability_value(tokens[1].value),
    )


def parse_response_weight_args(text: str) -> ParsedResponseWeight:
    tokens = tokenize_shell_like(text)
    if len(tokens) != 2:
        raise RuleError(
            _default_i18n_text("wordbank.error.entry_id_numeric"),
            key="wordbank.error.entry_id_numeric",
        )
    return ParsedResponseWeight(
        response_item_id=_parse_positive_int(
            tokens[0].value,
            fallback=_default_i18n_text("wordbank.error.entry_id_numeric"),
            key="wordbank.error.entry_id_numeric",
        ),
        weight=_parse_weight_value(tokens[1].value),
    )


def parse_trigger_set_args(text: str) -> ParsedTriggerSet:
    tokens = tokenize_shell_like(text)
    if not tokens:
        raise RuleError(
            _default_i18n_text("wordbank.error.group_id_numeric"),
            key="wordbank.error.group_id_numeric",
        )
    trigger_group_id = _parse_positive_int(
        tokens[0].value,
        fallback=_default_i18n_text("wordbank.error.group_id_numeric"),
        key="wordbank.error.group_id_numeric",
    )
    return ParsedTriggerSet(
        trigger_group_id=trigger_group_id,
        text=rest_after_token(text, tokens[0]),
    )


def parse_response_set_args(text: str) -> ParsedResponseSet:
    tokens = tokenize_shell_like(text)
    if not tokens:
        raise RuleError(
            _default_i18n_text("wordbank.error.entry_id_numeric"),
            key="wordbank.error.entry_id_numeric",
        )
    response_item_id = _parse_positive_int(
        tokens[0].value,
        fallback=_default_i18n_text("wordbank.error.entry_id_numeric"),
        key="wordbank.error.entry_id_numeric",
    )
    return ParsedResponseSet(
        response_item_id=response_item_id,
        text=rest_after_token(text, tokens[0]),
    )


def parse_text_add_args(text: str) -> ParsedTextAdd:
    source, raw_rule = _parse_flags(text)
    pair = split_add_pair(source)
    if pair is not None:
        trigger, response = pair
        if not trigger or not response:
            raise RuleError(
                _default_i18n_text("wordbank.error.add_pair_required"),
                key="wordbank.error.add_pair_required",
            )
        return ParsedTextAdd(trigger, response, raw_rule)
    raise RuleError(
        _default_i18n_text("wordbank.error.add_format"),
        key="wordbank.error.add_format",
    )


def parse_add_media_args(text: str) -> ParsedAddMedia:
    source, raw_rule = _parse_flags(text)
    return ParsedAddMedia(
        source=source,
        raw_rule=raw_rule,
        pair=split_add_pair(source),
    )


def _parse_flags(text: str) -> tuple[str, dict[str, Any]]:
    try:
        tokens = tokenize_shell_like(text)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.parse_flags", reason=str(exc)),
            key="wordbank.error.parse_flags",
            reason=str(exc),
        ) from exc
    consumed_ranges: list[tuple[int, int]] = []
    raw_rule: dict[str, Any] = {}
    idx = 0
    while idx < len(tokens):
        token = tokens[idx].value
        if token in {"--mode", "-m"}:
            raise RuleError(
                _default_i18n_text("wordbank.error.mode_unsupported"),
                key="wordbank.error.mode_unsupported",
            )
        elif token in {"--scope", "-s"}:
            flag_token = tokens[idx]
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--scope",
                        expected=_label("scope"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--scope",
                    expected=_label("scope"),
                )
            raw_rule["scope"] = _normalize_inline_scope_value(tokens[idx].value)
            consumed_ranges.append((flag_token.start, tokens[idx].end))
        elif token in {"--prob", "--probability", "-p"}:
            flag_token = tokens[idx]
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--prob",
                        expected=_label("probability"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--prob",
                    expected=_label("probability"),
                )
            raw_rule["probability"] = tokens[idx].value
            consumed_ranges.append((flag_token.start, tokens[idx].end))
        elif token in {"--weight", "-w"}:
            flag_token = tokens[idx]
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--weight",
                        expected=_label("weight"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--weight",
                    expected=_label("weight"),
                )
            raw_rule["weight"] = tokens[idx].value
            consumed_ranges.append((flag_token.start, tokens[idx].end))
        elif token in {"--role", "--roles", "-r"}:
            flag_token = tokens[idx]
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--role",
                        expected=_label("role"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--role",
                    expected=_label("role"),
                )
            raw_rule["roles"] = _normalize_inline_role_value(tokens[idx].value)
            consumed_ranges.append((flag_token.start, tokens[idx].end))
        elif token == "--call":
            flag_token = tokens[idx]
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--call",
                        expected="window:min:max",
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--call",
                    expected="window:min:max",
                )
            parts = tokens[idx].value.split(":")
            if len(parts) != 3:
                raise RuleError(
                    _default_i18n_text("wordbank.error.call_flag_format"),
                    key="wordbank.error.call_flag_format",
                )
            raw_rule["call_count"] = {
                "window_seconds": parts[0],
                "min": parts[1],
                "max": parts[2],
            }
            consumed_ranges.append((flag_token.start, tokens[idx].end))
        idx += 1

    source = text
    for start, end in sorted(consumed_ranges, reverse=True):
        source = source[:start] + source[end:]
    return source, raw_rule


def build_mutation_actor(
    *,
    user_id: str,
    group_id: str = "",
    can_moderate_group: bool = False,
    is_superuser: bool = False,
) -> MutationActor:
    return MutationActor(
        user_id=user_id,
        group_id=group_id,
        can_moderate_group=can_moderate_group,
        is_superuser=is_superuser,
    )


def parse_guided_scope_choice(text: str, *, is_group: bool) -> str:
    choice = text.strip().casefold()
    if choice in {"", "1", "default", "默认", "本群", "当前群", "current_group"}:
        return "current_group" if is_group else "self"
    if choice in {"2", "all", "all_groups", "全群", "所有群"}:
        return "all_groups"
    if not is_group:
        raise RuleError(
            _default_i18n_text("wordbank.error.guided_scope_invalid"),
            key="wordbank.error.guided_scope_invalid",
        )
    if choice in {"3", "self", "only_me", "仅自己", "自己"}:
        return "self_in_current_group" if is_group else "self"
    if choice in {"4", "private", "private_only", "私聊"}:
        return "private_only"
    raise RuleError(
        _default_i18n_text("wordbank.error.guided_scope_invalid"),
        key="wordbank.error.guided_scope_invalid",
    )


def parse_guided_advanced_options(text: str) -> GuidedAdvancedOptions:
    choice = text.strip()
    if not choice or choice.casefold() in {
        "n",
        "no",
        "否",
        "不",
        "不用",
        "不需要",
        "无",
        "跳过",
    }:
        return GuidedAdvancedOptions(raw_rule={})
    source, raw_rule = _parse_flags(choice)
    if has_meaningful_text(source):
        raise RuleError(
            _default_i18n_text(
                "wordbank.error.guided_advanced_unknown", options=source
            ),
            key="wordbank.error.guided_advanced_unknown",
            options=source,
        )
    if "scope" in raw_rule:
        raise RuleError(
            _default_i18n_text("wordbank.error.guided_scope_in_advanced"),
            key="wordbank.error.guided_scope_in_advanced",
        )
    return GuidedAdvancedOptions(raw_rule=raw_rule)


def parse_study_mode_choice(text: str) -> str:
    choice = text.strip().casefold()
    if choice in {"a", "all", "所有人", "所有"}:
        return "a"
    if choice in {"m", "me", "self", "自己", "仅自己"}:
        return "m"
    raise RuleError(
        _default_i18n_text("wordbank.error.study_mode_invalid"),
        key="wordbank.error.study_mode_invalid",
    )


def parse_study_group_block_choice(text: str) -> str:
    choice = text.strip().casefold()
    if choice in {"t", "true", "yes", "y", "开", "开启", "本群"}:
        return "t"
    if choice in {"f", "false", "no", "n", "关", "关闭", "全群"}:
        return "f"
    raise RuleError(
        _default_i18n_text("wordbank.error.study_group_block_invalid"),
        key="wordbank.error.study_group_block_invalid",
    )


def parse_guided_weight(text: str) -> int:
    choice = text.strip()
    if not choice or choice.casefold() in {"default", "默认"}:
        return 3
    try:
        weight = int(choice)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.weight_invalid"),
            key="wordbank.error.weight_invalid",
        ) from exc
    if weight < 1 or weight > 5:
        raise RuleError(
            _default_i18n_text("wordbank.error.weight_invalid"),
            key="wordbank.error.weight_invalid",
        )
    return weight


def parse_search_args(text: str) -> ParsedSearch:
    try:
        tokens = tokenize_shell_like(text)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.parse_flags", reason=str(exc)),
            key="wordbank.error.parse_flags",
            reason=str(exc),
        ) from exc

    keyword_tokens: list[TokenSpan] = []
    page = 1
    limit = DEFAULT_SEARCH_LIMIT
    field = "all"
    creator_id = ""
    idx = 0
    while idx < len(tokens):
        token = tokens[idx].value
        if token in {"--page", "-p"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--page",
                        expected=_label("page"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--page",
                    expected=_label("page"),
                )
            page = _parse_positive_int(
                tokens[idx].value,
                fallback=_default_i18n_text("wordbank.error.search_page_invalid"),
                key="wordbank.error.search_page_invalid",
            )
        elif token in {"--limit", "-n"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--limit",
                        expected=_label("limit"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--limit",
                    expected=_label("limit"),
                )
            limit = _parse_positive_int(
                tokens[idx].value,
                fallback=_default_i18n_text(
                    "wordbank.error.search_limit_invalid", max_limit=MAX_SEARCH_LIMIT
                ),
                key="wordbank.error.search_limit_invalid",
                max_limit=MAX_SEARCH_LIMIT,
            )
            if limit > MAX_SEARCH_LIMIT:
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.search_limit_invalid",
                        max_limit=MAX_SEARCH_LIMIT,
                    ),
                    key="wordbank.error.search_limit_invalid",
                    max_limit=MAX_SEARCH_LIMIT,
                )
        elif token in {"--field", "-f"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--field",
                        expected=_label("search_field"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--field",
                    expected=_label("search_field"),
                )
            normalized_field = SEARCH_FIELD_ALIASES.get(tokens[idx].value.casefold())
            if normalized_field is None:
                raise RuleError(
                    _default_i18n_text("wordbank.error.search_field_invalid"),
                    key="wordbank.error.search_field_invalid",
                )
            field = normalized_field
        elif token in {"--creator", "-c"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--creator",
                        expected=_label("creator_id"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--creator",
                    expected=_label("creator_id"),
                )
            creator_id = tokens[idx].value.strip()
            if not creator_id:
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--creator",
                        expected=_label("creator_id"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--creator",
                    expected=_label("creator_id"),
                )
        else:
            keyword_tokens.append(tokens[idx])
        idx += 1

    return ParsedSearch(
        keyword=join_tokens_with_original_spacing(text, keyword_tokens),
        page=page,
        limit=limit,
        field=field,
        creator_id=creator_id,
    )


def parse_group_view_args(text: str) -> ParsedGroupView:
    try:
        tokens = tokenize_shell_like(text)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.parse_flags", reason=str(exc)),
            key="wordbank.error.parse_flags",
            reason=str(exc),
        ) from exc

    positional: list[str] = []
    page = 1
    idx = 0
    while idx < len(tokens):
        token = tokens[idx].value
        if token in {"--page", "-p"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    _default_i18n_text(
                        "wordbank.error.flag_missing",
                        flag="--page",
                        expected=_label("page"),
                    ),
                    key="wordbank.error.flag_missing",
                    flag="--page",
                    expected=_label("page"),
                )
            page = _parse_positive_int(
                tokens[idx].value,
                fallback=_default_i18n_text("wordbank.error.group_page_invalid"),
                key="wordbank.error.group_page_invalid",
            )
        else:
            positional.append(token)
        idx += 1

    if not positional:
        raise RuleError(
            _default_i18n_text("wordbank.error.group_id_numeric"),
            key="wordbank.error.group_id_numeric",
        )

    trigger_group_id = _parse_positive_int(
        positional[0],
        fallback=_default_i18n_text("wordbank.error.group_id_numeric"),
        key="wordbank.error.group_id_numeric",
    )
    if len(positional) >= 2:
        page = _parse_positive_int(
            positional[1],
            fallback=_default_i18n_text("wordbank.error.group_page_invalid"),
            key="wordbank.error.group_page_invalid",
        )
    if len(positional) > 2:
        raise RuleError(
            _default_i18n_text("wordbank.reply.group_command_invalid"),
            key="wordbank.reply.group_command_invalid",
        )
    return ParsedGroupView(trigger_group_id=trigger_group_id, page=page)


def parse_guided_search_mode_choice(text: str) -> GuidedSearchSelection:
    choice = "".join(text.strip().split())
    if not choice:
        raise RuleError(
            _default_i18n_text("wordbank.error.guided_search_mode_invalid"),
            key="wordbank.error.guided_search_mode_invalid",
        )
    if any(char not in {"1", "2", "3"} for char in choice):
        raise RuleError(
            _default_i18n_text("wordbank.error.guided_search_mode_invalid"),
            key="wordbank.error.guided_search_mode_invalid",
        )
    if len(set(choice)) != len(choice):
        raise RuleError(
            _default_i18n_text("wordbank.error.guided_search_mode_invalid"),
            key="wordbank.error.guided_search_mode_invalid",
        )
    includes_trigger = "1" in choice
    includes_response = "2" in choice
    includes_creator = "3" in choice
    if includes_trigger and includes_response:
        field = "all"
    elif includes_trigger:
        field = "trigger"
    elif includes_response:
        field = "response"
    else:
        field = "all"
    return GuidedSearchSelection(
        field=field,
        requires_query=includes_trigger or includes_response,
        requires_creator=includes_creator,
    )


def parse_guided_search_creator_filter(text: str) -> str:
    choice = text.strip()
    if not choice or choice.casefold() in {"n", "no", "否", "不", "跳过", "none", "无"}:
        return ""
    return choice


def parse_guided_search_page_choice(text: str) -> int | None:
    choice = text.strip().casefold()
    if choice in {"", "exit", "q", "quit", "结束"}:
        return None
    if choice.startswith("page "):
        choice = choice.removeprefix("page ").strip()
    return _parse_positive_int(
        choice,
        fallback=_default_i18n_text("wordbank.error.search_page_invalid"),
        key="wordbank.error.search_page_invalid",
    )


def parse_search_session_command(text: str) -> ParsedSearchSessionCommand:
    source = text.strip()
    if not source:
        return ParsedSearchSessionCommand(action="exit")
    compact_detail = _parse_compact_group_view_command(source)
    if compact_detail is not None:
        return ParsedSearchSessionCommand(
            action="detail",
            page=compact_detail.page,
            trigger_group_id=compact_detail.trigger_group_id,
        )
    action, _, rest = source.partition(" ")
    normalized = action.casefold()
    if normalized in {"exit", "q", "quit"} or source == "结束":
        return ParsedSearchSessionCommand(action="exit")
    if action in {"详情", "group", "grp", "展开"}:
        parsed = parse_group_view_args(rest)
        return ParsedSearchSessionCommand(
            action="detail", page=parsed.page, trigger_group_id=parsed.trigger_group_id
        )
    if action in {"delete", "del", "remove", "删除"}:
        raw_indexes = tuple(part for part in rest.split() if part)
        if not raw_indexes:
            raise RuleError(
                _default_i18n_text("wordbank.error.search_delete_index_invalid"),
                key="wordbank.error.search_delete_index_invalid",
            )
        indexes: list[int] = []
        for raw_index in raw_indexes:
            if not raw_index.isdigit() or int(raw_index) <= 0:
                raise RuleError(
                    _default_i18n_text("wordbank.error.search_delete_index_invalid"),
                    key="wordbank.error.search_delete_index_invalid",
                )
            value = int(raw_index)
            if value not in indexes:
                indexes.append(value)
        return ParsedSearchSessionCommand(
            action="delete", delete_indexes=tuple(indexes)
        )
    if source.isdigit() or normalized == "page":
        return ParsedSearchSessionCommand(
            action="page", page=parse_guided_search_page_choice(source)
        )
    raise RuleError(
        _default_i18n_text("wordbank.error.search_session_command_invalid"),
        key="wordbank.error.search_session_command_invalid",
    )


def _parse_compact_group_view_command(text: str) -> ParsedGroupView | None:
    match = _COMPACT_GROUP_VIEW_RE.fullmatch(text.strip())
    if match is None:
        return None
    group_id = _parse_positive_int(
        match.group("group_id"),
        fallback=_default_i18n_text("wordbank.error.group_id_numeric"),
        key="wordbank.error.group_id_numeric",
    )
    page_value = match.group("page")
    page = (
        _parse_positive_int(
            page_value,
            fallback=_default_i18n_text("wordbank.error.group_page_invalid"),
            key="wordbank.error.group_page_invalid",
        )
        if page_value
        else 1
    )
    return ParsedGroupView(trigger_group_id=group_id, page=page)


def parse_study_media_prefix(text: str, *, is_group: bool) -> ParsedStudyMediaPrefix:
    try:
        tokens = tokenize_shell_like(text)
    except ValueError as exc:
        raise RuleError(
            _default_i18n_text("wordbank.error.study_format"),
            key="wordbank.error.study_format",
        ) from exc
    if (
        len(tokens) >= 2
        and tokens[0].value.casefold() in {"a", "m"}
        and tokens[1].value.casefold() in {"t", "f"}
    ):
        from src.plugins.wordbank.services.rules import build_legacy_study_shortcut_rule

        return ParsedStudyMediaPrefix(
            source=rest_after_token(text, tokens[1]),
            raw_rule=build_legacy_study_shortcut_rule(
                tokens[0].value.casefold(),
                tokens[1].value.casefold(),
                is_group=is_group,
            ),
        )
    if tokens and tokens[0].value.casefold() in {"a", "m"}:
        raise RuleError(
            _default_i18n_text("wordbank.error.study_format"),
            key="wordbank.error.study_format",
        )
    return ParsedStudyMediaPrefix(source=text, raw_rule={})


def build_forced_command_text(action: str | None, text: str) -> str:
    action = (action or "").strip()
    if not action:
        return text
    if not text:
        return action
    return f"{action} {text}"


def localize_wordbank_error(exc: Exception, locale: LocaleCode) -> str:
    if isinstance(exc, WordbankUserError):
        return exc.localize(locale)
    return str(exc)
