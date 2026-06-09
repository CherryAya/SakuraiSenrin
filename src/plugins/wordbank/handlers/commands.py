"""Wordbank command handlers."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any

from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.message import Message

from src.plugins.wordbank.services.core import (
    WordbankService,
    format_add_result,
    format_search_items,
)
from src.plugins.wordbank.services.media import WordbankMediaService
from src.plugins.wordbank.services.rules import RuleError, parse_legacy_study_text

ADD_ALIASES = {"add", "添加", "学习"}
SEARCH_ALIASES = {"search", "find", "查询", "搜索"}
DELETE_ALIASES = {"delete", "del", "remove", "删除"}
RESTORE_ALIASES = {"restore", "恢复"}
IMAGE_ALIASES = {"image", "img", "图片"}


@dataclass(slots=True, frozen=True)
class ParsedTextAdd:
    trigger_text: str
    response_text: str
    trigger_mode: str | None
    raw_rule: dict[str, Any]


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
        raise RuleError(f"参数解析失败: {exc}") from exc
    remaining: list[str] = []
    raw_rule: dict[str, Any] = {}
    trigger_mode: str | None = None
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"--mode", "-m"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError("--mode 需要提供触发模式")
            trigger_mode = tokens[idx]
        elif token in {"--scope", "-s"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError("--scope 需要提供生效范围")
            raw_rule["scope"] = tokens[idx]
        elif token in {"--prob", "--probability", "-p"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError("--prob 需要提供概率")
            raw_rule["probability"] = tokens[idx]
        elif token in {"--weight", "-w"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError("--weight 需要提供权重")
            raw_rule["weight"] = tokens[idx]
        elif token in {"--role", "--roles", "-r"}:
            idx += 1
            if idx >= len(tokens):
                raise RuleError("--role 需要提供角色")
            raw_rule["roles"] = tokens[idx]
        elif token == "--call":
            idx += 1
            if idx >= len(tokens):
                raise RuleError("--call 需要提供 window:min:max")
            parts = tokens[idx].split(":")
            if len(parts) != 3:
                raise RuleError("--call 格式应为 window:min:max")
            raw_rule["call_count"] = {
                "window_seconds": parts[0],
                "min": parts[1],
                "max": parts[2],
            }
        else:
            remaining.append(token)
        idx += 1
    return " ".join(remaining), raw_rule, trigger_mode


def parse_text_add_args(text: str) -> ParsedTextAdd:
    source, raw_rule, trigger_mode = _parse_flags(text)
    for sep in ("=>", "->", "回答", "回复"):
        if sep in source:
            trigger, response = source.split(sep, 1)
            trigger = trigger.strip()
            response = response.strip()
            if not trigger or not response:
                raise RuleError("添加词条需要同时包含触发词和响应词")
            return ParsedTextAdd(trigger, response, trigger_mode, raw_rule)
    raise RuleError("添加格式: wordbank add 触发词 => 响应词")


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
    return format_add_result(result)


async def handle_study_shortcut(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
) -> str:
    trigger, response, raw_rule = parse_legacy_study_text(text)
    result = await service.add_text_entry(
        trigger_text=trigger,
        response_text=response,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=isinstance(event, GroupMessageEvent),
    )
    return format_add_result(result)


async def handle_search(
    service: WordbankService,
    *,
    keyword: str,
) -> str:
    return format_search_items(await service.search(keyword.strip(), limit=10))


async def handle_delete(
    service: WordbankService,
    *,
    entry_id_text: str,
) -> str:
    if not entry_id_text.isdigit():
        return "词条 ID 必须是数字。"
    entry_id = int(entry_id_text)
    if await service.delete_entry(entry_id):
        return f"词条 #{entry_id} 已删除。"
    return f"未找到可删除的词条 #{entry_id}。"


async def handle_restore(
    service: WordbankService,
    *,
    entry_id_text: str,
) -> str:
    if not entry_id_text.isdigit():
        return "词条 ID 必须是数字。"
    entry_id = int(entry_id_text)
    if await service.restore_entry(entry_id):
        return f"词条 #{entry_id} 已恢复。"
    return f"未找到可恢复的词条 #{entry_id}。"


async def handle_add_image(
    service: WordbankService,
    media_service: WordbankMediaService,
    *,
    event: MessageEvent,
    image_bytes: bytes,
    text: str,
) -> str:
    source, raw_rule, _ = _parse_flags(text)
    response_text = source.strip()
    if not response_text:
        raise RuleError("图片词条需要提供响应词")
    image = await media_service.ingest_image_bytes(image_bytes)
    result = await service.add_image_entry(
        canonical_image_id=image.canonical_id,
        response_text=response_text,
        raw_rule=raw_rule,
        group_id=str(getattr(event, "group_id", "")),
        user_id=str(event.user_id),
        is_group=isinstance(event, GroupMessageEvent),
    )
    return format_add_result(result)


def wordbank_help_text() -> str:
    return (
        "词库命令:\n"
        "wordbank add 触发词 => 响应词 [--mode contains|fullmatch|prefix]\n"
        "wordbank search [关键词]\n"
        "wordbank delete <id>\n"
        "wordbank restore <id>\n"
        "wordbank image 响应词  并附带一张图片\n"
        "study 触发词 => 响应词"
    )


async def dispatch_wordbank_command(
    service: WordbankService,
    *,
    event: MessageEvent,
    text: str,
) -> str:
    action, rest = _split_command(text)
    if not action or action in {"help", "帮助"}:
        return wordbank_help_text()
    if action in ADD_ALIASES:
        return await handle_add_text(service, event=event, text=rest)
    if action in SEARCH_ALIASES:
        return await handle_search(service, keyword=rest)
    if action in DELETE_ALIASES:
        return await handle_delete(service, entry_id_text=rest)
    if action in RESTORE_ALIASES:
        return await handle_restore(service, entry_id_text=rest)
    if action in IMAGE_ALIASES:
        return "图片词条需要在命令消息中附带图片。"
    return f"未知词库子命令: {action}\n\n{wordbank_help_text()}"
