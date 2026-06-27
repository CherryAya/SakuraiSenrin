"""Unified wordbank message shapes and fingerprints."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import md5
import json
import re
from typing import Literal
import unicodedata

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.lib.i18n.runtime import tr
from src.lib.messages import empty_message

MessageAtomKind = Literal["text", "image", "at", "event"]
MessageAtomPayload = dict[str, int | str]
_SPACE_RE = re.compile(r"\s+")
EVENT_TRIGGER_ESCAPED_PREFIX = "\\"
EVENT_TRIGGER_BRACKET_PAIRS = (("[", "]"), ("【", "】"))
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
    "event:at / [@] / 【@】 / [at] / 【at】 -> @到机器人",
    "event:mention / [提及] / 【提及】 -> 提及机器人",
    "event:poke / [戳一戳] / 【戳一戳】 -> 戳一戳",
    "event:bot_join / [bot加群] / 【bot加群】 -> 机器人自己进群",
    "event:member_join / [成员加群] / 【成员加群】 -> 其他人进群",
    "event:join / [新人加入] / 【新人加入】 / [新成员加入]",
    " / 【新成员加入】 / [有人加群] / 【有人加群】 -> 入群",
    "event:group_join -> 入群",
    "event:group_increase -> 群成员增加",
    "event:bot_leave / [bot退群] / 【bot退群】 -> 机器人自己退群",
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


def shape_from_message(
    message: Message,
    *,
    image_ids: dict[int, int] | None = None,
    event_names: tuple[str, ...] = (),
    preserve_blank_text: bool = False,
) -> MessageShape:
    image_ids = image_ids or {}
    atoms: list[MessageAtom] = []
    image_index = 0
    for segment in message:
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
            parts.append(f"[@:{atom.target_id}]")
        elif atom.kind == "event" and atom.event_name:
            parts.append(
                tr(
                    "zh-CN",
                    "wordbank.shape.event_ref",
                    event_name=atom.event_name,
                )
            )
    return _join_shape_text_parts(parts)


def shape_to_search_text(shape: MessageShape) -> str:
    texts: list[str] = []
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            texts.append(atom.text)
        elif atom.kind == "at" and atom.target_id:
            texts.append(f"at {atom.target_id}")
        elif atom.kind == "event" and atom.event_name:
            texts.append(atom.event_name)
    return _join_shape_text_parts(texts)


def shape_to_message(shape: MessageShape) -> Message:
    message = empty_message()
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            message += MessageSegment.text(atom.text)
        elif atom.kind == "at" and atom.target_id:
            message += MessageSegment.at(atom.target_id)
    return message


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
    if len(parts) == 1:
        return parts[0]
    joined = " ".join(part for part in parts if part)
    if not joined:
        return ""
    stripped = joined.strip()
    if stripped:
        return stripped
    return " "
