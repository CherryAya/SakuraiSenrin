"""Wordbank command handlers."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.config import config
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.services.core import (
    WordbankService,
    format_add_result,
    format_search_items,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleError, parse_legacy_study_text

ADD_ALIASES = {"add", "添加", "学习"}
SEARCH_ALIASES = {"search", "find", "查询", "搜索"}
DELETE_ALIASES = {"delete", "del", "remove", "删除"}
RESTORE_ALIASES = {"restore", "恢复"}
IMAGE_ALIASES = {"image", "img", "图片"}
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 20


@dataclass(slots=True, frozen=True)
class ParsedTextAdd:
    trigger_text: str
    response_text: str
    trigger_mode: str | None
    raw_rule: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ParsedSearch:
    keyword: str
    page: int
    limit: int


@dataclass(slots=True, frozen=True)
class MutationActor:
    user_id: str
    group_id: str
    can_moderate_group: bool
    is_superuser: bool


def _split_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    action, _, rest = stripped.partition(" ")
    return action.lower(), rest.strip()


def _parse_flags(text: str) -> tuple[str, dict[str, Any], str | None]:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise RuleError(
            f"参数解析失败: {exc}",
            key="wordbank.error.parse_flags",
            reason=str(exc),
        ) from exc
    remaining: list[str] = []
    raw_rule: dict[str, Any] = {}
    trigger_mode: str | None = None
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"--mode", "-m"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--mode 需要提供触发模式",
                    key="wordbank.error.flag_missing",
                    flag="--mode",
                    expected="触发模式",
                )
            trigger_mode = tokens[idx]
        elif token in {"--scope", "-s"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--scope 需要提供生效范围",
                    key="wordbank.error.flag_missing",
                    flag="--scope",
                    expected="生效范围",
                )
            raw_rule["scope"] = tokens[idx]
        elif token in {"--prob", "--probability", "-p"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--prob 需要提供概率",
                    key="wordbank.error.flag_missing",
                    flag="--prob",
                    expected="概率",
                )
            raw_rule["probability"] = tokens[idx]
        elif token in {"--weight", "-w"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--weight 需要提供权重",
                    key="wordbank.error.flag_missing",
                    flag="--weight",
                    expected="权重",
                )
            raw_rule["weight"] = tokens[idx]
        elif token in {"--role", "--roles", "-r"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--role 需要提供角色",
                    key="wordbank.error.flag_missing",
                    flag="--role",
                    expected="角色",
                )
            raw_rule["roles"] = tokens[idx]
        elif token == "--call":
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--call 需要提供 window:min:max",
                    key="wordbank.error.flag_missing",
                    flag="--call",
                    expected="window:min:max",
                )
            parts = tokens[idx].split(":")
            if len(parts) != 3:
                raise RuleError(
                    "--call 格式应为 window:min:max",
                    key="wordbank.error.call_flag_format",
                )
            raw_rule["call_count"] = {
                "window_seconds": parts[0],
                "min": parts[1],
                "max": parts[2],
            }
        else:
            remaining.append(token)
        idx += 1
    return " ".join(remaining), raw_rule, trigger_mode


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


def parse_text_add_args(text: str) -> ParsedTextAdd:
    source, raw_rule, trigger_mode = _parse_flags(text)
    for sep in ("=>", "->", "回答", "回复"):
        if sep in source:
            trigger, response = source.split(sep, 1)
            trigger = trigger.strip()
            response = response.strip()
            if not trigger or not response:
                raise RuleError(
                    "添加词条需要同时包含触发词和响应词",
                    key="wordbank.error.add_pair_required",
                )
            return ParsedTextAdd(trigger, response, trigger_mode, raw_rule)
    raise RuleError(
        "添加格式: wordbank add 触发词 => 响应词",
        key="wordbank.error.add_format",
    )


def parse_search_args(text: str) -> ParsedSearch:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise RuleError(
            f"参数解析失败: {exc}",
            key="wordbank.error.parse_flags",
            reason=str(exc),
        ) from exc

    keyword_parts: list[str] = []
    page = 1
    limit = DEFAULT_SEARCH_LIMIT
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"--page", "-p"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--page 需要提供页码",
                    key="wordbank.error.flag_missing",
                    flag="--page",
                    expected="页码",
                )
            page = _parse_positive_int(
                tokens[idx],
                fallback="页码必须是大于 0 的整数",
                key="wordbank.error.search_page_invalid",
            )
        elif token in {"--limit", "-n"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError(
                    "--limit 需要提供每页数量",
                    key="wordbank.error.flag_missing",
                    flag="--limit",
                    expected="每页数量",
                )
            limit = _parse_positive_int(
                tokens[idx],
                fallback=f"每页数量必须是 1 到 {MAX_SEARCH_LIMIT} 之间的整数",
                key="wordbank.error.search_limit_invalid",
                max_limit=MAX_SEARCH_LIMIT,
            )
            if limit > MAX_SEARCH_LIMIT:
                raise RuleError(
                    f"每页数量必须是 1 到 {MAX_SEARCH_LIMIT} 之间的整数",
                    key="wordbank.error.search_limit_invalid",
                    max_limit=MAX_SEARCH_LIMIT,
                )
        else:
            keyword_parts.append(token)
        idx += 1

    return ParsedSearch(
        keyword=" ".join(keyword_parts).strip(),
        page=page,
        limit=limit,
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


def extract_image_urls(message: Message) -> list[str]:
    urls: list[str] = []
    for segment in message:
        if segment.type != "image":
            continue
        url = str(segment.data.get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


async def handle_add_text(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    parsed = parse_text_add_args(text)
    result = await service.add_text_entry(
        trigger_text=parsed.trigger_text,
        response_text=parsed.response_text,
        trigger_mode=parsed.trigger_mode,
        raw_rule=parsed.raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=isinstance(event, GroupMessageEvent),
    )
    return format_add_result(result, locale=locale)


async def handle_study_shortcut(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    is_group = isinstance(event, GroupMessageEvent)
    trigger, response, raw_rule = parse_legacy_study_text(text, is_group=is_group)
    result = await service.add_text_entry(
        trigger_text=trigger,
        response_text=response,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )
    return format_add_result(result, locale=locale)


async def handle_search(
    service: WordbankService,
    *,
    keyword: str,
    locale: LocaleCode,
) -> str:
    parsed = parse_search_args(keyword)
    offset = (parsed.page - 1) * parsed.limit
    items = await service.search(
        parsed.keyword,
        limit=parsed.limit + 1,
        offset=offset,
    )
    has_more = len(items) > parsed.limit
    return format_search_items(
        items[: parsed.limit],
        locale=locale,
        page=parsed.page,
        limit=parsed.limit,
        has_more=has_more,
    )


async def handle_delete(
    service: WordbankService,
    *,
    event: MessageEvent,
    entry_id_text: str,
    locale: LocaleCode,
) -> str:
    if not entry_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    entry_id = int(entry_id_text)
    actor = build_mutation_actor(event)
    if await service.delete_entry(
        entry_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.delete.success", entry_id=entry_id)
    return tr(locale, "wordbank.delete.not_found", entry_id=entry_id)


async def handle_restore(
    service: WordbankService,
    *,
    event: MessageEvent,
    entry_id_text: str,
    locale: LocaleCode,
) -> str:
    if not entry_id_text.isdigit():
        return tr(locale, "wordbank.error.entry_id_numeric")
    entry_id = int(entry_id_text)
    actor = build_mutation_actor(event)
    if await service.restore_entry(
        entry_id,
        actor_user_id=actor.user_id,
        actor_group_id=actor.group_id,
        can_moderate_group=actor.can_moderate_group,
        is_superuser=actor.is_superuser,
    ):
        return tr(locale, "wordbank.restore.success", entry_id=entry_id)
    return tr(locale, "wordbank.restore.not_found", entry_id=entry_id)


async def handle_add_image(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    image_bytes: bytes,
    text: str,
    locale: LocaleCode,
) -> str:
    source, raw_rule, _ = _parse_flags(text)
    response_text = source.strip()
    if not response_text:
        raise RuleError(
            "图片词条需要提供响应词",
            key="wordbank.error.image_response_required",
        )
    image = await media_service.ingest_image_bytes(image_bytes)
    result = await service.add_image_entry(
        canonical_image_id=image.canonical_id,
        response_text=response_text,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=isinstance(event, GroupMessageEvent),
    )
    return format_add_result(result, locale=locale)


def wordbank_help_text(locale: LocaleCode = "zh-CN") -> str:
    return tr(locale, "wordbank.help")


async def dispatch_wordbank_command(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
    locale: LocaleCode,
) -> str:
    action, rest = _split_command(text)
    if not action or action in {"help", "帮助"}:
        return wordbank_help_text(locale)
    if action in ADD_ALIASES:
        return await handle_add_text(service, event=event, text=rest, locale=locale)
    if action in SEARCH_ALIASES:
        return await handle_search(service, keyword=rest, locale=locale)
    if action in DELETE_ALIASES:
        return await handle_delete(
            service,
            event=event,
            entry_id_text=rest,
            locale=locale,
        )
    if action in RESTORE_ALIASES:
        return await handle_restore(
            service,
            event=event,
            entry_id_text=rest,
            locale=locale,
        )
    if action in IMAGE_ALIASES:
        return tr(locale, "wordbank.error.image_missing")
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
