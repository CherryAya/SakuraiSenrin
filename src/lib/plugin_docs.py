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

DEMO_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEMO_AVATAR_PATH = DEMO_ASSETS_DIR / "senrin-demo-avatar.png"
DEMO_STANDEE_PATH = DEMO_ASSETS_DIR / "senrin-demo-standee.png"
SUPPORT_NOTE = "如需进一步支持，请联系管理员，或加入反馈群「427842039」💬。"


@dataclass(slots=True, frozen=True)
class DocsRenderContext:
    locale: LocaleCode
    feature_query: str | None = None
    include_demo: bool = True
    view: Literal["text", "index", "plugin", "feature"] = "text"


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
    include_demo = (
        ctx.include_demo if ctx is not None and ctx.view == "plugin" else False
    )
    return render_overview_message(
        bundle,
        locale=locale,
        include_demo=include_demo,
    )


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
    include_demo: bool = False,
) -> Message:
    lines = [
        f"📖 ===== {bundle.title} =====",
        "",
    ]
    if bundle.index:
        for index, feature in enumerate(bundle.index, start=1):
            lines.append(f"{index}. {feature.title}")
            lines.append(f"  {_feature_command_for_display(bundle, feature)}")
            lines.append("")
    else:
        lines.append("暂无可用功能。")
        lines.append("")
    lines.extend(
        [
            "⚠️ 注意事项:",
            "1. 请确认指令参数填写完整。",
            f"2. {SUPPORT_NOTE}",
        ]
    )
    message = Message("\n".join(line for line in lines if line is not None).strip())
    if not include_demo:
        return message
    demo_bytes = load_representative_demo_bytes(bundle)
    if demo_bytes is None:
        return message
    return message + MessageSegment.image(demo_bytes)


def render_feature_message(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    include_demo: bool = True,
) -> Message:
    lines = [
        f"📖 ===== {bundle.title} / {feature.title} =====",
        "",
        f"功能名: {feature.title}",
        "",
        "指令:",
        f"  {_feature_command_for_display(bundle, feature)}",
        "",
        "⚠️ 注意事项:",
    ]
    for index, note in enumerate(_feature_notice_items(feature), start=1):
        lines.append(f"{index}. {note}")
    message = Message("\n".join(lines).strip())
    if not include_demo:
        return message
    demo_bytes = load_demo_bytes(bundle, feature)
    if demo_bytes is None:
        return message
    return message + MessageSegment.image(demo_bytes)


def _feature_command_for_display(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
) -> str:
    command = _normalize_inline_text(feature.trigger)
    if command:
        return command
    return f"#help {bundle.title} {feature.slug}"


def _feature_notice_items(feature: FeatureDoc) -> list[str]:
    notes: list[str] = []
    preconditions = _normalize_inline_text(feature.preconditions)
    if preconditions and preconditions != "无":
        notes.append(preconditions)
    else:
        notes.append("请确认指令参数填写完整。")
    notes.append(SUPPORT_NOTE)
    return notes


