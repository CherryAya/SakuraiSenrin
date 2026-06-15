"""Wordbank command handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import math
import shlex
from typing import Any

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.config import config
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankSearchPage,
    WordbankSearchRequest,
)
from src.plugins.wordbank.handlers.search_cards import SearchCardQuery
from src.plugins.wordbank.message_model import (
    MessageShape,
    combine_shapes,
    shape_from_image,
    shape_from_message,
    shape_from_text,
)
from src.plugins.wordbank.services.core import (
    WordbankAddResult,
    WordbankService,
    format_add_result,
    format_pending_items,
    format_search_items,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import (
    RuleError,
    build_legacy_study_shortcut_rule,
    parse_legacy_study_text,
)
from src.plugins.wordbank.text_parsing import (
    TokenSpan,
    has_meaningful_text,
    join_tokens_with_original_spacing,
    rest_after_token,
    split_command_text,
    tokenize_shell_like,
)

from .rendering import (
    GROUP_PAGE_SIZE,
    render_group_detail_page_message,
    render_search_results_card_message,
)

ADD_ALIASES = {"add", "添加", "学习"}
SEARCH_ALIASES = {"search", "find", "查询", "搜索"}
GROUP_ALIASES = {"group", "grp", "展开"}
DELETE_ALIASES = {"delete", "del", "remove", "删除"}
RESTORE_ALIASES = {"restore", "恢复"}
APPROVE_ALIASES = {"approve", "pass", "通过", "审核通过"}
REJECT_ALIASES = {"reject", "deny", "拒绝", "驳回"}
PENDING_ALIASES = {"pending", "review", "待审", "待审核", "审核列表"}
TRIGGER_ALIASES = {"trigger", "触发", "触发词"}
RESPONSE_ALIASES = {"response", "响应", "响应词"}
SET_ALIASES = {"set", "edit", "修改"}
PROBABILITY_ALIASES = {"prob", "probability", "概率"}
WEIGHT_ALIASES = {"weight", "权重"}
ADD_SEPARATORS = ("=>", "->", "回答", "回复")
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 20
IMAGE_DOWNLOAD_RETRY_ATTEMPTS = 3
IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
GUIDED_MESSAGE_IMAGE_LIMIT = 4
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


def _default_i18n_text(key: MessageKey, **params: object) -> str:
    return tr("zh-CN", key, **params)


WORD_BANK_LABEL_KEYS: dict[str, MessageKey] = {
    "scope": "wordbank.label.scope",
    "probability": "wordbank.label.probability",
    "weight": "wordbank.label.weight",
    "role": "wordbank.label.role",
    "page": "wordbank.label.page",
    "limit": "wordbank.label.limit",
    "search_field": "wordbank.label.search_field",
    "creator_id": "wordbank.label.creator_id",
}


def _label(name: str) -> str:
    return tr("zh-CN", WORD_BANK_LABEL_KEYS[name])


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
class MutationActor:
    user_id: str
    group_id: str
    can_moderate_group: bool
    is_superuser: bool


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


def _split_command(text: str) -> tuple[str, str]:
    return split_command_text(text)


def build_forced_command_text(action: str | None, raw_text: str) -> str:
    if action is None:
        return raw_text
    return f"{action} {raw_text}" if raw_text else action


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
            raw_rule["scope"] = tokens[idx].value
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
            raw_rule["roles"] = tokens[idx].value
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


def _parse_positive_int(
    value: str,
    *,
    fallback: str,
    key: MessageKey,
    **params: object,
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
    trigger_group_id = _parse_positive_int(
        tokens[0].value,
        fallback=_default_i18n_text("wordbank.error.group_id_numeric"),
        key="wordbank.error.group_id_numeric",
    )
    return ParsedTriggerProbability(
        trigger_group_id=trigger_group_id,
        probability=_parse_probability_value(tokens[1].value),
    )


def parse_response_weight_args(text: str) -> ParsedResponseWeight:
    tokens = tokenize_shell_like(text)
    if len(tokens) != 2:
        raise RuleError(
            _default_i18n_text("wordbank.error.entry_id_numeric"),
            key="wordbank.error.entry_id_numeric",
        )
    response_item_id = _parse_positive_int(
        tokens[0].value,
        fallback=_default_i18n_text("wordbank.error.entry_id_numeric"),
        key="wordbank.error.entry_id_numeric",
    )
    return ParsedResponseWeight(
        response_item_id=response_item_id,
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


def split_add_pair(source: str) -> tuple[str, str] | None:
    for sep in ADD_SEPARATORS:
        if sep in source:
            trigger, response = source.split(sep, 1)
            return trigger.rstrip(), response.lstrip()
    return None


def parse_guided_scope_choice(text: str, *, is_group: bool) -> str:
    choice = text.strip().casefold()
    if choice in {"", "1", "default", "默认", "本群", "当前群", "current_group"}:
        return "current_group" if is_group else "self"
    if choice in {"2", "all", "all_groups", "全群", "所有群"}:
        return "all_groups"
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
                "wordbank.error.guided_advanced_unknown",
                options=source,
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
                    "wordbank.error.search_limit_invalid",
                    max_limit=MAX_SEARCH_LIMIT,
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

    keyword = join_tokens_with_original_spacing(text, keyword_tokens)

    return ParsedSearch(
        keyword=keyword,
        page=page,
        limit=limit,
        field=field,
        creator_id=creator_id,
    )


def parse_group_view_args(text: str) -> ParsedGroupView:
    try:
        tokens = shlex.split(text)
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
        token = tokens[idx]
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
                tokens[idx],
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
    if not choice or choice.casefold() in {
        "n",
        "no",
        "否",
        "不",
        "跳过",
        "none",
        "无",
    }:
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


def build_mutation_actor(event: MessageEvent) -> MutationActor:
    user_id = str(event.user_id)
    group_id = str(getattr(event, "group_id", ""))
    sender = getattr(event, "sender", None)
    role = str(getattr(sender, "role", "") or "")
    return MutationActor(
        user_id=user_id,
        group_id=group_id,
        can_moderate_group=isinstance(event, GroupMessageEvent)
        and role in {"owner", "admin"},
        is_superuser=user_id in config.SUPERUSERS,
    )


def actor_can_review(actor: MutationActor) -> bool:
    return actor.is_superuser or actor.can_moderate_group


def extract_image_urls(message: Message) -> list[str]:
    urls: list[str] = []
    for segment in message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


async def fetch_image_bytes_with_retry(
    url: str,
    *,
    attempts: int = IMAGE_DOWNLOAD_RETRY_ATTEMPTS,
    retry_delay_seconds: float = IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS,
) -> bytes | None:
    from .passive import fetch_image_bytes

    for attempt_index in range(max(1, attempts)):
        data = await fetch_image_bytes(url)
        if data is not None:
            return data
        if attempt_index < attempts - 1:
            await asyncio.sleep(retry_delay_seconds)
    return None


async def fetch_first_image_bytes_from_message(message: Message) -> bytes | None:
    items = await fetch_image_bytes_from_message(message, limit=1)
    return items[0] if items else None


async def fetch_image_bytes_from_message(
    message: Message,
    *,
    limit: int = 2,
) -> tuple[bytes, ...]:
    urls = extract_image_urls(message)
    if not urls:
        return ()

    items: list[bytes] = []
    for url in urls[: max(1, limit)]:
        data = await fetch_image_bytes_with_retry(url)
        if data is None:
            raise WordbankUserError(
                _default_i18n_text("wordbank.error.image_download_failed"),
                key="wordbank.error.image_download_failed",
            )
        items.append(data)
    return tuple(items)


def _shape_from_text_value(text: str) -> MessageShape:
    return shape_from_text(text)


def _shape_from_response_parts(
    text: str,
    *,
    image_id: int | None = None,
) -> MessageShape:
    parts = [_shape_from_text_value(text)]
    if image_id is not None:
        parts.append(shape_from_image(image_id))
    return combine_shapes(*parts)


def _shape_from_trigger_parts(
    text: str,
    *,
    image_id: int | None = None,
) -> MessageShape:
    parts = [_shape_from_text_value(text)]
    if image_id is not None:
        parts.append(shape_from_image(image_id))
    return combine_shapes(*parts)


async def build_message_shape_from_message(
    media_service: WordbankMediaService,
    message: Message,
    *,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
) -> MessageShape:
    image_bytes_items = await fetch_image_bytes_from_message(
        message,
        limit=image_limit,
    )
    image_ids: dict[int, int] = {}
    for index, image_bytes in enumerate(image_bytes_items):
        image = await media_service.ingest_image_bytes(image_bytes)
        image_ids[index] = image.canonical_id
    return shape_from_message(message, image_ids=image_ids)


async def build_shape_from_text_and_images(
    media_service: WordbankMediaService,
    *,
    text: str,
    message: Message,
    image_limit: int = GUIDED_MESSAGE_IMAGE_LIMIT,
) -> MessageShape:
    image_bytes_items = await fetch_image_bytes_from_message(
        message,
        limit=image_limit,
    )
    parts: list[MessageShape] = []
    if has_meaningful_text(text):
        parts.append(shape_from_text(text))
    for image_bytes in image_bytes_items:
        image = await media_service.ingest_image_bytes(image_bytes)
        parts.append(shape_from_image(image.canonical_id))
    return combine_shapes(*parts)


async def handle_add_text(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    result = await handle_add_text_result(service, event=event, text=text)
    return format_add_result(result, locale=locale)


async def handle_add_text_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
) -> WordbankAddResult:
    parsed = parse_text_add_args(text)
    return await service.add_message_entry(
        trigger_shape=_shape_from_text_value(parsed.trigger_text),
        response_shape=_shape_from_response_parts(parsed.response_text),
        raw_rule=parsed.raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=isinstance(event, GroupMessageEvent),
    )


async def handle_add_with_media(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    locale: LocaleCode,
) -> str:
    result = await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_bytes,
    )
    return format_add_result(result, locale=locale)


async def handle_add_with_media_result(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
) -> WordbankAddResult:
    if image_bytes is None:
        return await handle_add_text_result(service, event=event, text=text)

    source, raw_rule = _parse_flags(text)
    pair = split_add_pair(source)
    is_group = isinstance(event, GroupMessageEvent)
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.user_id)

    if pair is None:
        trigger_text = source
        if not has_meaningful_text(trigger_text):
            raise RuleError(
                _default_i18n_text("wordbank.error.trigger_empty"),
                key="wordbank.error.trigger_empty",
            )
        image = await media_service.ingest_image_bytes(image_bytes)
        return await service.add_message_entry(
            trigger_shape=_shape_from_text_value(trigger_text),
            response_shape=shape_from_image(image.canonical_id),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    trigger_text, response_text = pair
    if trigger_text:
        image = await media_service.ingest_image_bytes(image_bytes)
        return await service.add_message_entry(
            trigger_shape=_shape_from_text_value(trigger_text),
            response_shape=_shape_from_response_parts(
                response_text,
                image_id=image.canonical_id,
            ),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    if response_text:
        image = await media_service.ingest_image_bytes(image_bytes)
        return await service.add_message_entry(
            trigger_shape=shape_from_image(image.canonical_id),
            response_shape=_shape_from_response_parts(response_text),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    raise RuleError(
        _default_i18n_text("wordbank.error.add_pair_required"),
        key="wordbank.error.add_pair_required",
    )


async def handle_guided_add_shape_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_shape: MessageShape,
    response_shape: MessageShape,
    scope_text: str,
    advanced_text: str = "",
) -> WordbankAddResult:
    is_group = isinstance(event, GroupMessageEvent)
    scope = parse_guided_scope_choice(scope_text, is_group=is_group)
    advanced = parse_guided_advanced_options(advanced_text)
    raw_rule = {"scope": scope, **advanced.raw_rule}
    return await service.add_message_entry(
        trigger_shape=trigger_shape,
        response_shape=response_shape,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )


async def handle_study_shortcut(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    result = await handle_study_shortcut_result(service, event=event, text=text)
    return format_add_result(result, locale=locale)


async def handle_study_shortcut_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
) -> WordbankAddResult:
    is_group = isinstance(event, GroupMessageEvent)
    trigger, response, raw_rule = parse_legacy_study_text(text, is_group=is_group)
    return await service.add_message_entry(
        trigger_shape=_shape_from_text_value(trigger),
        response_shape=_shape_from_response_parts(response),
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )


async def handle_study_with_media(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    locale: LocaleCode,
) -> str:
    result = await handle_study_with_media_result(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_bytes,
    )
    return format_add_result(result, locale=locale)


async def handle_study_with_media_result(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    extra_image_bytes: Sequence[bytes] = (),
) -> WordbankAddResult:
    image_items = ((image_bytes,) if image_bytes is not None else ()) + tuple(
        extra_image_bytes
    )
    if not image_items:
        return await handle_study_shortcut_result(
            service,
            event=event,
            text=text,
        )

    is_group = isinstance(event, GroupMessageEvent)
    parsed = parse_study_media_prefix(text, is_group=is_group)
    if parsed.raw_rule:
        return await handle_study_media_with_rule_result(
            service,
            media_service,
            event=event,
            source=parsed.source,
            raw_rule=parsed.raw_rule,
            image_bytes=image_items,
        )

    return await handle_add_with_media_result(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_items[0],
    )


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


async def handle_study_media_with_rule_result(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    source: str,
    raw_rule: dict[str, Any],
    image_bytes: Sequence[bytes],
) -> WordbankAddResult:
    if not image_bytes:
        return await handle_study_shortcut_result(
            service,
            event=event,
            text=source,
        )

    if not has_meaningful_text(source):
        source = ""

    is_group = isinstance(event, GroupMessageEvent)
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.user_id)
    pair = split_add_pair(source)

    if pair is None and not source:
        if len(image_bytes) < 2:
            raise RuleError(
                _default_i18n_text("wordbank.error.study_pair_required"),
                key="wordbank.error.study_pair_required",
            )
        trigger_image = await media_service.ingest_image_bytes(image_bytes[0])
        response_image = await media_service.ingest_image_bytes(image_bytes[1])
        return await service.add_message_entry(
            trigger_shape=shape_from_image(trigger_image.canonical_id),
            response_shape=shape_from_image(response_image.canonical_id),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    if pair is None:
        response_image = await media_service.ingest_image_bytes(image_bytes[0])
        return await service.add_message_entry(
            trigger_shape=_shape_from_text_value(source),
            response_shape=shape_from_image(response_image.canonical_id),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    trigger_text, response_text = pair
    if trigger_text:
        response_image = await media_service.ingest_image_bytes(image_bytes[0])
        return await service.add_message_entry(
            trigger_shape=_shape_from_text_value(trigger_text),
            response_shape=_shape_from_response_parts(
                response_text,
                image_id=response_image.canonical_id,
            ),
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )

    trigger_image = await media_service.ingest_image_bytes(image_bytes[0])
    response_image_id: int | None = None
    if len(image_bytes) >= 2:
        response_image = await media_service.ingest_image_bytes(image_bytes[1])
        response_image_id = response_image.canonical_id
    return await service.add_message_entry(
        trigger_shape=shape_from_image(trigger_image.canonical_id),
        response_shape=_shape_from_response_parts(
            response_text,
            image_id=response_image_id,
        ),
        raw_rule=raw_rule,
        group_id=group_id,
        user_id=user_id,
        is_group=is_group,
    )


async def handle_guided_study_shape_result(
    service: WordbankService,
    *,
    event: MessageEvent,
    trig_mode_text: str,
    group_block_text: str,
    trigger_shape: MessageShape,
    response_shape: MessageShape,
    weight_text: str,
) -> WordbankAddResult:
    trig_mode = parse_study_mode_choice(trig_mode_text)
    group_block = parse_study_group_block_choice(group_block_text)
    weight = parse_guided_weight(weight_text)
    is_group = isinstance(event, GroupMessageEvent)
    raw_rule = build_legacy_study_shortcut_rule(
        trig_mode,
        group_block,
        is_group=is_group,
    )
    raw_rule["weight"] = weight
    return await service.add_message_entry(
        trigger_shape=trigger_shape,
        response_shape=response_shape,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )


async def handle_search(
    service: WordbankService,
    *,
    keyword: str,
    image_scores: dict[int, float] | None = None,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> Message:
    parsed = parse_search_args(keyword)
    page = await execute_search_page(
        service,
        parsed=parsed,
        image_scores=image_scores,
    )
    return await render_search_page_message(
        page,
        parsed=parsed,
        locale=locale,
        has_image=image_scores is not None,
        media_service=media_service,
    )


async def execute_search_page(
    service: WordbankService,
    *,
    parsed: ParsedSearch,
    image_scores: dict[int, float] | None = None,
) -> WordbankSearchPage:
    offset = (parsed.page - 1) * parsed.limit
    return await service.search_page(
        WordbankSearchRequest(
            keyword=parsed.keyword,
            field=parsed.field,
            creator_id=parsed.creator_id,
            has_image=image_scores is not None,
            image_scores=dict(image_scores or {}),
        ),
        limit=parsed.limit,
        offset=offset,
    )


async def render_search_page_message(
    page: WordbankSearchPage,
    *,
    parsed: ParsedSearch,
    locale: LocaleCode,
    has_image: bool,
    media_service: WordbankMediaService,
) -> Message:
    try:
        return await render_search_results_card_message(
            items=page.items,
            query=SearchCardQuery(
                keyword=parsed.keyword,
                field=parsed.field,
                creator_id=parsed.creator_id,
                has_image=has_image,
                page=parsed.page,
                total_count=page.total_count,
                limit=parsed.limit,
            ),
            locale=locale,
            media_service=media_service,
        )
    except Exception:
        logger.exception(
            "[Wordbank] search card render failed; fallback to text. "
            f"keyword={parsed.keyword!r} page={parsed.page} field={parsed.field} "
            f"has_image={has_image} total_count={page.total_count}"
        )
        text = format_search_items(
            list(page.items),
            locale=locale,
            page=parsed.page,
            limit=parsed.limit,
            has_more=page.has_more,
        )
        return Message(text)


async def build_group_detail_message(
    service: WordbankService,
    *,
    trigger_group_id: int,
    page: int,
    locale: LocaleCode,
    media_service: WordbankMediaService,
) -> tuple[Message, WordbankGroupDetail, int]:
    detail = await service.get_group_detail(trigger_group_id)
    if detail is None:
        raise RuleError(
            _default_i18n_text(
                "wordbank.group.not_found",
                group_id=trigger_group_id,
            ),
            key="wordbank.group.not_found",
            group_id=trigger_group_id,
        )
    total_pages = max(1, math.ceil(len(detail.responses) / max(GROUP_PAGE_SIZE, 1)))
    if page > total_pages:
        raise RuleError(
            _default_i18n_text(
                "wordbank.error.guided_search_page_out_of_range",
            ),
            key="wordbank.error.guided_search_page_out_of_range",
            total_pages=total_pages,
        )
    message, total_pages = await render_group_detail_page_message(
        detail=detail,
        page=page,
        locale=locale,
        media_service=media_service,
    )
    return message, detail, total_pages


async def handle_pending_entries(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return tr(locale, "wordbank.approval.permission_denied")
    parsed = parse_search_args(text)
    offset = (parsed.page - 1) * parsed.limit
    items = await service.list_pending_entries(
        keyword=parsed.keyword,
        limit=parsed.limit + 1,
        offset=offset,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    )
    has_more = len(items) > parsed.limit
    return format_pending_items(
        items[: parsed.limit],
        locale=locale,
        page=parsed.page,
        limit=parsed.limit,
        has_more=has_more,
    )


async def handle_approve(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return tr(locale, "wordbank.approval.permission_denied")
    response_item_id = int(response_item_id_text)
    if await service.approve_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.approval.approved", entry_id=response_item_id)
    return tr(locale, "wordbank.approval.not_found", entry_id=response_item_id)


async def handle_reject(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    actor = build_mutation_actor(event)
    if not actor_can_review(actor):
        return tr(locale, "wordbank.approval.permission_denied")
    response_item_id = int(response_item_id_text)
    if await service.reject_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.approval.rejected", entry_id=response_item_id)
    return tr(locale, "wordbank.approval.not_found", entry_id=response_item_id)


async def handle_delete(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    response_item_id = int(response_item_id_text)
    actor = build_mutation_actor(event)
    if await service.delete_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.delete.success", entry_id=response_item_id)

    return tr(locale, "wordbank.delete.not_found", entry_id=response_item_id)


async def handle_restore(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id_text: str,
    locale: LocaleCode,
) -> str:
    if not response_item_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    response_item_id = int(response_item_id_text)
    actor = build_mutation_actor(event)
    if await service.restore_response_item(
        response_item_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.restore.success", entry_id=response_item_id)
    return tr(locale, "wordbank.restore.not_found", entry_id=response_item_id)


async def handle_trigger_probability_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    probability: float,
    locale: LocaleCode,
) -> str:
    actor = build_mutation_actor(event)
    if await service.update_trigger_probability(
        trigger_group_id,
        probability=probability,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return f"trigger group #{trigger_group_id} 的触发概率已更新为 {probability:g}。"
    return f"未找到可修改的 trigger group #{trigger_group_id}，或你没有操作权限。"


async def handle_trigger_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    trigger_group_id: int,
    text: str,
    raw_message: Message,
    locale: LocaleCode,
) -> str:
    actor = build_mutation_actor(event)
    trigger_shape = await build_shape_from_text_and_images(
        media_service,
        text=text,
        message=raw_message,
    )
    if await service.update_trigger_content(
        trigger_group_id,
        trigger_shape=trigger_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return (
            f"trigger group #{trigger_group_id} 的触发词已更新，"
            "该组响应已重新进入待审核。"
        )
    return f"未找到可修改的 trigger group #{trigger_group_id}，或你没有操作权限。"


async def handle_response_weight_update(
    service: WordbankService,
    *,
    event: MessageEvent,
    response_item_id: int,
    weight: int,
    locale: LocaleCode,
) -> str:
    actor = build_mutation_actor(event)
    if await service.update_response_weight(
        response_item_id,
        weight=weight,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return f"词条 #{response_item_id} 的响应权重已更新为 {weight}。"
    return f"未找到可修改的词条 #{response_item_id}，或你没有操作权限。"


async def handle_response_content_update(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    response_item_id: int,
    text: str,
    raw_message: Message,
    locale: LocaleCode,
) -> str:
    actor = build_mutation_actor(event)
    response_shape = await build_shape_from_text_and_images(
        media_service,
        text=text,
        message=raw_message,
    )
    if await service.update_response_content(
        response_item_id,
        response_shape=response_shape,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return f"词条 #{response_item_id} 的响应内容已更新，并重新进入待审核。"
    return f"未找到可修改的词条 #{response_item_id}，或你没有操作权限。"


async def handle_trigger_command(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    raw_message: Message | None,
    locale: LocaleCode,
) -> str:
    action, rest = _split_command(text)
    if action in PROBABILITY_ALIASES:
        parsed = parse_trigger_probability_args(rest)
        return await handle_trigger_probability_update(
            service,
            event=event,
            trigger_group_id=parsed.trigger_group_id,
            probability=parsed.probability,
            locale=locale,
        )
    if action in SET_ALIASES:
        if raw_message is None:
            raise RuntimeError("wordbank raw message is required for trigger set")
        parsed = parse_trigger_set_args(rest)
        return await handle_trigger_content_update(
            service,
            media_service,
            event=event,
            trigger_group_id=parsed.trigger_group_id,
            text=parsed.text,
            raw_message=raw_message,
            locale=locale,
        )
    raise RuleError(
        "trigger 子命令仅支持 prob / set。",
        key="wordbank.error.unknown_subcommand",
        action=f"trigger {action}".strip(),
        help=wordbank_help_text(locale),
    )


async def handle_response_command(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    raw_message: Message | None,
    locale: LocaleCode,
) -> str:
    action, rest = _split_command(text)
    if action in WEIGHT_ALIASES:
        parsed = parse_response_weight_args(rest)
        return await handle_response_weight_update(
            service,
            event=event,
            response_item_id=parsed.response_item_id,
            weight=parsed.weight,
            locale=locale,
        )
    if action in SET_ALIASES:
        if raw_message is None:
            raise RuntimeError("wordbank raw message is required for response set")
        parsed = parse_response_set_args(rest)
        return await handle_response_content_update(
            service,
            media_service,
            event=event,
            response_item_id=parsed.response_item_id,
            text=parsed.text,
            raw_message=raw_message,
            locale=locale,
        )
    raise RuleError(
        "response 子命令仅支持 weight / set。",
        key="wordbank.error.unknown_subcommand",
        action=f"response {action}".strip(),
        help=wordbank_help_text(locale),
    )


def wordbank_help_text(locale: LocaleCode = "zh-CN") -> str:
    return tr(locale, "wordbank.help")


async def dispatch_wordbank_command(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
    raw_message: Message | None = None,
    search_image_scores: dict[int, float] | None = None,
    media_service: WordbankMediaService | None = None,
) -> str | Message:
    action, rest = _split_command(text)
    if not action or action in {"help", "帮助"}:
        return wordbank_help_text(locale)
    if action in ADD_ALIASES:
        return await handle_add_text(service, event=event, text=rest, locale=locale)
    if action in SEARCH_ALIASES:
        if media_service is None:
            raise RuntimeError(
                "wordbank media service is required for search rendering"
            )
        return await handle_search(
            service,
            keyword=rest,
            image_scores=search_image_scores,
            locale=locale,
            media_service=media_service,
        )
    if action in PENDING_ALIASES:
        return await handle_pending_entries(
            service,
            event=event,
            text=rest,
            locale=locale,
        )
    if action in APPROVE_ALIASES:
        return await handle_approve(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in REJECT_ALIASES:
        return await handle_reject(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in DELETE_ALIASES:
        return await handle_delete(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in RESTORE_ALIASES:
        return await handle_restore(
            service,
            event=event,
            response_item_id_text=rest,
            locale=locale,
        )
    if action in TRIGGER_ALIASES:
        if media_service is None:
            raise RuntimeError("wordbank media service is required for trigger editing")
        return await handle_trigger_command(
            service,
            media_service,
            event=event,
            text=rest,
            raw_message=raw_message,
            locale=locale,
        )
    if action in RESPONSE_ALIASES:
        if media_service is None:
            raise RuntimeError(
                "wordbank media service is required for response editing"
            )
        return await handle_response_command(
            service,
            media_service,
            event=event,
            text=rest,
            raw_message=raw_message,
            locale=locale,
        )
    return tr(
        locale,
        "wordbank.error.unknown_subcommand",
        action=action,
        help=wordbank_help_text(locale),
    )


def localize_command_error(exc: Exception, locale: LocaleCode) -> str:
    if isinstance(exc, WordbankUserError):
        return exc.localize(locale)
    return str(exc)
