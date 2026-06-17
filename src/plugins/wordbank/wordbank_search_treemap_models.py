"""Data models, fixture parsing, and treemap layout for wordbank search cards."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path

import squarify

TREEMAP_LAYOUT_RESPONSE_CAP = 64
TREEMAP_LAYOUT_MIN_WEIGHT = 14
TREEMAP_LAYOUT_MAX_WEIGHT = 100


@dataclass(slots=True, frozen=True)
class SearchTreemapQuery:
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


@dataclass(slots=True, frozen=True)
class SearchTreemapResponseSegment:
    kind: str
    text: str = ""
    image_path: str = ""


@dataclass(slots=True, frozen=True)
class SearchTreemapResponseCard:
    text: str
    created_by: str
    weight: int
    rule: str
    image_path: str = ""
    segments: tuple[SearchTreemapResponseSegment, ...] = ()

    @property
    def ordered_segments(self) -> tuple[SearchTreemapResponseSegment, ...]:
        if self.segments:
            return self.segments
        built: list[SearchTreemapResponseSegment] = []
        if self.text.strip():
            built.append(SearchTreemapResponseSegment(kind="text", text=self.text))
        if self.image_path:
            built.append(SearchTreemapResponseSegment(kind="image", image_path=self.image_path))
        return tuple(built)

    @property
    def primary_image_path(self) -> str:
        for segment in self.ordered_segments:
            if segment.kind == "image" and segment.image_path:
                return segment.image_path
        return self.image_path

    @property
    def has_image(self) -> bool:
        return bool(self.primary_image_path)

    @property
    def visible_text(self) -> str:
        text_parts = [
            segment.text.strip()
            for segment in self.ordered_segments
            if segment.kind == "text" and segment.text.strip()
        ]
        if text_parts:
            return " ".join(text_parts)
        return self.text.strip()


@dataclass(slots=True, frozen=True)
class SearchTreemapItem:
    trigger_group_id: int
    trigger_text: str
    status: str
    created_by: str
    response_count: int
    responses: tuple[SearchTreemapResponseCard, ...]
    remaining_response_count: int = 0
    matched_by: str = ""

    @property
    def hidden_response_count(self) -> int:
        return max(0, self.response_count - len(self.responses))


@dataclass(slots=True, frozen=True)
class SearchTreemapPage:
    query: SearchTreemapQuery
    items: tuple[SearchTreemapItem, ...]


@dataclass(slots=True, frozen=True)
class TreemapRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(slots=True, frozen=True)
class SearchTreemapTile:
    item: SearchTreemapItem
    rect: TreemapRect
    raw_weight: int
    normalized_weight: int


@dataclass(slots=True, frozen=True)
class ResponseCardVerticalLayout:
    content_y: int
    content_height: int
    divider_y: int
    meta_y: int


def load_search_treemap_fixture(path: str | Path) -> SearchTreemapPage:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Search treemap fixture must be a JSON object")
    query_payload = payload.get("query")
    items_payload = payload.get("items")
    if not isinstance(query_payload, dict):
        raise ValueError("Search treemap fixture is missing object field: query")
    if not isinstance(items_payload, list):
        raise ValueError("Search treemap fixture is missing array field: items")
    query = SearchTreemapQuery(
        keyword=_require_str(query_payload, "keyword"),
        field=_require_str(query_payload, "field"),
        creator_id=_require_str(query_payload, "creator_id"),
        has_image=_require_bool(query_payload, "has_image"),
        page=_require_int(query_payload, "page", min_value=1),
        total_count=_require_int(query_payload, "total_count", min_value=0),
        limit=_require_int(query_payload, "limit", min_value=1),
    )
    items = tuple(_parse_treemap_item(item, index) for index, item in enumerate(items_payload))
    return SearchTreemapPage(query=query, items=items)


def build_search_treemap_layout(
    page: SearchTreemapPage,
    *,
    content_x: int,
    content_y: int,
    content_width: int,
    content_height: int,
) -> tuple[SearchTreemapTile, ...]:
    if not page.items:
        return ()
    weights = [max(item.response_count, 1) for item in page.items]
    normalized = [_layout_weight_from_response_count(weight) for weight in weights]
    sizes = squarify.normalize_sizes(normalized, content_width, content_height)
    rects = _build_rects_from_squarify(
        squarify.padded_squarify(sizes, content_x, content_y, content_width, content_height)
    )
    return tuple(
        SearchTreemapTile(item=item, rect=rect, raw_weight=weight, normalized_weight=normalized_weight)
        for item, rect, weight, normalized_weight in zip(page.items, rects, weights, normalized, strict=True)
    )


def _parse_treemap_item(payload: object, index: int) -> SearchTreemapItem:
    if not isinstance(payload, dict):
        raise ValueError(f"Search treemap item at index {index} must be an object")
    response_count = _require_int(payload, "response_count", min_value=1)
    raw_responses = payload.get("responses")
    if not isinstance(raw_responses, list):
        raise ValueError(f"Search treemap item at index {index} is missing array field: responses")
    responses = tuple(
        _parse_response_card(item, item_index, parent_index=index)
        for item_index, item in enumerate(raw_responses)
    )
    if len(responses) > response_count:
        raise ValueError(f"Search treemap item at index {index} has more response cards than response_count")
    remaining = payload.get("remaining_response_count")
    remaining_count = (
        _coerce_int(remaining, field_name="remaining_response_count", min_value=0)
        if remaining is not None
        else max(0, response_count - len(responses))
    )
    return SearchTreemapItem(
        trigger_group_id=_require_int(payload, "trigger_group_id", min_value=1),
        trigger_text=_require_str(payload, "trigger_text"),
        status=_require_str(payload, "status"),
        created_by=_require_str(payload, "created_by"),
        response_count=response_count,
        responses=responses,
        remaining_response_count=remaining_count,
        matched_by=str(payload.get("matched_by", "") or ""),
    )


def _parse_response_card(payload: object, index: int, *, parent_index: int) -> SearchTreemapResponseCard:
    if not isinstance(payload, dict):
        raise ValueError(
            "Search treemap response card at item index "
            f"{parent_index}, response index {index} must be an object"
        )
    raw_segments = payload.get("segments")
    segments = (
        tuple(
            _parse_response_segment(item, item_index, parent_index=parent_index)
            for item_index, item in enumerate(raw_segments)
        )
        if isinstance(raw_segments, list)
        else ()
    )
    return SearchTreemapResponseCard(
        text=_require_str(payload, "text"),
        created_by=_require_str(payload, "created_by"),
        weight=_require_int(payload, "weight", min_value=0),
        rule=_require_str(payload, "rule"),
        image_path=str(payload.get("image_path", "") or ""),
        segments=segments,
    )


def _parse_response_segment(payload: object, index: int, *, parent_index: int) -> SearchTreemapResponseSegment:
    if not isinstance(payload, dict):
        raise ValueError(
            "Search treemap response segment at item index "
            f"{parent_index}, segment index {index} must be an object"
        )
    kind = _require_str(payload, "kind")
    if kind not in {"text", "image"}:
        raise ValueError(f"Unsupported response segment kind: {kind}")
    return SearchTreemapResponseSegment(
        kind=kind,
        text=str(payload.get("text", "") or ""),
        image_path=str(payload.get("image_path", "") or ""),
    )


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Search treemap fixture field must be string: {key}")
    return value


def _require_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Search treemap fixture field must be bool: {key}")
    return value


def _require_int(payload: dict[str, object], key: str, *, min_value: int) -> int:
    return _coerce_int(payload.get(key), field_name=key, min_value=min_value)


def _coerce_int(value: object, *, field_name: str, min_value: int) -> int:
    if not isinstance(value, int):
        raise ValueError(f"Search treemap fixture field must be int: {field_name}")
    if value < min_value:
        raise ValueError(f"Search treemap fixture field must be >= {min_value}: {field_name}")
    return value


def _layout_weight_from_response_count(response_count: int) -> int:
    clamped = min(max(response_count, 1), TREEMAP_LAYOUT_RESPONSE_CAP)
    ratio = (clamped - 1) / max(TREEMAP_LAYOUT_RESPONSE_CAP - 1, 1)
    return round(
        TREEMAP_LAYOUT_MIN_WEIGHT
        + ratio * (TREEMAP_LAYOUT_MAX_WEIGHT - TREEMAP_LAYOUT_MIN_WEIGHT)
    )


def _build_rects_from_squarify(rects: Sequence[dict[str, float]]) -> list[TreemapRect]:
    built: list[TreemapRect] = []
    for rect in rects:
        built.append(
            TreemapRect(
                x=round(rect["x"]),
                y=round(rect["y"]),
                width=max(1, round(rect["dx"])),
                height=max(1, round(rect["dy"])),
            )
        )
    return built
