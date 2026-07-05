"""Shared helpers for wordbank search card rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import WordbankSearchItem
from src.plugins.wordbank.message_model import (
    MessageShape,
    format_at_summary_text,
    format_event_summary_text,
)


@dataclass(slots=True, frozen=True)
class SearchCardContentBlock:
    kind: Literal["text", "image", "label"]
    text: str = ""
    image_id: int | None = None
    label: str = ""


@dataclass(slots=True, frozen=True)
class SearchCardResponseRenderItem:
    index: int
    response_item_id: int
    blocks: tuple[SearchCardContentBlock, ...]


def safe_field_label(locale: LocaleCode, key: str) -> str:
    labels = {
        "scope": {
            "zh-CN": "范围",
            "lzh": "範圍",
            "x-meme": "范围",
        },
        "probability": {
            "zh-CN": "概率",
            "lzh": "概率",
            "x-meme": "概率",
        },
        "weight": {
            "zh-CN": "权重",
            "lzh": "權重",
            "x-meme": "权重",
        },
    }
    locale_labels = labels.get(key, {})
    return locale_labels.get(locale, locale_labels.get("zh-CN", key))


def safe_meta_label(locale: LocaleCode) -> str:
    return {
        "zh-CN": "配置",
        "lzh": "配置",
        "x-meme": "配置",
    }.get(locale, "配置")


def build_search_card_footer_text(year: int) -> str:
    return f"© 2020-{year} SakuraiSenrin"


def summary_chips(
    *,
    keyword: str,
    field: str,
    creator_id: str,
    has_image: bool,
    locale: LocaleCode,
    field_label: str,
) -> tuple[str, ...]:
    return (
        tr(
            locale,
            "wordbank.search_card.summary.field",
            field=field_label,
        ),
        tr(
            locale,
            "wordbank.search_card.summary.keyword",
            keyword=keyword or tr(locale, "wordbank.search_card.none"),
        ),
        tr(
            locale,
            "wordbank.search_card.summary.has_image",
            has_image=tr(
                locale,
                (
                    "wordbank.search_card.boolean.yes"
                    if has_image
                    else "wordbank.search_card.boolean.no"
                ),
            ),
        ),
        tr(
            locale,
            "wordbank.search_card.summary.creator",
            creator_id=creator_id or tr(locale, "wordbank.search_card.none"),
        ),
    )


def fallback_match_label(
    *,
    has_image: bool,
    keyword: str,
    creator_id: str,
    locale: LocaleCode,
) -> str:
    if has_image and keyword:
        return tr(locale, "wordbank.search_card.match.text_image")
    if has_image:
        return tr(locale, "wordbank.search_card.match.image")
    if keyword:
        return tr(locale, "wordbank.search_card.match.text")
    if creator_id:
        return tr(locale, "wordbank.search_card.match.creator")
    return tr(locale, "wordbank.search_card.match.recent")


def response_preview(item: WordbankSearchItem, locale: LocaleCode) -> str:
    summaries = list(item.response_summaries[:3]) or (
        [item.response_text] if item.response_text else []
    )
    preview = "\n".join(summary for summary in summaries if summary)
    if item.remaining_response_count > 0:
        suffix = tr(
            locale,
            "wordbank.search_card.more_responses",
            count=item.remaining_response_count,
        )
        preview = f"{preview}\n{suffix}".strip() if preview else suffix.strip()
    return preview or tr(locale, "wordbank.search_card.none")


def trigger_preview_blocks(
    item: WordbankSearchItem,
    locale: LocaleCode,
) -> tuple[SearchCardContentBlock, ...]:
    return _content_blocks_from_shape_or_text(
        shape=item.trigger_shape,
        fallback_text=item.trigger_text,
        locale=locale,
    )


def response_preview_items(
    item: WordbankSearchItem,
    locale: LocaleCode,
) -> tuple[SearchCardResponseRenderItem, ...]:
    summaries = [summary for summary in item.response_summaries[:3] if summary]
    if not summaries and item.response_text:
        summaries = [item.response_text]
    if not summaries:
        summaries = [tr(locale, "wordbank.search_card.none")]
    response_ids = list(item.response_item_ids[: len(summaries)])
    while len(response_ids) < len(summaries):
        response_ids.append(0)
    return tuple(
        SearchCardResponseRenderItem(
            index=index,
            response_item_id=response_ids[index - 1],
            blocks=(
                _content_blocks_from_shape_or_text(
                    shape=item.response_shape,
                    fallback_text=summary,
                    locale=locale,
                )
                if index == 1
                else _content_blocks_from_text(summary, locale)
            ),
        )
        for index, summary in enumerate(summaries, start=1)
    )


def search_card_image_ids(
    item: WordbankSearchItem,
    locale: LocaleCode,
) -> tuple[int, ...]:
    image_ids: list[int] = []
    for block in trigger_preview_blocks(item, locale):
        if block.kind == "image" and block.image_id is not None:
            image_ids.append(block.image_id)
    for response in response_preview_items(item, locale):
        for block in response.blocks:
            if block.kind == "image" and block.image_id is not None:
                image_ids.append(block.image_id)
    return tuple(dict.fromkeys(image_ids))


def preview_summary_count(item: WordbankSearchItem) -> int:
    summaries = [summary for summary in item.response_summaries[:3] if summary]
    if summaries:
        return len(summaries)
    return 1 if item.response_text else 0


def has_folded_preview(item: WordbankSearchItem) -> bool:
    shown_count = preview_summary_count(item)
    return item.response_count > max(1, shown_count)


def fold_hint(item: WordbankSearchItem, locale: LocaleCode) -> str:
    shown_count = preview_summary_count(item)
    if item.response_count <= max(1, shown_count):
        return ""
    return tr(
        locale,
        "wordbank.search_card.folded_hint",
        total=item.response_count,
        shown=max(1, shown_count),
        group_id=item.trigger_group_id,
    )


def folded_preview_note(item: WordbankSearchItem, locale: LocaleCode) -> str:
    return f"💬 {fold_hint(item, locale)}"


def line_height(font: Any) -> int:
    from PIL import Image, ImageDraw

    bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox((0, 0), "Ag", font=font)
    return int(bbox[3] - bbox[1] + 8)


def text_width(text: str, font: Any) -> int:
    from PIL import Image, ImageDraw

    return int(
        ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(
            text,
            font=font,
        )
    )


def _content_blocks_from_shape_or_text(
    *,
    shape: MessageShape | None,
    fallback_text: str,
    locale: LocaleCode,
) -> tuple[SearchCardContentBlock, ...]:
    if shape is not None and not shape.is_empty():
        blocks = _content_blocks_from_shape(shape)
        if blocks:
            return blocks
    return _content_blocks_from_text(fallback_text, locale)


def _content_blocks_from_shape(
    shape: MessageShape,
) -> tuple[SearchCardContentBlock, ...]:
    blocks: list[SearchCardContentBlock] = []
    for atom in shape.atoms:
        if atom.kind == "text" and atom.text:
            blocks.append(SearchCardContentBlock(kind="text", text=atom.text))
        elif atom.kind == "image" and atom.canonical_image_id is not None:
            blocks.append(
                SearchCardContentBlock(
                    kind="image",
                    image_id=atom.canonical_image_id,
                )
            )
        elif atom.kind == "at" and atom.target_id:
            blocks.append(
                SearchCardContentBlock(
                    kind="label",
                    label=f"[{format_at_summary_text(atom.target_id)}]",
                )
            )
        elif atom.kind == "event" and atom.event_name:
            event_label = format_event_summary_text(atom.event_name, atom.target_id)
            blocks.append(
                SearchCardContentBlock(
                    kind="label",
                    label=f"[{event_label}]",
                )
            )
    return tuple(blocks)


def _content_blocks_from_text(
    text: str,
    locale: LocaleCode,
) -> tuple[SearchCardContentBlock, ...]:
    normalized = text.strip()
    if normalized:
        return (SearchCardContentBlock(kind="text", text=text),)
    return (
        SearchCardContentBlock(
            kind="text",
            text=tr(locale, "wordbank.search_card.none"),
        ),
    )
