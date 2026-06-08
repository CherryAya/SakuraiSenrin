"""Plugin docs metadata, README parsing, and demo rendering helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Literal, TypedDict, cast

from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from PIL import Image, ImageDraw, ImageFont

from src.database.core.consts import Permission
from src.lib.consts import MAPLE_FONT_PATH
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .consts import TriggerType


@dataclass(slots=True, frozen=True)
class DocsRenderContext:
    locale: LocaleCode
    feature_query: str | None = None
    include_demo: bool = True


type DocsResult = Message | Awaitable[Message] | str | Awaitable[str]
type DocsProvider = Callable[..., DocsResult]


class DocsMeta(TypedDict):
    visible: bool
    category: str
    order: int
    provider: DocsProvider
    source: str
    hidden: bool


@dataclass(slots=True, frozen=True)
class DocsDemoTurn:
    speaker: Literal["USER", "BOT", "SYSTEM"]
    text: str


@dataclass(slots=True, frozen=True)
class FeatureDoc:
    slug: str
    title: str
    summary: str
    aliases: tuple[str, ...]
    trigger: str
    demo_filename: str
    overview: str
    preconditions: str
    flow_notes: str
    failures: str
    demo_turns: tuple[DocsDemoTurn, ...]

    @property
    def search_tokens(self) -> set[str]:
        return {
            self.slug.lower(),
            self.title.lower(),
            *(alias.lower() for alias in self.aliases),
        }


@dataclass(slots=True, frozen=True)
class PluginDocBundle:
    title: str
    description: str
    summary: str
    trigger: str
    permission: str
    author: str
    version: str
    index: tuple[FeatureDoc, ...]
    source_path: Path


@dataclass(slots=True, frozen=True)
class FeatureMatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    feature: FeatureDoc | None = None
    candidates: tuple[FeatureDoc, ...] = ()


def create_docs_meta(
    provider: DocsProvider,
    *,
    visible: bool,
    category: str,
    order: int,
    source: str | Path | None = None,
    hidden: bool = False,
) -> DocsMeta:
    docs: DocsMeta = {
        "visible": visible,
        "category": category,
        "order": order,
        "provider": provider,
        "source": str(source) if source is not None else "",
        "hidden": hidden,
    }
    return docs


def build_static_docs(
    *,
    name: str | None = None,
    description: str | None = None,
    content: str | None = None,
    name_key: MessageKey | None = None,
    description_key: MessageKey | None = None,
    content_key: MessageKey | None = None,
    trigger: TriggerType,
    permission: Permission,
    locale: LocaleCode = "zh-CN",
) -> Message:
    body = (
        tr(locale, content_key).strip()
        if content_key is not None
        else (content or "").strip()
    ) or tr(locale, "docs.default.empty")
    desc = (
        tr(locale, description_key).strip()
        if description_key is not None
        else (description or "").strip()
    ) or tr(locale, "docs.default.no_description")
    title = tr(locale, name_key) if name_key is not None else (name or "")
    return Message(
        (
            f"===== {title} =====\n"
            f"{tr(locale, 'docs.default.trigger')}: {trigger}\n"
            f"{tr(locale, 'docs.default.permission')}: {permission}\n\n"
            f"{desc}\n\n"
            f"{tr(locale, 'docs.default.usage')}:\n"
            f"{body}"
        ).strip()
    )


def build_readme_docs(
    *,
    source: str | Path,
    name: str,
    description: str,
    trigger: TriggerType,
    permission: Permission,
    ctx: DocsRenderContext | None = None,
) -> Message:
    locale = ctx.locale if ctx is not None else "zh-CN"
    bundle = load_plugin_doc_bundle(
        source=source,
        default_name=name,
        default_description=description,
        trigger=trigger,
        permission=permission,
    )
    if ctx is not None and ctx.feature_query:
        match = match_feature(bundle.index, ctx.feature_query)
        if match.status == "matched" and match.feature is not None:
            return render_feature_message(
                bundle,
                match.feature,
                locale=locale,
                include_demo=ctx.include_demo,
            )
        if match.status == "ambiguous":
            return Message(
                "\n".join(
                    [
                        f"子功能查询存在歧义: {ctx.feature_query}",
                        "请使用更精确的子功能名。",
                        "",
                        *(
                            f"- {feature.title} ({feature.slug})"
                            for feature in match.candidates
                        ),
                    ]
                ).strip()
            )
        return Message(f"未找到子功能文档: {ctx.feature_query}".strip())
    return render_overview_message(bundle, locale=locale)


def load_plugin_doc_bundle(
    *,
    source: str | Path,
    default_name: str,
    default_description: str,
    trigger: TriggerType,
    permission: Permission,
) -> PluginDocBundle:
    source_path = Path(source).resolve()
    raw_text = source_path.read_text(encoding="utf-8")
    title = _extract_title(raw_text) or default_name
    sections = _split_sections(raw_text, level=2)
    summary = sections.get("概览", "").strip() or default_description
    meta = _parse_meta_block(sections.get("权限与触发", ""))
    feature_index = _parse_feature_index(sections.get("子功能目录", ""))
    details = _parse_feature_details(sections.get("子功能详情", ""), source_path)
    features = _merge_features(feature_index, details)
    author, version = _resolve_doc_signature(source_path)
    return PluginDocBundle(
        title=title,
        description=default_description,
        summary=summary,
        trigger=meta.get("触发方式", trigger.label),
        permission=meta.get("权限", permission.label),
        author=author,
        version=version,
        index=features,
        source_path=source_path,
    )


def match_feature(
    features: Sequence[FeatureDoc],
    query: str,
) -> FeatureMatchResult:
    normalized = query.strip().lower()
    if not normalized:
        return FeatureMatchResult(status="not_found")

    exact: list[FeatureDoc] = []
    fuzzy: list[FeatureDoc] = []
    for feature in features:
        tokens = feature.search_tokens
        if normalized in tokens:
            exact.append(feature)
            continue
        if normalized in feature.slug.lower() or normalized in feature.title.lower():
            fuzzy.append(feature)
            continue
        if any(normalized in alias.lower() for alias in feature.aliases):
            fuzzy.append(feature)

    if len(exact) == 1:
        return FeatureMatchResult(status="matched", feature=exact[0])
    if len(exact) > 1:
        return FeatureMatchResult(status="ambiguous", candidates=tuple(exact))
    if len(fuzzy) == 1:
        return FeatureMatchResult(status="matched", feature=fuzzy[0])
    if len(fuzzy) > 1:
        unique = {feature.slug: feature for feature in fuzzy}
        return FeatureMatchResult(
            status="ambiguous",
            candidates=tuple(unique.values()),
        )
    return FeatureMatchResult(status="not_found")


def render_overview_message(
    bundle: PluginDocBundle,
    *,
    locale: LocaleCode,
) -> Message:
    lines = [
        f"===== {bundle.title} =====",
        f"{tr(locale, 'docs.default.trigger')}: {bundle.trigger}",
        f"{tr(locale, 'docs.default.permission')}: {bundle.permission}",
        "",
        bundle.summary.strip(),
    ]
    if bundle.index:
        lines.extend(
            [
                "",
                "子功能目录",
                *(
                    f"- {feature.title} ({feature.slug}): {feature.summary}"
                    for feature in bundle.index
                ),
                "",
                "发送 #help <插件名> <子功能名> 查看完整流程与 demo。",
            ]
        )
    return Message("\n".join(line for line in lines if line is not None).strip())


def render_feature_message(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    include_demo: bool = True,
) -> Message:
    lines = [
        f"===== {bundle.title} / {feature.title} =====",
        f"{tr(locale, 'docs.default.trigger')}: {feature.trigger}",
    ]
    if feature.aliases:
        lines.append(f"别名: {' / '.join(feature.aliases)}")
    lines.extend(
        [
            "",
            feature.summary,
            "",
            "说明",
            feature.overview or tr(locale, "docs.default.empty"),
            "",
            "前置条件",
            feature.preconditions or tr(locale, "docs.default.empty"),
            "",
            "完整流程",
            feature.flow_notes or "见下方 demo 图。",
            "",
            "失败情况",
            feature.failures or tr(locale, "docs.default.empty"),
        ]
    )
    message = Message("\n".join(lines).strip())
    if not include_demo:
        return message
    demo_bytes = load_demo_bytes(bundle, feature)
    if demo_bytes is None:
        return message
    return message + MessageSegment.image(demo_bytes)


def load_demo_bytes(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes | None:
    demo_path = bundle.source_path.parent / "demos" / feature.demo_filename
    if demo_path.exists():
        return demo_path.read_bytes()
    if not feature.demo_turns:
        return None
    return render_demo_png(bundle, feature)


def render_demo_png(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes:
    return DemoImageRenderer().render(
        plugin_title=bundle.title,
        feature_title=feature.title,
        feature_trigger=feature.trigger,
        plugin_version=bundle.version,
        plugin_author=bundle.author,
        turns=feature.demo_turns,
    )


def _extract_title(text: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _split_sections(text: str, *, level: int) -> dict[str, str]:
    pattern = re.compile(rf"^{'#' * level}\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = _normalize_heading(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections


def _normalize_heading(raw: str) -> str:
    return raw.strip().strip("`")


def _parse_meta_block(block: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.startswith("- "):
            continue
        payload = line[2:].strip()
        if ":" not in payload:
            continue
        key, value = payload.split(":", 1)
        meta[key.strip()] = value.strip().strip("`")
    return meta


def _parse_feature_index(block: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        match = re.match(r"-\s+`([^`]+)`\s+([^:]+):\s*(.+)$", stripped)
        if not match:
            continue
        slug, title, summary = match.groups()
        entries[slug.strip()] = (title.strip(), summary.strip())
    return entries


def _parse_feature_details(block: str, source_path: Path) -> dict[str, FeatureDoc]:
    features: dict[str, FeatureDoc] = {}
    for raw_heading, body in _split_sections(block, level=3).items():
        slug, title = _parse_feature_heading(raw_heading)
        if not slug or not title:
            continue
        meta_lines: list[str] = []
        body_lines = body.splitlines()
        index = 0
        while index < len(body_lines):
            line = body_lines[index].strip()
            if line.startswith("#### "):
                break
            if line.startswith("- "):
                meta_lines.append(line)
            index += 1
        meta = _parse_meta_block("\n".join(meta_lines))
        subsections = _split_sections("\n".join(body_lines[index:]), level=4)
        flow_notes, demo_turns = _parse_flow_section(subsections.get("完整流程", ""))
        demo_filename = meta.get(
            "Demo",
            f"{source_path.parent.parent.name}-{slug}.png",
        ).strip("`")
        features[slug] = FeatureDoc(
            slug=slug,
            title=title.strip(),
            summary=meta.get("摘要", "").strip() or title.strip(),
            aliases=_split_csv(meta.get("别名", "")),
            trigger=meta.get("指令", "").strip() or meta.get("触发", "").strip(),
            demo_filename=demo_filename,
            overview=subsections.get("说明", "").strip(),
            preconditions=subsections.get("前置条件", "").strip(),
            flow_notes=flow_notes.strip(),
            failures=subsections.get("失败情况", "").strip(),
            demo_turns=demo_turns,
        )
    return features


def _parse_feature_heading(raw_heading: str) -> tuple[str, str]:
    heading = raw_heading.strip()
    match = re.match(r"`([^`]+)`\s+(.+)$", heading)
    if match:
        slug, title = match.groups()
        return slug.strip(), title.strip()
    parts = heading.split(maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip("`").strip(), parts[1].strip()


def _merge_features(
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
                    demo_filename="",
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
                    demo_filename=detail.demo_filename,
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


def _parse_flow_section(block: str) -> tuple[str, tuple[DocsDemoTurn, ...]]:
    demo_turns: list[DocsDemoTurn] = []
    cleaned = block
    fence_match = re.search(r"```demo\s*(.*?)```", block, re.DOTALL)
    if fence_match:
        cleaned = (block[: fence_match.start()] + block[fence_match.end() :]).strip()
        for line in fence_match.group(1).splitlines():
            stripped = line.strip()
            if not stripped or ":" not in stripped:
                continue
            speaker, text = stripped.split(":", 1)
            normalized = speaker.strip().upper()
            if normalized not in {"USER", "BOT", "SYSTEM"}:
                continue
            demo_turns.append(
                DocsDemoTurn(
                    cast(Literal["USER", "BOT", "SYSTEM"], normalized),
                    text.strip(),
                )
            )
    return cleaned.strip(), tuple(demo_turns)


def _split_csv(value: str) -> tuple[str, ...]:
    items = [part.strip().strip("`") for part in value.split(",")]
    return tuple(item for item in items if item)


def _resolve_doc_signature(source_path: Path) -> tuple[str, str]:
    module_path = _resolve_doc_owner_module_path(source_path)
    if module_path is None or not module_path.exists():
        return "Unknown", "0.0.0"

    raw_text = module_path.read_text(encoding="utf-8")
    author = _extract_metadata_field(raw_text, "author") or "Unknown"
    version = _extract_metadata_field(raw_text, "version") or "0.0.0"
    return author, version


def _resolve_doc_owner_module_path(source_path: Path) -> Path | None:
    src_root = next(
        (parent for parent in source_path.parents if parent.name == "src"),
        None,
    )
    if src_root is None:
        return None

    try:
        rel_path = source_path.relative_to(src_root)
    except ValueError:
        return None

    parts = rel_path.parts
    if len(parts) < 4 or parts[-1] != "README.MD":
        return None

    namespace = parts[0]
    if namespace not in {"plugins", "hooks"}:
        return None

    repo_root = src_root.parent
    if parts[1] == "docs":
        return repo_root / "src" / namespace / f"{parts[2]}.py"

    owner = parts[1]
    if len(parts) == 4 and parts[2] == "docs":
        return repo_root / "src" / namespace / owner / "__init__.py"
    if len(parts) == 5 and parts[2] == "docs":
        return repo_root / "src" / namespace / owner / f"{parts[3]}.py"
    return None


def _extract_metadata_field(raw_text: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}":\s*"([^"]+)"', raw_text)
    return match.group(1).strip() if match else ""


class DemoImageRenderer:
    """Render a cute but serious pseudo-chat demo card from a docs script."""

    WIDTH = 1280
    OUTER_MARGIN = 32
    SHELL_RADIUS = 40
    HEADER_HEIGHT = 236
    FOOTER_HEIGHT = 132
    CONVERSATION_SIDE_PADDING = 56
    BUBBLE_RADIUS = 30
    BUBBLE_PADDING_X = 32
    BUBBLE_PADDING_Y = 24
    CONTENT_WIDTH = 760
    TURN_GAP = 28
    SECTION_GAP = 32
    AVATAR_SIZE = 68
    SYSTEM_CARD_PADDING = 28

    def __init__(self) -> None:
        try:
            self.eyebrow_font = ImageFont.truetype(MAPLE_FONT_PATH, 18)
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 40)
            self.feature_font = ImageFont.truetype(MAPLE_FONT_PATH, 30)
            self.body_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 18)
            self.footer_label_font = ImageFont.truetype(MAPLE_FONT_PATH, 16)
            self.footer_value_font = ImageFont.truetype(MAPLE_FONT_PATH, 20)
        except OSError:
            self.eyebrow_font = ImageFont.load_default()
            self.title_font = ImageFont.load_default()
            self.feature_font = ImageFont.load_default()
            self.body_font = ImageFont.load_default()
            self.meta_font = ImageFont.load_default()
            self.footer_label_font = ImageFont.load_default()
            self.footer_value_font = ImageFont.load_default()

    def render(
        self,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        plugin_version: str,
        plugin_author: str,
        turns: Sequence[DocsDemoTurn],
    ) -> bytes:
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = sum(
            spec.height for spec in turn_specs
        ) + self.TURN_GAP * max(
            len(turn_specs) - 1,
            0,
        )
        conversation_top = self.HEADER_HEIGHT + self.SECTION_GAP
        footer_top = conversation_top + conversation_height + self.SECTION_GAP + 32
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        image = Image.new("RGB", (self.WIDTH, height), "#fffaf6")
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN,
                self.OUTER_MARGIN,
                self.WIDTH - self.OUTER_MARGIN,
                height - self.OUTER_MARGIN,
            ),
            radius=self.SHELL_RADIUS,
            fill="#fffdfb",
            outline="#f3d8d1",
            width=3,
        )
        self._draw_header(
            draw,
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_trigger=feature_trigger,
            turn_count=len(turns),
        )
        self._draw_conversation_panel(
            draw,
            top=conversation_top - 18,
            bottom=footer_top - 18,
        )

        y = conversation_top
        for spec in turn_specs:
            self._draw_turn(draw, spec, y)
            y += spec.height + self.TURN_GAP

        self._draw_footer(
            draw,
            top=footer_top,
            plugin_title=plugin_title,
            plugin_version=plugin_version,
            plugin_author=plugin_author,
        )

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _paint_background(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        top_color = (255, 247, 243)
        bottom_color = (245, 250, 255)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            color = tuple(
                int(top_color[index] * (1 - ratio) + bottom_color[index] * ratio)
                for index in range(3)
            )
            draw.line((0, y, width, y), fill=color)

        decorations = [
            ((84, 96, 252, 252), "#ffd9ce"),
            ((1036, 84, 1206, 242), "#dff3ff"),
            ((108, height - 280, 300, height - 120), "#ffe9d8"),
            ((930, height - 240, 1160, height - 60), "#eef4ff"),
        ]
        for bounds, fill in decorations:
            draw.ellipse(bounds, fill=fill)

        self._draw_sparkle(draw, 188, 148, "#ffffff")
        self._draw_sparkle(draw, 1128, 132, "#ffffff")
        self._draw_sparkle(draw, 1030, height - 132, "#ffffff")

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        plugin_title: str,
        feature_title: str,
        feature_trigger: str,
        turn_count: int,
    ) -> None:
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN + 28,
                self.OUTER_MARGIN + 24,
                self.WIDTH - self.OUTER_MARGIN - 28,
                self.OUTER_MARGIN + self.HEADER_HEIGHT - 20,
            ),
            radius=34,
            fill="#fff1ec",
            outline="#ffd8c9",
            width=2,
        )
        draw.rounded_rectangle((96, 86, 164, 154), radius=22, fill="#ffbfa9")
        draw.rounded_rectangle((1110, 74, 1178, 142), radius=22, fill="#b9e3ff")
        self._draw_avatar_badge(draw, 130, 120, "凛", "#ff9a7d")
        self._draw_avatar_badge(draw, 1144, 108, "Q", "#79bfff")
        self._draw_chip(
            draw,
            x=136,
            y=56,
            text="PLUGIN DEMO",
            fill="#ffffff",
            text_fill="#a85b4d",
            font=self.eyebrow_font,
        )
        self._draw_chip(
            draw,
            x=950,
            y=56,
            text=f"{turn_count} STEP{'S' if turn_count != 1 else ''}",
            fill="#ffffff",
            text_fill="#4d7598",
            font=self.eyebrow_font,
        )
        draw.text((136, 104), plugin_title, font=self.title_font, fill="#56352d")
        draw.text((136, 158), feature_title, font=self.feature_font, fill="#7b4a3f")
        if feature_trigger.strip():
            self._draw_chip(
                draw,
                x=136,
                y=198,
                text=f"指令示例: {feature_trigger}",
                fill="#fffaf6",
                text_fill="#805a52",
                font=self.meta_font,
                min_width=320,
            )
        self._draw_chip(
            draw,
            x=920,
            y=198,
            text="Serious Copy, Cute Layout",
            fill="#fffaf6",
            text_fill="#5d7088",
            font=self.meta_font,
        )

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
            radius=34,
            fill="#ffffff",
            outline="#eee1db",
            width=2,
        )
        draw.rounded_rectangle(
            (
                self.OUTER_MARGIN + 52,
                top + 24,
                self.WIDTH - self.OUTER_MARGIN - 52,
                top + 58,
            ),
            radius=16,
            fill="#fff5ef",
        )
        draw.text(
            (self.OUTER_MARGIN + 74, top + 30),
            "Demo Conversation",
            font=self.meta_font,
            fill="#936a60",
        )

    def _measure_turn(self, turn: DocsDemoTurn) -> "_TurnSpec":
        if turn.speaker == "SYSTEM":
            lines = self._wrap_text(
                turn.text,
                max_width=self.WIDTH - self.OUTER_MARGIN * 2 - 240,
            )
            text_height = self._line_block_height(lines, self.body_font)
            return _TurnSpec(
                turn=turn,
                lines=lines,
                width=self.WIDTH - self.OUTER_MARGIN * 2 - 180,
                height=text_height + self.SYSTEM_CARD_PADDING * 2,
            )

        lines = self._wrap_text(turn.text, max_width=self.CONTENT_WIDTH)
        text_height = self._line_block_height(lines, self.body_font)
        bubble_height = text_height + self.BUBBLE_PADDING_Y * 2
        return _TurnSpec(
            turn=turn,
            lines=lines,
            width=min(self.CONTENT_WIDTH + self.BUBBLE_PADDING_X * 2, self.WIDTH - 320),
            height=max(bubble_height, self.AVATAR_SIZE),
        )

    def _draw_turn(
        self,
        draw: ImageDraw.ImageDraw,
        spec: "_TurnSpec",
        top: int,
    ) -> None:
        if spec.turn.speaker == "SYSTEM":
            left = 140
            right = self.WIDTH - 140
            draw.rounded_rectangle(
                (left, top, right, top + spec.height),
                radius=24,
                fill="#fff3db",
                outline="#f0dcc3",
                width=2,
            )
            self._draw_chip(
                draw,
                x=left + 22,
                y=top + 16,
                text="SYSTEM",
                fill="#ffffff",
                text_fill="#8f6a45",
                font=self.eyebrow_font,
            )
            self._draw_multiline_text(
                draw,
                x=left + 26,
                y=top + 56,
                lines=spec.lines,
                font=self.body_font,
                fill="#5d5143",
            )
            return

        is_user = spec.turn.speaker == "USER"
        bubble_width = (
            self._max_line_width(spec.lines, self.body_font) + self.BUBBLE_PADDING_X * 2
        )
        bubble_width = min(max(bubble_width, 220), self.WIDTH - 320)
        avatar_x = (
            self.WIDTH
            - self.OUTER_MARGIN
            - self.CONVERSATION_SIDE_PADDING
            - self.AVATAR_SIZE
            if is_user
            else self.OUTER_MARGIN + self.CONVERSATION_SIDE_PADDING
        )
        bubble_x = (
            avatar_x - 20 - bubble_width
            if is_user
            else avatar_x + self.AVATAR_SIZE + 20
        )
        bubble_y = top + max((self.AVATAR_SIZE - spec.height) // 2, 0)
        fill = "#ffe2d9" if is_user else "#e4f3ff"
        outline = "#f2bfb0" if is_user else "#bfdff0"
        text_fill = "#3d2f31" if is_user else "#2d4b5f"
        label = "你" if is_user else "凛"
        avatar_fill = "#ff9f87" if is_user else "#7dc1f5"

        self._draw_avatar(
            draw,
            x=avatar_x,
            y=top,
            label=label,
            fill=avatar_fill,
        )
        draw.rounded_rectangle(
            (bubble_x, bubble_y, bubble_x + bubble_width, bubble_y + spec.height),
            radius=self.BUBBLE_RADIUS,
            fill=fill,
            outline=outline,
            width=2,
        )
        self._draw_chip(
            draw,
            x=bubble_x + 18,
            y=bubble_y + 14,
            text="USER" if is_user else "BOT",
            fill="#fffaf6",
            text_fill="#815e58" if is_user else "#4c708c",
            font=self.eyebrow_font,
        )
        self._draw_multiline_text(
            draw,
            x=bubble_x + self.BUBBLE_PADDING_X,
            y=bubble_y + self.BUBBLE_PADDING_Y + 18,
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
        draw.ellipse(
            (
                x + 6,
                y + 8,
                x + self.AVATAR_SIZE + 6,
                y + self.AVATAR_SIZE + 8,
            ),
            fill="#f4d7cf",
        )
        draw.ellipse((x, y, x + self.AVATAR_SIZE, y + self.AVATAR_SIZE), fill=fill)
        draw.ellipse((x + 12, y + 10, x + 28, y + 24), fill="#ffffff")
        bbox = draw.textbbox((0, 0), label, font=self.meta_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.text(
            (
                x + (self.AVATAR_SIZE - text_width) / 2,
                y + (self.AVATAR_SIZE - text_height) / 2 - 2,
            ),
            label,
            font=self.meta_font,
            fill="#ffffff",
        )

    def _draw_avatar_badge(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        label: str,
        fill: str,
    ) -> None:
        draw.ellipse((x - 26, y - 26, x + 26, y + 26), fill=fill)
        draw.polygon(((x - 18, y - 24), (x - 6, y - 48), (x + 2, y - 20)), fill=fill)
        draw.polygon(((x + 18, y - 24), (x + 6, y - 48), (x - 2, y - 20)), fill=fill)
        bbox = draw.textbbox((0, 0), label, font=self.meta_font)
        draw.text(
            (
                x - (bbox[2] - bbox[0]) / 2,
                y - (bbox[3] - bbox[1]) / 2 - 1,
            ),
            label,
            font=self.meta_font,
            fill="#ffffff",
        )

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        top: int,
        plugin_title: str,
        plugin_version: str,
        plugin_author: str,
    ) -> None:
        left = self.OUTER_MARGIN + 28
        right = self.WIDTH - self.OUTER_MARGIN - 28
        draw.rounded_rectangle(
            (left, top, right, top + self.FOOTER_HEIGHT),
            radius=30,
            fill="#fff2ee",
            outline="#f3d8d1",
            width=2,
        )
        self._draw_chip(
            draw,
            x=left + 28,
            y=top + 20,
            text="PLUGIN SIGNATURE",
            fill="#ffffff",
            text_fill="#9c6557",
            font=self.eyebrow_font,
        )
        columns = [
            ("Plugin", plugin_title),
            ("Version", f"v{plugin_version.lstrip('v')}"),
            ("Author", plugin_author),
        ]
        base_x = left + 34
        gap = 364
        for index, (label, value) in enumerate(columns):
            x = base_x + index * gap
            draw.text(
                (x, top + 58),
                label,
                font=self.footer_label_font,
                fill="#a17b71",
            )
            draw.text(
                (x, top + 82),
                value,
                font=self.footer_value_font,
                fill="#4f3a35",
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
        bbox = draw.textbbox((0, 0), text, font=font)
        width = max(int(bbox[2] - bbox[0] + 28), min_width)
        height = int(bbox[3] - bbox[1] + 18)
        draw.rounded_rectangle((x, y, x + width, y + height), radius=16, fill=fill)
        draw.text((x + 14, y + 8), text, font=font, fill=text_fill)

    def _draw_sparkle(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        fill: str,
    ) -> None:
        draw.line((x - 10, y, x + 10, y), fill=fill, width=3)
        draw.line((x, y - 10, x, y + 10), fill=fill, width=3)
        draw.line((x - 7, y - 7, x + 7, y + 7), fill=fill, width=2)
        draw.line((x - 7, y + 7, x + 7, y - 7), fill=fill, width=2)

    def _wrap_text(self, text: str, *, max_width: int) -> list[str]:
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#ffffff"))
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            bbox = draw.textbbox((0, 0), candidate, font=self.body_font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = candidate
                continue
            lines.append(current)
            current = char
        if current:
            lines.append(current)
        return lines or [text]

    def _line_block_height(self, lines: Iterable[str], font: Any) -> int:
        count = 0
        for _ in lines:
            count += 1
        if count == 0:
            return 0
        return count * self._font_line_height(font) - 10

    def _max_line_width(self, lines: Sequence[str], font: Any) -> int:
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#ffffff"))
        return int(
            max(
                (draw.textbbox((0, 0), line, font=font)[2] for line in lines),
                default=0,
            )
        )

    def _draw_multiline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        lines: Sequence[str],
        font: Any,
        fill: str,
    ) -> None:
        line_height = self._font_line_height(font)
        for index, line in enumerate(lines):
            draw.text((x, y + index * line_height), line, font=font, fill=fill)

    def _font_line_height(self, font: Any) -> int:
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#ffffff"))
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return int(bbox[3] - bbox[1] + 10)


@dataclass(slots=True, frozen=True)
class _TurnSpec:
    turn: DocsDemoTurn
    lines: list[str]
    width: int
    height: int
