"""Response card layout helpers for wordbank treemap rendering."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any

from src.lib.i18n.types import LocaleCode

from .models import (
    ResponseCardVerticalLayout,
    SearchTreemapResponseCard,
    TreemapRect,
)


class SearchTreemapResponseLayoutMixin:
    def _dual_response_rects(
        self: Any,
        *,
        width: int,
        height: int,
    ) -> tuple[TreemapRect, TreemapRect] | None:
        if width <= 0 or height <= 0:
            return None
        gap = 8
        if width >= max(320, height + 80):
            card_width = max(1, (width - gap) // 2)
            return (
                TreemapRect(x=0, y=0, width=card_width, height=height),
                TreemapRect(
                    x=card_width + gap,
                    y=0,
                    width=max(1, width - card_width - gap),
                    height=height,
                ),
            )
        card_height = max(1, (height - gap) // 2)
        return (
            TreemapRect(x=0, y=0, width=width, height=card_height),
            TreemapRect(
                x=0,
                y=card_height + gap,
                width=width,
                height=max(1, height - card_height - gap),
            ),
        )

    def _can_use_dual_response_layout(
        self: Any,
        *,
        responses: Sequence[SearchTreemapResponseCard],
        locale: LocaleCode,
        rects: Sequence[TreemapRect],
    ) -> bool:
        if len(responses) < 2 or len(rects) < 2:
            return False
        for response, rect in zip(responses[:2], rects):
            estimated_height = self._estimate_response_card_height(
                response,
                locale,
                width=rect.width,
            )
            if estimated_height > rect.height:
                return False
        return True

    def _choose_card_layout(
        self: Any,
        *,
        width: int,
        height: int,
        responses: Sequence[SearchTreemapResponseCard],
        response_count: int,
        locale: LocaleCode = "zh-CN",
    ) -> tuple[int, int]:
        if response_count <= 0:
            return (1, 0)
        card_gap = 8
        sample = responses[: min(len(responses), 4)]
        has_images = any(response.has_image for response in sample)
        average_text_len = sum(
            len(response.visible_text) + len(response.rule) for response in sample
        ) / max(len(sample), 1)
        min_card_height = 94 if has_images else (58 if average_text_len <= 18 else 66)
        preferred_height = 120 if has_images else (76 if average_text_len <= 18 else 92)
        candidates: list[tuple[int, int, tuple[int, int, int]]] = []
        for cols in (3, 2, 1):
            if response_count < cols:
                continue
            if cols == 3 and width < (560 if has_images else 480):
                continue
            if cols == 2 and width < 320:
                continue
            card_width = (width - card_gap * (cols - 1)) // cols
            if card_width < (176 if has_images else 136):
                continue
            placements = self._build_masonry_layout(
                responses=responses[:response_count],
                locale=locale,
                x=0,
                y=0,
                width=width,
                height=height,
                cols=cols,
            )
            shown = len(placements)
            if shown <= 0:
                continue
            used_heights = [
                placement[1].y + placement[1].height for placement in placements
            ]
            card_height = sum(rect.height for _, rect in placements) // max(
                len(placements), 1
            )
            if card_height < min_card_height:
                continue
            column_bottom = max(used_heights, default=0)
            score = (shown, -abs(card_height - preferred_height), cols)
            if column_bottom > 0:
                score = (shown, -abs(card_height - preferred_height), -column_bottom)
            candidates.append((cols, shown, score))
        if not candidates:
            return (1, 1 if width >= 180 and height >= min_card_height else 0)
        cols, shown, _ = max(candidates, key=lambda item: item[2])
        return (cols, shown)

    def _build_masonry_layout(
        self: Any,
        *,
        responses: Sequence[SearchTreemapResponseCard],
        locale: LocaleCode,
        x: int,
        y: int,
        width: int,
        height: int,
        cols: int,
    ) -> list[tuple[int, TreemapRect]]:
        if cols <= 0 or width <= 0 or height <= 0:
            return []
        card_gap = 8
        card_width = max(1, (width - card_gap * (cols - 1)) // cols)
        column_heights = [0] * cols
        placements: list[tuple[int, TreemapRect]] = []
        for index, response in enumerate(responses):
            estimated_height = self._estimate_response_card_height(
                response,
                locale,
                width=card_width,
            )
            column = min(range(cols), key=lambda item: column_heights[item])
            next_y = y + column_heights[column]
            if column_heights[column] > 0:
                next_y += card_gap
            if next_y + estimated_height > y + height:
                break
            rect = TreemapRect(
                x=x + column * (card_width + card_gap),
                y=next_y,
                width=card_width,
                height=estimated_height,
            )
            placements.append((index, rect))
            column_heights[column] = rect.y + rect.height - y
        return placements

    def _expand_masonry_layout(
        self: Any,
        placements: Sequence[tuple[int, TreemapRect]],
        *,
        responses: Sequence[SearchTreemapResponseCard],
        x: int,
        y: int,
        height: int,
        cols: int,
    ) -> list[tuple[int, TreemapRect]]:
        if not placements or cols <= 0 or height <= 0:
            return list(placements)
        column_map: dict[int, list[tuple[int, TreemapRect]]] = {
            index: [] for index in range(cols)
        }
        for item_index, rect in placements:
            column = max(0, round((rect.x - x) / max(rect.width + 8, 1)))
            column_map[min(cols - 1, column)].append((item_index, rect))

        expanded: list[tuple[int, TreemapRect]] = []
        column_bottom = y + height
        for column in range(cols):
            entries = column_map.get(column, [])
            if not entries:
                continue
            used_bottom = max(rect.y + rect.height for _, rect in entries)
            leftover = column_bottom - used_bottom
            if leftover <= 2:
                expanded.extend(entries)
                continue
            weights = [
                max(
                    1,
                    self._estimate_card_flex_weight(responses[item_index], rect.height),
                )
                for item_index, rect in entries
            ]
            total_weight = sum(weights)
            if total_weight <= 0:
                expanded.extend(entries)
                continue
            cursor_y = entries[0][1].y
            consumed = 0
            column_expanded: list[tuple[int, TreemapRect]] = []
            for entry_index, ((item_index, rect), weight) in enumerate(
                zip(entries, weights)
            ):
                extra = (
                    leftover - consumed
                    if entry_index == len(entries) - 1
                    else round(leftover * weight / total_weight)
                )
                consumed += extra
                new_rect = TreemapRect(
                    x=rect.x,
                    y=cursor_y,
                    width=rect.width,
                    height=rect.height + max(0, extra),
                )
                column_expanded.append((item_index, new_rect))
                cursor_y = new_rect.y + new_rect.height + 8
            if column_expanded:
                last_index, last_rect = column_expanded[-1]
                delta = column_bottom - (last_rect.y + last_rect.height)
                if delta != 0:
                    column_expanded[-1] = (
                        last_index,
                        TreemapRect(
                            x=last_rect.x,
                            y=last_rect.y,
                            width=last_rect.width,
                            height=max(1, last_rect.height + delta),
                        ),
                    )
            expanded.extend(column_expanded)
        expanded.sort(key=lambda item: (item[1].y, item[1].x))
        return expanded

    def _estimate_card_flex_weight(
        self: Any,
        response: SearchTreemapResponseCard,
        estimated_height: int,
    ) -> int:
        segment_count = len(response.ordered_segments) or 1
        text_weight = max(1, len(response.visible_text.strip()) // 10)
        image_weight = 2 if response.has_image else 0
        base_weight = max(1, estimated_height // 48)
        return base_weight + segment_count + text_weight + image_weight

    def _estimate_response_card_height(
        self: Any,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        width: int,
    ) -> int:
        normalized_text = self._normalize_response_text(
            response.visible_text,
            locale,
            has_image_preview=response.has_image,
        )
        single_text = self._single_text_response_text(response, locale)
        compact_card = len(normalized_text) <= 14 and len(response.rule) <= 10
        spacious_card = width >= 240
        narrow_text_card = single_text is not None and width < 220
        if narrow_text_card:
            pad = 2
        elif spacious_card and not compact_card:
            pad = 12
        else:
            pad = 8 if compact_card else 10
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
        title_line_height = self._line_height(title_font)
        meta_lines = self._build_response_meta_lines(
            response,
            locale,
            font=meta_font,
            max_width=max(1, width - pad * 2),
        )
        meta_line_height = self._line_height(meta_font)
        meta_height = (
            len(meta_lines) * meta_line_height + max(0, len(meta_lines) - 1) * 2
        )
        content_height = self._measure_response_content_height_for_layout(
            response,
            locale,
            font=title_font,
            width=max(1, width - pad * 2),
        )
        meta_gap = 0 if narrow_text_card else (6 if compact_card else 8)
        base_height = pad * 2 + content_height + meta_gap + meta_height
        if response.has_image:
            minimum_content = max(
                58 if len(response.ordered_segments) <= 1 else 72, content_height
            )
        elif single_text is not None:
            minimum_content = max(title_line_height + 6, content_height)
        else:
            minimum_content = max(
                title_line_height + (6 if compact_card else 10), content_height
            )
        minimum = pad * 2 + meta_gap + meta_height + minimum_content
        maximum = 248 if response.has_image else 212
        return max(minimum, min(maximum, base_height))

    def _estimate_response_content_height(
        self: Any,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        width: int,
    ) -> int:
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if not segments:
            return 0
        line_height = self._line_height(font)
        if all(segment.kind == "image" for segment in segments):
            if len(segments) == 1 or width < 180:
                return self._preferred_sequence_image_height(
                    width,
                    image_path=segments[0].image_path,
                )
            rows = max(1, math.ceil(min(len(segments), 4) / 2))
            cell_width = max(1, (width - 6) // 2)
            cell_height = max(
                self._preferred_sequence_image_height(
                    cell_width,
                    image_path=segment.image_path,
                )
                for segment in segments[:4]
            )
            return rows * cell_height + (rows - 1) * 6
        content_height = 0
        for index, segment in enumerate(segments):
            if segment.kind == "text":
                text_lines = self._wrap_text(
                    self._normalize_response_text(
                        segment.text,
                        locale,
                        has_image_preview=bool(response.primary_image_path),
                    ),
                    font,
                    width,
                    max_lines=8 if width >= 220 else 6,
                )
                content_height += len(text_lines) * line_height
            else:
                content_height += self._preferred_sequence_image_height(
                    width,
                    image_path=segment.image_path,
                )
            if index < len(segments) - 1:
                content_height += 6
        return content_height

    def _measure_response_content_height_for_layout(
        self: Any,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        width: int,
    ) -> int:
        single_text = self._single_text_response_text(response, locale)
        if single_text is not None:
            layout_width = self._single_text_layout_width(
                width,
                height_cap=self._single_text_layout_height_cap(width, text=single_text),
                text=single_text,
            )
            layout_font, lines = self._fit_single_text_response_layout(
                single_text,
                max_width=layout_width,
                max_height=self._single_text_layout_height_cap(width, text=single_text),
            )
            line_gap = self._single_text_line_gap(
                width=width,
                height_cap=self._single_text_layout_height_cap(width, text=single_text),
                text=single_text,
                line_count=len(lines),
            )
            return (
                len(lines) * self._line_height(layout_font)
                + max(0, len(lines) - 1) * line_gap
            )
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if not segments:
            return 0
        mixed_content = any(segment.kind == "text" for segment in segments) and any(
            segment.kind == "image" for segment in segments
        )
        content_height = 0
        for index, segment in enumerate(segments):
            if segment.kind == "text":
                content_height += self._measure_response_text_height_for_layout(
                    self._normalize_response_text(
                        segment.text,
                        locale,
                        has_image_preview=bool(response.primary_image_path),
                    ),
                    width=width,
                    preferred_size=getattr(font, "size", None),
                    has_image=response.has_image,
                )
            else:
                content_height += self._estimate_layout_image_height(
                    width,
                    image_path=segment.image_path,
                    mixed_content=mixed_content,
                )
            if index < len(segments) - 1:
                content_height += 6
        return content_height

    def _measure_response_text_height_for_layout(
        self: Any,
        text: str,
        *,
        width: int,
        preferred_size: int | None,
        has_image: bool,
    ) -> int:
        fitted_font, lines = self._fit_lxgw_text_block_layout(
            text,
            max_width=width,
            max_height=self._layout_text_height_cap(
                width, has_image=has_image, text=text
            ),
            preferred_size=preferred_size,
        )
        return len(lines) * self._line_height(fitted_font)

    def _layout_text_height_cap(self, width: int, *, has_image: bool, text: str) -> int:
        if width < 120:
            base = 88
        elif width < 168:
            base = 108
        elif width < 224:
            base = 132
        else:
            base = 156
        if not has_image:
            base += 20
        if len(text.strip()) >= 28:
            base += 16
        return base

    def _single_text_layout_height_cap(self, width: int, *, text: str) -> int:
        if width < 120:
            base = 96
        elif width < 168:
            base = 136
        elif width < 224:
            base = 146
        else:
            base = 236
        if len(text.strip()) >= 18:
            base += 18
        elif len(text.strip()) <= 8:
            base += 64
        return base

    def _estimate_layout_image_height(
        self: Any,
        width: int,
        *,
        image_path: str,
        mixed_content: bool,
    ) -> int:
        natural_height = self._preferred_sequence_image_height(
            width, image_path=image_path
        )
        if not mixed_content:
            return natural_height
        mixed_floor = max(40, int(width * 0.24))
        mixed_ceiling = max(72, int(width * 0.72))
        return max(mixed_floor, min(mixed_ceiling, natural_height))

    def _preferred_sequence_image_height(
        self: Any, width: int, *, image_path: str
    ) -> int:
        if width <= 0:
            return 0
        image_size = self._load_image_size(image_path)
        if image_size is None:
            natural_height = int(width * 0.62)
        else:
            image_width, image_height = image_size
            natural_height = max(
                36, round(width * (image_height / max(image_width, 1)))
            )
        soft_floor = max(52, int(width * 0.26))
        if natural_height < soft_floor:
            natural_height = (natural_height + soft_floor) // 2
        soft_ceiling = max(120, int(width * 1.05))
        return max(44, min(soft_ceiling, natural_height))

    def _response_content_mode(
        self: Any,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        single_text: str | None,
    ) -> str:
        if single_text is not None:
            return "single_text"
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        has_text = any(segment.kind == "text" for segment in segments)
        has_image = any(segment.kind == "image" for segment in segments)
        if has_image and has_text:
            return "mixed"
        if has_image:
            return "image"
        normalized = self._normalize_response_text(
            response.visible_text,
            locale,
            has_image_preview=False,
        )
        return "text_short" if len(normalized) <= 24 else "text"

    def _compute_response_card_vertical_layout(
        self: Any,
        *,
        y: int,
        height: int,
        width: int,
        pad: int,
        content_height: int,
        meta_height: int,
        meta_gap: int,
        content_mode: str,
        narrow_text_card: bool = False,
    ) -> ResponseCardVerticalLayout:
        inner_height = max(1, height - pad * 2)
        divider_gap = 2 if narrow_text_card else (8 if inner_height >= 118 else 6)
        max_content_height = max(1, inner_height - meta_height - meta_gap - divider_gap)
        clipped_content_height = min(max_content_height, max(1, content_height))
        block_height = clipped_content_height + divider_gap + meta_gap + meta_height
        spare_height = max(0, inner_height - block_height)
        bias = {
            "single_text": 0.52,
            "text_short": 0.44,
            "text": 0.27,
            "mixed": 0.20,
            "image": 0.16,
        }.get(content_mode, 0.24)
        if content_mode == "single_text" and height >= max(240, width + 56):
            bias = 0.66 if narrow_text_card else 0.60
        top_offset = round(spare_height * bias)
        content_y = y + pad + top_offset
        divider_y = content_y + clipped_content_height + divider_gap - 2
        meta_y = divider_y + meta_gap
        return ResponseCardVerticalLayout(
            content_y=content_y,
            content_height=clipped_content_height,
            divider_y=divider_y,
            meta_y=meta_y,
        )

    def _choose_response_meta_font(
        self: Any,
        *,
        width: int,
        spacious: bool,
        rule: str,
    ) -> Any:
        if width < 220:
            return self._load_lxgw_font(13)
        if spacious and len(rule.strip()) <= 16:
            return self.card_large_meta_font
        return self.card_meta_font

    def _build_response_meta_lines(
        self: Any,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
        *,
        font: Any,
        max_width: int,
    ) -> list[str]:
        rule_text = self._normalize_text(response.rule, locale)
        return [
            self._truncate_line(f"创建者 {response.created_by}", font, max_width),
            self._truncate_line(f"权重 {response.weight}", font, max_width),
            self._truncate_line(f"规则 {rule_text}", font, max_width),
        ]

    def _single_text_response_text(
        self: Any,
        response: SearchTreemapResponseCard,
        locale: LocaleCode,
    ) -> str | None:
        segments = tuple(
            segment
            for segment in response.ordered_segments
            if (segment.kind == "text" and segment.text.strip())
            or (segment.kind == "image" and segment.image_path)
        )
        if len(segments) != 1 or segments[0].kind != "text":
            return None
        text = self._normalize_response_text(
            segments[0].text,
            locale,
            has_image_preview=False,
        )
        return text or None

    def _single_text_layout_width(
        self,
        width: int,
        *,
        height_cap: int,
        text: str,
    ) -> int:
        if width < 220 and height_cap >= 220 and 5 < len(text.strip()) <= 10:
            return max(1, int(width * 0.84))
        return width

    def _single_text_line_gap(
        self,
        *,
        width: int,
        height_cap: int,
        text: str,
        line_count: int,
    ) -> int:
        if (
            width < 220
            and height_cap >= 220
            and len(text.strip()) >= 6
            and line_count >= 2
        ):
            return 4
        return 0
