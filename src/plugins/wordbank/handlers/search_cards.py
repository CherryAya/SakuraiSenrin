"""Pillow search card rendering for wordbank search results."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from typing import Any

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.message import Message
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.types import LocaleCode
from src.plugins.wordbank.database.types import WordbankSearchItem

CARD_WIDTH = 1320
CARD_PADDING_X = 56
CARD_PADDING_Y = 48
CARD_ITEM_GAP = 22
CARD_ITEM_PADDING = 26
CARD_SUMMARY_GAP = 14
CARD_LINE_GAP = 10
CARD_MAX_TEXT_WIDTH = CARD_WIDTH - CARD_PADDING_X * 2 - CARD_ITEM_PADDING * 2


@dataclass(slots=True, frozen=True)
class SearchCardQuery:
    keyword: str
    field: str
    creator_id: str
    has_image: bool
    page: int
    total_count: int
    limit: int

    @property
    def total_pages(self) -> int:
        if self.total_count <= 0:
            return 1
        return max(1, math.ceil(self.total_count / max(self.limit, 1)))


class SearchResultCardRenderer:
    BG = "#FFF9FB"
    PANEL = "#FFFFFF"
    HEADER = "#2E2533"
    BODY = "#5E5263"
    MUTED = "#8C7A88"
    ACCENT = "#E2799D"
    ACCENT_SOFT = "#FFE8F0"
    BORDER = "#F2D7E2"

    def __init__(self) -> None:
        self.title_font = self._load_font(38)
        self.summary_font = self._load_font(22)
        self.item_title_font = self._load_font(24)
        self.item_body_font = self._load_font(22)
        self.item_meta_font = self._load_font(20)

    def render(
        self,
        *,
        items: tuple[WordbankSearchItem, ...],
        query: SearchCardQuery,
        locale: LocaleCode,
    ) -> bytes:
        _ = locale
        height = self._measure_height(items, query)
        image = Image.new("RGB", (CARD_WIDTH, height), self.BG)
        draw = ImageDraw.Draw(image)

        cursor_y = CARD_PADDING_Y
        cursor_y = self._draw_header(draw, query, cursor_y)
        cursor_y += 22
        cursor_y = self._draw_summary(draw, query, cursor_y)
        cursor_y += 28

        if items:
            for index, item in enumerate(items, start=1):
                cursor_y = self._draw_item(draw, item, query, index, cursor_y)
                cursor_y += CARD_ITEM_GAP
        else:
            cursor_y = self._draw_empty_state(draw, query, cursor_y)

        cursor_y += 8
        self._draw_footer(draw, query, cursor_y)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _measure_height(
        self,
        items: tuple[WordbankSearchItem, ...],
        query: SearchCardQuery,
    ) -> int:
        height = CARD_PADDING_Y
        height += self._line_height(self.title_font) + 12
        height += self._summary_block_height(query) + 22
        if items:
            for index, item in enumerate(items, start=1):
                height += self._item_block_height(item, index)
                if index < len(items):
                    height += CARD_ITEM_GAP
        else:
            height += 120
        height += 64
        return height

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        cursor_y: int,
    ) -> int:
        title = "词库搜索结果"
        page_text = f"第 {query.page} / {query.total_pages} 页"
        draw.text(
            (CARD_PADDING_X, cursor_y),
            title,
            font=self.title_font,
            fill=self.HEADER,
        )
        page_width = int(draw.textlength(page_text, font=self.summary_font))
        draw.text(
            (CARD_WIDTH - CARD_PADDING_X - page_width, cursor_y + 10),
            page_text,
            font=self.summary_font,
            fill=self.MUTED,
        )
        return cursor_y + self._line_height(self.title_font)

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        cursor_y: int,
    ) -> int:
        lines = self._summary_lines(query)
        box_height = self._summary_block_height(query)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + box_height,
            ),
            radius=28,
            fill=self.ACCENT_SOFT,
            outline=self.BORDER,
            width=2,
        )
        text_y = cursor_y + 20
        for line in lines:
            draw.text(
                (CARD_PADDING_X + 24, text_y),
                line,
                font=self.summary_font,
                fill=self.BODY,
            )
            text_y += self._line_height(self.summary_font) + CARD_SUMMARY_GAP
        return cursor_y + box_height

    def _draw_item(
        self,
        draw: ImageDraw.ImageDraw,
        item: WordbankSearchItem,
        query: SearchCardQuery,
        index: int,
        cursor_y: int,
    ) -> int:
        height = self._item_block_height(item, index)
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + height,
            ),
            radius=28,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )

        inner_x = CARD_PADDING_X + CARD_ITEM_PADDING
        inner_y = cursor_y + CARD_ITEM_PADDING

        badge = f"{index + (query.page - 1) * query.limit:02d}"
        badge_bbox = draw.textbbox((0, 0), badge, font=self.item_title_font)
        badge_width = badge_bbox[2] - badge_bbox[0] + 26
        badge_height = badge_bbox[3] - badge_bbox[1] + 12
        draw.rounded_rectangle(
            (
                inner_x,
                inner_y,
                inner_x + badge_width,
                inner_y + badge_height,
            ),
            radius=18,
            fill=self.ACCENT_SOFT,
        )
        draw.text(
            (inner_x + 13, inner_y + 6),
            badge,
            font=self.item_title_font,
            fill=self.ACCENT,
        )

        meta_text = (
            f"#{item.entry_id}  "
            f"[{item.status}/{item.trigger_mode}/{item.scope}]  "
            f"创建者: {item.created_by}"
        )
        draw.text(
            (inner_x + badge_width + 18, inner_y + 8),
            meta_text,
            font=self.item_meta_font,
            fill=self.MUTED,
        )

        body_y = int(inner_y + badge_height + 18)
        for label, value in (
            ("触发", item.trigger_text),
            ("响应", item.response_text),
            ("命中", item.matched_by or self._fallback_match_label(query)),
        ):
            body_y = self._draw_labeled_lines(
                draw,
                x=inner_x,
                y=body_y,
                label=label,
                value=value,
            )
            body_y += CARD_LINE_GAP
        return cursor_y + height

    def _draw_empty_state(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        cursor_y: int,
    ) -> int:
        draw.rounded_rectangle(
            (
                CARD_PADDING_X,
                cursor_y,
                CARD_WIDTH - CARD_PADDING_X,
                cursor_y + 120,
            ),
            radius=28,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        draw.text(
            (CARD_PADDING_X + 26, cursor_y + 32),
            f"没有找到匹配词条。当前页: {query.page}",
            font=self.item_title_font,
            fill=self.HEADER,
        )
        draw.text(
            (CARD_PADDING_X + 26, cursor_y + 72),
            "可以换关键词、切换搜索范围，或追加创建者过滤后重试。",
            font=self.item_body_font,
            fill=self.MUTED,
        )
        return cursor_y + 120

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        query: SearchCardQuery,
        cursor_y: int,
    ) -> None:
        footer = (
            f"共 {query.total_count} 条结果" if query.total_count else "共 0 条结果"
        )
        if query.page < query.total_pages:
            footer += f"  可继续查看第 {query.page + 1} 页"
        draw.text(
            (CARD_PADDING_X, cursor_y),
            footer,
            font=self.item_meta_font,
            fill=self.MUTED,
        )

    def _summary_lines(self, query: SearchCardQuery) -> list[str]:
        lines = [
            f"搜索范围: {self._field_label(query.field)}",
            f"关键词: {query.keyword or '无'}",
            f"图片查询: {'是' if query.has_image else '否'}",
            f"创建者过滤: {query.creator_id or '无'}",
        ]
        return lines

    def _summary_block_height(self, query: SearchCardQuery) -> int:
        line_height = self._line_height(self.summary_font)
        return (
            40
            + len(self._summary_lines(query)) * line_height
            + (len(self._summary_lines(query)) - 1) * CARD_SUMMARY_GAP
        )

    def _item_block_height(self, item: WordbankSearchItem, index: int) -> int:
        _ = index
        total = CARD_ITEM_PADDING * 2
        badge_bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "00",
            font=self.item_title_font,
        )
        total += int(badge_bbox[3] - badge_bbox[1] + 30)
        for label, value in (
            ("触发", item.trigger_text),
            ("响应", item.response_text),
            ("命中", item.matched_by or "默认"),
        ):
            wrapped = self._wrap_text(f"{label}: {value}", self.item_body_font)
            total += len(wrapped) * self._line_height(self.item_body_font)
            total += CARD_LINE_GAP
        return int(total)

    def _draw_labeled_lines(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        label: str,
        value: str,
    ) -> int:
        wrapped = self._wrap_text(f"{label}: {value}", self.item_body_font)
        cursor_y = y
        for line in wrapped:
            draw.text(
                (x, cursor_y),
                line,
                font=self.item_body_font,
                fill=self.BODY,
            )
            cursor_y += self._line_height(self.item_body_font)
        return cursor_y

    def _fallback_match_label(self, query: SearchCardQuery) -> str:
        if query.has_image and query.keyword:
            return "文本 + 图片"
        if query.has_image:
            return "图片"
        if query.keyword:
            return "文本"
        if query.creator_id:
            return "创建者"
        return "最近词条"

    def _wrap_text(self, text: str, font: Any) -> list[str]:
        if not text:
            return [""]
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = f"{current}{char}"
            if self._text_width(candidate, font) <= CARD_MAX_TEXT_WIDTH:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = char
        if current:
            lines.append(current)
        return [self._truncate_line(line, font) for line in lines[:3]]

    def _truncate_line(self, text: str, font: Any) -> str:
        if self._text_width(text, font) <= CARD_MAX_TEXT_WIDTH:
            return text
        candidate = text
        while (
            candidate
            and self._text_width(f"{candidate}...", font) > CARD_MAX_TEXT_WIDTH
        ):
            candidate = candidate[:-1]
        return f"{candidate}..."

    def _field_label(self, field: str) -> str:
        return {
            "all": "全量",
            "trigger": "触发词",
            "response": "响应词",
        }.get(field, field)

    def _line_height(self, font: Any) -> int:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        return int(bbox[3] - bbox[1] + 6)

    def _text_width(self, text: str, font: Any) -> int:
        return int(
            ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(
                text,
                font=font,
            )
        )

    def _load_font(self, size: int) -> Any:
        try:
            return ImageFont.truetype(MAPLE_FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()


def render_search_results_card(
    *,
    items: tuple[WordbankSearchItem, ...],
    query: SearchCardQuery,
    locale: LocaleCode,
) -> Message:
    renderer = SearchResultCardRenderer()
    image_bytes = renderer.render(items=items, query=query, locale=locale)
    return Message(MessageSegment.image(image_bytes))
