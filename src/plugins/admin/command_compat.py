from __future__ import annotations

from collections.abc import Iterable
import shlex

import nonebot
from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.rule import Rule


def _as_plain_text(message: Message | str) -> str:
    if isinstance(message, Message):
        return message.extract_plain_text()
    return str(message)


def _strip_command_start(text: str) -> str:
    stripped = text.strip()
    command_start = getattr(nonebot.get_driver().config, "command_start", None)
    starts = (
        tuple(sorted(command_start, key=len, reverse=True)) if command_start else ()
    )
    for start in starts or ("#", "/", "＃", "井"):
        if stripped.startswith(start):
            return stripped[len(start) :].strip()
    return stripped


def _matches_candidate(text: str, candidate: str) -> bool:
    lowered = text.lower()
    candidate_lower = candidate.lower()
    return lowered == candidate_lower or lowered.startswith(f"{candidate_lower} ")


def build_admin_subcommand_rule(
    subcommand: str,
    *,
    aliases: Iterable[str] = (),
) -> Rule:
    candidates = (f"admin {subcommand}", f"admin.{subcommand}", *aliases)

    async def _(event: MessageEvent) -> bool:
        stripped = _strip_command_start(event.get_plaintext())
        return any(_matches_candidate(stripped, candidate) for candidate in candidates)

    return Rule(_)


def extract_admin_subcommand_args(
    message: Message | str,
    *,
    subcommand: str,
) -> list[str]:
    tokens = _as_plain_text(message).strip().split()
    if tokens and tokens[0].lower() == subcommand.lower():
        return tokens[1:]
    return tokens


def extract_admin_subcommand_argv(
    message: Message | str,
    *,
    subcommand: str,
) -> list[str]:
    text = _as_plain_text(message).strip()
    if not text:
        return []

    lowered = text.lower()
    subcommand_lower = subcommand.lower()
    if lowered == subcommand_lower:
        return []
    if lowered.startswith(f"{subcommand_lower} "):
        text = text[len(subcommand) :].lstrip()

    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()
