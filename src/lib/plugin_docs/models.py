"""Shared plugin docs dataclasses and metadata shapes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from markdown_it.token import Token

from src.database.core.consts import Permission
from src.lib.i18n.types import LocaleCode

type DocNodeKind = Literal["plugin", "overview", "internal", "static"]
type DocRenderView = Literal["text", "index", "plugin", "feature"]


@dataclass(slots=True, frozen=True)
class DocsRenderContext:
    locale: LocaleCode
    feature_query: str | None = None
    include_demo: bool = True
    view: DocRenderView = "text"
    actor_permission: Permission = Permission.NORMAL


class DocSourceMeta(TypedDict):
    kind: Literal["readme"]
    readme_path: str


class DocTreeMeta(TypedDict):
    slug: str
    parent_slug: str | None
    category: str
    order: int


class DocVisibilityMeta(TypedDict):
    visible: bool
    hidden: bool
    internal: bool


class DocsMeta(TypedDict):
    kind: DocNodeKind
    source: DocSourceMeta
    tree: DocTreeMeta
    visibility: DocVisibilityMeta
    permission: Permission | int | str
    aliases: tuple[str, ...]


type DocsMetaValue = DocsMeta | tuple[DocsMeta, ...]


@dataclass(slots=True, frozen=True)
class DocsDemoTurn:
    speaker: Literal["USER", "BOT", "SYSTEM"]
    text: str
    section: str = ""


@dataclass(slots=True, frozen=True)
class MarkdownSection:
    title: str
    heading: Token
    tokens: tuple[Token, ...]


@dataclass(slots=True, frozen=True)
class FeatureDoc:
    slug: str
    title: str
    summary: str
    aliases: tuple[str, ...]
    trigger: str
    permission: Permission
    demo_filename: str
    overview: str
    preconditions: str
    flow_notes: str
    failures: str
    demo_turns: tuple[DocsDemoTurn, ...]
    hero: bool = False
    priority: int = 1000
    advanced: bool = False

    @property
    def search_tokens(self) -> set[str]:
        return {
            self.slug.lower(),
            self.title.lower(),
            *(alias.lower() for alias in self.aliases),
        }


@dataclass(slots=True, frozen=True)
class VirtualFeatureDocSpec:
    slug: str
    title: str
    summary: str
    trigger: str
    overview: str
    preconditions: str
    failures: str
    demo_turns: tuple[DocsDemoTurn, ...]
    aliases: tuple[str, ...] = ()
    permission: Permission = Permission.NORMAL
    hero: bool = False
    priority: int = 1000
    advanced: bool = False
    demo_filename: str = ""


@dataclass(slots=True, frozen=True)
class PluginDocBundle:
    title: str
    description: str
    summary: str
    trigger: str
    help_query: str
    permission: str
    author: str
    version: str
    impression_color: str
    index: tuple[FeatureDoc, ...]
    source_path: Path


@dataclass(slots=True, frozen=True)
class VirtualPluginDocSpec:
    slug: str
    title: str
    summary: str
    description: str
    trigger: str
    author: str
    version: str
    impression_color: str
    features: tuple[VirtualFeatureDocSpec, ...]
    aliases: tuple[str, ...] = ()
    permission: Permission = Permission.NORMAL
    category: str = "general"
    order: int = 100
    visible: bool = True
    hidden: bool = False
    internal: bool = False
    kind: DocNodeKind = "plugin"
    parent_slug: str | None = None
    plugin_name: str = ""
    module_name: str = ""
    origin_plugin_slug: str = ""


@dataclass(slots=True, frozen=True)
class FeatureMatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    feature: FeatureDoc | None = None
    candidates: tuple[FeatureDoc, ...] = ()


@dataclass(slots=True, frozen=True)
class DocNode:
    kind: DocNodeKind
    slug: str
    parent_slug: str | None
    category: str
    order: int
    visible: bool
    hidden: bool
    internal: bool
    permission: Permission
    title: str
    summary: str
    description: str
    help_query: str
    aliases: tuple[str, ...]
    source_path: Path
    bundle: PluginDocBundle
    module_name: str = ""
    plugin_name: str = ""

    @property
    def features(self) -> tuple[FeatureDoc, ...]:
        return self.bundle.index

    @property
    def search_tokens(self) -> set[str]:
        tokens = {
            self.slug.lower(),
            self.title.lower(),
            *(alias.lower() for alias in self.aliases),
        }
        leaf = self.slug.split(".")[-1].strip()
        if leaf:
            tokens.add(leaf.lower())
        if self.plugin_name:
            tokens.add(self.plugin_name.lower())
        if self.module_name:
            module = self.module_name.lower()
            tokens.add(module)
            tokens.add(module.split(".")[-1])
        return {token for token in tokens if token}


@dataclass(slots=True, frozen=True)
class HelpDashboardSection:
    kind: str
    title: str
    nodes: tuple[DocNode, ...]
    accent: str | None = None
    panel_bg: str | None = None
    panel_soft_bg: str | None = None
    text: str | None = None
    hint: str | None = None
    command_bg: str | None = None
    command_text: str | None = None
    marker: str = "•"
    column: int = 0


@dataclass(slots=True, frozen=True)
class DocTree:
    nodes: tuple[DocNode, ...]
    children_by_slug: dict[str, tuple[DocNode, ...]]

    def children_of(self, slug: str) -> tuple[DocNode, ...]:
        return self.children_by_slug.get(slug, ())

    def roots(self) -> tuple[DocNode, ...]:
        return tuple(node for node in self.nodes if node.parent_slug is None)

    def root_of(self, slug: str) -> DocNode | None:
        mapping = {node.slug: node for node in self.nodes}
        current = mapping.get(slug)
        while current is not None and current.parent_slug is not None:
            current = mapping.get(current.parent_slug)
        return current


@dataclass(slots=True, frozen=True)
class NodeMatchResult:
    status: Literal["matched", "not_found", "ambiguous"]
    node: DocNode | None = None
    candidates: tuple[DocNode, ...] = ()
