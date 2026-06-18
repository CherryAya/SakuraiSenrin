"""Legacy plugin docs demo renderer."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Any, ClassVar, Literal

from PIL import Image, ImageDraw, ImageFont
from pil_utils import BuildImage
from pil_utils.text2image import Text2Image

from src.lib.consts import MAPLE_FONT_NAME, MAPLE_FONT_PATH
from src.lib.demo_theme import DEFAULT_DEMO_THEME, SENRIN_V3_THEME
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.lib.plugin_docs.command_layout import InlineTextSpan, split_inline_text_spans
from src.lib.plugin_docs.models import DocsDemoTurn

from .demo import DEMO_AVATAR_PATH, DEMO_STANDEE_PATH, _TurnSpec


class LegacyDemoImageRenderer:
    """Render a compact plugin docs demo card."""

    WIDTH = 1280
    OUTER_MARGIN = 40
    SHELL_RADIUS = 32
    HEADER_HEIGHT = 248
    HEADER_LEFT = 126
    HEADER_TOP = 70
    HEADER_CHIP_TOP = 72
    HEADER_TITLE_TOP = 112
    HEADER_FEATURE_TOP = 178
    HEADER_TRIGGER_TOP = 218
    HEADER_RIGHT = 1138
    HEADER_STANDEE_SIZE = 150
    HEADER_STANDEE_X = 1030
    HEADER_STANDEE_Y = 104
    HEADER_STEPS_X = 302
    BODY_TOP_GAP = 20
    BODY_PADDING_X = 40
    BODY_PADDING_Y = 36
    TURN_GAP = 24
    FOOTER_HEIGHT = 52
    FOOTER_TOP_GAP = 24
    FOOTER_SIDE_PADDING = 28
    FOOTER_TEXT_GAP = 20
    CONVERSATION_SIDE_PADDING = 76
    AVATAR_SIZE = 48
    BUBBLE_RADIUS = 22
    BUBBLE_PADDING_X = 24
    BUBBLE_PADDING_Y = 20
    BUBBLE_LABEL_GAP = 12
    USER_CONTENT_WIDTH = 560
    BOT_CONTENT_WIDTH = 640
    SYSTEM_CONTENT_WIDTH = 860
    USER_MIN_BUBBLE_WIDTH = 270
    BOT_MIN_BUBBLE_WIDTH = 310
    SYSTEM_MIN_BUBBLE_WIDTH = 480
    CHIP_HEIGHT = 38
    FOOTER_RIGHT_TEXT = "help docs"
    FONT_FAMILIES: ClassVar[list[str]] = [MAPLE_FONT_NAME]

    def __init__(self) -> None:
        self.theme_name = SENRIN_V3_THEME.name
        self.theme = DEFAULT_DEMO_THEME
        try:
            # 移动端优化：增大字体以便手机聊天查看
            self.eyebrow_font = ImageFont.truetype(MAPLE_FONT_PATH, 22)  # 16 → 22
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 56)  # 42 → 56
            self.feature_font = ImageFont.truetype(MAPLE_FONT_PATH, 34)  # 26 → 34
            self.body_font = ImageFont.truetype(MAPLE_FONT_PATH, 32)  # 24 → 32
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 22)  # 16 → 22
            self.footer_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)  # 15 → 20
        except OSError:
            self.eyebrow_font = ImageFont.load_default()
            self.title_font = ImageFont.load_default()
            self.feature_font = ImageFont.load_default()
            self.body_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.footer_font = ImageFont.load_default()
        self.senrin_avatar = self._load_asset(DEMO_AVATAR_PATH, self.AVATAR_SIZE)
        self.senrin_standee = self._load_asset(
            DEMO_STANDEE_PATH,
            self.HEADER_STANDEE_SIZE,
            alpha=168,
        )

    def render(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
    ) -> bytes:
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = self._conversation_height(turn_specs)
        panel_top = self.OUTER_MARGIN + self.HEADER_HEIGHT + self.BODY_TOP_GAP
        body_top = panel_top + self.BODY_PADDING_Y
        panel_bottom = body_top + conversation_height + self.BODY_PADDING_Y
        footer_top = panel_bottom + self.FOOTER_TOP_GAP
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        image = Image.new("RGB", (self.WIDTH, height), self.theme.page_bg)
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN,
                self.OUTER_MARGIN,
                self.WIDTH - self.OUTER_MARGIN,
                height - self.OUTER_MARGIN,
            ),
            radius=self.theme.shell_radius,
            fill=self.theme.shell_bg,
            outline=self.theme.shell_border,
            width=2,
        )
        self._draw_header(
            image,
            draw,
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_trigger=feature_trigger,
            turn_count=len(turns),
            locale=locale,
        )
        self._draw_conversation_panel(
            draw,
            top=panel_top,
            bottom=panel_bottom,
        )

        y = body_top
        for spec in turn_specs:
            self._draw_turn(image, draw, spec, y)
            y += spec.height + self.TURN_GAP

        self._draw_footer(
            draw,
            top=footer_top,
            plugin_title=plugin_title,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
        )

        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _paint_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        draw.rectangle((0, 0, width, height), fill=self.theme.page_bg)
        draw.rectangle((0, 0, width, 10), fill=self.theme.accent)
        draw.rounded_rectangle(
            (74, 66, 112, height - 76),
            radius=19,
            fill=self.theme.showcase_accent_rail_bg,
        )
        draw.rounded_rectangle(
            (width - 142, 126, width - 86, height - 124),
            radius=28,
            fill=self.theme.showcase_support_rail_bg,
        )

    def _draw_header(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        turn_count: int,
        locale: LocaleCode,
    ) -> None:
        draw.rounded_rectangle((96, 94, 106, 190), radius=5, fill=self.theme.accent)
        self._draw_chip(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_CHIP_TOP,
            text="PLUGIN DEMO",
            fill=self.theme.muted_light,
            text_fill=self.theme.strong,
            font=self.eyebrow_font,
        )
        self._draw_chip(
            draw,
            x=self.HEADER_STEPS_X,
            y=self.HEADER_CHIP_TOP,
            text=f"{turn_count} STEP{'S' if turn_count != 1 else ''}",
            fill=self.theme.indigo_soft,
            text_fill=self.theme.indigo_text,
            font=self.eyebrow_font,
            min_width=154,
        )
        title_text = self._fit_text(
            draw,
            plugin_title,
            self.title_font,
            max_width=720,
        )
        self._draw_text(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_TITLE_TOP,
            text=title_text,
            font=self.title_font,
            fill=self.theme.deep,
        )
        feature_text = self._fit_text(
            draw,
            feature_title,
            self.feature_font,
            max_width=720,
        )
        self._draw_text(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_FEATURE_TOP,
            text=feature_text,
            font=self.feature_font,
            fill=self.theme.strong,
        )
        if feature_trigger.strip():
            trigger_example = tr(
                locale,
                "docs.feature.trigger_example",
                command=feature_trigger,
            )
            self._draw_inline_chip(
                draw,
                x=self.HEADER_LEFT,
                y=self.HEADER_TRIGGER_TOP,
                text=trigger_example,
                max_width=760,
                fill=self.theme.inline_code_bg,
                text_fill=self.theme.hint,
                font=self.meta_font,
                min_width=300,
            )
        self._draw_header_standee(image, draw)

    def _draw_conversation_panel(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        top: int,
        bottom: int,
    ) -> None:
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN + 28,
                top,
                self.WIDTH - self.OUTER_MARGIN - 28,
                bottom,
            ),
            radius=self.theme.panel_radius,
            fill=self.theme.panel_bg,
            outline=self.theme.line,
            width=2,
        )

    def _measure_turn(self, turn: DocsDemoTurn) -> "_TurnSpec":
        if turn.speaker == "SYSTEM":
            lines = self._wrap_inline_text(
                self._normalize_demo_text(turn.text),
                max_width=self.SYSTEM_CONTENT_WIDTH,
                font=self.body_font,
            )
            text_height = self._line_block_height(lines, self.body_font)
            width = (
                self._max_inline_line_width(lines, self.body_font)
                + self.BUBBLE_PADDING_X * 2
            )
            return _TurnSpec(
                turn=turn,
                lines=lines,
                width=max(width, self.SYSTEM_MIN_BUBBLE_WIDTH),
                height=text_height + self.BUBBLE_PADDING_Y * 2 + 18,
            )

        is_user = turn.speaker == "USER"
        lines = self._wrap_inline_text(
            self._normalize_demo_text(turn.text),
            max_width=self.USER_CONTENT_WIDTH if is_user else self.BOT_CONTENT_WIDTH,
            font=self.body_font,
        )
        text_height = self._line_block_height(lines, self.body_font)
        label_height = self._font_line_height(self.eyebrow_font)
        bubble_height = (
            text_height
            + label_height
            + self.BUBBLE_LABEL_GAP
            + self.BUBBLE_PADDING_Y * 2
        )
        bubble_width = (
            self._max_inline_line_width(lines, self.body_font)
            + self.BUBBLE_PADDING_X * 2
        )
        min_width = self.USER_MIN_BUBBLE_WIDTH if is_user else self.BOT_MIN_BUBBLE_WIDTH
        return _TurnSpec(
            turn=turn,
            lines=lines,
            width=min(
                max(bubble_width, min_width),
                self.USER_CONTENT_WIDTH + self.BUBBLE_PADDING_X * 2
                if is_user
                else self.BOT_CONTENT_WIDTH + self.BUBBLE_PADDING_X * 2,
            ),
            height=max(bubble_height, self.AVATAR_SIZE),
        )

    def _draw_turn(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        spec: "_TurnSpec",
        top: int,
    ) -> None:
        if spec.turn.speaker == "SYSTEM":
            left = (self.WIDTH - spec.width) // 2
            right = left + spec.width
            draw.rounded_rectangle(
                (left, top, right, top + spec.height),
                radius=20,
                fill=self.theme.system_bubble,
            )
            self._draw_multiline_text(
                draw,
                x=left + self.BUBBLE_PADDING_X,
                y=top + self.BUBBLE_PADDING_Y + 8,
                lines=spec.lines,
                font=self.body_font,
                fill=self.theme.system_text,
            )
            label = "SYSTEM"
            label_box = self._text_size(label, self.eyebrow_font)
            self._draw_text(
                draw,
                x=left + self.BUBBLE_PADDING_X,
                y=top + 12 - label_box[1],
                text=label,
                font=self.eyebrow_font,
                fill=self.theme.system_label,
            )
            return

        is_user = spec.turn.speaker == "USER"
        bubble_width = spec.width
        avatar_x = (
            self.WIDTH
            - self.OUTER_MARGIN
            - self.CONVERSATION_SIDE_PADDING
            - self.AVATAR_SIZE
            if is_user
            else self.OUTER_MARGIN + self.CONVERSATION_SIDE_PADDING
        )
        bubble_x = (
            avatar_x - 18 - bubble_width
            if is_user
            else avatar_x + self.AVATAR_SIZE + 18
        )
        bubble_y = top + max((self.AVATAR_SIZE - spec.height) // 2, 0)
        fill = self.theme.user_bubble if is_user else self.theme.bot_bubble
        text_fill = self.theme.deep if is_user else self.theme.indigo_text
        label = (
            tr("zh-CN", "docs.demo.avatar.user")
            if is_user
            else tr("zh-CN", "docs.demo.avatar.bot")
        )
        avatar_fill = self.theme.accent if is_user else self.theme.indigo

        if is_user:
            self._draw_avatar(draw, x=avatar_x, y=top, label=label, fill=avatar_fill)
        else:
            self._draw_bot_avatar(image, draw, x=avatar_x, y=top)
        draw.rounded_rectangle(
            (bubble_x, bubble_y, bubble_x + bubble_width, bubble_y + spec.height),
            radius=self.BUBBLE_RADIUS,
            fill=fill,
        )
        speaker = "USER" if is_user else "BOT"
        label_fill = self.theme.strong if is_user else self.theme.indigo
        label_box = self._text_size(speaker, self.eyebrow_font)
        self._draw_text(
            draw,
            x=bubble_x + self.BUBBLE_PADDING_X,
            y=bubble_y + self.BUBBLE_PADDING_Y - label_box[1],
            text=speaker,
            font=self.eyebrow_font,
            fill=label_fill,
        )
        self._draw_multiline_text(
            draw,
            x=bubble_x + self.BUBBLE_PADDING_X,
            y=(
                bubble_y
                + self.BUBBLE_PADDING_Y
                + self._font_line_height(self.eyebrow_font)
                + self.BUBBLE_LABEL_GAP
            ),
            lines=spec.lines,
            font=self.body_font,
            fill=text_fill,
        )

    def _draw_avatar(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        label: str,
        fill: str,
    ) -> None:
        draw.ellipse((x, y, x + self.AVATAR_SIZE, y + self.AVATAR_SIZE), fill=fill)
        bbox = self._text_size(label, self.meta_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        self._draw_text(
            draw,
            x=x + (self.AVATAR_SIZE - text_width) / 2,
            y=y + (self.AVATAR_SIZE - text_height) / 2 - 2,
            text=label,
            font=self.meta_font,
            fill=self.theme.avatar_text,
        )

    def _draw_bot_avatar(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
    ) -> None:
        if self.senrin_avatar is None:
            self._draw_avatar(
                draw,
                x=x,
                y=y,
                label=tr("zh-CN", "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        avatar = self.senrin_avatar
        mask = Image.new("L", avatar.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, avatar.width - 1, avatar.height - 1), fill=255)
        draw.ellipse(
            (x, y, x + self.AVATAR_SIZE, y + self.AVATAR_SIZE),
            fill=self.theme.bot_avatar_bg,
            outline=self.theme.bot_avatar_border,
            width=2,
        )
        image.paste(avatar, (x, y), mask)

    def _draw_header_standee(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        if self.senrin_standee is None:
            self._draw_avatar(
                draw,
                x=1088,
                y=128,
                label=tr("zh-CN", "docs.demo.avatar.bot"),
                fill=self.theme.indigo,
            )
            return
        image.paste(
            self.senrin_standee,
            (self.HEADER_STANDEE_X, self.HEADER_STANDEE_Y),
            self.senrin_standee,
        )

    def _load_asset(
        self,
        path: Path,
        size: int,
        *,
        alpha: int = 255,
    ) -> Image.Image | None:
        if not path.exists():
            return None
        try:
            image = Image.open(path).convert("RGBA")
        except OSError:
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        if alpha < 255:
            image = image.copy()
            alpha_channel = image.getchannel("A")
            alpha_channel = alpha_channel.point(
                [value * alpha // 255 for value in range(256)]
            )
            image.putalpha(alpha_channel)
        return image

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        top: int,
        plugin_title: str,
        plugin_version: str,
        plugin_author: str,
    ) -> None:
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN + 28,
                top,
                self.WIDTH - self.OUTER_MARGIN - 28,
                top + self.FOOTER_HEIGHT,
            ),
            radius=18,
            fill=self.theme.footer_bg,
        )
        footer_rect = (
            self.OUTER_MARGIN + 28,
            top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            top + self.FOOTER_HEIGHT,
        )
        right_text = self.FOOTER_RIGHT_TEXT
        right_bbox = self._text_size(right_text, self.footer_font)
        right_width = int(right_bbox[2] - right_bbox[0])
        right_rect = (
            footer_rect[2] - self.FOOTER_SIDE_PADDING - right_width,
            footer_rect[1],
            footer_rect[2] - self.FOOTER_SIDE_PADDING,
            footer_rect[3],
        )
        left_text = self._fit_text(
            draw,
            f"{plugin_title} · v{plugin_version.lstrip('v')} · {plugin_author}",
            self.footer_font,
            max_width=right_rect[0]
            - footer_rect[0]
            - self.FOOTER_SIDE_PADDING
            - self.FOOTER_TEXT_GAP,
        )
        self._draw_text_centered(
            draw,
            footer_rect,
            left_text,
            font=self.footer_font,
            fill=self.theme.hint,
            align="left",
            padding_x=self.FOOTER_SIDE_PADDING,
        )
        self._draw_text_centered(
            draw,
            right_rect,
            right_text,
            font=self.footer_font,
            fill=self.theme.hint,
            align="right",
        )

    def _draw_chip(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        fill: str,
        text_fill: str,
        font: Any,
        min_width: int = 0,
    ) -> None:
        rect = self._chip_rect(
            draw, x=x, y=y, text=text, font=font, min_width=min_width
        )
        draw.rounded_rectangle(rect, radius=self.theme.chip_radius, fill=fill)
        self._draw_text_centered(
            draw,
            rect,
            text,
            font=font,
            fill=text_fill,
        )

    def _draw_inline_chip(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        max_width: int,
        fill: str,
        text_fill: str,
        font: Any,
        min_width: int = 0,
    ) -> None:
        line = self._fit_inline_spans(split_inline_text_spans(text), font, max_width)
        rect = self._inline_chip_rect(
            x=x,
            y=y,
            line=line,
            font=font,
            min_width=min_width,
        )
        draw.rounded_rectangle(rect, radius=self.theme.chip_radius, fill=fill)
        line_width = self._inline_line_width(line, font)
        line_height = self._font_line_height(font)
        draw_x = rect[0] + (rect[2] - rect[0] - line_width) / 2
        draw_y = rect[1] + (rect[3] - rect[1] - line_height) / 2
        self._draw_inline_text_line(
            draw,
            x=draw_x,
            y=draw_y,
            line=line,
            font=font,
            fill=text_fill,
        )

    def audit(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
        locale: LocaleCode = "zh-CN",
    ) -> tuple[str, ...]:
        errors: list[str] = []
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = self._conversation_height(turn_specs)
        panel_top = self.OUTER_MARGIN + self.HEADER_HEIGHT + self.BODY_TOP_GAP
        body_top = panel_top + self.BODY_PADDING_Y
        panel_bottom = body_top + conversation_height + self.BODY_PADDING_Y
        footer_top = panel_bottom + self.FOOTER_TOP_GAP
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        draw = ImageDraw.Draw(
            Image.new("RGB", (self.WIDTH, height), self.theme.panel_bg)
        )

        hero_rect = (
            self.OUTER_MARGIN,
            self.OUTER_MARGIN,
            self.WIDTH - self.OUTER_MARGIN - 28,
            self.OUTER_MARGIN + self.HEADER_HEIGHT,
        )
        panel_rect = (
            self.OUTER_MARGIN + 28,
            panel_top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            panel_bottom,
        )
        shell_rect = (
            self.OUTER_MARGIN,
            self.OUTER_MARGIN,
            self.WIDTH - self.OUTER_MARGIN,
            height - self.OUTER_MARGIN,
        )

        title_rect = self._text_rect(
            draw,
            self.HEADER_LEFT,
            self.HEADER_TITLE_TOP,
            self._fit_text(draw, plugin_title, self.title_font, max_width=720),
            self.title_font,
        )
        feature_rect = self._text_rect(
            draw,
            self.HEADER_LEFT,
            self.HEADER_FEATURE_TOP,
            self._fit_text(draw, feature_title, self.feature_font, max_width=720),
            self.feature_font,
        )
        plugin_chip_rect = self._chip_rect(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_CHIP_TOP,
            text="PLUGIN DEMO",
            font=self.eyebrow_font,
        )
        steps_chip_rect = self._chip_rect(
            draw,
            x=self.HEADER_STEPS_X,
            y=self.HEADER_CHIP_TOP,
            text=f"{len(turns)} STEP{'S' if len(turns) != 1 else ''}",
            font=self.eyebrow_font,
            min_width=154,
        )
        accent_rect = (96, 94, 106, 190)
        header_standee_rect = (
            self.HEADER_STANDEE_X,
            self.HEADER_STANDEE_Y,
            self.HEADER_STANDEE_X + self.HEADER_STANDEE_SIZE,
            self.HEADER_STANDEE_Y + self.HEADER_STANDEE_SIZE,
        )
        trigger_rect: tuple[int, int, int, int] | None = None
        if feature_trigger.strip():
            trigger_example = tr(
                locale,
                "docs.feature.trigger_example",
                command=feature_trigger,
            )
            trigger_rect = self._inline_chip_rect(
                x=self.HEADER_LEFT,
                y=self.HEADER_TRIGGER_TOP,
                line=self._fit_inline_spans(
                    split_inline_text_spans(trigger_example),
                    self.meta_font,
                    760,
                ),
                font=self.meta_font,
                min_width=300,
            )

        self._ensure_inside(hero_rect, plugin_chip_rect, "plugin chip", errors)
        self._ensure_inside(hero_rect, title_rect, "plugin title", errors)
        self._ensure_inside(hero_rect, feature_rect, "feature title", errors)
        self._ensure_inside(hero_rect, steps_chip_rect, "steps chip", errors)
        self._ensure_inside(hero_rect, header_standee_rect, "header standee", errors)
        if trigger_rect is not None:
            self._ensure_inside(hero_rect, trigger_rect, "trigger chip", errors)

        self._ensure_no_overlap(
            accent_rect, title_rect, "accent bar", "plugin title", errors
        )
        self._ensure_no_overlap(
            accent_rect, feature_rect, "accent bar", "feature title", errors
        )
        self._ensure_no_overlap(
            plugin_chip_rect, title_rect, "plugin chip", "plugin title", errors
        )
        self._ensure_no_overlap(
            title_rect, feature_rect, "plugin title", "feature title", errors
        )
        self._ensure_no_overlap(
            steps_chip_rect, header_standee_rect, "steps chip", "header standee", errors
        )
        self._ensure_no_overlap(
            title_rect, steps_chip_rect, "plugin title", "steps chip", errors
        )
        self._ensure_no_overlap(
            feature_rect, header_standee_rect, "feature title", "header standee", errors
        )
        if trigger_rect is not None:
            self._ensure_no_overlap(
                feature_rect, trigger_rect, "feature title", "trigger chip", errors
            )
            self._ensure_no_overlap(
                trigger_rect,
                header_standee_rect,
                "trigger chip",
                "header standee",
                errors,
            )

        y = body_top
        prior_rects: list[tuple[str, tuple[int, int, int, int]]] = []
        for index, spec in enumerate(turn_specs, start=1):
            for name, rect in self._turn_rects(spec, y):
                self._ensure_inside(panel_rect, rect, f"turn {index} {name}", errors)
                for prior_name, prior_rect in prior_rects:
                    self._ensure_no_overlap(
                        prior_rect,
                        rect,
                        prior_name,
                        f"turn {index} {name}",
                        errors,
                        padding=4,
                    )
                prior_rects.append((f"turn {index} {name}", rect))
            y += spec.height + self.TURN_GAP

        footer_rect = (
            self.OUTER_MARGIN + 28,
            footer_top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            footer_top + self.FOOTER_HEIGHT,
        )
        self._ensure_inside(shell_rect, footer_rect, "footer bar", errors)
        footer_right_bbox = self._text_size(self.FOOTER_RIGHT_TEXT, self.footer_font)
        footer_right_width = int(footer_right_bbox[2] - footer_right_bbox[0])
        footer_right_rect = (
            footer_rect[2] - self.FOOTER_SIDE_PADDING - footer_right_width,
            footer_rect[1],
            footer_rect[2] - self.FOOTER_SIDE_PADDING,
            footer_rect[3],
        )
        self._ensure_inside(footer_rect, footer_right_rect, "footer right text", errors)
        _ = plugin_version, plugin_author
        return tuple(errors)

    def _turn_rects(
        self,
        spec: "_TurnSpec",
        top: int,
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        if spec.turn.speaker == "SYSTEM":
            left = (self.WIDTH - spec.width) // 2
            right = left + spec.width
            return [("system bubble", (left, top, right, top + spec.height))]

        is_user = spec.turn.speaker == "USER"
        bubble_width = spec.width
        avatar_x = (
            self.WIDTH
            - self.OUTER_MARGIN
            - self.CONVERSATION_SIDE_PADDING
            - self.AVATAR_SIZE
            if is_user
            else self.OUTER_MARGIN + self.CONVERSATION_SIDE_PADDING
        )
        bubble_x = (
            avatar_x - 18 - bubble_width
            if is_user
            else avatar_x + self.AVATAR_SIZE + 18
        )
        bubble_y = top + max((self.AVATAR_SIZE - spec.height) // 2, 0)
        return [
            (
                "avatar",
                (avatar_x, top, avatar_x + self.AVATAR_SIZE, top + self.AVATAR_SIZE),
            ),
            (
                "bubble",
                (bubble_x, bubble_y, bubble_x + bubble_width, bubble_y + spec.height),
            ),
        ]

    def _text_rect(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        font: Any,
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = self._text_size(text, font)
        return (
            int(x + left),
            int(y + top),
            int(x + right),
            int(y + bottom),
        )

    def _draw_text_centered(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[int, int, int, int],
        text: str,
        *,
        font: Any,
        fill: str,
        align: Literal["center", "left", "right"] = "center",
        padding_x: int = 0,
    ) -> None:
        bbox = self._text_size(text, font)
        text_width = int(bbox[2] - bbox[0])
        text_height = int(bbox[3] - bbox[1])
        if align == "left":
            x = rect[0] + padding_x
        elif align == "right":
            x = rect[2] - text_width
        else:
            x = rect[0] + (rect[2] - rect[0] - text_width) / 2
        y = rect[1] + (rect[3] - rect[1] - text_height) / 2 - bbox[1]
        self._draw_text(draw, x=x, y=y, text=text, font=font, fill=fill)

    def _chip_rect(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        font: Any,
        min_width: int = 0,
    ) -> tuple[int, int, int, int]:
        bbox = self._text_size(text, font)
        width = max(int(bbox[2] - bbox[0] + 28), min_width)
        height = max(int(bbox[3] - bbox[1] + 18), self.CHIP_HEIGHT)
        return (x, y, x + width, y + height)

    def _conversation_height(self, turn_specs: Sequence["_TurnSpec"]) -> int:
        return sum(spec.height for spec in turn_specs) + self.TURN_GAP * max(
            len(turn_specs) - 1,
            0,
        )

    def _ensure_inside(
        self,
        outer: tuple[int, int, int, int],
        inner: tuple[int, int, int, int],
        label: str,
        errors: list[str],
    ) -> None:
        if (
            inner[0] < outer[0]
            or inner[1] < outer[1]
            or inner[2] > outer[2]
            or inner[3] > outer[3]
        ):
            errors.append(f"{label} exceeds its container bounds")

    def _ensure_no_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        a_label: str,
        b_label: str,
        errors: list[str],
        *,
        padding: int = 0,
    ) -> None:
        if self._boxes_overlap(a, b, padding=padding):
            errors.append(f"{a_label} overlaps {b_label}")

    def _boxes_overlap(
        self,
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
        *,
        padding: int = 0,
    ) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return not (
            ax2 + padding <= bx1
            or bx2 + padding <= ax1
            or ay2 + padding <= by1
            or by2 + padding <= ay1
        )

    def _fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        *,
        max_width: int,
    ) -> str:
        if self._text_width(text, font) <= max_width:
            return text

        ellipsis = "..."
        current = text
        while current:
            current = current[:-1]
            candidate = current.rstrip() + ellipsis
            if self._text_width(candidate, font) <= max_width:
                return candidate
        return ellipsis

    def _normalize_demo_text(self, text: str) -> str:
        return text

    def _wrap_inline_text(
        self,
        text: str,
        *,
        max_width: int,
        font: Any,
    ) -> list[tuple[InlineTextSpan, ...]]:
        lines: list[tuple[InlineTextSpan, ...]] = []
        for paragraph in text.splitlines():
            current: list[InlineTextSpan] = []
            for span in split_inline_text_spans(paragraph):
                for char in span.text:
                    candidate = self._append_inline_char(
                        current,
                        char,
                        code=span.code,
                    )
                    if (
                        not current
                        or self._inline_line_width(candidate, font) <= max_width
                    ):
                        current = candidate
                        continue
                    lines.append(tuple(current))
                    current = [InlineTextSpan(char, code=span.code)]
            lines.append(tuple(current))
        return lines or [split_inline_text_spans(text)]

    def _line_block_height(
        self,
        lines: Iterable[tuple[InlineTextSpan, ...]],
        font: Any,
    ) -> int:
        count = 0
        for _ in lines:
            count += 1
        if count == 0:
            return 0
        return count * self._font_line_height(font) - 10

    def _max_inline_line_width(
        self,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
    ) -> int:
        return int(
            max(
                (self._inline_line_width(line, font) for line in lines),
                default=0,
            )
        )

    def _draw_multiline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        lines: Sequence[tuple[InlineTextSpan, ...]],
        font: Any,
        fill: str,
    ) -> None:
        line_height = self._font_line_height(font)
        for index, line in enumerate(lines):
            self._draw_inline_text_line(
                draw,
                x=x,
                y=y + index * line_height,
                line=line,
                font=font,
                fill=fill,
            )

    def _draw_inline_text_line(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        line: Sequence[InlineTextSpan],
        font: Any,
        fill: str,
    ) -> None:
        cursor_x = x
        line_height = self._font_line_height(font)
        for span in line:
            if not span.text:
                continue
            if not span.code:
                self._draw_text(
                    draw,
                    x=cursor_x,
                    y=y,
                    text=span.text,
                    font=font,
                    fill=fill,
                )
                cursor_x += self._text_width(span.text, font)
                continue
            text_bbox = self._text_size(span.text, font)
            text_width = int(text_bbox[2] - text_bbox[0])
            text_height = int(text_bbox[3] - text_bbox[1])
            chip_height = max(text_height + self.theme.inline_code_pad_y * 2, 22)
            chip_y = y + max((line_height - chip_height) / 2, 0)
            chip_width = text_width + self.theme.inline_code_pad_x * 2
            draw.rounded_rectangle(
                (
                    cursor_x,
                    chip_y,
                    cursor_x + chip_width,
                    chip_y + chip_height,
                ),
                radius=self.theme.inline_code_radius,
                fill=self.theme.inline_code_bg,
            )
            text_y = chip_y + (chip_height - text_height) / 2 - text_bbox[1]
            self._draw_text(
                draw,
                x=cursor_x + self.theme.inline_code_pad_x,
                y=text_y,
                text=span.text,
                font=font,
                fill=self.theme.inline_code_text,
            )
            cursor_x += chip_width

    def _font_line_height(self, font: Any) -> int:
        bbox = self._text_size("Ag", font)
        return int(bbox[3] - bbox[1] + 10)

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: float,
        y: float,
        text: str,
        font: Any,
        fill: str,
    ) -> None:
        if not text:
            return
        if not self._contains_emoji(text):
            draw.text((x, y), text, font=font, fill=fill)
            return
        text_box = self._text_size(text, font)
        text_width = max(text_box[2] - text_box[0] + 8, 1)
        text_height = max(text_box[3] - text_box[1] + 8, 1)
        text_layer = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
        BuildImage(text_layer).draw_text(
            (0, 0),
            text,
            font_size=self._font_size(font),
            fill=fill,
            font_families=self.FONT_FAMILIES,
            stroke_ratio=0,
        )
        draw._image.paste(text_layer, (int(x), int(y)), text_layer)

    def _text_size(self, text: str, font: Any) -> tuple[int, int, int, int]:
        if not text:
            return (0, 0, 0, self._font_line_height(font))
        if not self._contains_emoji(text):
            draw = ImageDraw.Draw(Image.new("RGB", (10, 10), self.theme.panel_bg))
            bbox = draw.textbbox((0, 0), text, font=font)
            return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        text_image = Text2Image.from_text(
            text,
            self._font_size(font),
            fill=self.theme.deep,
            stroke_width=0,
            font_families=self.FONT_FAMILIES,
        )
        return (0, 0, ceil(text_image.longest_line), ceil(text_image.height))

    def _text_width(self, text: str, font: Any) -> int:
        return self._text_size(text, font)[2]

    def _inline_line_width(
        self,
        line: Sequence[InlineTextSpan],
        font: Any,
    ) -> int:
        width = 0
        for span in line:
            if not span.text:
                continue
            width += self._text_width(span.text, font)
            if span.code:
                width += self.theme.inline_code_pad_x * 2
        return width

    def _append_inline_char(
        self,
        spans: Sequence[InlineTextSpan],
        char: str,
        *,
        code: bool,
    ) -> list[InlineTextSpan]:
        updated = list(spans)
        if updated and updated[-1].code is code:
            updated[-1] = InlineTextSpan(updated[-1].text + char, code=code)
        else:
            updated.append(InlineTextSpan(char, code=code))
        return updated

    def _inline_chip_rect(
        self,
        *,
        x: int,
        y: int,
        line: Sequence[InlineTextSpan],
        font: Any,
        min_width: int = 0,
    ) -> tuple[int, int, int, int]:
        width = max(self._inline_line_width(line, font) + 28, min_width)
        height = max(self._font_line_height(font) + 8, self.CHIP_HEIGHT)
        return (x, y, x + int(width), y + int(height))

    def _fit_inline_spans(
        self,
        spans: Sequence[InlineTextSpan],
        font: Any,
        max_width: int,
    ) -> tuple[InlineTextSpan, ...]:
        if self._inline_line_width(spans, font) <= max_width:
            return tuple(spans)

        ellipsis = InlineTextSpan("...", code=False)
        current: list[InlineTextSpan] = list(spans)
        while current:
            last = current[-1]
            if len(last.text) > 1:
                current[-1] = InlineTextSpan(last.text[:-1], code=last.code)
                if not current[-1].text:
                    current.pop()
            else:
                current.pop()
            candidate = [*current, ellipsis]
            if self._inline_line_width(candidate, font) <= max_width:
                return tuple(candidate)
        return (ellipsis,)

    def _font_size(self, font: Any) -> int:
        return int(getattr(font, "size", 16))

    def _contains_emoji(self, text: str) -> bool:
        return any(
            "\U0001f000" <= char <= "\U0001faff" or char == "\ufe0f" for char in text
        )
