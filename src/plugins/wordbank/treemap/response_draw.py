"""Response card drawing helpers for wordbank treemap rendering."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any

from PIL import Image, ImageDraw

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .models import (
    SearchTreemapResponseCard,
    SearchTreemapResponseSegment,
    SearchTreemapTile,
)


class SearchTreemapResponseDrawMixin:
    def _draw_response_card_grid(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        tile: SearchTreemapTile,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        responses = tile.item.responses
        if width < 150 or height < 54:
            return

        hidden_count = tile.item.hidden_response_count
        overflow_height = 0
        if hidden_count > 0 and height >= 112:
            overflow_height = min(28, max(22, self._line_height(self.tile_meta_font)))

        grid_height = max(1, height - overflow_height)
        if len(responses) == 1:
            self._draw_response_card(
                image,
                draw,
                responses[0],
                locale,
                x=x,
                y=y,
                width=width,
                height=grid_height,
            )
            total_hidden = max(0, tile.item.response_count - 1)
            if total_hidden > 0 and overflow_height > 0:
                self._draw_overflow_banner(
                    draw,
                    locale,
                    x=x,
                    y=y + height - overflow_height,
                    width=width,
                    height=overflow_height,
                    hidden_count=total_hidden,
                )
            return

        dual_rects = (
            self._dual_response_rects(width=width, height=grid_height)
            if len(responses) == 2 and tile.item.hidden_response_count <= 0
            else None
        )
        if dual_rects and self._can_use_dual_response_layout(
            responses=responses,
            locale=locale,
            rects=dual_rects,
        ):
            self._draw_dual_response_cards(
                image,
                draw,
                responses=responses,
                locale=locale,
                x=x,
                y=y,
                width=width,
                height=grid_height,
            )
            return

        cols, _ = self._choose_card_layout(
            width=width,
            height=grid_height,
            responses=responses,
            response_count=len(responses),
            locale=locale,
        )
        placements = self._build_masonry_layout(
            responses=responses,
            locale=locale,
            x=x,
            y=y,
            width=width,
            height=grid_height,
            cols=cols,
        )
        placements = self._expand_masonry_layout(
            placements,
            responses=responses,
            x=x,
            y=y,
            height=grid_height,
            cols=cols,
        )
        shown_count = len(placements)
        if shown_count <= 0:
            if responses:
                self._draw_response_card(
                    image,
                    draw,
                    responses[0],
                    locale,
                    x=x,
                    y=y,
                    width=width,
                    height=grid_height,
                )
                shown_count = 1
            else:
                self._draw_overflow_banner(
                    draw,
                    locale,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    hidden_count=hidden_count,
                )
                return

        for card_index, rect in placements:
            self._draw_response_card(
                image,
                draw,
                responses[card_index],
                locale,
                x=rect.x,
                y=rect.y,
                width=rect.width,
                height=rect.height,
            )

        total_hidden = max(0, tile.item.response_count - shown_count)
        if total_hidden > 0 and overflow_height > 0:
            self._draw_overflow_banner(
                draw,
                locale,
                x=x,
                y=y + height - overflow_height,
                width=width,
                height=overflow_height,
                hidden_count=total_hidden,
            )

    def _draw_dual_response_cards(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        responses: Sequence[SearchTreemapResponseCard],
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        rects = self._dual_response_rects(width=width, height=height)
        if rects is None:
            return
        for response, rect in zip(responses[:2], rects):
            self._draw_response_card(
                image,
                draw,
                response,
                locale,
                x=rect.x,
                y=rect.y,
                width=rect.width,
                height=rect.height,
            )

    def _draw_response_card(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        draw.rectangle(
            (x, y, x + width, y + height),
            fill=self.CARD_BG,
            outline=self.BORDER,
            width=1,
        )
        normalized_text = self._normalize_response_text(
            response.visible_text,
            locale,
            has_image_preview=response.has_image,
        )
        single_text = self._single_text_response_text(response, locale)
        compact_card = len(normalized_text) <= 14 and len(response.rule) <= 10
        spacious_card = width >= 240 and height >= 150
        narrow_text_card = single_text is not None and width < 220 and height >= 220
        if narrow_text_card:
            pad = 2
        elif spacious_card and not compact_card:
            pad = 12
        else:
            pad = 8 if compact_card or min(width, height) < 120 else 10

        meta_font = self._choose_response_meta_font(
            width=width,
            spacious=spacious_card,
            rule=response.rule,
        )
        title_font = self._choose_response_title_font(
            normalized_text,
            width=width - pad * 2,
            spacious=spacious_card,
            has_image=response.has_image,
        )
        meta_lines = self._build_response_meta_lines(
            response,
            locale,
            font=meta_font,
            max_width=max(1, width - pad * 2),
        )
        meta_line_height = self._line_height(meta_font)
        meta_gap = 0 if narrow_text_card else (6 if compact_card else 8)
        meta_height = (
            len(meta_lines) * meta_line_height + max(0, len(meta_lines) - 1) * 2
        )
        content_x = x + pad
        content_width = max(1, width - pad * 2)
        measured_content_height = max(
            1,
            self._measure_response_content_height_for_layout(
                response,
                locale,
                font=title_font,
                width=content_width,
            ),
        )
        content_mode = self._response_content_mode(
            response,
            locale,
            single_text=single_text,
        )
        card_layout = self._compute_response_card_vertical_layout(
            y=y,
            height=height,
            width=width,
            pad=pad,
            content_height=measured_content_height,
            meta_height=meta_height,
            meta_gap=meta_gap,
            content_mode=content_mode,
            narrow_text_card=narrow_text_card,
        )
        if single_text is not None:
            self._draw_fitted_single_text_response(
                draw,
                single_text,
                x=content_x,
                y=card_layout.content_y,
                width=content_width,
                height=card_layout.content_height,
            )
        else:
            self._draw_response_content(
                image,
                draw,
                response,
                locale,
                font=title_font,
                x=content_x,
                y=card_layout.content_y,
                width=content_width,
                height=card_layout.content_height,
            )
        if card_layout.divider_y > card_layout.content_y + 8:
            draw.line(
                (
                    content_x,
                    card_layout.divider_y,
                    content_x + content_width,
                    card_layout.divider_y,
                ),
                fill=self.DIVIDER,
                width=1,
            )
        cursor_y = card_layout.meta_y
        for line in meta_lines:
            if cursor_y + meta_line_height > y + height - pad + 2:
                break
            draw.text((content_x, cursor_y), line, font=meta_font, fill=self.BODY)
            cursor_y += meta_line_height + (0 if narrow_text_card else 2)

    def _draw_fitted_single_text_response(
        self: Any,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        layout_width = self._single_text_layout_width(
            width,
            height_cap=height,
            text=text,
        )
        font, lines = self._fit_single_text_response_layout(
            text,
            max_width=layout_width,
            max_height=height,
        )
        if not lines:
            return
        line_height = self._line_height(font)
        line_gap = self._single_text_line_gap(
            width=width,
            height_cap=height,
            text=text,
            line_count=len(lines),
        )
        total_height = len(lines) * line_height + max(0, len(lines) - 1) * line_gap
        if len(lines) <= 2:
            cursor_y = y + max(0, (height - total_height) // 2)
        elif total_height < int(height * 0.55):
            cursor_y = y + max(0, (height - total_height) // 3)
        else:
            cursor_y = y
        centered_lines = len(lines) <= 4 and len(text.strip()) <= 24
        for line in lines:
            line_x = x
            if centered_lines:
                line_x += max(0, (width - self._text_width(line, font)) // 2)
            draw.text((line_x, cursor_y), line, font=font, fill=self.CARD_ACCENT)
            cursor_y += line_height + line_gap

    def _draw_response_content(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if not segments:
            return
        estimated_height = self._estimate_response_content_height(
            response,
            locale,
            font=font,
            width=width,
        )
        if estimated_height > 0 and estimated_height < height:
            spare_height = height - estimated_height
            if not response.has_image and len(response.visible_text.strip()) <= 20:
                offset = min(64, max(0, spare_height // 2))
            else:
                offset = min(
                    36 if response.has_image else 48,
                    max(0, spare_height // (3 if response.has_image else 2)),
                )
            y += offset
            height = max(1, height - offset)
        text_segments = [segment for segment in segments if segment.kind == "text"]
        image_segments = [segment for segment in segments if segment.kind == "image"]
        if image_segments and not text_segments:
            self._draw_response_image_grid(
                image,
                draw,
                image_segments=image_segments,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            return
        self._draw_response_sequence_content(
            image,
            draw,
            response,
            locale,
            segments=segments,
            font=font,
            x=x,
            y=y,
            width=width,
            height=height,
        )

    def _draw_response_sequence_content(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        segments: Sequence[SearchTreemapResponseSegment],
        font: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        gap = 6
        cursor_y = y
        for index, segment in enumerate(segments):
            remaining_height = y + height - cursor_y
            if remaining_height <= 16:
                break
            remaining_count = len(segments) - index
            available_height = max(
                1, remaining_height - gap * max(0, remaining_count - 1)
            )
            if segment.kind == "text":
                used = self._draw_text_block(
                    draw,
                    self._normalize_response_text(
                        segment.text,
                        locale,
                        has_image_preview=bool(response.primary_image_path),
                    ),
                    font=font,
                    x=x,
                    y=cursor_y,
                    width=width,
                    height=available_height,
                )
            else:
                used = self._draw_image_block(
                    image,
                    draw,
                    segment.image_path,
                    x=x,
                    y=cursor_y,
                    width=width,
                    height=max(
                        44,
                        min(
                            available_height,
                            self._preferred_sequence_image_height(
                                width,
                                image_path=segment.image_path,
                            ),
                        ),
                    ),
                )
            if used <= 0:
                continue
            cursor_y += used + gap

    def _draw_response_image_grid(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        *,
        image_segments: Sequence[SearchTreemapResponseSegment],
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        if len(image_segments) == 1 or width < 180:
            self._draw_image_block(
                image,
                draw,
                image_segments[0].image_path,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            return
        gap = 6
        cols = 2
        rows = max(1, math.ceil(min(len(image_segments), 4) / cols))
        cell_width = max(1, (width - gap * (cols - 1)) // cols)
        cell_height = max(1, (height - gap * (rows - 1)) // rows)
        for index, segment in enumerate(image_segments[:4]):
            row = index // cols
            col = index % cols
            self._draw_image_block(
                image,
                draw,
                segment.image_path,
                x=x + col * (cell_width + gap),
                y=y + row * (cell_height + gap),
                width=cell_width,
                height=cell_height,
            )

    def _draw_text_block(
        self: Any,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        font: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> int:
        if not text or width <= 0 or height <= 0:
            return 0
        fitted_font, lines = self._fit_lxgw_text_block_layout(
            text,
            max_width=width,
            max_height=height,
            preferred_size=getattr(font, "size", None),
        )
        line_height = self._line_height(fitted_font)
        cursor_y = y
        for line in lines:
            if cursor_y + line_height > y + height + 2:
                break
            draw.text((x, cursor_y), line, font=fitted_font, fill=self.CARD_ACCENT)
            cursor_y += line_height
        return max(0, cursor_y - y)

    def _draw_image_block(
        self: Any,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        image_path: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> int:
        if not image_path or width <= 0 or height <= 0:
            return 0
        preview = self._fit_preview_image(
            image_path,
            max_width=width,
            max_height=height,
        )
        if preview is None:
            return 0
        offset_x = x + max(0, (width - preview.width) // 2)
        offset_y = y + max(0, (height - preview.height) // 2)
        image.paste(preview, (offset_x, offset_y))
        draw.rectangle(
            (
                offset_x,
                offset_y,
                offset_x + preview.width,
                offset_y + preview.height,
            ),
            outline=self.BORDER,
            width=1,
        )
        return preview.height

    def _draw_overflow_banner(
        self: Any,
        draw: ImageDraw.ImageDraw,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        hidden_count: int,
    ) -> None:
        if hidden_count <= 0:
            return
        draw.rectangle(
            (x, y, x + width, y + height),
            fill=self.theme.highlight_fill,
            outline=self.BORDER,
            width=1,
        )
        label = tr(
            locale,
            "wordbank.search_card.more_responses",
            count=hidden_count,
        ).strip()
        draw.text(
            (
                x + 10,
                y + max(2, (height - self._line_height(self.tile_meta_font)) // 2),
            ),
            self._truncate_line(label, self.tile_meta_font, max(1, width - 20)),
            font=self.tile_meta_font,
            fill=self.ACCENT,
        )

    def _draw_overflow_card(
        self: Any,
        draw: ImageDraw.ImageDraw,
        tile: SearchTreemapTile,
        locale: LocaleCode,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        hidden_count: int,
    ) -> None:
        draw.rectangle(
            (x, y, x + width, y + height),
            fill=self.theme.highlight_fill,
            outline=self.BORDER,
            width=1,
        )
        pad = 10 if min(width, height) >= 120 else 8
        lines = (
            self._normalize_text(
                tr(
                    locale,
                    "wordbank.search_card.more_responses",
                    count=hidden_count,
                ).strip(),
                locale,
            ),
            f"总响应 {tile.item.response_count}",
            f"命中 {self._format_matched_by_label(tile.item.matched_by, locale)}",
        )
        cursor_y = y + pad
        for index, line in enumerate(lines):
            font = self.card_title_font if index == 0 else self.card_meta_font
            color = self.ACCENT if index == 0 else self.BODY
            wrapped = self._wrap_text(
                line,
                font,
                max(1, width - pad * 2),
                max_lines=2 if index == 0 else 1,
            )
            for item in wrapped:
                if cursor_y + self._line_height(font) > y + height - pad:
                    return
                draw.text((x + pad, cursor_y), item, font=font, fill=color)
                cursor_y += self._line_height(font)
            cursor_y += 2
