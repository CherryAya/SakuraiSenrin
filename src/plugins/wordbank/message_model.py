"""Unified wordbank message shapes and fingerprints."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import md5
import json
import re
from typing import Any, Literal
import unicodedata

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr

MessageAtomKind = Literal["text", "image", "at", "event"]
MessageAtomPayload = dict[str, int | str]
type MessageInput = (
    Message | MessageSegment | str | list[Any] | tuple[Any, ...] | dict[str, Any]
)
_SPACE_RE = re.compile(r"\s+")
_RESPONSE_PLACEHOLDER_RE = re.compile(r"(\[[^\]]+\]|【[^】]+】)")
EVENT_TRIGGER_ESCAPED_PREFIX = "\\"
EVENT_TRIGGER_BRACKET_PAIRS = (("[", "]"), ("【", "】"))
RESPONSE_TARGET_SENDER = "__sender__"
EVENT_TRIGGER_NAMES = frozenset(
    {
        "event:at",
        "event:mention",
        "event:poke",
        "event:join",
        "event:bot_join",
        "event:member_join",
        "event:group_join",
        "event:group_increase",
        "event:leave",
        "event:bot_leave",
        "event:member_leave",
        "event:group_leave",
        "event:group_decrease",
    }
)
EVENT_TRIGGER_ALIASES = {
    "@": "event:at",
    "at": "event:at",
    "戳一戳": "event:poke",
    "提及": "event:mention",
    "新人加入": "event:join",
    "新成员加入": "event:join",
    "有人加群": "event:join",
    "bot加群": "event:bot_join",
    "bot加入": "event:bot_join",
    "凛凛加群": "event:bot_join",
    "凛凛加入": "event:bot_join",
    "成员加群": "event:member_join",
    "成员加入": "event:member_join",
    "成员退群": "event:member_leave",
    "有人退群": "event:leave",
    "离群": "event:leave",
    "bot退群": "event:bot_leave",
    "凛凛退群": "event:bot_leave",
    "成员离群": "event:member_leave",
}
EVENT_TRIGGER_DISPLAY_LINES = (
    "event:at / [@] / 【@】 / [at] / 【at】 -> @到凛凛",
    "event:mention / [提及] / 【提及】 -> 提及凛凛",
    "event:poke / [戳一戳] / 【戳一戳】 -> 戳一戳",
    "event:bot_join / [bot加群] / 【bot加群】 -> 凛凛自己进群",
    "event:member_join / [成员加群] / 【成员加群】 -> 其他人进群",
    "event:join / [新人加入] / 【新人加入】 / [新成员加入]",
    " / 【新成员加入】 / [有人加群] / 【有人加群】 -> 入群",
    "event:group_join -> 入群",
    "event:group_increase -> 群成员增加",
    "event:bot_leave / [bot退群] / 【bot退群】 -> 凛凛自己退群",
    "event:member_leave / [成员离群] / 【成员离群】 -> 其他人退群",
    "event:leave / [成员退群] / 【成员退群】 / [有人退群]",
    " / 【有人退群】 / [离群] / 【离群】 -> 离群",
    "event:group_leave -> 离群",
    "event:group_decrease -> 群成员减少",
)


@dataclass(slots=True, frozen=True)
class MessageAtom:
    kind: MessageAtomKind
    text: str = ""
    canonical_image_id: int | None = None
    target_id: str = ""
    event_name: str = ""


@dataclass(slots=True, frozen=True)
class MessageShape:
    atoms: tuple[MessageAtom, ...]

    def is_empty(self) -> bool:
        return not self.atoms


@dataclass(slots=True, frozen=True)
class MessageFingerprint:
    exact_md5: str
    structure_key: str
    summary_text: str
    search_text: str
    search_tokens: str
    image_keys: str


def shape_from_text(text: str, *, preserve_blank_text: bool = False) -> MessageShape:
    if not is_valid_message_text(text, preserve_blank_text=preserve_blank_text):
        return MessageShape(())
    return MessageShape(atoms=((MessageAtom(kind="text", text=text),)))


def shape_from_image(canonical_image_id: int) -> MessageShape:
    return MessageShape(
        (MessageAtom(kind="image", canonical_image_id=canonical_image_id),)
    )


def shape_from_event(event_name: str) -> MessageShape:
    normalized = normalize_text(event_name, casefold=False)
    return MessageShape((MessageAtom(kind="event", event_name=normalized),))


def shape_from_response_text(text: str) -> MessageShape:
    if not is_valid_message_text(text, preserve_blank_text=False):
        return MessageShape(())
    atoms: list[MessageAtom] = []
    last_index = 0
    for match in _RESPONSE_PLACEHOLDER_RE.finditer(text):
        if match.start() > last_index:
            _append_response_text_atom(atoms, text[last_index : match.start()])
        placeholder_atom = _parse_response_placeholder(match.group(0))
        if placeholder_atom is None:
            _append_response_text_atom(atoms, match.group(0))
        else:
            atoms.append(placeholder_atom)
        last_index = match.end()
    if last_index < len(text):
        _append_response_text_atom(atoms, text[last_index:])
    return MessageShape(tuple(atoms))


def shape_from_trigger_text(text: str) -> MessageShape:
    event_name = extract_event_trigger_name(text)
    if event_name is not None:
        return shape_from_event(event_name)
    return shape_from_text(unescape_trigger_text_literal(text))


def extract_event_trigger_name(text: str) -> str | None:
    normalized = normalize_text(text, casefold=True)
    if normalized.startswith(EVENT_TRIGGER_ESCAPED_PREFIX):
        return None
    alias_event = _resolve_event_trigger_alias(normalized)
    if alias_event is not None:
        return alias_event
    return normalized if normalized in EVENT_TRIGGER_NAMES else None


def unescape_trigger_text_literal(text: str) -> str:
    if not text.startswith(EVENT_TRIGGER_ESCAPED_PREFIX):
        return text
    if len(text) >= 2 and text[1] == EVENT_TRIGGER_ESCAPED_PREFIX:
        return text[1:]
    return text[1:]


def event_trigger_display_lines() -> tuple[str, ...]:
    return EVENT_TRIGGER_DISPLAY_LINES


def _resolve_event_trigger_alias(normalized: str) -> str | None:
    if normalized in EVENT_TRIGGER_ALIASES:
        return EVENT_TRIGGER_ALIASES[normalized]
    bracket_content = _extract_bracket_content(normalized)
    if bracket_content is None:
        return None
    return EVENT_TRIGGER_ALIASES.get(bracket_content)


def _extract_bracket_content(text: str) -> str | None:
    for left, right in EVENT_TRIGGER_BRACKET_PAIRS:
        if text.startswith(left) and text.endswith(right) and len(text) > 2:
            return text[len(left) : -len(right)].strip()
    return None


def combine_shapes(*shapes: MessageShape) -> MessageShape:
    atoms: list[MessageAtom] = []
    for shape in shapes:
        atoms.extend(shape.atoms)
    return MessageShape(tuple(atoms))


def iter_message_segments(message: MessageInput) -> Iterator[MessageSegment]:
    if isinstance(message, Message):
        yield from message
        return
    if isinstance(message, MessageSegment):
        yield message
        return
    if isinstance(message, str):
        if message:
            yield MessageSegment.text(message)
        return
    if isinstance(message, (list, tuple)):
        for item in message:
            yield from iter_message_segments(item)
        return
    if not isinstance(message, dict):
        return
    segment = _coerce_message_segment(message)
    if segment is not None:
        yield segment
        return
    for key in ("content", "message", "messages", "raw_message"):
        nested = message.get(key)
        if nested is not None:
            yield from iter_message_segments(nested)
    data = message.get("data")
    if isinstance(data, dict):
        for key in ("content", "message", "messages", "raw_message"):
            nested = data.get(key)
            if nested is not None:
                yield from iter_message_segments(nested)


def shape_from_message(
    message: Message,
    *,
    image_ids: dict[int, int] | None = None,
    event_names: tuple[str, ...] = (),
    preserve_blank_text: bool = False,
) -> MessageShape:
    return shape_from_message_input(
        message,
        image_ids=image_ids,
        event_names=event_names,
        preserve_blank_text=preserve_blank_text,
    )


def shape_from_message_input(
    message: MessageInput,
    *,
    image_ids: dict[int, int] | None = None,
    event_names: tuple[str, ...] = (),
    preserve_blank_text: bool = False,
) -> MessageShape:
    image_ids = image_ids or {}
    atoms: list[MessageAtom] = []
    image_index = 0
    for segment in iter_message_segments(message):
        if segment.type == "text":
            raw_text = str(segment.data.get("text", ""))
            if is_valid_message_text(
                raw_text,
                preserve_blank_text=preserve_blank_text,
            ):
                atoms.append(MessageAtom(kind="text", text=raw_text))
        elif segment.type == "image":
            canonical_image_id = image_ids.get(image_index)
            if canonical_image_id is not None:
                atoms.append(
                    MessageAtom(kind="image", canonical_image_id=canonical_image_id)
                )
            image_index += 1
        elif segment.type == "at":
            target_id = str(segment.data.get("qq", "") or "").strip()
            if target_id:
                atoms.append(MessageAtom(kind="at", target_id=target_id))
    for event_name in event_names:
        if event_name:
            atoms.append(MessageAtom(kind="event", event_name=event_name))
    return MessageShape(tuple(atoms))


def shape_from_forward_message(message: MessageInput) -> MessageShape:
    atoms: list[MessageAtom] = []
    for segment in iter_message_segments(message):
        if segment.type == "text":
            raw_text = str(segment.data.get("text", ""))
            if is_valid_message_text(raw_text, preserve_blank_text=False):
                atoms.append(MessageAtom(kind="text", text=raw_text))
        elif segment.type == "image":
            atoms.append(MessageAtom(kind="image"))
        elif segment.type == "at":
            target_id = str(segment.data.get("qq", "") or "").strip()
            if target_id:
                atoms.append(MessageAtom(kind="at", target_id=target_id))
    return MessageShape(tuple(atoms))


def _coerce_message_segment(raw: dict[str, Any]) -> MessageSegment | None:
    segment_type = raw.get("type")
    data = raw.get("data")
    if not isinstance(segment_type, str) or not isinstance(data, dict):
        return None
    try:
        return MessageSegment(segment_type, data)
    except TypeError:
        return None


def shape_to_payload(shape: MessageShape) -> str:
    return json.dumps(
        [_atom_payload(atom) for atom in shape.atoms],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def shape_from_payload(payload: str) -> MessageShape:
    if not payload:
        return MessageShape(())
    raw_items = json.loads(payload)
    atoms: list[MessageAtom] = []
    for raw_item in raw_items:
        item = dict(raw_item)
        kind = str(item.get("kind", "") or "")
        atoms.append(
            MessageAtom(
                kind=kind,  # type: ignore[arg-type]
                text=str(item.get("text", "") or ""),
                canonical_image_id=(
                    int(item["canonical_image_id"])
                    if item.get("canonical_image_id") is not None
                    else None
                ),
                target_id=str(item.get("target_id", "") or ""),
                event_name=str(item.get("event_name", "") or ""),
            )
        )
    return MessageShape(tuple(atoms))


def fingerprint_shape(shape: MessageShape) -> MessageFingerprint:
    payload = shape_to_payload(shape)
    return MessageFingerprint(
        exact_md5=md5(payload.encode("utf-8")).hexdigest(),
        structure_key="|".join(atom.kind for atom in shape.atoms),
        summary_text=shape_to_summary_text(shape),
        search_text=shape_to_search_text(shape),
        search_tokens=_build_ngram_tokens(shape_to_search_text(shape)),
        image_keys=_shape_image_keys(shape),
    )


def shape_to_summary_text(shape: MessageShape) -> str:
    parts: list[str] = []
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            parts.append(atom.text)
        elif atom.kind == "image" and atom.canonical_image_id is not None:
            parts.append(
                tr(
                    "zh-CN",
                    "wordbank.shape.image_ref",
                    image_id=atom.canonical_image_id,
                )
            )
        elif atom.kind == "at" and atom.target_id:
            parts.append(format_at_summary_text(atom.target_id))
        elif atom.kind == "event" and atom.event_name:
            parts.append(format_event_summary_text(atom.event_name, atom.target_id))
    return _join_shape_text_parts(parts)


def shape_to_search_text(shape: MessageShape) -> str:
    texts: list[str] = []
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            texts.append(atom.text)
        elif atom.kind == "at" and atom.target_id:
            texts.append(f"at {atom.target_id}")
        elif atom.kind == "event" and atom.event_name:
            if atom.target_id:
                texts.append(f"{atom.event_name} {atom.target_id}")
            else:
                texts.append(atom.event_name)
    return _join_shape_text_parts(texts)


def format_at_fallback_text(target_id: str) -> str:
    if target_id == "all":
        return "@全体成员"
    return f"@用户({target_id})"


def is_response_sender_target(target_id: str) -> bool:
    return target_id == RESPONSE_TARGET_SENDER


def is_safe_executable_at_target(target_id: str) -> bool:
    if is_response_sender_target(target_id):
        return True
    return target_id.isdigit()


def format_at_summary_text(target_id: str) -> str:
    if is_response_sender_target(target_id):
        return "@触发者"
    return format_at_fallback_text(target_id)


def format_event_summary_text(event_name: str, target_id: str = "") -> str:
    if event_name == "event:poke":
        if is_response_sender_target(target_id):
            return "戳一戳触发者"
        if target_id:
            return f"戳一戳用户({target_id})"
    return tr(
        "zh-CN",
        "wordbank.shape.event_ref",
        event_name=event_name,
    )


def _shape_image_keys(shape: MessageShape) -> str:
    ids = [
        str(atom.canonical_image_id)
        for atom in shape.atoms
        if atom.kind == "image" and atom.canonical_image_id is not None
    ]
    if not ids:
        return ""
    return "|" + "|".join(ids) + "|"


def _atom_payload(atom: MessageAtom) -> MessageAtomPayload:
    payload: MessageAtomPayload = {"kind": atom.kind}
    if atom.text:
        payload["text"] = atom.text
    if atom.canonical_image_id is not None:
        payload["canonical_image_id"] = atom.canonical_image_id
    if atom.target_id:
        payload["target_id"] = atom.target_id
    if atom.event_name:
        payload["event_name"] = atom.event_name
    return payload


def _build_ngram_tokens(text_value: str, *, max_gram_size: int = 3) -> str:
    condensed = normalize_text(text_value).replace(" ", "")
    if not condensed:
        return ""
    tokens: list[str] = []
    for gram_size in range(1, min(max_gram_size, len(condensed)) + 1):
        for index in range(0, len(condensed) - gram_size + 1):
            tokens.append(condensed[index : index + gram_size])
    return " ".join(dict.fromkeys(tokens))


def normalize_text(text: str, *, casefold: bool = True) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.casefold() if casefold else normalized


def _append_response_text_atom(atoms: list[MessageAtom], text: str) -> None:
    if is_valid_message_text(text, preserve_blank_text=False):
        atoms.append(MessageAtom(kind="text", text=text))


def _parse_response_placeholder(raw_text: str) -> MessageAtom | None:
    content = _extract_bracket_content(raw_text)
    if content is None:
        return None
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", content).strip())
    if compact == "@触发者":
        return MessageAtom(kind="at", target_id=RESPONSE_TARGET_SENDER)
    if compact == "戳触发者":
        return MessageAtom(
            kind="event",
            event_name="event:poke",
            target_id=RESPONSE_TARGET_SENDER,
        )
    if compact.startswith("戳:"):
        target_id = compact.removeprefix("戳:").strip()
        if target_id.isdigit():
            return MessageAtom(
                kind="event",
                event_name="event:poke",
                target_id=target_id,
            )
    return None


def normalize_message_text(
    text: str,
    *,
    casefold: bool = True,
    preserve_blank_text: bool = False,
) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    collapsed = _SPACE_RE.sub(" ", normalized)
    if collapsed == "":
        return ""
    if preserve_blank_text and collapsed.strip() == "":
        return " "
    return normalize_text(collapsed, casefold=casefold)


def is_valid_message_text(
    text: str,
    *,
    preserve_blank_text: bool = False,
) -> bool:
    return bool(
        normalize_message_text(
            text,
            casefold=False,
            preserve_blank_text=preserve_blank_text,
        )
    )


def _join_shape_text_parts(parts: list[str]) -> str:
    joined = ""
    for part in parts:
        if not part:
            continue
        if not joined:
            joined = part
            continue
        if joined[-1].isspace() or part[0].isspace():
            joined += part
            continue
        joined += f" {part}"
    if not joined:
        return ""
    stripped = joined.strip()
    return stripped or " "
