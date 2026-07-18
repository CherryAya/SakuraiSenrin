"""Shared helpers for wordbank group detail card rendering."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import (
    WordbankGroupDetail,
    WordbankResponseItemDetail,
)
from src.plugins.wordbank.message_model import (
    MessageAtom,
    MessageShape,
    format_at_fallback_text,
    format_placeholder_summary_text,
)


def build_copyright_text(year: int) -> str:
    return f"© 2020-{year} SakuraiSenrin"


def format_enabled(enabled: int, locale: LocaleCode) -> str:
    return tr(
        locale,
        "wordbank.state.enabled" if enabled else "wordbank.state.disabled",
    )


def format_rule_text(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    role = str(rule.get("roles", "") or "").strip()
    if role:
        parts.append(f"roles={role}")
    call_count = rule.get("call_count")
    if isinstance(call_count, dict):
        window_seconds = int(call_count.get("window_seconds", 0))
        min_count = int(call_count.get("min", 0))
        max_count = int(call_count.get("max", 0))
        parts.append(f"call={window_seconds}:{min_count}:{max_count}")
    return ", ".join(parts) if parts else "-"


def line_height(font: Any) -> int:
    bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox((0, 0), "Ag", font=font)
    return int(bbox[3] - bbox[1] + 8)


def text_width(text: str, font: Any) -> int:
    return int(ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(text, font=font))


def centered_text_origin(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Any,
    *,
    canvas_width: int,
    y: int,
) -> tuple[int, int]:
    rendered_width = int(draw.textlength(text, font=font))
    return (int((canvas_width - rendered_width) / 2), y)


def summary_chips(
    detail: WordbankGroupDetail,
    *,
    locale: LocaleCode,
) -> tuple[str, ...]:
    active_response_count = sum(
        1
        for item in detail.responses
        if item.status == "approved" and item.enabled == 1 and item.deleted_at == 0
    )
    raw_lines = (
        tr(
            locale,
            "wordbank.group.card.summary",
            group_id=detail.trigger_group_id,
            status=detail.status,
            created_by=detail.created_by,
        ),
        tr(
            locale,
            "wordbank.group.card.summary_extra",
            probability=f"{detail.probability:g}",
            response_count=len(detail.responses),
            active_response_count=active_response_count,
        ),
    )
    return tuple(
        part.strip() for line in raw_lines for part in line.split("  ") if part.strip()
    )


def atom_text(atom: MessageAtom, locale: LocaleCode) -> str:
    if atom.kind == "text":
        return atom.text
    if atom.kind == "at" and atom.target_id:
        return format_at_fallback_text(atom.target_id)
    if atom.kind == "event" and atom.event_name:
        return tr(locale, "wordbank.shape.event_ref", event_name=atom.event_name)
    if atom.kind == "placeholder" and atom.placeholder_name:
        return format_placeholder_summary_text(atom.placeholder_name)
    return ""


def shape_blocks(
    shape: MessageShape,
    *,
    locale: LocaleCode,
    preview_bytes: dict[int, bytes | None],
) -> tuple[tuple[str, str, int | None], ...]:
    blocks: list[tuple[str, str, int | None]] = []
    text_buffer: list[str] = []
    for atom in shape.atoms:
        if atom.kind == "image" and atom.canonical_image_id is not None:
            if not preview_bytes.get(atom.canonical_image_id):
                continue
            if text_buffer:
                text = "".join(text_buffer).strip()
                if text:
                    blocks.append(("text", text, None))
                text_buffer = []
            blocks.append(("image", "", atom.canonical_image_id))
            continue

        rendered = atom_text(atom, locale)
        if not rendered:
            continue
        if text_buffer and not text_buffer[-1].endswith((" ", "\n")):
            if atom.kind in {"at", "event"}:
                text_buffer.append(" ")
        text_buffer.append(rendered)

    if text_buffer:
        text = "".join(text_buffer).strip()
        if text:
            blocks.append(("text", text, None))

    if not blocks:
        blocks.append(("text", tr(locale, "wordbank.search_card.none"), None))
    return tuple(blocks)


def measure_preview_size(
    image_bytes: bytes,
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, int] | None:
    try:
        with Image.open(BytesIO(image_bytes)) as preview:
            prepared = preview.convert("RGB")
            width, height = prepared.size
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    scale = min(max_width / width, max_height / height, 1.0)
    return (max(1, int(width * scale)), max(1, int(height * scale)))


def prepare_preview_image(
    image_bytes: bytes,
    *,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    measured = measure_preview_size(
        image_bytes,
        max_width=max_width,
        max_height=max_height,
    )
    if measured is None:
        return None
    try:
        with Image.open(BytesIO(image_bytes)) as preview:
            prepared = preview.convert("RGB")
            return ImageOps.contain(prepared, measured).copy()
    except Exception:
        return None


def response_meta_text(
    response: WordbankResponseItemDetail,
    *,
    locale: LocaleCode,
) -> str:
    meta_line = tr(
        locale,
        "wordbank.group.card.response_label",
        response_item_id=response.response_item_id,
        status=response.status,
        enabled=format_enabled(response.enabled, locale),
        scope=response.scope,
        weight=response.weight,
    )
    rule_line = (
        f"{tr(locale, 'wordbank.group.card.rule_label')}: "
        f"{format_rule_text(response.rule)}"
    )
    return f"{meta_line}  ·  {rule_line}"


def paste_rounded_image(
    image: Image.Image,
    preview: Image.Image,
    origin: tuple[int, int],
    *,
    radius: int,
) -> None:
    mask = Image.new("L", preview.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (0, 0, preview.width, preview.height),
        radius=radius,
        fill=255,
    )
    image.paste(preview, origin, mask)


def wrap_text(
    text: str,
    font: Any,
    *,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for raw_line in text.splitlines() or [text]:
        if not raw_line:
            lines.append("")
            continue
        current = ""
        for char in raw_line:
            candidate = f"{current}{char}"
            if text_width(candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = char
        if current:
            lines.append(current)
    if not lines:
        return [""]
    if max_lines is None or len(lines) <= max_lines:
        return [truncate_line(line, font, max_width) for line in lines]
    truncated = lines[:max_lines]
    truncated[-1] = truncate_line(f"{truncated[-1]}...", font, max_width)
    return truncated


def truncate_line(text: str, font: Any, max_width: int) -> str:
    if text_width(text, font) <= max_width:
        return text
    candidate = text
    while candidate and text_width(f"{candidate}...", font) > max_width:
        candidate = candidate[:-1]
    return f"{candidate}..."
