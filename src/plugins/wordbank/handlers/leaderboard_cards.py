"""Pillow leaderboard rendering for the monthly wordbank creator board."""

from __future__ import annotations

from io import BytesIO

import arrow
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.utils.common import get_current_time
from src.plugins.wordbank.services.core import (
    WordbankLeaderboardCardData,
    WordbankLeaderboardCardItem,
)

CARD_WIDTH = 1320
PADDING_X = 56
PADDING_Y = 52
SECTION_GAP = 24
ROW_GAP = 14
FOOTER_HEIGHT = 72


class WordbankLeaderboardCardRenderer:
    BG = "#FAF7F2"
    PANEL = "#FFFFFF"
    PANEL_SOFT = "#FFF2E9"
    HEADER = "#2E2630"
    BODY = "#5B4F58"
    MUTED = "#8E7D86"
    ACCENT = "#D87E52"
    ACCENT_SOFT = "#F7E2D6"
    BORDER = "#EADCCF"
    HERO = "#FFF6EF"
    GOLD = "#E4A64B"
    SILVER = "#8C93B9"
    BRONZE = "#6FA89D"
    CHIP = "#F3ECE5"

    def __init__(self) -> None:
        self.title_font = self._load_font(48)
        self.subtitle_font = self._load_font(24)
        self.month_font = self._load_font(22)
        self.summary_label_font = self._load_font(18)
        self.summary_value_font = self._load_font(30)
        self.hero_rank_font = self._load_font(40)
        self.hero_name_font = self._load_font(34)
        self.hero_count_font = self._load_font(56)
        self.hero_meta_font = self._load_font(22)
        self.row_rank_font = self._load_font(22)
        self.row_name_font = self._load_font(28)
        self.row_value_font = self._load_font(30)
        self.row_meta_font = self._load_font(20)
        self.footer_font = self._load_font(18)
        self.avatar_font = self._load_font(30)

    def render(self, *, data: WordbankLeaderboardCardData, locale: LocaleCode) -> bytes:
        height = self._measure_height(data)
        image = Image.new("RGB", (CARD_WIDTH, height), self.BG)
        draw = ImageDraw.Draw(image)
        self._paint_background(draw, height)

        cursor_y = PADDING_Y
        cursor_y = self._draw_header(draw, data, cursor_y)
        cursor_y += 18
        cursor_y = self._draw_summary(draw, data, locale, cursor_y)
        cursor_y += SECTION_GAP

        if data.items:
            cursor_y = self._draw_hero(draw, data, locale, cursor_y)
            cursor_y += SECTION_GAP
            cursor_y = self._draw_rows(draw, data, locale, cursor_y)
        else:
            cursor_y = self._draw_empty(draw, locale, cursor_y)

        self._draw_footer(draw, data, locale, cursor_y + 12)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _measure_height(self, data: WordbankLeaderboardCardData) -> int:
        height = PADDING_Y
        height += 112
        height += 136 + SECTION_GAP
        if data.items:
            height += 240 + SECTION_GAP
            row_count = max(0, len(data.items) - 1)
            if row_count:
                height += row_count * 112 + max(0, row_count - 1) * ROW_GAP
        else:
            height += 180
        height += FOOTER_HEIGHT + PADDING_Y
        return height

    def _paint_background(self, draw: ImageDraw.ImageDraw, height: int) -> None:
        draw.rectangle((0, 0, CARD_WIDTH, height), fill=self.BG)
        draw.rounded_rectangle((42, 34, 138, height - 42), radius=28, fill="#FFF2EA")
        draw.rounded_rectangle(
            (CARD_WIDTH - 126, 112, CARD_WIDTH - 72, height - 96),
            radius=28,
            fill="#F2F7FF",
        )
        draw.rectangle((0, 0, CARD_WIDTH, 12), fill=self.ACCENT)

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        cursor_y: int,
    ) -> int:
        draw.text(
            (PADDING_X, cursor_y),
            data.title,
            font=self.title_font,
            fill=self.HEADER,
        )
        draw.text(
            (PADDING_X, cursor_y + 58),
            data.subtitle,
            font=self.subtitle_font,
            fill=self.BODY,
        )
        month_text = data.month_label
        chip_w = int(draw.textlength(month_text, font=self.month_font)) + 40
        chip_x = CARD_WIDTH - PADDING_X - chip_w
        draw.rounded_rectangle(
            (chip_x, cursor_y + 8, chip_x + chip_w, cursor_y + 50),
            radius=20,
            fill=self.CHIP,
            outline=self.BORDER,
            width=2,
        )
        draw.text(
            (chip_x + 20, cursor_y + 18),
            month_text,
            font=self.month_font,
            fill=self.ACCENT,
        )
        return cursor_y + 92

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        box_h = 136
        draw.rounded_rectangle(
            (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + box_h),
            radius=30,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        stat_w = (CARD_WIDTH - PADDING_X * 2 - 32 * 2 - 24 * 2) // 3
        stats = (
            (
                tr(locale, "wordbank.rank.summary.creators"),
                str(data.total_creator_count),
            ),
            (
                tr(locale, "wordbank.rank.summary.entries"),
                str(data.total_approved_count),
            ),
            (
                tr(locale, "wordbank.rank.summary.share"),
                f"{data.top_share * 100:.0f}%",
            ),
        )
        for index, (label, value) in enumerate(stats):
            x0 = PADDING_X + 32 + index * (stat_w + 24)
            draw.rounded_rectangle(
                (x0, cursor_y + 24, x0 + stat_w, cursor_y + box_h - 24),
                radius=24,
                fill=self.PANEL_SOFT,
            )
            draw.text(
                (x0 + 20, cursor_y + 42),
                label,
                font=self.summary_label_font,
                fill=self.MUTED,
            )
            draw.text(
                (x0 + 20, cursor_y + 74),
                value,
                font=self.summary_value_font,
                fill=self.HEADER,
            )
        return cursor_y + box_h

    def _draw_hero(
        self,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        champion = data.items[0]
        box_h = 240
        draw.rounded_rectangle(
            (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + box_h),
            radius=34,
            fill=self.HERO,
            outline=self.BORDER,
            width=2,
        )
        avatar_x = PADDING_X + 34
        avatar_y = cursor_y + 38
        self._draw_avatar(draw, champion, avatar_x, avatar_y, 120, self.GOLD)
        draw.text(
            (avatar_x + 150, cursor_y + 42),
            "NO.1",
            font=self.hero_rank_font,
            fill=self.GOLD,
        )
        draw.text(
            (avatar_x + 150, cursor_y + 96),
            self._fit_text(draw, champion.display_name, self.hero_name_font, 520),
            font=self.hero_name_font,
            fill=self.HEADER,
        )
        share_text = tr(
            locale,
            "wordbank.rank.hero.share",
            share=f"{champion.share * 100:.1f}%",
        )
        scope_text = self._scope_summary(locale, champion)
        draw.text(
            (avatar_x + 150, cursor_y + 146),
            share_text,
            font=self.hero_meta_font,
            fill=self.BODY,
        )
        draw.text(
            (avatar_x + 150, cursor_y + 176),
            scope_text,
            font=self.hero_meta_font,
            fill=self.MUTED,
        )

        count_text = str(champion.approved_count)
        count_w = int(draw.textlength(count_text, font=self.hero_count_font))
        count_x = CARD_WIDTH - PADDING_X - 40 - count_w
        draw.text(
            (count_x, cursor_y + 66),
            count_text,
            font=self.hero_count_font,
            fill=self.ACCENT,
        )
        meta_text = tr(
            locale,
            "wordbank.rank.hero.gap",
            gap=max(0, data.champion_gap),
        )
        meta_w = int(draw.textlength(meta_text, font=self.hero_meta_font))
        draw.text(
            (CARD_WIDTH - PADDING_X - 40 - meta_w, cursor_y + 148),
            meta_text,
            font=self.hero_meta_font,
            fill=self.BODY,
        )
        return cursor_y + box_h

    def _draw_rows(
        self,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        for item in data.items[1:]:
            row_h = 112
            draw.rounded_rectangle(
                (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + row_h),
                radius=28,
                fill=self.PANEL,
                outline=self.BORDER,
                width=2,
            )
            badge_fill = self._rank_fill(item.current_rank)
            draw.rounded_rectangle(
                (PADDING_X + 18, cursor_y + 22, PADDING_X + 90, cursor_y + 90),
                radius=22,
                fill=badge_fill,
            )
            draw.text(
                (PADDING_X + 54, cursor_y + 56),
                str(item.current_rank),
                font=self.row_rank_font,
                fill="#FFFFFF",
                anchor="mm",
            )
            self._draw_avatar(
                draw,
                item,
                PADDING_X + 112,
                cursor_y + 20,
                72,
                self._rank_fill(item.current_rank),
            )
            draw.text(
                (PADDING_X + 208, cursor_y + 28),
                self._fit_text(draw, item.display_name, self.row_name_font, 520),
                font=self.row_name_font,
                fill=self.HEADER,
            )
            draw.text(
                (PADDING_X + 208, cursor_y + 66),
                tr(
                    locale,
                    "wordbank.rank.row.meta",
                    group_count=item.group_count,
                    date=arrow.get(item.latest_created_at)
                    .to("Asia/Shanghai")
                    .format("MM-DD HH:mm"),
                ),
                font=self.row_meta_font,
                fill=self.MUTED,
            )
            scope_text = self._scope_summary(locale, item)
            scope_w = int(draw.textlength(scope_text, font=self.row_meta_font))
            draw.text(
                (CARD_WIDTH - PADDING_X - 34 - scope_w, cursor_y + 28),
                scope_text,
                font=self.row_meta_font,
                fill=self.BODY,
            )
            count_text = str(item.approved_count)
            count_w = int(draw.textlength(count_text, font=self.row_value_font))
            draw.text(
                (CARD_WIDTH - PADDING_X - 34 - count_w, cursor_y + 58),
                count_text,
                font=self.row_value_font,
                fill=self.ACCENT,
            )
            cursor_y += row_h + ROW_GAP
        return cursor_y - ROW_GAP

    def _draw_empty(
        self,
        draw: ImageDraw.ImageDraw,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        box_h = 180
        draw.rounded_rectangle(
            (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + box_h),
            radius=30,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        draw.text(
            (CARD_WIDTH / 2, cursor_y + 84),
            tr(locale, "wordbank.rank.empty"),
            font=self.hero_name_font,
            fill=self.MUTED,
            anchor="mm",
        )
        return cursor_y + box_h

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> None:
        footer_time = arrow.get(data.generated_at or get_current_time()).to(
            "Asia/Shanghai"
        )
        footer_text = tr(
            locale,
            "wordbank.rank.footer",
            time=footer_time.format("YYYY-MM-DD HH:mm"),
        )
        draw.text(
            (PADDING_X, cursor_y),
            footer_text,
            font=self.footer_font,
            fill=self.MUTED,
        )
        brand = "© 2020-2026 SakuraiSenrin"
        brand_w = int(draw.textlength(brand, font=self.footer_font))
        draw.text(
            (CARD_WIDTH - PADDING_X - brand_w, cursor_y),
            brand,
            font=self.footer_font,
            fill=self.MUTED,
        )

    def _draw_avatar(
        self,
        draw: ImageDraw.ImageDraw,
        item: WordbankLeaderboardCardItem,
        x: int,
        y: int,
        size: int,
        fill: str,
    ) -> None:
        draw.ellipse((x, y, x + size, y + size), fill=fill)
        label = item.display_name[:1] if item.display_name else item.user_id[-1:] or "?"
        draw.text(
            (x + size / 2, y + size / 2),
            label,
            font=self.avatar_font,
            fill="#FFFFFF",
            anchor="mm",
        )

    def _scope_summary(
        self,
        locale: LocaleCode,
        item: WordbankLeaderboardCardItem,
    ) -> str:
        values: tuple[tuple[MessageKey, int], ...] = (
            ("wordbank.rank.scope.current_group", item.current_group_count),
            ("wordbank.rank.scope.all_groups", item.all_groups_count),
            ("wordbank.rank.scope.self", item.self_count),
            ("wordbank.rank.scope.private_only", item.private_only_count),
        )
        parts = [tr(locale, key, count=count) for key, count in values if count > 0]
        return " · ".join(parts[:2]) if parts else "-"

    def _rank_fill(self, rank: int) -> str:
        if rank == 1:
            return self.GOLD
        if rank == 2:
            return self.SILVER
        if rank == 3:
            return self.BRONZE
        return self.ACCENT

    def _fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        if int(draw.textlength(text, font=font)) <= max_width:
            return text
        suffix = "..."
        for end in range(len(text), 0, -1):
            candidate = text[:end].rstrip() + suffix
            if int(draw.textlength(candidate, font=font)) <= max_width:
                return candidate
        return suffix

    @staticmethod
    def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(MAPLE_FONT_PATH, size)
        except OSError:
            return ImageFont.load_default()


def render_wordbank_leaderboard_card_bytes(
    *,
    data: WordbankLeaderboardCardData,
    locale: LocaleCode,
) -> bytes:
    return WordbankLeaderboardCardRenderer().render(data=data, locale=locale)


def render_wordbank_leaderboard_card(
    *,
    data: WordbankLeaderboardCardData,
    locale: LocaleCode,
) -> Message:
    return Message(
        MessageSegment.image(
            render_wordbank_leaderboard_card_bytes(data=data, locale=locale)
        )
    )