def _normalize_inline_text(value: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", value.strip())
    return re.sub(r"\s+", " ", text).strip()


def load_demo_bytes(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes | None:
    if feature.demo_filename:
        demo_path = bundle.source_path.parent / "demos" / feature.demo_filename
        if demo_path.is_file():
            return demo_path.read_bytes()
    if not feature.demo_turns:
        return None
    return render_demo_png(bundle, feature)


def load_representative_demo_bytes(bundle: PluginDocBundle) -> bytes | None:
    for feature in bundle.index:
        demo_bytes = load_demo_bytes(bundle, feature)
        if demo_bytes is not None:
            return demo_bytes
    return None


def render_demo_png(bundle: PluginDocBundle, feature: FeatureDoc) -> bytes:
    return DemoImageRenderer().render(
        plugin_title=bundle.title,
        feature_title=feature.title,
        feature_trigger=feature.trigger,
        plugin_version=bundle.version,
        plugin_author=bundle.author,
        turns=feature.demo_turns,
    )


def audit_demo_layout(bundle: PluginDocBundle, feature: FeatureDoc) -> tuple[str, ...]:
    return DemoImageRenderer().audit(
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
            if not stripped:
                continue
            if ":" in stripped:
                speaker, text = stripped.split(":", 1)
                normalized = speaker.strip().upper()
                if normalized in {"USER", "BOT", "SYSTEM"}:
                    demo_turns.append(
                        DocsDemoTurn(
                            cast(Literal["USER", "BOT", "SYSTEM"], normalized),
                            text.strip(),
                        )
                    )
                    continue
            if demo_turns:
                previous = demo_turns[-1]
                demo_turns[-1] = DocsDemoTurn(
                    previous.speaker,
                    f"{previous.text}\n{stripped}",
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
    """Render a compact plugin docs demo card."""

    WIDTH = 1280
    OUTER_MARGIN = 40
    SHELL_RADIUS = 32
    HEADER_HEIGHT = 226
    HEADER_LEFT = 126
    HEADER_TOP = 70
    HEADER_CHIP_TOP = 72
    HEADER_TITLE_TOP = 112
    HEADER_FEATURE_TOP = 166
    HEADER_TRIGGER_TOP = 202
    HEADER_RIGHT = 1138
    HEADER_STANDEE_SIZE = 150
    HEADER_STANDEE_X = 1030
    HEADER_STANDEE_Y = 96
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

    PAGE_BG = "#FAFAF8"
    SHELL_BG = "#FFFFFF"
    SHELL_BORDER = "#EFE9ED"
    PANEL_BG = "#FFFFFF"
    PANEL_BORDER = "#F0E8EE"
    ACCENT = "#E987A5"
    ACCENT_DARK = "#9A3F62"
    INDIGO = "#4F6FAE"
    INDIGO_SOFT = "#EAF0FF"
    INDIGO_TEXT = "#2F4A7C"
    DEEP = "#2E2630"
    MUTED = "#8F8190"
    MUTED_LIGHT = "#F7F3F5"
    USER_BUBBLE = "#FFF0F5"
    BOT_BUBBLE = "#EEF4FF"
    SYSTEM_BUBBLE = "#FFF7DF"
    SYSTEM_TEXT = "#67522B"
    SYSTEM_LABEL = "#9B7524"
    FOOTER_BG = "#FBF6F8"

    def __init__(self) -> None:
        try:
            self.eyebrow_font = ImageFont.truetype(MAPLE_FONT_PATH, 16)
            self.title_font = ImageFont.truetype(MAPLE_FONT_PATH, 42)
            self.feature_font = ImageFont.truetype(MAPLE_FONT_PATH, 26)
            self.body_font = ImageFont.truetype(MAPLE_FONT_PATH, 24)
            self.meta_font = ImageFont.truetype(MAPLE_FONT_PATH, 16)
            self.footer_font = ImageFont.truetype(MAPLE_FONT_PATH, 15)
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
    ) -> bytes:
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = self._conversation_height(turn_specs)
        panel_top = self.OUTER_MARGIN + self.HEADER_HEIGHT + self.BODY_TOP_GAP
        body_top = panel_top + self.BODY_PADDING_Y
        panel_bottom = body_top + conversation_height + self.BODY_PADDING_Y
        footer_top = panel_bottom + self.FOOTER_TOP_GAP
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        image = Image.new("RGB", (self.WIDTH, height), self.PAGE_BG)
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
            fill=self.SHELL_BG,
            outline=self.SHELL_BORDER,
            width=2,
        )
        self._draw_header(
            image,
            draw,
            plugin_title=plugin_title,
            feature_title=feature_title,
            feature_trigger=feature_trigger,
            turn_count=len(turns),
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
        draw.rectangle((0, 0, width, height), fill=self.PAGE_BG)
        draw.rectangle((0, 0, width, 10), fill=self.ACCENT)
        draw.rounded_rectangle((74, 66, 112, height - 76), radius=19, fill="#FFF4F7")
        draw.rounded_rectangle(
            (width - 142, 126, width - 86, height - 124),
            radius=28,
            fill="#F1F4FF",
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
    ) -> None:
        draw.rounded_rectangle((96, 94, 106, 190), radius=5, fill=self.ACCENT)
        self._draw_chip(
            draw,
            x=self.HEADER_LEFT,
            y=self.HEADER_CHIP_TOP,
            text="PLUGIN DEMO",
            fill=self.MUTED_LIGHT,
            text_fill=self.ACCENT_DARK,
            font=self.eyebrow_font,
        )
        self._draw_chip(
            draw,
            x=self.HEADER_STEPS_X,
            y=self.HEADER_CHIP_TOP,
            text=f"{turn_count} STEP{'S' if turn_count != 1 else ''}",
            fill=self.INDIGO_SOFT,
            text_fill=self.INDIGO_TEXT,
            font=self.eyebrow_font,
            min_width=154,
        )
        draw.text(
            (self.HEADER_LEFT, self.HEADER_TITLE_TOP),
            self._fit_text(
                draw,
                plugin_title,
                self.title_font,
                max_width=720,
            ),
            font=self.title_font,
            fill=self.DEEP,
        )
        draw.text(
            (self.HEADER_LEFT, self.HEADER_FEATURE_TOP),
            self._fit_text(
                draw,
                feature_title,
                self.feature_font,
                max_width=720,
            ),
            font=self.feature_font,
            fill=self.ACCENT_DARK,
        )
        if feature_trigger.strip():
            self._draw_chip(
                draw,
                x=self.HEADER_LEFT,
                y=self.HEADER_TRIGGER_TOP,
                text=self._fit_text(
                    draw,
                    f"指令示例: {feature_trigger}",
                    self.meta_font,
                    max_width=760,
                ),
                fill="#FFF7FA",
                text_fill=self.MUTED,
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
            radius=28,
            fill=self.PANEL_BG,
            outline=self.PANEL_BORDER,
            width=2,
        )

    def _measure_turn(self, turn: DocsDemoTurn) -> "_TurnSpec":
        if turn.speaker == "SYSTEM":
            lines = self._wrap_text(
                self._normalize_demo_text(turn.text),
                max_width=self.SYSTEM_CONTENT_WIDTH,
            )
            text_height = self._line_block_height(lines, self.body_font)
            width = (
                self._max_line_width(lines, self.body_font) + self.BUBBLE_PADDING_X * 2
            )
            return _TurnSpec(
                turn=turn,
                lines=lines,
                width=max(width, self.SYSTEM_MIN_BUBBLE_WIDTH),
                height=text_height + self.BUBBLE_PADDING_Y * 2 + 18,
            )

        is_user = turn.speaker == "USER"
        lines = self._wrap_text(
            self._normalize_demo_text(turn.text),
            max_width=self.USER_CONTENT_WIDTH if is_user else self.BOT_CONTENT_WIDTH,
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
            self._max_line_width(lines, self.body_font) + self.BUBBLE_PADDING_X * 2
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
                fill=self.SYSTEM_BUBBLE,
            )
            self._draw_multiline_text(
                draw,
                x=left + self.BUBBLE_PADDING_X,
                y=top + self.BUBBLE_PADDING_Y + 8,
                lines=spec.lines,
                font=self.body_font,
                fill=self.SYSTEM_TEXT,
            )
            label = "SYSTEM"
            label_box = draw.textbbox((0, 0), label, font=self.eyebrow_font)
            draw.text(
                (left + self.BUBBLE_PADDING_X, top + 12 - label_box[1]),
                label,
                font=self.eyebrow_font,
                fill=self.SYSTEM_LABEL,
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
        fill = self.USER_BUBBLE if is_user else self.BOT_BUBBLE
        text_fill = self.DEEP if is_user else self.INDIGO_TEXT
        label = "你" if is_user else "凛"
        avatar_fill = self.ACCENT if is_user else self.INDIGO

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
        label_fill = self.ACCENT_DARK if is_user else self.INDIGO
        label_box = draw.textbbox((0, 0), speaker, font=self.eyebrow_font)
        draw.text(
            (
                bubble_x + self.BUBBLE_PADDING_X,
                bubble_y + self.BUBBLE_PADDING_Y - label_box[1],
            ),
            speaker,
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
            fill="#FFFFFF",
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
            self._draw_avatar(draw, x=x, y=y, label="凛", fill=self.INDIGO)
            return
        avatar = self.senrin_avatar
        mask = Image.new("L", avatar.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, avatar.width - 1, avatar.height - 1), fill=255)
        draw.ellipse(
            (x, y, x + self.AVATAR_SIZE, y + self.AVATAR_SIZE),
            fill="#F7FAFF",
            outline="#D8E3FF",
            width=2,
        )
        image.paste(avatar, (x, y), mask)

    def _draw_header_standee(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        if self.senrin_standee is None:
            self._draw_avatar(draw, x=1088, y=128, label="凛", fill=self.INDIGO)
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
            fill=self.FOOTER_BG,
        )
        footer_rect = (
            self.OUTER_MARGIN + 28,
            top,
            self.WIDTH - self.OUTER_MARGIN - 28,
            top + self.FOOTER_HEIGHT,
        )
        right_text = self.FOOTER_RIGHT_TEXT
        right_bbox = draw.textbbox((0, 0), right_text, font=self.footer_font)
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
            fill=self.MUTED,
            align="left",
            padding_x=self.FOOTER_SIDE_PADDING,
        )
        self._draw_text_centered(
            draw,
            right_rect,
            right_text,
            font=self.footer_font,
            fill=self.MUTED,
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
        draw.rounded_rectangle(rect, radius=15, fill=fill)
        self._draw_text_centered(
            draw,
            rect,
            text,
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
    ) -> tuple[str, ...]:
        errors: list[str] = []
        turn_specs = [self._measure_turn(turn) for turn in turns]
        conversation_height = self._conversation_height(turn_specs)
        panel_top = self.OUTER_MARGIN + self.HEADER_HEIGHT + self.BODY_TOP_GAP
        body_top = panel_top + self.BODY_PADDING_Y
        panel_bottom = body_top + conversation_height + self.BODY_PADDING_Y
        footer_top = panel_bottom + self.FOOTER_TOP_GAP
        height = footer_top + self.FOOTER_HEIGHT + self.OUTER_MARGIN
        draw = ImageDraw.Draw(Image.new("RGB", (self.WIDTH, height), "#FFFFFF"))

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
            trigger_rect = self._chip_rect(
                draw,
                x=self.HEADER_LEFT,
                y=self.HEADER_TRIGGER_TOP,
                text=self._fit_text(
                    draw,
                    f"指令示例: {feature_trigger}",
                    self.meta_font,
                    max_width=760,
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
        footer_right_bbox = draw.textbbox(
            (0, 0), self.FOOTER_RIGHT_TEXT, font=self.footer_font
        )
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
        bbox = draw.textbbox((x, y), text, font=font)
        return int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])

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
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = int(bbox[2] - bbox[0])
        text_height = int(bbox[3] - bbox[1])
        if align == "left":
            x = rect[0] + padding_x
        elif align == "right":
            x = rect[2] - text_width
        else:
            x = rect[0] + (rect[2] - rect[0] - text_width) / 2
        y = rect[1] + (rect[3] - rect[1] - text_height) / 2 - bbox[1]
        draw.text((x, y), text, font=font, fill=fill)

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
        bbox = draw.textbbox((0, 0), text, font=font)
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
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text

        ellipsis = "..."
        current = text
        while current:
            current = current[:-1]
            candidate = current.rstrip() + ellipsis
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                return candidate
        return ellipsis

    def _normalize_demo_text(self, text: str) -> str:
        return text.replace("📩", "[通知]")

    def _wrap_text(self, text: str, *, max_width: int) -> list[str]:
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#FFFFFF"))
        lines: list[str] = []
        for paragraph in text.splitlines():
            current = ""
            for char in paragraph:
                candidate = current + char
                bbox = draw.textbbox((0, 0), candidate, font=self.body_font)
                if bbox[2] - bbox[0] <= max_width or not current:
                    current = candidate
                    continue
                lines.append(current)
                current = char
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
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#FFFFFF"))
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
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "#FFFFFF"))
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return int(bbox[3] - bbox[1] + 10)


@dataclass(slots=True, frozen=True)
class _TurnSpec:
    turn: DocsDemoTurn
    lines: list[str]
    width: int
    height: int
