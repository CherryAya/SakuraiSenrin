"""Wordbank command handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import shlex
from typing import Any

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.config import config
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import WordbankImageRecord
from src.plugins.wordbank.services.core import (
    WordbankDeleteVoteResult,
    WordbankService,
    format_add_result,
    format_search_items,
)
from src.plugins.wordbank.services.errors import WordbankUserError
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import (
    RuleError,
    build_legacy_study_shortcut_rule,
    parse_legacy_study_text,
)

ADD_ALIASES = {"add", "添加", "学习"}
SEARCH_ALIASES = {"search", "find", "查询", "搜索"}
DELETE_ALIASES = {"delete", "del", "remove", "删除"}
RESTORE_ALIASES = {"restore", "恢复"}
SUPPORT_ALIASES = {"support", "支持", "支持删除"}
VOTE_ALIASES = {"vote", "投票", "查看投票", "查看投票状态", "查看投票结果"}
ADD_SEPARATORS = ("=>", "->", "回答", "回复")
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 20
DEFAULT_DELETE_VOTE_THRESHOLD = 3
IMAGE_DOWNLOAD_RETRY_ATTEMPTS = 3
IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
IMAGE_PREPARE_TIMEOUT_SECONDS = 12.0


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


@dataclass(slots=True, frozen=True)
class GuidedAdvancedOptions:
    raw_rule: dict[str, Any]
    trigger_mode: str | None


@dataclass(slots=True, frozen=True)
class PendingWordbankImage:
    url: str
    task: asyncio.Task[WordbankImageRecord]


def _split_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    action, _, rest = stripped.partition(" ")
    return action.lower(), rest.strip()


def build_forced_command_text(action: str | None, raw_text: str) -> str:
    raw_text = raw_text.strip()
    return f"{action} {raw_text}".strip() if action else raw_text


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
    pair = split_add_pair(source)
    if pair is not None:
        trigger, response = pair
        if not trigger or not response:
            raise RuleError(
                "添加词条需要同时包含触发词和响应词",
                key="wordbank.error.add_pair_required",
            )
        return ParsedTextAdd(trigger, response, trigger_mode, raw_rule)
    raise RuleError(
        "添加格式: wordbank add 触发词 => 响应词；图片回复: wordbank add 触发词 [图片]",
        key="wordbank.error.add_format",
    )


def split_add_pair(source: str) -> tuple[str, str] | None:
    for sep in ADD_SEPARATORS:
        if sep in source:
            trigger, response = source.split(sep, 1)
            return trigger.strip(), response.strip()
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
        "生效范围选择无效",
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
        return GuidedAdvancedOptions(raw_rule={}, trigger_mode=None)
    source, raw_rule, trigger_mode = _parse_flags(choice)
    if source.strip():
        raise RuleError(
            f"无法识别高级选项: {source.strip()}",
            key="wordbank.error.guided_advanced_unknown",
            options=source.strip(),
        )
    if "scope" in raw_rule:
        raise RuleError(
            "引导模式中生效范围已经单独选择，请不要在高级选项里重复设置 --scope",
            key="wordbank.error.guided_scope_in_advanced",
        )
    return GuidedAdvancedOptions(raw_rule=raw_rule, trigger_mode=trigger_mode)


def parse_study_mode_choice(text: str) -> str:
    choice = text.strip().casefold()
    if choice in {"a", "all", "所有人", "所有"}:
        return "a"
    if choice in {"m", "me", "self", "自己", "仅自己"}:
        return "m"
    raise RuleError(
        "触发方式输入错误，请输入 a 或 m",
        key="wordbank.error.study_mode_invalid",
    )


def parse_study_group_block_choice(text: str) -> str:
    choice = text.strip().casefold()
    if choice in {"t", "true", "yes", "y", "开", "开启", "本群"}:
        return "t"
    if choice in {"f", "false", "no", "n", "关", "关闭", "全群"}:
        return "f"
    raise RuleError(
        "群组隔离开关输入错误，请输入 t 或 f",
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
            "权重必须是 1 到 5 之间的整数",
            key="wordbank.error.weight_invalid",
        ) from exc
    if weight < 1 or weight > 5:
        raise RuleError(
            "权重必须是 1 到 5 之间的整数",
            key="wordbank.error.weight_invalid",
        )
    return weight


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


def format_delete_vote_result(
    result: WordbankDeleteVoteResult,
    *,
    locale: LocaleCode,
) -> str:
    if result.entry_deleted:
        return tr(
            locale,
            "wordbank.vote.passed",
            vote_id=result.vote_id,
            entry_id=result.entry_id,
            support_count=result.support_count,
            threshold=result.threshold,
        )
    if result.status != "open":
        return tr(
            locale,
            "wordbank.vote.closed",
            vote_id=result.vote_id,
            entry_id=result.entry_id,
            status=result.status,
            support_count=result.support_count,
            threshold=result.threshold,
        )
    if result.already_supported:
        return tr(
            locale,
            "wordbank.vote.already_supported",
            vote_id=result.vote_id,
            entry_id=result.entry_id,
            support_count=result.support_count,
            threshold=result.threshold,
        )
    if result.created:
        return tr(
            locale,
            "wordbank.vote.created",
            vote_id=result.vote_id,
            entry_id=result.entry_id,
            support_count=result.support_count,
            threshold=result.threshold,
        )
    return tr(
        locale,
        "wordbank.vote.supported",
        vote_id=result.vote_id,
        entry_id=result.entry_id,
        support_count=result.support_count,
        threshold=result.threshold,
    )


def format_delete_vote_status(
    result: WordbankDeleteVoteResult,
    *,
    locale: LocaleCode,
) -> str:
    return tr(
        locale,
        "wordbank.vote.status",
        vote_id=result.vote_id,
        entry_id=result.entry_id,
        status=result.status,
        support_count=result.support_count,
        threshold=result.threshold,
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
    urls = extract_image_urls(message)
    if not urls:
        return None

    data = await fetch_image_bytes_with_retry(urls[0])
    if data is None:
        raise WordbankUserError(
            "图片下载失败，无法加入词库。",
            key="wordbank.error.image_download_failed",
        )
    return data


async def ingest_first_image_from_message(
    media_service: WordbankMediaService,
    message: Message,
) -> WordbankImageRecord | None:
    data = await fetch_first_image_bytes_from_message(message)
    if data is None:
        return None
    return await media_service.ingest_image_bytes(data)


def start_ingest_first_image_from_message(
    media_service: WordbankMediaService,
    message: Message,
) -> PendingWordbankImage | None:
    urls = extract_image_urls(message)
    if not urls:
        return None

    async def _download_and_ingest() -> WordbankImageRecord:
        data = await fetch_image_bytes_with_retry(urls[0])
        if data is None:
            raise WordbankUserError(
                "图片下载失败，无法加入词库。",
                key="wordbank.error.image_download_failed",
            )
        return await media_service.ingest_image_bytes(data)

    def _consume_task_exception(task: asyncio.Task[WordbankImageRecord]) -> None:
        if task.cancelled():
            return
        task.exception()

    task = asyncio.create_task(_download_and_ingest())
    task.add_done_callback(_consume_task_exception)
    return PendingWordbankImage(
        url=urls[0],
        task=task,
    )


async def resolve_pending_image(
    pending: PendingWordbankImage,
    *,
    timeout_seconds: float = IMAGE_PREPARE_TIMEOUT_SECONDS,
) -> WordbankImageRecord:
    try:
        return await asyncio.wait_for(pending.task, timeout=timeout_seconds)
    except TimeoutError as exc:
        pending.task.cancel()
        raise WordbankUserError(
            "词条设置失败：图片下载超时，无法加入词库。",
            key="wordbank.error.image_prepare_failed",
            reason="图片下载超时，无法加入词库。",
        ) from exc
    except WordbankUserError as exc:
        raise WordbankUserError(
            f"词条设置失败：{exc}",
            key="wordbank.error.image_prepare_failed",
            reason=str(exc),
        ) from exc
    except Exception as exc:
        reason = str(exc) or "图片处理失败，无法加入词库。"
        raise WordbankUserError(
            f"词条设置失败：{reason}",
            key="wordbank.error.image_prepare_failed",
            reason=reason,
        ) from exc


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


async def handle_add_with_media(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    locale: LocaleCode,
) -> str:
    if image_bytes is None:
        return await handle_add_text(service, event=event, text=text, locale=locale)

    source, raw_rule, trigger_mode = _parse_flags(text)
    pair = split_add_pair(source)
    is_group = isinstance(event, GroupMessageEvent)
    group_id = str(getattr(event, "group_id", ""))
    user_id = str(event.user_id)

    if pair is None:
        trigger_text = source.strip()
        if not trigger_text:
            raise RuleError(
                "触发词不能为空",
                key="wordbank.error.trigger_empty",
            )
        image = await media_service.ingest_image_bytes(image_bytes)
        result = await service.add_text_entry(
            trigger_text=trigger_text,
            response_text="",
            response_canonical_image_id=image.canonical_id,
            trigger_mode=trigger_mode,
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )
        return format_add_result(result, locale=locale)

    trigger_text, response_text = pair
    if trigger_text:
        image = await media_service.ingest_image_bytes(image_bytes)
        result = await service.add_text_entry(
            trigger_text=trigger_text,
            response_text=response_text,
            response_canonical_image_id=image.canonical_id,
            trigger_mode=trigger_mode,
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )
        return format_add_result(result, locale=locale)

    if response_text:
        image = await media_service.ingest_image_bytes(image_bytes)
        result = await service.add_image_entry(
            canonical_image_id=image.canonical_id,
            response_text=response_text,
            raw_rule=raw_rule,
            group_id=group_id,
            user_id=user_id,
            is_group=is_group,
        )
        return format_add_result(result, locale=locale)

    raise RuleError(
        "添加词条需要同时包含触发词和响应词",
        key="wordbank.error.add_pair_required",
    )


async def handle_guided_add_text(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_text: str,
    response_text: str,
    scope_text: str,
    response_canonical_image_id: int | None = None,
    advanced_text: str = "",
    locale: LocaleCode,
) -> str:
    is_group = isinstance(event, GroupMessageEvent)
    scope = parse_guided_scope_choice(scope_text, is_group=is_group)
    advanced = parse_guided_advanced_options(advanced_text)
    raw_rule = {"scope": scope, **advanced.raw_rule}
    result = await service.add_text_entry(
        trigger_text=trigger_text,
        response_text=response_text,
        response_canonical_image_id=response_canonical_image_id,
        trigger_mode=advanced.trigger_mode,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )
    return format_add_result(result, locale=locale)


async def handle_guided_add_image_trigger(
    service: WordbankService,
    *,
    event: MessageEvent,
    trigger_canonical_image_id: int,
    response_text: str,
    scope_text: str,
    response_canonical_image_id: int | None = None,
    advanced_text: str = "",
    locale: LocaleCode,
) -> str:
    is_group = isinstance(event, GroupMessageEvent)
    scope = parse_guided_scope_choice(scope_text, is_group=is_group)
    advanced = parse_guided_advanced_options(advanced_text)
    raw_rule = {"scope": scope, **advanced.raw_rule}
    result = await service.add_image_entry(
        canonical_image_id=trigger_canonical_image_id,
        response_text=response_text,
        response_canonical_image_id=response_canonical_image_id,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
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


async def handle_study_with_media(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    text: str,
    image_bytes: bytes | None,
    locale: LocaleCode,
) -> str:
    if image_bytes is None:
        return await handle_study_shortcut(
            service,
            event=event,
            text=text,
            locale=locale,
        )
    return await handle_add_with_media(
        service,
        media_service,
        event=event,
        text=text,
        image_bytes=image_bytes,
        locale=locale,
    )


async def handle_guided_study_shortcut(
    service: WordbankService,
    *,
    event: MessageEvent,
    trig_mode_text: str,
    group_block_text: str,
    trigger_text: str,
    response_text: str,
    weight_text: str,
    response_canonical_image_id: int | None = None,
    locale: LocaleCode,
) -> str:
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
    result = await service.add_text_entry(
        trigger_text=trigger_text,
        response_text=response_text,
        response_canonical_image_id=response_canonical_image_id,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=is_group,
    )
    return format_add_result(result, locale=locale)


async def handle_guided_study_image_trigger(
    service: WordbankService,
    *,
    event: MessageEvent,
    trig_mode_text: str,
    group_block_text: str,
    trigger_canonical_image_id: int,
    response_text: str,
    weight_text: str,
    response_canonical_image_id: int | None = None,
    locale: LocaleCode,
) -> str:
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
    result = await service.add_image_entry(
        canonical_image_id=trigger_canonical_image_id,
        response_text=response_text,
        response_canonical_image_id=response_canonical_image_id,
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

    if actor.group_id:
        vote_result = await service.request_delete_vote(
            entry_id=entry_id,
            group_id=actor.group_id,
            user_id=actor.user_id,
            threshold=DEFAULT_DELETE_VOTE_THRESHOLD,
        )
        if vote_result is not None:
            return format_delete_vote_result(vote_result, locale=locale)

    return tr(locale, "wordbank.delete.not_found", entry_id=entry_id)


async def handle_support_delete_vote(
    service: WordbankService,
    *,
    event: MessageEvent,
    vote_id_text: str,
    locale: LocaleCode,
) -> str:
    if not vote_id_text.isdigit():
        return tr(locale, "wordbank.error.vote_id_numeric")
    actor = build_mutation_actor(event)
    if not actor.group_id:
        return tr(locale, "wordbank.vote.not_found", vote_id=int(vote_id_text))
    result = await service.support_delete_vote(
        vote_id=int(vote_id_text),
        group_id=actor.group_id,
        user_id=actor.user_id,
    )
    if result is None:
        return tr(locale, "wordbank.vote.not_found", vote_id=int(vote_id_text))
    return format_delete_vote_result(result, locale=locale)


async def handle_delete_vote_status(
    service: WordbankService,
    *,
    event: MessageEvent,
    vote_id_text: str,
    locale: LocaleCode,
) -> str:
    if not vote_id_text.isdigit():
        return tr(locale, "wordbank.error.vote_id_numeric")
    actor = build_mutation_actor(event)
    if not actor.group_id:
        return tr(locale, "wordbank.vote.not_found", vote_id=int(vote_id_text))
    result = await service.get_delete_vote(
        int(vote_id_text),
        group_id=actor.group_id,
    )
    if result is None:
        return tr(locale, "wordbank.vote.not_found", vote_id=int(vote_id_text))
    return format_delete_vote_status(result, locale=locale)


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
    if action in SUPPORT_ALIASES:
        return await handle_support_delete_vote(
            service,
            event=event,
            vote_id_text=rest,
            locale=locale,
        )
    if action in VOTE_ALIASES:
        return await handle_delete_vote_status(
            service,
            event=event,
            vote_id_text=rest,
            locale=locale,
        )
    if action in RESTORE_ALIASES:
        return await handle_restore(
            service,
            event=event,
            entry_id_text=rest,
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
