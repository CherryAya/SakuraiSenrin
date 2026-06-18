"""Command layout helpers extracted from plugin docs rendering."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

type CommandLineKind = Literal["root", "flag", "continuation", "alternative"]


@lru_cache(maxsize=1)
def _markdown_parser() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False, "breaks": False})


def _default_parse_inline_tokens(text: str) -> tuple[Token, ...]:
    if not text:
        return ()
    inline = _markdown_parser().parseInline(text)
    if not inline:
        return ()
    return tuple(inline[0].children or ())


@dataclass(slots=True, frozen=True)
class InlineTextSpan:
    text: str
    code: bool = False
    fill: str | None = None


@dataclass(slots=True, frozen=True)
class CommandLayoutLine:
    segments: tuple[InlineTextSpan, ...]
    indent_level: int
    kind: CommandLineKind


@dataclass(slots=True, frozen=True)
class CommandLayout:
    lines: tuple[CommandLayoutLine, ...]
    line_height: int
    indent_px: int
    max_line_width: int
    total_height: int
    has_guide: bool


@dataclass(slots=True, frozen=True)
class CommandPalette:
    root: str
    text: str
    param: str
    flag: str


def split_inline_text_spans(
    text: str,
    *,
    parse_inline_tokens: Callable[[str], tuple[Token, ...]] = _default_parse_inline_tokens,
) -> tuple[InlineTextSpan, ...]:
    if not text:
        return ()

    spans: list[InlineTextSpan] = []
    for token in parse_inline_tokens(text):
        if token.type == "text":
            _append_inline_text_span(spans, token.content, code=False)
            continue
        if token.type == "code_inline":
            _append_inline_text_span(spans, token.content, code=True)
            continue
        if token.type in {"softbreak", "hardbreak"}:
            _append_inline_text_span(spans, "\n", code=False)
            continue
        if token.type == "image":
            _append_inline_text_span(spans, token.content, code=False)
    return tuple(span for span in spans if span.text)


def normalize_inline_text(
    value: str,
    *,
    parse_inline_tokens: Callable[[str], tuple[Token, ...]] = _default_parse_inline_tokens,
) -> str:
    text = "".join(
        span.text for span in split_inline_text_spans(value.strip(), parse_inline_tokens=parse_inline_tokens)
    )
    return re.sub(r"\s+", " ", text).strip()


def build_command_layout(
    text: str,
    *,
    max_width: int,
    line_height: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
    parse_inline_tokens: Callable[[str], tuple[Token, ...]] = _default_parse_inline_tokens,
) -> CommandLayout:
    inline_code_variants = _split_inline_code_command_variants(
        text,
        parse_inline_tokens=parse_inline_tokens,
    )
    if inline_code_variants is not None:
        raw_lines: list[CommandLayoutLine] = []
        for index, group in enumerate(inline_code_variants):
            raw_lines.extend(
                _format_command_tokens(
                    group,
                    base_indent=0 if index == 0 else 1,
                    first_kind="root" if index == 0 else "alternative",
                    max_width=max_width,
                    indent_px=indent_px,
                    measure_text=measure_text,
                    palette=palette,
                )
            )
        max_line_width = max(
            (
                line.indent_level * indent_px
                + _command_segments_width(line.segments, measure_text)
                for line in raw_lines
            ),
            default=0,
        )
        has_guide = sum(1 for line in raw_lines if line.indent_level > 0) >= 2
        return CommandLayout(
            lines=tuple(raw_lines),
            line_height=line_height,
            indent_px=indent_px,
            max_line_width=max_line_width,
            total_height=len(raw_lines) * line_height,
            has_guide=has_guide,
        )

    normalized = normalize_inline_text(
        text,
        parse_inline_tokens=parse_inline_tokens,
    )
    if not normalized:
        return CommandLayout(
            lines=(),
            line_height=line_height,
            indent_px=indent_px,
            max_line_width=0,
            total_height=0,
            has_guide=False,
        )

    tokens = _split_command_tokens(normalized)
    raw_lines: list[CommandLayoutLine] = []
    alternative_groups = _split_command_alternatives(tokens)
    if alternative_groups is not None:
        for index, group in enumerate(alternative_groups):
            raw_lines.extend(
                _format_command_tokens(
                    group,
                    base_indent=0 if index == 0 else 1,
                    first_kind="root" if index == 0 else "alternative",
                    max_width=max_width,
                    indent_px=indent_px,
                    measure_text=measure_text,
                    palette=palette,
                )
            )
    else:
        raw_lines.extend(
            _format_command_tokens(
                tokens,
                base_indent=0,
                first_kind="root",
                max_width=max_width,
                indent_px=indent_px,
                measure_text=measure_text,
                palette=palette,
            )
        )

    max_line_width = max(
        (
            line.indent_level * indent_px
            + _command_segments_width(line.segments, measure_text)
            for line in raw_lines
        ),
        default=0,
    )
    has_guide = sum(1 for line in raw_lines if line.indent_level > 0) >= 2
    return CommandLayout(
        lines=tuple(raw_lines),
        line_height=line_height,
        indent_px=indent_px,
        max_line_width=max_line_width,
        total_height=len(raw_lines) * line_height,
        has_guide=has_guide,
    )


def _split_inline_code_command_variants(
    text: str,
    *,
    parse_inline_tokens: Callable[[str], tuple[Token, ...]],
) -> tuple[tuple[str, ...], ...] | None:
    spans = split_inline_text_spans(
        text.strip(),
        parse_inline_tokens=parse_inline_tokens,
    )
    code_values = [
        span.text.strip() for span in spans if span.code and span.text.strip()
    ]
    if len(code_values) < 2:
        return None
    if any(not span.code and span.text.strip() for span in spans):
        return None
    return (
        tuple(tokens for raw in code_values if (tokens := _split_command_tokens(raw)))
        or None
    )


def _split_command_tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    buffer: list[str] = []
    bracket_depth = 0
    angle_depth = 0
    brace_depth = 0
    paren_depth = 0
    for char in text:
        if char in {"；", ";"} and not any(
            (bracket_depth, angle_depth, brace_depth, paren_depth)
        ):
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
            tokens.append(char)
            continue
        if char.isspace() and not any(
            (bracket_depth, angle_depth, brace_depth, paren_depth)
        ):
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
            continue
        buffer.append(char)
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth > 0:
            bracket_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth > 0:
            angle_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth > 0:
            brace_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth > 0:
            paren_depth -= 1
    if buffer:
        tokens.append("".join(buffer))
    return tuple(tokens)


def _split_command_alternatives(
    tokens: Sequence[str],
) -> tuple[tuple[str, ...], ...] | None:
    if not tokens:
        return None
    groups: list[tuple[str, ...]] = []
    current: list[str] = []
    saw_separator = False
    for token in tokens:
        if token in {"/", "|"}:
            if current:
                groups.append(tuple(current))
                current = []
            saw_separator = True
            continue
        current.append(token)
    if current:
        groups.append(tuple(current))
    if not saw_separator or len(groups) < 2:
        return None
    return tuple(group for group in groups if group)


def _format_command_tokens(
    tokens: Sequence[str],
    *,
    base_indent: int,
    first_kind: CommandLineKind,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> list[CommandLayoutLine]:
    root_tokens, flag_clauses = _split_command_flag_clauses(tokens)
    lines: list[CommandLayoutLine] = []
    if root_tokens:
        annotated_root = _annotate_root_tokens(root_tokens)
        available_width = max(0, max_width - base_indent * indent_px)
        if (
            _command_role_width(annotated_root, measure_text, palette)
            <= available_width
        ):
            lines.append(
                CommandLayoutLine(
                    segments=_command_segments_for_roles(
                        annotated_root,
                        palette=palette,
                    ),
                    indent_level=base_indent,
                    kind=first_kind,
                )
            )
        else:
            lines.extend(
                _wrap_command_token_roles(
                    annotated_root,
                    base_indent=base_indent,
                    continuation_indent=base_indent + 1,
                    first_kind=first_kind,
                    continuation_kind="continuation",
                    max_width=max_width,
                    indent_px=indent_px,
                    measure_text=measure_text,
                    palette=palette,
                )
            )
    for clause in flag_clauses:
        lines.extend(
            _format_flag_clause(
                clause,
                indent_level=base_indent + 1,
                max_width=max_width,
                indent_px=indent_px,
                measure_text=measure_text,
                palette=palette,
            )
        )
    if lines:
        return lines
    return [
        CommandLayoutLine(
            segments=_command_segments_for_roles(
                ((token, "root") for token in tokens),
                palette=palette,
            ),
            indent_level=base_indent,
            kind=first_kind,
        )
    ]


def _split_command_flag_clauses(
    tokens: Sequence[str],
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    normalized_tokens: list[str] = []
    for token in tokens:
        expanded = _expand_bracketed_flag_token(token)
        if expanded is not None:
            normalized_tokens.extend(expanded)
            continue
        normalized_tokens.append(token)

    root_tokens: list[str] = []
    clauses: list[tuple[str, ...]] = []
    current_clause: list[str] | None = None
    for token in normalized_tokens:
        if _is_option_flag(token):
            if current_clause:
                clauses.append(tuple(current_clause))
            current_clause = [token]
            continue
        if current_clause is None:
            root_tokens.append(token)
            continue
        current_clause.append(token)
    if current_clause:
        clauses.append(tuple(current_clause))
    return tuple(root_tokens), tuple(clauses)


def _expand_bracketed_flag_token(token: str) -> tuple[str, ...] | None:
    if not (token.startswith("[") and token.endswith("]")):
        return None
    inner_tokens = _split_command_tokens(token[1:-1].strip())
    if not inner_tokens:
        return None
    if not inner_tokens[0].startswith("-"):
        return None
    return inner_tokens


def _is_option_flag(token: str) -> bool:
    if not token.startswith("-") or token in {"-", "--"}:
        return False
    if token.startswith("--"):
        return len(token) > 2 and token[2].isalpha()
    return len(token) > 1 and token[1].isalpha()


def _annotate_root_tokens(
    tokens: Sequence[str],
) -> tuple[tuple[str, Literal["root", "text", "param", "flag"]], ...]:
    parameter_indexes = {
        index
        for index, token in enumerate(tokens)
        if _is_placeholder_token(token)
        or (index > 0 and tokens[index - 1] == "=>")
        or (index + 1 < len(tokens) and tokens[index + 1] == "=>")
    }
    annotated: list[tuple[str, Literal["root", "text", "param", "flag"]]] = []
    for index, token in enumerate(tokens):
        if token == "=>":
            annotated.append((token, "text"))
            continue
        if index in parameter_indexes:
            annotated.append((token, "param"))
            continue
        annotated.append((token, "root"))
    return tuple(annotated)


def _annotate_value_tokens(
    tokens: Sequence[str],
) -> tuple[tuple[str, Literal["root", "text", "param", "flag"]], ...]:
    annotated: list[tuple[str, Literal["root", "text", "param", "flag"]]] = []
    for token in tokens:
        if token in {"=>", "/", "|"}:
            annotated.append((token, "text"))
            continue
        annotated.append((token, "param"))
    return tuple(annotated)


def _is_placeholder_token(token: str) -> bool:
    return bool(re.fullmatch(r"(\[[^\]]+\]|<[^>]+>)", token))


def _format_flag_clause(
    clause: Sequence[str],
    *,
    indent_level: int,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> list[CommandLayoutLine]:
    if not clause:
        return []
    flag_token = clause[0]
    value_tokens = clause[1:]
    flag_role: Literal["flag"] = "flag"
    full_tokens: tuple[
        tuple[str, Literal["root", "text", "param", "flag"]],
        ...,
    ] = ((flag_token, flag_role), *_annotate_value_tokens(value_tokens))
    available_width = max(0, max_width - indent_level * indent_px)
    if _command_role_width(full_tokens, measure_text, palette) <= available_width:
        return [
            CommandLayoutLine(
                segments=_command_segments_for_roles(full_tokens, palette=palette),
                indent_level=indent_level,
                kind="flag",
            )
        ]

    lines = [
        CommandLayoutLine(
            segments=_command_segments_for_roles(
                ((flag_token, flag_role),),
                palette=palette,
            ),
            indent_level=indent_level,
            kind="flag",
        )
    ]
    if value_tokens:
        lines.extend(
            _wrap_command_token_roles(
                _annotate_value_tokens(value_tokens),
                base_indent=indent_level + 1,
                continuation_indent=indent_level + 1,
                first_kind="continuation",
                continuation_kind="continuation",
                max_width=max_width,
                indent_px=indent_px,
                measure_text=measure_text,
                palette=palette,
            )
        )
    return lines


def _wrap_command_token_roles(
    roles: Sequence[tuple[str, Literal["root", "text", "param", "flag"]]],
    *,
    base_indent: int,
    continuation_indent: int,
    first_kind: CommandLineKind,
    continuation_kind: CommandLineKind,
    max_width: int,
    indent_px: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> list[CommandLayoutLine]:
    if not roles:
        return []

    lines: list[CommandLayoutLine] = []
    current: list[tuple[str, Literal["root", "text", "param", "flag"]]] = []
    current_indent = base_indent
    current_kind = first_kind
    index = 0
    while index < len(roles):
        token, role = roles[index]
        available_width = max(0, max_width - current_indent * indent_px)
        candidate = [*current, (token, role)]
        if current and (
            _command_role_width(candidate, measure_text, palette) <= available_width
        ):
            current = candidate
            index += 1
            continue
        if not current and (
            _command_role_width(candidate, measure_text, palette) <= available_width
        ):
            current = candidate
            index += 1
            continue

        if current:
            lines.append(
                CommandLayoutLine(
                    segments=_command_segments_for_roles(current, palette=palette),
                    indent_level=current_indent,
                    kind=current_kind,
                )
            )
            current = []
            current_indent = continuation_indent
            current_kind = continuation_kind
            continue

        split_tokens = _split_oversized_command_token(
            token,
            role=role,
            max_width=available_width,
            measure_text=measure_text,
            palette=palette,
        )
        if not split_tokens:
            current = [(token, role)]
            index += 1
            continue
        current = [(split_tokens[0], role)]
        for piece in split_tokens[1:]:
            lines.append(
                CommandLayoutLine(
                    segments=_command_segments_for_roles(current, palette=palette),
                    indent_level=current_indent,
                    kind=current_kind,
                )
            )
            current = [(piece, role)]
            current_indent = continuation_indent
            current_kind = continuation_kind
        index += 1

    if current:
        lines.append(
            CommandLayoutLine(
                segments=_command_segments_for_roles(current, palette=palette),
                indent_level=current_indent,
                kind=current_kind,
            )
        )
    return lines


def _split_oversized_command_token(
    token: str,
    *,
    role: Literal["root", "text", "param", "flag"],
    max_width: int,
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> tuple[str, ...]:
    if max_width <= 0:
        return (token,)

    chunks: list[str] = []
    current = ""
    for unit in _split_command_token_units(token):
        candidate = current + unit
        if (
            current
            and _command_role_width(((candidate, role),), measure_text, palette)
            > max_width
        ):
            chunks.append(current)
            current = ""
        if _command_role_width(((unit, role),), measure_text, palette) <= max_width:
            current += unit
            continue
        for char in unit:
            candidate = current + char
            if (
                current
                and _command_role_width(((candidate, role),), measure_text, palette)
                > max_width
            ):
                chunks.append(current)
                current = char
                continue
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunk for chunk in chunks if chunk) or (token,)


def _split_command_token_units(token: str) -> tuple[str, ...]:
    parts = re.split(r"(=>|\||/|:|,|_)", token)
    units: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in {"=>", "|", "/", ":", ",", "_"} and units:
            units[-1] += part
            continue
        units.append(part)
    return tuple(units)


def _command_role_width(
    roles: Sequence[tuple[str, Literal["root", "text", "param", "flag"]]],
    measure_text: Callable[[str], int],
    palette: CommandPalette,
) -> int:
    return _command_segments_width(
        _command_segments_for_roles(roles, palette=palette),
        measure_text,
    )


def _command_segments_for_roles(
    roles: Iterable[tuple[str, Literal["root", "text", "param", "flag"]]],
    *,
    palette: CommandPalette,
) -> tuple[InlineTextSpan, ...]:
    segments: list[InlineTextSpan] = []
    ordered = list(roles)
    for index, (token, role) in enumerate(ordered):
        fill = {
            "root": palette.root,
            "text": palette.text,
            "param": palette.param,
            "flag": palette.flag,
        }[role]
        _append_inline_command_segment(segments, token, fill=fill)
        if index < len(ordered) - 1:
            _append_inline_command_segment(segments, " ", fill=palette.text)
    return tuple(segment for segment in segments if segment.text)


def _append_inline_command_segment(
    spans: list[InlineTextSpan],
    text: str,
    *,
    fill: str,
) -> None:
    if not text:
        return
    if spans and spans[-1].code is False and spans[-1].fill == fill:
        previous = spans[-1]
        spans[-1] = InlineTextSpan(previous.text + text, code=False, fill=fill)
        return
    spans.append(InlineTextSpan(text, code=False, fill=fill))


def _command_segments_width(
    segments: Sequence[InlineTextSpan],
    measure_text: Callable[[str], int],
) -> int:
    return sum(measure_text(span.text) for span in segments if span.text)


def _append_inline_text_span(
    spans: list[InlineTextSpan],
    text: str,
    *,
    code: bool,
) -> None:
    if not text:
        return
    if spans and spans[-1].code is code:
        previous = spans[-1]
        spans[-1] = InlineTextSpan(previous.text + text, code=code)
        return
    spans.append(InlineTextSpan(text, code=code))
