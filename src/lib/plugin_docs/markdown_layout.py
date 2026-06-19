from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .command_layout import InlineTextSpan, split_inline_text_spans


@dataclass(slots=True, frozen=True)
class MarkdownBlock:
    kind: str
    text: str = ""
    level: int = 0
    ordered: bool = False
    fence_info: str = ""


@dataclass(slots=True, frozen=True)
class MarkdownLayoutLine:
    kind: str
    segments: tuple[InlineTextSpan, ...]
    indent_level: int
    bullet: str | None = None
    code: bool = False


@dataclass(slots=True, frozen=True)
class MarkdownLayout:
    lines: tuple[MarkdownLayoutLine, ...]
    line_height: int
    indent_px: int
    gap_after_paragraph: int
    gap_after_list: int
    gap_after_code: int
    total_height: int


@lru_cache(maxsize=1)
def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False, "breaks": False})
    parser.enable("table")
    return parser


def parse_markdown_blocks(text: str) -> tuple[MarkdownBlock, ...]:
    if not text.strip():
        return ()
    return _extract_blocks(tuple(_markdown_parser().parse(text)))


def build_markdown_layout(
    text: str,
    *,
    max_width: int,
    line_height: int,
    indent_px: int,
    measure_text: Callable[[str, bool], int],
) -> MarkdownLayout:
    blocks = parse_markdown_blocks(text)
    if not blocks:
        return MarkdownLayout(
            lines=(),
            line_height=line_height,
            indent_px=indent_px,
            gap_after_paragraph=0,
            gap_after_list=0,
            gap_after_code=0,
            total_height=0,
        )

    lines: list[MarkdownLayoutLine] = []
    total_height = 0
    gap_after_paragraph = max(10, line_height // 3)
    gap_after_list = max(8, line_height // 4)
    gap_after_code = max(14, line_height // 2)

    for index, block in enumerate(blocks):
        block_lines = _layout_block(
            block,
            max_width=max_width,
            indent_px=indent_px,
            measure_text=measure_text,
        )
        lines.extend(block_lines)
        total_height += len(block_lines) * line_height
        if index == len(blocks) - 1:
            continue
        next_gap = (
            gap_after_code
            if block.kind == "code"
            else gap_after_list
            if block.kind == "list_item"
            else gap_after_paragraph
        )
        total_height += next_gap
        lines.append(
            MarkdownLayoutLine(
                kind="spacer",
                segments=(),
                indent_level=next_gap,
            )
        )

    return MarkdownLayout(
        lines=tuple(lines),
        line_height=line_height,
        indent_px=indent_px,
        gap_after_paragraph=gap_after_paragraph,
        gap_after_list=gap_after_list,
        gap_after_code=gap_after_code,
        total_height=total_height,
    )


def _extract_blocks(
    tokens: Sequence[Token], *, base_level: int = 0
) -> tuple[MarkdownBlock, ...]:
    blocks: list[MarkdownBlock] = []
    index = 0
    ordered_stack: list[bool] = []
    list_level = base_level
    ordered_counter: list[int] = []
    while index < len(tokens):
        token = tokens[index]
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            ordered = token.type == "ordered_list_open"
            ordered_stack.append(ordered)
            ordered_counter.append(int(token.attrGet("start") or "1"))
            list_level += 1
            index += 1
            continue
        if token.type in {"bullet_list_close", "ordered_list_close"}:
            if ordered_stack:
                ordered_stack.pop()
            if ordered_counter:
                ordered_counter.pop()
            list_level = max(base_level, list_level - 1)
            index += 1
            continue
        if token.type == "list_item_open":
            depth = 1
            cursor = index + 1
            while cursor < len(tokens) and depth > 0:
                if tokens[cursor].type == "list_item_open":
                    depth += 1
                elif tokens[cursor].type == "list_item_close":
                    depth -= 1
                cursor += 1
            item_tokens = tokens[index + 1 : cursor - 1]
            child_blocks = _extract_blocks(item_tokens, base_level=list_level)
            bullet = ordered_stack[-1] if ordered_stack else False
            number = None
            if bullet and ordered_counter:
                number = ordered_counter[-1]
                ordered_counter[-1] += 1
            if child_blocks:
                first = child_blocks[0]
                lead = MarkdownBlock(
                    kind="list_item",
                    text=first.text,
                    level=list_level - 1,
                    ordered=bullet,
                    fence_info=str(number or ""),
                )
                blocks.append(lead)
                for extra in child_blocks[1:]:
                    blocks.append(
                        MarkdownBlock(
                            kind=extra.kind,
                            text=extra.text,
                            level=extra.level,
                            ordered=extra.ordered,
                            fence_info=extra.fence_info,
                        )
                    )
            index = cursor
            continue
        if token.type == "paragraph_open":
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            text = _render_inline(inline.children or ()) if inline is not None else ""
            if text.strip():
                if text.startswith("[!TIP]"):
                    tip_text = text.removeprefix("[!TIP]").strip()
                    if tip_text:
                        blocks.append(
                            MarkdownBlock(
                                kind="tip",
                                text=tip_text,
                                level=base_level,
                            )
                        )
                    index += 3
                    continue
                blocks.append(
                    MarkdownBlock(
                        kind="paragraph",
                        text=text,
                        level=base_level,
                    )
                )
            index += 3
            continue
        if token.type == "blockquote_open":
            depth = 1
            cursor = index + 1
            while cursor < len(tokens) and depth > 0:
                if tokens[cursor].type == "blockquote_open":
                    depth += 1
                elif tokens[cursor].type == "blockquote_close":
                    depth -= 1
                cursor += 1
            quote_blocks = _extract_blocks(
                tokens[index + 1 : cursor - 1],
                base_level=base_level,
            )
            if quote_blocks:
                first = quote_blocks[0]
                text = first.text.strip()
                if text.startswith("[!TIP]"):
                    tip_text = text.removeprefix("[!TIP]").strip()
                    if tip_text:
                        blocks.append(
                            MarkdownBlock(
                                kind="tip",
                                text=tip_text,
                                level=base_level,
                            )
                        )
                    for extra in quote_blocks[1:]:
                        blocks.append(
                            MarkdownBlock(
                                kind="tip",
                                text=extra.text,
                                level=base_level,
                            )
                        )
                else:
                    for extra in quote_blocks:
                        blocks.append(extra)
            index = cursor
            continue
        if token.type == "fence":
            content = token.content.rstrip("\n")
            if content.strip():
                blocks.append(
                    MarkdownBlock(
                        kind="code",
                        text=content,
                        level=base_level,
                        fence_info=token.info.strip(),
                    )
                )
            index += 1
            continue
        index += 1
    return tuple(blocks)


def _render_inline(tokens: Sequence[Token]) -> str:
    fragments: list[str] = []
    for token in tokens:
        if token.type == "inline":
            fragments.append(_render_inline(token.children or ()))
        elif token.type == "text":
            fragments.append(token.content)
        elif token.type == "code_inline":
            fragments.append(f"`{token.content}`")
        elif token.type in {"softbreak", "hardbreak"}:
            fragments.append("\n")
        elif token.type == "image":
            fragments.append(token.content)
    return "".join(fragments)


def _layout_block(
    block: MarkdownBlock,
    *,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str, bool], int],
) -> list[MarkdownLayoutLine]:
    if block.kind == "code":
        return _wrap_code_block(
            block.text,
            max_width=max_width,
            indent_level=block.level,
            measure_text=measure_text,
        )
    if block.kind == "tip":
        return _wrap_inline_block(
            block.text,
            max_width=max_width,
            indent_level=block.level,
            bullet=None,
            indent_px=indent_px,
            measure_text=measure_text,
            line_kind="tip",
        )

    bullet = None
    indent_level = block.level
    if block.kind == "list_item":
        bullet = f"{block.fence_info}." if block.ordered and block.fence_info else "•"
        indent_level = block.level + 1
    return _wrap_inline_block(
        block.text,
        max_width=max_width,
        indent_level=indent_level,
        bullet=bullet,
        indent_px=indent_px,
        measure_text=measure_text,
    )


def _wrap_inline_block(
    text: str,
    *,
    max_width: int,
    indent_level: int,
    bullet: str | None,
    indent_px: int,
    measure_text: Callable[[str, bool], int],
    line_kind: str = "text",
) -> list[MarkdownLayoutLine]:
    spans = split_inline_text_spans(text)
    lines: list[MarkdownLayoutLine] = []
    current: list[InlineTextSpan] = []
    first_line = True
    bullet_width = measure_text(f"{bullet} ", False) if bullet else 0
    for span in spans:
        if span.code:
            candidate = [*current, span]
            current_limit = max_width - indent_level * indent_px
            if first_line:
                current_limit -= bullet_width
            if not current or _segments_width(candidate, measure_text) <= current_limit:
                current = candidate
                continue
            lines.append(
                MarkdownLayoutLine(
                    kind=line_kind,
                    segments=tuple(current),
                    indent_level=indent_level,
                    bullet=bullet if first_line else None,
                )
            )
            current = [span]
            first_line = False
            continue
        for char in span.text:
            candidate = [
                *current,
                InlineTextSpan(char, code=span.code, fill=span.fill),
            ]
            current_limit = max_width - indent_level * indent_px
            if first_line:
                current_limit -= bullet_width
            if not current or _segments_width(candidate, measure_text) <= current_limit:
                current = candidate
                continue
            lines.append(
                MarkdownLayoutLine(
                    kind=line_kind,
                    segments=tuple(current),
                    indent_level=indent_level,
                    bullet=bullet if first_line else None,
                )
            )
            current = [InlineTextSpan(char, code=span.code, fill=span.fill)]
            first_line = False
    if current or not lines:
        lines.append(
            MarkdownLayoutLine(
                kind=line_kind,
                segments=tuple(current),
                indent_level=indent_level,
                bullet=bullet if first_line else None,
            )
        )
    return lines


def _wrap_code_block(
    text: str,
    *,
    max_width: int,
    indent_level: int,
    measure_text: Callable[[str, bool], int],
) -> list[MarkdownLayoutLine]:
    lines: list[MarkdownLayoutLine] = []
    for raw_line in text.splitlines() or [""]:
        current = ""
        if not raw_line:
            lines.append(
                MarkdownLayoutLine(
                    kind="code",
                    segments=(InlineTextSpan("", code=True),),
                    indent_level=indent_level,
                    code=True,
                )
            )
            continue
        for char in raw_line:
            candidate = current + char
            if not current or measure_text(candidate, True) <= max_width:
                current = candidate
                continue
            lines.append(
                MarkdownLayoutLine(
                    kind="code",
                    segments=(InlineTextSpan(current, code=True),),
                    indent_level=indent_level,
                    code=True,
                )
            )
            current = char
        lines.append(
            MarkdownLayoutLine(
                kind="code",
                segments=(InlineTextSpan(current, code=True),),
                indent_level=indent_level,
                code=True,
            )
        )
    return lines


def _segments_width(
    segments: Sequence[InlineTextSpan],
    measure_text: Callable[[str, bool], int],
) -> int:
    width = 0
    for segment in segments:
        width += measure_text(segment.text, segment.code)
    return width
