"""Lightweight text parsing helpers that preserve user input slices."""

from __future__ import annotations

from dataclasses import dataclass
import re

_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_LEADING_CQ_AT_RE = re.compile(r"^(?:\s*\[CQ:at,[^\]]+\])+\s*")


@dataclass(slots=True, frozen=True)
class TokenSpan:
    value: str
    start: int
    end: int


def tokenize_shell_like(text: str) -> tuple[TokenSpan, ...]:
    tokens: list[TokenSpan] = []
    index = 0
    length = len(text)

    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break

        start = index
        value_parts: list[str] = []
        while index < length and not text[index].isspace():
            char = text[index]
            if char in {'"', "'"}:
                quote = char
                index += 1
                while index < length:
                    quoted_char = text[index]
                    if quoted_char == quote:
                        index += 1
                        break
                    if quoted_char == "\\" and quote == '"' and index + 1 < length:
                        index += 1
                        value_parts.append(text[index])
                        index += 1
                        continue
                    value_parts.append(quoted_char)
                    index += 1
                else:
                    raise ValueError("No closing quotation")
                continue
            if char == "\\" and index + 1 < length:
                index += 1
                value_parts.append(text[index])
                index += 1
                continue
            value_parts.append(char)
            index += 1

        tokens.append(TokenSpan("".join(value_parts), start=start, end=index))

    return tuple(tokens)


def rest_after_token(text: str, token: TokenSpan) -> str:
    rest = text[token.end :]
    if rest and rest[0].isspace():
        return rest[1:]
    return rest


def split_command_text(text: str) -> tuple[str, str]:
    offset = len(text) - len(text.lstrip())
    source = text[offset:]
    if not source:
        return "", ""
    tokens = tokenize_shell_like(source)
    if not tokens:
        return "", ""
    action_token = tokens[0]
    rest = rest_after_token(source, action_token)
    return action_token.value.lower(), rest


def has_meaningful_text(text: str) -> bool:
    return any(not char.isspace() for char in text)


def normalize_cq_plain_text(
    text: str,
    *,
    strip_leading_at: bool = False,
    collapse_cq_only_text: bool = False,
) -> str:
    normalized = text.strip()
    if strip_leading_at:
        normalized = _LEADING_CQ_AT_RE.sub("", normalized).strip()
    if collapse_cq_only_text and normalized:
        without_cq = _CQ_CODE_RE.sub("", normalized)
        if not has_meaningful_text(without_cq):
            return ""
    return normalized


def join_tokens_with_original_spacing(
    text: str,
    tokens: tuple[TokenSpan, ...] | list[TokenSpan],
) -> str:
    if not tokens:
        return ""

    parts = [tokens[0].value]
    previous = tokens[0]
    for token in tokens[1:]:
        gap = text[previous.end : token.start]
        parts.append(gap if gap and gap.isspace() else " ")
        parts.append(token.value)
        previous = token
    return "".join(parts)
