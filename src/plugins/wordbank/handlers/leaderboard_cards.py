"""Pillow leaderboard rendering for the wordbank creator board."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import arrow
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from PIL import Image, ImageDraw, ImageFont

from src.lib.consts import MAPLE_FONT_PATH
from src.lib.demo_theme import SENRIN_V3_WORDBANK_LEADERBOARD_THEME
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
ROW_GAP = 16
FOOTER_HEIGHT = 72
SENRIN_MASCOT_PATH = Path("data/image/senrin-v3-transparent.png")


class WordbankLeaderboardCardRenderer:
    THEME = SENRIN_V3_WORDBANK_LEADERBOARD_THEME

    def __init__(self) -> None:
        self.theme = self.THEME
        self.BG = self.theme.bg
        self.BG_STRONG = self.theme.bg_strong
        self.PANEL = self.theme.panel
        self.PANEL_SOFT = self.theme.panel_soft
        self.PANEL_PINK = self.theme.panel_pink
        self.HEADER = self.theme.header
        self.BODY = self.theme.body
        self.MUTED = self.theme.muted
        self.ACCENT = self.theme.accent
        self.ACCENT_DEEP = self.theme.accent_deep
        self.ACCENT_SOFT = self.theme.accent_soft
        self.BORDER = self.theme.border
        self.HERO = self.theme.hero
        self.GOLD = self.theme.gold
        self.SILVER = self.theme.silver
        self.BRONZE = self.theme.bronze
        self.CHIP_BG = self.theme.chip_bg
        self.CHIP_FG = self.theme.chip_fg
        self.title_font = self._load_font(50)
        self.subtitle_font = self._load_font(24)
        self.range_font = self._load_font(20)
        self.badge_font = self._load_font(22)
        self.summary_label_font = self._load_font(18)
        self.summary_value_font = self._load_font(32)
        self.hero_rank_font = self._load_font(26)
        self.hero_name_font = self._load_font(38)
        self.hero_count_font = self._load_font(64)
        self.hero_meta_font = self._load_font(22)
        self.row_rank_font = self._load_font(22)
        self.row_name_font = self._load_font(28)
        self.row_value_font = self._load_font(34)
        self.row_meta_font = self._load_font(20)
        self.row_chip_font = self._load_font(18)
        self.footer_font = self._load_font(18)
        self.avatar_font = self._load_font(30)
        self.mascot_image = self._load_mascot_image()

    def render(self, *, data: WordbankLeaderboardCardData, locale: LocaleCode) -> bytes:
        height = self._measure_height(data)
        image = Image.new("RGB", (CARD_WIDTH, height), self.BG)
        draw = ImageDraw.Draw(image)
        self._paint_background(image, draw, height)

        cursor_y = PADDING_Y
        cursor_y = self._draw_header(draw, data, cursor_y)
        cursor_y += 18
        cursor_y = self._draw_summary(draw, data, locale, cursor_y)
        cursor_y += SECTION_GAP

        if data.items:
            cursor_y = self._draw_hero(image, draw, data, locale, cursor_y)
            cursor_y += SECTION_GAP
            cursor_y = self._draw_rows(image, draw, data, locale, cursor_y)
        else:
            cursor_y = self._draw_empty(draw, locale, cursor_y)

        self._draw_footer(draw, data, locale, cursor_y + 12)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _measure_height(self, data: WordbankLeaderboardCardData) -> int:
        height = PADDING_Y
        height += 132
        height += 152 + SECTION_GAP
        if data.items:
            height += 268 + SECTION_GAP
            row_count = max(0, len(data.items) - 1)
            if row_count:
                height += row_count * 124 + max(0, row_count - 1) * ROW_GAP
        else:
            height += 190
        height += FOOTER_HEIGHT + PADDING_Y
        return height

    def _paint_background(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        height: int,
    ) -> None:
        draw.rectangle((0, 0, CARD_WIDTH, height), fill=self.BG)
        draw.ellipse((-120, -40, 220, 300), fill=self.BG_STRONG)
        draw.ellipse(
            (CARD_WIDTH - 320, 40, CARD_WIDTH + 80, 420),
            fill=self.theme.halo_fill,
        )
        self._paste_mascot(image)
        draw.rounded_rectangle(
            (30, 20, CARD_WIDTH - 30, height - 24),
            radius=42,
            outline=self.theme.halo_outline,
            width=2,
        )
        draw.rectangle((0, 0, CARD_WIDTH, 10), fill=self.ACCENT)

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
            (PADDING_X, cursor_y + 60),
            data.subtitle,
            font=self.subtitle_font,
            fill=self.BODY,
        )
        draw.text(
            (PADDING_X, cursor_y + 94),
            data.range_text,
            font=self.range_font,
            fill=self.MUTED,
        )

        badge_w = int(draw.textlength(data.badge_text, font=self.badge_font)) + 48
        badge_x = CARD_WIDTH - PADDING_X - badge_w
        badge_y = cursor_y + 14
        draw.rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_w, badge_y + 42),
            radius=21,
            fill=self.ACCENT_SOFT,
            outline=self.BORDER,
            width=2,
        )
        draw.text(
            (badge_x + badge_w / 2, badge_y + 21),
            data.badge_text,
            font=self.badge_font,
            fill=self.ACCENT_DEEP,
            anchor="mm",
        )
        return cursor_y + 114

    def _draw_summary(
        self,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        box_h = 152
        draw.rounded_rectangle(
            (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + box_h),
            radius=34,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        stat_w = (CARD_WIDTH - PADDING_X * 2 - 32 * 2 - 22 * 2) // 3
        stats = (
            (
                tr(locale, "wordbank.rank.summary.creators"),
                str(data.total_creator_count),
                self.PANEL_SOFT,
                self.ACCENT_DEEP,
            ),
            (
                tr(locale, "wordbank.rank.summary.entries"),
                str(data.total_approved_count),
                self.theme.violet_panel,
                self.theme.violet_panel_outline,
            ),
            (
                tr(locale, "wordbank.rank.summary.share"),
                f"{data.top_share * 100:.0f}%",
                self.theme.amber_panel,
                self.theme.amber_panel_outline,
            ),
        )
        for index, (label, value, bg, fg) in enumerate(stats):
            x0 = PADDING_X + 32 + index * (stat_w + 22)
            draw.rounded_rectangle(
                (x0, cursor_y + 24, x0 + stat_w, cursor_y + box_h - 24),
                radius=24,
                fill=bg,
            )
            draw.text(
                (x0 + 20, cursor_y + 42),
                label,
                font=self.summary_label_font,
                fill=self.MUTED,
            )
            draw.text(
                (x0 + 20, cursor_y + 80),
                value,
                font=self.summary_value_font,
                fill=fg,
            )
        return cursor_y + box_h

    def _draw_hero(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        champion = data.items[0]
        box_h = 268
        draw.rounded_rectangle(
            (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + box_h),
            radius=38,
            fill=self.HERO,
            outline=self.BORDER,
            width=2,
        )
        avatar_x = PADDING_X + 36
        avatar_y = cursor_y + 40
        self._paste_avatar(image, champion, avatar_x, avatar_y, 132, self.GOLD)
        self._draw_rank_capsule(
            draw,
            x=avatar_x + 160,
            y=cursor_y + 42,
            text="#1",
            fill=self.theme.hero_summary_fill,
            fg=self.theme.hero_summary_text,
            width=84,
        )
        draw.text(
            (avatar_x + 160, cursor_y + 98),
            self._fit_text(draw, champion.display_name, self.hero_name_font, 460),
            font=self.hero_name_font,
            fill=self.HEADER,
        )
        draw.text(
            (avatar_x + 160, cursor_y + 150),
            tr(
                locale,
                "wordbank.rank.hero.share",
                share=f"{champion.share * 100:.1f}%",
            ),
            font=self.hero_meta_font,
            fill=self.BODY,
        )
        draw.text(
            (avatar_x + 160, cursor_y + 182),
            tr(
                locale,
                "wordbank.rank.row.meta",
                group_count=champion.group_count,
                date=arrow.get(champion.latest_created_at)
                .to("Asia/Shanghai")
                .format("MM-DD HH:mm"),
            ),
            font=self.hero_meta_font,
            fill=self.MUTED,
        )
        chip_y = cursor_y + 212
        self._draw_scope_chips(draw, champion, locale, x=avatar_x + 160, y=chip_y)

        count_text = str(champion.approved_count)
        count_w = int(draw.textlength(count_text, font=self.hero_count_font))
        count_x = CARD_WIDTH - PADDING_X - 42 - count_w
        draw.text(
            (count_x, cursor_y + 74),
            count_text,
            font=self.hero_count_font,
            fill=self.ACCENT_DEEP,
        )
        self._draw_rank_capsule(
            draw,
            x=CARD_WIDTH - PADDING_X - 240,
            y=cursor_y + 166,
            text=tr(locale, "wordbank.rank.hero.gap", gap=max(0, data.champion_gap)),
            fill=self.theme.hero_stat_fill,
            fg=self.ACCENT_DEEP,
            width=198,
        )
        return cursor_y + box_h

    def _draw_rows(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        data: WordbankLeaderboardCardData,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        for item in data.items[1:]:
            row_h = 124
            fill, capsule_fill, capsule_fg = self._row_theme(item.current_rank)
            draw.rounded_rectangle(
                (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + row_h),
                radius=30,
                fill=fill,
                outline=self.BORDER,
                width=2,
            )
            self._draw_rank_capsule(
                draw,
                x=PADDING_X + 24,
                y=cursor_y + 26,
                text=f"#{item.current_rank}",
                fill=capsule_fill,
                fg=capsule_fg,
                width=76,
            )
            self._paste_avatar(
                image,
                item,
                PADDING_X + 120,
                cursor_y + 24,
                74,
                capsule_fg,
            )
            draw.text(
                (PADDING_X + 220, cursor_y + 26),
                self._fit_text(draw, item.display_name, self.row_name_font, 480),
                font=self.row_name_font,
                fill=self.HEADER,
            )
            draw.text(
                (PADDING_X + 220, cursor_y + 64),
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
            self._draw_scope_chips(
                draw,
                item,
                locale,
                x=PADDING_X + 220,
                y=cursor_y + 88,
            )

            count_text = str(item.approved_count)
            count_w = int(draw.textlength(count_text, font=self.row_value_font))
            count_x = CARD_WIDTH - PADDING_X - 34 - count_w
            draw.text(
                (count_x, cursor_y + 42),
                count_text,
                font=self.row_value_font,
                fill=self.ACCENT_DEEP,
            )
            cursor_y += row_h + ROW_GAP
        return cursor_y - ROW_GAP

    def _draw_empty(
        self,
        draw: ImageDraw.ImageDraw,
        locale: LocaleCode,
        cursor_y: int,
    ) -> int:
        box_h = 190
        draw.rounded_rectangle(
            (PADDING_X, cursor_y, CARD_WIDTH - PADDING_X, cursor_y + box_h),
            radius=34,
            fill=self.PANEL,
            outline=self.BORDER,
            width=2,
        )
        self._draw_rank_capsule(
            draw,
            x=(CARD_WIDTH - 132) // 2,
            y=cursor_y + 44,
            text="NO DATA",
            fill=self.ACCENT_SOFT,
            fg=self.ACCENT_DEEP,
            width=132,
        )
        draw.text(
            (CARD_WIDTH / 2, cursor_y + 114),
            tr(locale, "wordbank.rank.empty"),
            font=self.hero_meta_font,
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

    def _draw_scope_chips(
        self,
        draw: ImageDraw.ImageDraw,
        item: WordbankLeaderboardCardItem,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
    ) -> None:
        cursor_x = x
        for label, bg, fg in self._scope_chips(locale, item)[:2]:
            width = int(draw.textlength(label, font=self.row_chip_font)) + 28
            draw.rounded_rectangle(
                (cursor_x, y, cursor_x + width, y + 30),
                radius=15,
                fill=bg,
            )
            draw.text(
                (cursor_x + 14, y + 6),
                label,
                font=self.row_chip_font,
                fill=fg,
            )
            cursor_x += width + 10

    def _scope_chips(
        self,
        locale: LocaleCode,
        item: WordbankLeaderboardCardItem,
    ) -> list[tuple[str, str, str]]:
        values: tuple[tuple[MessageKey, int, str, str], ...] = (
            (
                "wordbank.rank.scope.current_group",
                item.current_group_count,
                self.theme.row_pink_fill,
                self.theme.row_pink_text,
            ),
            (
                "wordbank.rank.scope.all_groups",
                item.all_groups_count,
                self.theme.row_blue_fill,
                self.theme.row_blue_text,
            ),
            (
                "wordbank.rank.scope.self",
                item.self_count,
                self.theme.row_amber_fill,
                self.theme.row_amber_text,
            ),
            (
                "wordbank.rank.scope.private_only",
                item.private_only_count,
                self.theme.row_mint_fill,
                self.theme.row_mint_text,
            ),
        )
        return [
            (tr(locale, key, count=count), bg, fg)
            for key, count, bg, fg in values
            if count > 0
        ]

    def _row_theme(self, rank: int) -> tuple[str, str, str]:
        if rank == 2:
            return (
                self.theme.avatar_violet_fill,
                self.theme.avatar_violet_outline,
                self.theme.avatar_violet_text,
            )
        if rank == 3:
            return (
                self.theme.avatar_mint_fill,
                self.theme.avatar_mint_outline,
                self.theme.avatar_mint_text,
            )
        if rank == 4:
            return (self.BG, self.theme.avatar_pink_fill, self.ACCENT_DEEP)
        return (self.PANEL, self.theme.row_pink_fill, self.ACCENT_DEEP)

    def _draw_rank_capsule(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        fill: str,
        fg: str,
        width: int,
    ) -> None:
        height = 36
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=height // 2,
            fill=fill,
        )
        draw.text(
            (x + width / 2, y + height / 2),
            text,
            font=self.row_rank_font,
            fill=fg,
            anchor="mm",
        )

    def _paste_avatar(
        self,
        image: Image.Image,
        item: WordbankLeaderboardCardItem,
        x: int,
        y: int,
        size: int,
        fallback_fill: str,
    ) -> None:
        avatar = getattr(item, "avatar", None)
        if avatar is not None:
            avatar_image = avatar.circle().resize((size, size)).image
            mask = (
                avatar_image.getchannel("A") if "A" in avatar_image.getbands() else None
            )
            image.paste(avatar_image, (x, y), mask)
            return

        fallback = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        fallback_draw = ImageDraw.Draw(fallback)
        fallback_draw.ellipse((0, 0, size, size), fill=fallback_fill)
        label = item.display_name[:1] if item.display_name else item.user_id[-1:] or "?"
        fallback_draw.text(
            (size / 2, size / 2),
            label,
            font=self.avatar_font,
            fill=self.theme.white,
            anchor="mm",
        )
        image.paste(fallback, (x, y), fallback)

    def _paste_mascot(self, image: Image.Image) -> None:
        mascot = self.mascot_image
        if mascot is None:
            return
        mascot = mascot.copy()
        mascot.thumbnail((340, 340))
        alpha = mascot.getchannel("A").point(lambda value: value * 0.26)
        mascot.putalpha(alpha)
        x = CARD_WIDTH - mascot.width - 68
        y = 26
        image.paste(mascot, (x, y), mascot)

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

    @staticmethod
    def _load_mascot_image() -> Image.Image | None:
        try:
            return Image.open(SENRIN_MASCOT_PATH).convert("RGBA")
        except OSError:
            return None


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
