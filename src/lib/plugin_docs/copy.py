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
        tr(locale, "docs.render.feature.command_label"),
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
        lines.extend(
            [
                "",
                f"{tr(locale, 'docs.render.feature.description_label')}{note_items[0]}",
            ]
        )
    lines.extend(["", support_text_block(locale)])
    return "\n".join(lines).strip()


def build_plugin_guide_copy_text(
    node: DocNode,
    *,
    features: Sequence[FeatureDoc],
    child_nodes: Sequence[DocNode] = (),
    normalize_inline_text: NormalizeInlineText,
    support_text_block: SupportTextBlockProvider,
    locale: LocaleCode,
) -> str:
    lines = [node.title, ""]

    for feature in features:
        lines.append(
            tr(locale, "docs.render.plugin_summary.entry_marker", title=feature.title)
        )
        for command in feature_command_sections(
            node.bundle,
            feature,
            node.title,
            normalize_inline_text=normalize_inline_text,
        ):
            normalized_command = command.strip()
            if normalized_command and not normalized_command.startswith("#"):
                normalized_command = f"#{normalized_command}"
            lines.append(f"  {normalized_command}")
        lines.append("")

    if child_nodes:
        lines.append(tr(locale, "docs.render.guide.child_module_label"))
        for child in child_nodes:
            lines.append(
                tr(locale, "docs.render.plugin_summary.entry_marker", title=child.title)
            )
            lines.append(f"  {node_help_command(child)}")
            summary = normalize_inline_text(child.summary)
            if summary:
                lines.append(f"  {summary}")
            lines.append("")

    lines.append(support_text_block(locale))
    return "\n".join(lines).strip()


def build_plugin_summary_copy_text(
    node: DocNode,
    *,
    normalize_inline_text: NormalizeInlineText,
) -> str:
    summary = normalize_inline_text(node.summary)
    description = normalize_inline_text(node.description)
    lines: list[str] = []
    if summary:
        lines.append(summary)
    if description and description != summary:
        lines.extend(["", description])
    return "\n".join(lines).strip()


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
        tr(locale, "docs.render.feature.command_label"),
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
        lines.extend(
            [
                "",
                f"{tr(locale, 'docs.render.feature.description_label')}{note_items[0]}",
            ]
        )
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
            tr(locale, "docs.render.static_entry.summary_line1"),
            tr(locale, "docs.render.static_entry.summary_line2"),
            tr(locale, "docs.render.static_entry.summary_line3"),
            "",
            f"{tr(locale, 'docs.render.feature.description_label')}"
            f"{support_note(locale)}",
            "",
            support_text_block(locale),
        ]
    )
    return "\n".join(lines).strip()
