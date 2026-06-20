"""Copy text builders and command display helpers for plugin docs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import re

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .models import DocNode, FeatureDoc, PluginDocBundle

type SupportNoteProvider = Callable[[LocaleCode], str]
type SupportTextBlockProvider = Callable[[LocaleCode], str]
type NormalizeInlineText = Callable[[str], str]


def feature_command_for_display(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
    *,
    normalize_inline_text: NormalizeInlineText,
) -> str:
    command = normalize_inline_text(feature.trigger)
    if command:
        return command
    return f"#help {node_title} {feature.slug}"


def feature_demo_help_command(node: DocNode, feature: FeatureDoc) -> str:
    return f"{node_help_command(node)} {feature.slug}"


def node_help_command(node: DocNode) -> str:
    target = node.help_query.strip() or node.title
    return f"#help {target}"


def format_feature_command_lines(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
    *,
    locale: LocaleCode = "zh-CN",
    normalize_inline_text: NormalizeInlineText,
) -> list[str]:
    command = feature_command_for_display(
        bundle,
        feature,
        node_title,
        normalize_inline_text=normalize_inline_text,
    )
    sections = [
        part.strip() for part in re.split(r"\s*[；;]\s*", command) if part.strip()
    ]
    if len(sections) <= 1:
        return [f"  {command}"]

    lines: list[str] = []
    shortcut_groups: list[str] = []
    shortcut_sections: list[str] = []
    in_shortcut_section = False
    for section in sections:
        if match := re.match(r"快捷入口[:：]\s*(.+)", section):
            shortcut_groups.append(match.group(1).strip())
            in_shortcut_section = True
            continue
        if match := re.match(r"快捷入口分组[:：]\s*(.+)", section):
            shortcut_sections.append(match.group(1).strip())
            in_shortcut_section = False
            continue
        if shortcut_sections:
            shortcut_sections.append(section)
            continue
        if in_shortcut_section:
            shortcut_groups.append(section)
            continue
        lines.append(f"  {section}")

    if shortcut_sections:
        lines.append(f"  {tr(locale, 'docs.feature.shortcuts')}")
        lines.extend(_format_shortcut_section_lines(shortcut_sections))

    if shortcut_groups:
        lines.append(f"  {tr(locale, 'docs.feature.shortcuts')}")
        lines.extend(f"    {group}" for group in shortcut_groups)

    return lines or [f"  {command}"]


def _format_shortcut_section_lines(sections: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for index, section in enumerate(sections):
        if ":" not in section:
            lines.append(f"    {section}")
            continue
        _, commands = section.split(":", 1)
        summarized = _summarize_shortcut_commands(
            commands.strip(),
            keep_full=index == 0,
        )
        lines.append(f"    {summarized}")
    return lines


def _summarize_shortcut_commands(commands: str, *, keep_full: bool = False) -> str:
    if keep_full:
        return commands
    parts = [part.strip() for part in commands.split("/") if part.strip()]
    if not parts:
        return commands
    primary = parts[0]
    if len(parts) == 1:
        return primary
    return f"{primary} / ..."


def feature_notice_items(
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    normalize_inline_text: NormalizeInlineText,
    support_note: SupportNoteProvider,
) -> list[str]:
    notes: list[str] = []
    preconditions = normalize_inline_text(feature.preconditions)
    if preconditions and preconditions != "无":
        notes.append(preconditions)
    else:
        notes.append(tr(locale, "docs.node.notice.item1"))
    notes.append(support_note(locale))
    return notes


def feature_command_sections(
    bundle: PluginDocBundle,
    feature: FeatureDoc,
    node_title: str,
    *,
    normalize_inline_text: NormalizeInlineText,
) -> tuple[str, ...]:
    command = feature_command_for_display(
        bundle,
        feature,
        node_title,
        normalize_inline_text=normalize_inline_text,
    )
    sections = [
        part.strip() for part in re.split(r"\s*[；;]\s*", command) if part.strip()
    ]
    return tuple(sections) or (command,)


def build_feature_copy_text(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    normalize_inline_text: NormalizeInlineText,
    support_note: SupportNoteProvider,
    support_text_block: SupportTextBlockProvider,
) -> str:
    lines = [
        node.title,
        feature.title,
        "",
        "命令：",
        *(
            section
            for section in feature_command_sections(
                node.bundle,
                feature,
                node.title,
                normalize_inline_text=normalize_inline_text,
            )
        ),
    ]
    note_items = feature_notice_items(
        feature,
        locale=locale,
        normalize_inline_text=normalize_inline_text,
        support_note=support_note,
    )
    if note_items:
        lines.extend(["", f"说明：{note_items[0]}"])
    lines.extend(["", support_text_block(locale)])
    return "\n".join(lines).strip()


def build_plugin_guide_copy_text(
    node: DocNode,
    *,
    features: Sequence[FeatureDoc],
    normalize_inline_text: NormalizeInlineText,
    support_text_block: SupportTextBlockProvider,
    locale: LocaleCode,
) -> str:
    lines = [
        node.title,
        "下面这些命令可以直接复制发送：",
        "",
    ]
    for feature in features:
        lines.append(feature.title)
        lines.extend(
            feature_command_sections(
                node.bundle,
                feature,
                node.title,
                normalize_inline_text=normalize_inline_text,
            )
        )
        lines.append("")
    lines.append(support_text_block(locale))
    return "\n".join(line for line in lines if line is not None).strip()


def build_simple_leaf_copy_text(
    node: DocNode,
    feature: FeatureDoc,
    *,
    locale: LocaleCode,
    normalize_inline_text: NormalizeInlineText,
    support_note: SupportNoteProvider,
    support_text_block: SupportTextBlockProvider,
) -> str:
    lines = [
        node.title,
        "",
        "命令：",
        *feature_command_sections(
            node.bundle,
            feature,
            node.title,
            normalize_inline_text=normalize_inline_text,
        ),
    ]
    note_items = feature_notice_items(
        feature,
        locale=locale,
        normalize_inline_text=normalize_inline_text,
        support_note=support_note,
    )
    if note_items:
        lines.extend(["", f"说明：{note_items[0]}"])
    lines.extend(["", support_text_block(locale)])
    return "\n".join(lines).strip()


def build_static_entry_copy_text(
    node: DocNode,
    *,
    locale: LocaleCode,
    support_note: SupportNoteProvider,
    support_text_block: SupportTextBlockProvider,
) -> str:
    lines = [node.title]
    if node.summary:
        lines.extend(["", node.summary])
    if node.description and node.description != node.summary:
        lines.extend(["", node.description])
    lines.extend(
        [
            "",
            "这是一个静态社区入口说明页，不提供子功能级 help。",
            "实际可触发内容由社区词条或运行时数据决定。",
            "help 只负责暴露这个入口本身，不为每个社区词条派生独立命令说明。",
            "",
            f"说明：{support_note(locale)}",
            "",
            support_text_block(locale),
        ]
    )
    return "\n".join(lines).strip()
