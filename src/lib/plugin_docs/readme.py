"""Markdown README parser helpers for plugin docs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, cast

from markdown_it.token import Token

from src.database.core.consts import Permission

from .models import DocsDemoTurn, FeatureDoc, MarkdownSection

type NormalizeHeading = Callable[[str], str]
type RenderInlineMarkdown = Callable[[Sequence[Token]], str]
type ParseInlineTokens = Callable[[str], tuple[Token, ...]]
type DocAssetPrefix = Callable[[Path], str]


def extract_title(
    tokens: Sequence[Token],
    *,
    normalize_heading: NormalizeHeading,
    render_inline_markdown: RenderInlineMarkdown,
) -> str:
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open"
            and token.tag == "h1"
            and index + 1 < len(tokens)
        ):
            return normalize_heading(
                render_inline_markdown(tokens[index + 1].children or ())
            )
    return ""


def extract_heading_sections(
    tokens: Sequence[Token],
    *,
    tag: str,
    normalize_heading: NormalizeHeading,
    render_inline_markdown: RenderInlineMarkdown,
) -> tuple[MarkdownSection, ...]:
    sections: list[MarkdownSection] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type != "heading_open" or token.tag != tag or index + 1 >= len(tokens):
            index += 1
            continue
        heading = tokens[index + 1]
        body_start = min(index + 3, len(tokens))
        body_end = body_start
        while body_end < len(tokens):
            next_token = tokens[body_end]
            if next_token.type == "heading_open" and next_token.tag == tag:
                break
            body_end += 1
        sections.append(
            MarkdownSection(
                title=normalize_heading(render_inline_markdown(heading.children or ())),
                heading=heading,
                tokens=tuple(tokens[body_start:body_end]),
            )
        )
        index = body_end
    return tuple(sections)


def normalize_heading(raw: str) -> str:
    return raw.strip().strip("`")


def render_inline_markdown(tokens: Sequence[Token]) -> str:
    fragments: list[str] = []
    for token in tokens:
        if token.type == "inline":
            fragments.append(render_inline_markdown(token.children or ()))
            continue
        if token.type == "text":
            fragments.append(token.content)
            continue
        if token.type == "code_inline":
            fragments.append(f"`{token.content}`")
            continue
        if token.type in {"softbreak", "hardbreak"}:
            fragments.append("\n")
            continue
        if token.type == "image":
            fragments.append(token.content)
    return "".join(fragments)


def render_markdown_blocks(tokens: Sequence[Token]) -> str:
    blocks: list[str] = []
    index = 0
    ordered_stack: list[int | None] = []
    while index < len(tokens):
        token = tokens[index]
        if token.type == "ordered_list_open":
            ordered_stack.append(int(token.attrGet("start") or "1"))
            index += 1
            continue
        if token.type == "bullet_list_open":
            ordered_stack.append(None)
            index += 1
            continue
        if token.type in {"ordered_list_close", "bullet_list_close"}:
            if ordered_stack:
                ordered_stack.pop()
            index += 1
            continue
        if token.type == "paragraph_open":
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            text = (
                render_inline_markdown(inline.children or ()).strip()
                if inline is not None
                else ""
            )
            if text:
                blocks.append(text)
            index += 3
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
            item_payload = render_markdown_blocks(
                tokens[index + 1 : cursor - 1]
            ).strip()
            if item_payload:
                marker = "-"
                if ordered_stack and ordered_stack[-1] is not None:
                    marker = f"{ordered_stack[-1]}."
                    ordered_stack[-1] += 1
                item_lines = item_payload.splitlines()
                blocks.append(f"{marker} {item_lines[0]}".rstrip())
                blocks.extend(f"  {line}" for line in item_lines[1:])
            index = cursor
            continue
        if token.type == "fence":
            info = token.info.strip()
            opening = f"```{info}" if info else "```"
            content = token.content.rstrip("\n")
            blocks.append(f"{opening}\n{content}\n```".strip())
            index += 1
            continue
        if token.type == "heading_open":
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            text = (
                render_inline_markdown(inline.children or ()).strip()
                if inline is not None
                else ""
            )
            if text:
                if token.tag == "h3":
                    blocks.append(f"### {text}")
                elif token.tag == "h4":
                    blocks.append(f"#### {text}")
                else:
                    blocks.append(text)
            index += 3
            continue
        index += 1
    return "\n\n".join(blocks).strip()


def extract_list_item_tokens(tokens: Sequence[Token]) -> tuple[tuple[Token, ...], ...]:
    items: list[tuple[Token, ...]] = []
    index = 0
    while index < len(tokens):
        if tokens[index].type != "list_item_open":
            index += 1
            continue
        depth = 1
        cursor = index + 1
        while cursor < len(tokens) and depth > 0:
            if tokens[cursor].type == "list_item_open":
                depth += 1
            elif tokens[cursor].type == "list_item_close":
                depth -= 1
            cursor += 1
        items.append(tuple(tokens[index + 1 : max(index + 1, cursor - 1)]))
        index = cursor
    return tuple(items)


def parse_meta_block_tokens(
    tokens: Sequence[Token],
    *,
    parse_inline_tokens: ParseInlineTokens,
) -> dict[str, str]:
    meta: dict[str, str] = {}
    for item_tokens in extract_list_item_tokens(tokens):
        payload = render_markdown_blocks(item_tokens).replace("\n", " ").strip()
        key, value = split_key_value(payload)
        if not key:
            continue
        meta[key] = strip_wrapping_backticks(
            value, parse_inline_tokens=parse_inline_tokens
        )
    return meta


def split_key_value(value: str) -> tuple[str, str]:
    for separator in (":", "："):
        if separator not in value:
            continue
        key, payload = value.split(separator, 1)
        return key.strip(), payload.strip()
    return "", ""


def strip_wrapping_backticks(
    value: str,
    *,
    parse_inline_tokens: ParseInlineTokens,
) -> str:
    inline_tokens = parse_inline_tokens(value)
    if len(inline_tokens) == 1 and inline_tokens[0].type == "code_inline":
        return inline_tokens[0].content.strip()
    return value


def parse_permission(value: str) -> Permission:
    normalized = value.strip()
    if not normalized:
        return Permission.NORMAL
    try:
        return Permission[normalized]
    except KeyError:
        pass
    for permission in Permission:
        if normalized == permission.label:
            return permission
    try:
        return Permission(int(normalized))
    except ValueError:
        return Permission.NORMAL


def parse_bool_meta(value: str, *, default: bool = False) -> bool:
    normalized = value.strip().strip("`").lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "y", "on", "是", "真"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否", "假"}:
        return False
    return default


def parse_int_meta(value: str, *, default: int = 1000) -> int:
    normalized = value.strip().strip("`")
    if not normalized:
        return default
    try:
        return int(normalized)
    except ValueError:
        return default


def parse_feature_index_tokens(tokens: Sequence[Token]) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for item_tokens in extract_list_item_tokens(tokens):
        inline = next((token for token in item_tokens if token.type == "inline"), None)
        if inline is None:
            continue
        children = tuple(inline.children or ())
        if not children or children[0].type != "code_inline":
            continue
        slug = children[0].content.strip()
        title, summary = split_key_value(render_inline_markdown(children[1:]).strip())
        if not slug or not title:
            continue
        entries[slug] = (title, summary)
    return entries


def parse_feature_details_tokens(
    tokens: Sequence[Token],
    source_path: Path,
    *,
    doc_asset_prefix: DocAssetPrefix,
    parse_inline_tokens: ParseInlineTokens,
) -> dict[str, FeatureDoc]:
    features: dict[str, FeatureDoc] = {}
    for section in extract_heading_sections(
        tokens,
        tag="h3",
        normalize_heading=normalize_heading,
        render_inline_markdown=render_inline_markdown,
    ):
        slug, title = parse_feature_heading(section.heading)
        if not slug or not title:
            continue
        meta_tokens, body_tokens = split_tokens_before_heading(section.tokens, tag="h4")
        meta = parse_meta_block_tokens(
            meta_tokens,
            parse_inline_tokens=parse_inline_tokens,
        )
        subsections = {
            subsection.title: subsection.tokens
            for subsection in extract_heading_sections(
                body_tokens,
                tag="h4",
                normalize_heading=normalize_heading,
                render_inline_markdown=render_inline_markdown,
            )
        }
        flow_notes, demo_turns = parse_flow_section_tokens(
            subsections.get("完整流程", ())
        )
        demo_filename = meta.get("Demo", f"{doc_asset_prefix(source_path)}-{slug}.png")
        demo_filename = demo_filename.strip("`")
        features[slug] = FeatureDoc(
            slug=slug,
            title=title.strip(),
            summary=meta.get("摘要", "").strip() or title.strip(),
            aliases=split_csv(meta.get("别名", "")),
            trigger=meta.get("指令", "").strip() or meta.get("触发", "").strip(),
            permission=parse_permission(meta.get("权限", "")),
            demo_filename=demo_filename,
            hero=parse_bool_meta(
                meta.get("Hero", "").strip() or meta.get("主推", "").strip()
            ),
            priority=parse_int_meta(
                meta.get("Priority", "").strip() or meta.get("优先级", "").strip()
            ),
            advanced=parse_bool_meta(
                meta.get("Advanced", "").strip() or meta.get("高级", "").strip()
            ),
            overview=render_markdown_blocks(subsections.get("说明", ())).strip(),
            preconditions=render_markdown_blocks(
                subsections.get("前置条件", ())
            ).strip(),
            flow_notes=flow_notes.strip(),
            failures=render_markdown_blocks(subsections.get("失败情况", ())).strip(),
            demo_turns=demo_turns,
        )
    return features


def split_tokens_before_heading(
    tokens: Sequence[Token],
    *,
    tag: str,
) -> tuple[tuple[Token, ...], tuple[Token, ...]]:
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == tag:
            return tuple(tokens[:index]), tuple(tokens[index:])
    return tuple(tokens), ()


def parse_feature_heading(heading: Token) -> tuple[str, str]:
    children = tuple(heading.children or ())
    if children and children[0].type == "code_inline":
        slug = children[0].content.strip()
        title = render_inline_markdown(children[1:]).strip()
        if slug and title:
            return slug, title
    rendered = normalize_heading(render_inline_markdown(children))
    parts = rendered.split(maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip("`").strip(), parts[1].strip()


def parse_flow_section_tokens(
    tokens: Sequence[Token],
) -> tuple[str, tuple[DocsDemoTurn, ...]]:
    demo_turns: list[DocsDemoTurn] = []
    cleaned: list[str] = []
    for token in tokens:
        if token.type == "fence" and token.info.strip() == "demo":
            demo_turns.extend(parse_demo_turns(token.content))
            continue
        if token.type != "inline":
            continue
        text = render_inline_markdown(token.children or ()).strip()
        if text:
            cleaned.append(text)
    return "\n".join(cleaned).strip(), tuple(demo_turns)


def parse_demo_turns(content: str) -> list[DocsDemoTurn]:
    demo_turns: list[DocsDemoTurn] = []
    current_section = ""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped:
            speaker, text = stripped.split(":", 1)
            normalized = speaker.strip().upper()
            if normalized == "SECTION":
                current_section = text.strip()
                continue
        if ":" in stripped:
            speaker, text = stripped.split(":", 1)
            normalized = speaker.strip().upper()
            if normalized in {"USER", "BOT", "SYSTEM"}:
                demo_turns.append(
                    DocsDemoTurn(
                        cast(Literal["USER", "BOT", "SYSTEM"], normalized),
                        text.strip(),
                        current_section,
                    )
                )
                continue
        if demo_turns:
            previous = demo_turns[-1]
            demo_turns[-1] = DocsDemoTurn(
                previous.speaker,
                f"{previous.text}\n{stripped}",
                previous.section,
            )
    return demo_turns


def merge_features(
    feature_index: dict[str, tuple[str, str]],
    details: dict[str, FeatureDoc],
) -> tuple[FeatureDoc, ...]:
    ordered: list[FeatureDoc] = []
    seen: set[str] = set()
    for slug, (title, summary) in feature_index.items():
        detail = details.get(slug)
        if detail is None:
            ordered.append(
                FeatureDoc(
                    slug=slug,
                    title=title,
                    summary=summary,
                    aliases=(),
                    trigger="",
                    permission=Permission.NORMAL,
                    demo_filename="",
                    hero=False,
                    priority=1000,
                    advanced=False,
                    overview="",
                    preconditions="",
                    flow_notes="",
                    failures="",
                    demo_turns=(),
                )
            )
        else:
            ordered.append(
                FeatureDoc(
                    slug=detail.slug,
                    title=detail.title or title,
                    summary=detail.summary or summary,
                    aliases=detail.aliases,
                    trigger=detail.trigger,
                    permission=detail.permission,
                    demo_filename=detail.demo_filename,
                    hero=detail.hero,
                    priority=detail.priority,
                    advanced=detail.advanced,
                    overview=detail.overview,
                    preconditions=detail.preconditions,
                    flow_notes=detail.flow_notes,
                    failures=detail.failures,
                    demo_turns=detail.demo_turns,
                )
            )
        seen.add(slug)
    for slug, feature in details.items():
        if slug not in seen:
            ordered.append(feature)
    return tuple(ordered)


def split_csv(value: str) -> tuple[str, ...]:
    items = [part.strip().strip("`") for part in value.split(",")]
    return tuple(item for item in items if item)
