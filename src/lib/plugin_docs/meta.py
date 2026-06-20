"""Docs metadata and source identity helpers."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
import os
from pathlib import Path
import re
from typing import Any, cast

from nonebot.plugin import PluginMetadata

from src.database.core.consts import Permission
from src.lib.demo_theme import DEFAULT_IMPRESSION_COLOR, normalize_hex_color
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode

from .models import DocNodeKind, DocsMeta, HelpSupportBundle, SupportGroupLink

HELP_SUPPORT_QR_ASSET = (
    Path(__file__).resolve().parents[1] / "assets" / "help-support-qr-double.png"
)
HELP_SUPPORT_GROUPS: tuple[SupportGroupLink, ...] = (
    SupportGroupLink(
        title="❄️凛雪列車｜描摹重日冷影❄️",
        url="https://qm.qq.com/q/rnNzj9thG8",
    ),
    SupportGroupLink(
        title="❄️No Senrin No Life❄️｜原来你也喜欢凛凛？",
        url="https://qm.qq.com/q/JrIxb24HsI",
    ),
)


def support_note(locale: LocaleCode) -> str:
    main_group_id = resolve_main_group_id()
    return (
        tr(locale, "help.index.notice.item2", main_group_id=main_group_id)
        .removeprefix("2. ")
        .strip()
    )


def support_bundle(locale: LocaleCode) -> HelpSupportBundle:
    return HelpSupportBundle(
        title="反馈与交流群",
        tip_text=support_note(locale),
        groups=HELP_SUPPORT_GROUPS,
        qr_asset_path=HELP_SUPPORT_QR_ASSET,
    )


def support_text_block(locale: LocaleCode) -> str:
    bundle = support_bundle(locale)
    lines = [bundle.title, bundle.tip_text, ""]
    lines.extend(f"{group.title}：{group.url}" for group in bundle.groups)
    return "\n".join(lines).strip()


def resolve_main_group_id() -> str:
    env_main_group_id = os.getenv("MAIN_GROUP_ID", "").strip()
    try:
        config_module = import_module("src.config")
    except Exception:
        return env_main_group_id or "未配置"
    runtime_config = getattr(config_module, "config", None)
    config_main_group_id = str(getattr(runtime_config, "MAIN_GROUP_ID", "")).strip()
    return config_main_group_id or env_main_group_id or "未配置"


def create_docs_meta(
    *,
    visible: bool,
    category: str,
    order: int,
    source: str | Path | None = None,
    hidden: bool = False,
    slug: str | None = None,
    parent_slug: str | None = None,
    kind: DocNodeKind = "plugin",
    internal: bool = False,
    aliases: Sequence[str] = (),
) -> DocsMeta:
    source_path = Path(source).resolve() if source is not None else Path()
    derived_slug, derived_parent = derive_tree_identity_from_source(source_path)
    return {
        "kind": kind,
        "source": {
            "kind": "readme",
            "readme_path": str(source_path),
        },
        "tree": {
            "slug": slug or derived_slug,
            "parent_slug": parent_slug if parent_slug is not None else derived_parent,
            "category": category or "general",
            "order": order,
        },
        "visibility": {
            "visible": visible,
            "hidden": hidden,
            "internal": internal,
        },
        "permission": Permission.NORMAL,
        "aliases": tuple(alias.strip() for alias in aliases if alias.strip()),
    }


def read_docs_meta(metadata: PluginMetadata) -> DocsMeta | None:
    metas = read_docs_metas(metadata)
    return metas[0] if metas else None


def read_docs_metas(metadata: PluginMetadata) -> tuple[DocsMeta, ...]:
    raw = metadata.extra.get("docs")
    if isinstance(raw, dict):
        parsed = normalize_docs_meta(
            raw,
            default_permission=metadata.extra.get("permission", Permission.NORMAL),
        )
        return (parsed,) if parsed is not None else ()
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        metas: list[DocsMeta] = []
        default_permission = metadata.extra.get("permission", Permission.NORMAL)
        for item in raw:
            if not isinstance(item, dict):
                continue
            parsed = normalize_docs_meta(item, default_permission=default_permission)
            if parsed is not None:
                metas.append(parsed)
        return tuple(metas)
    return ()


def normalize_docs_meta(
    raw: dict[str, Any],
    *,
    default_permission: Permission | int | str,
) -> DocsMeta | None:
    source = raw.get("source")
    tree = raw.get("tree")
    visibility = raw.get("visibility")
    if not isinstance(source, dict) or not isinstance(tree, dict):
        return None
    if not isinstance(visibility, dict):
        return None
    readme_path = str(source.get("readme_path", "")).strip()
    slug = str(tree.get("slug", "")).strip()
    category = str(tree.get("category", "general")).strip()
    if not readme_path or not slug:
        return None
    aliases = raw.get("aliases", ())
    normalized_aliases = tuple(
        alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()
    )
    permission = raw.get("permission", default_permission)
    return {
        "kind": cast(DocNodeKind, raw.get("kind", "plugin")),
        "source": {
            "kind": "readme",
            "readme_path": readme_path,
        },
        "tree": {
            "slug": slug,
            "parent_slug": (
                str(tree["parent_slug"]).strip()
                if tree.get("parent_slug") is not None
                else None
            ),
            "category": category or "general",
            "order": int(tree.get("order", 100)),
        },
        "visibility": {
            "visible": bool(visibility.get("visible", True)),
            "hidden": bool(visibility.get("hidden", False)),
            "internal": bool(visibility.get("internal", False)),
        },
        "permission": permission,
        "aliases": normalized_aliases,
    }


def coerce_permission(value: Permission | int | str) -> Permission:
    if isinstance(value, Permission):
        return value
    if isinstance(value, str):
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
    try:
        return Permission(int(value))
    except (TypeError, ValueError):
        return Permission.NORMAL


def derive_tree_identity_from_source(source_path: Path) -> tuple[str, str | None]:
    parts = source_path.parts
    if "src" not in parts or "docs" not in parts:
        slug = source_path.stem.lower().replace("_", "-")
        return slug or "docs", None

    src_index = parts.index("src")
    docs_index = parts.index("docs")
    namespace = parts[src_index + 1] if src_index + 1 < len(parts) else ""
    if namespace == "hooks":
        if docs_index == src_index + 2:
            return f"hook.{source_path.stem.lower().replace('_', '-')}", None
        owner = parts[src_index + 2]
        if docs_index == src_index + 3:
            return f"{owner}.{source_path.stem.lower().replace('_', '-')}", owner
    if namespace == "plugins":
        plugin_name = parts[src_index + 2]
        rel_parts = list(parts[docs_index + 1 : -1])
        if not rel_parts:
            return plugin_name, None
        if len(rel_parts) == 1:
            leaf = rel_parts[0].lower().replace("_", "-")
            return f"{plugin_name}.{leaf}", plugin_name
        leaf = rel_parts[-1].lower().replace("_", "-")
        return f"{plugin_name}.{leaf}", plugin_name
    slug = source_path.stem.lower().replace("_", "-")
    return slug or "docs", None


def resolve_doc_signature(source_path: Path) -> tuple[str, str]:
    module_path = resolve_doc_owner_module_path(source_path)
    if module_path is None or not module_path.exists():
        return "Unknown", "0.0.0"
    raw_text = module_path.read_text(encoding="utf-8")
    author = extract_metadata_field(raw_text, "author") or "Unknown"
    version = extract_metadata_field(raw_text, "version") or "0.0.0"
    return author, version


def resolve_doc_impression_color(source_path: Path) -> str:
    module_path = resolve_doc_owner_module_path(source_path)
    if module_path is None or not module_path.exists():
        return DEFAULT_IMPRESSION_COLOR
    raw_text = module_path.read_text(encoding="utf-8")
    return normalize_hex_color(
        extract_metadata_field(raw_text, "impression_color"),
        fallback=DEFAULT_IMPRESSION_COLOR,
    )


def resolve_doc_owner_module_path(source_path: Path) -> Path | None:
    try:
        src_root = Path(*source_path.parts[: source_path.parts.index("src") + 1])
    except (ValueError, IndexError):
        return None

    parts = source_path.relative_to(src_root).parts
    if len(parts) < 3:
        return None

    namespace = parts[0]
    if namespace not in {"hooks", "plugins"}:
        return None

    repo_root = src_root.parent
    if namespace == "hooks" and len(parts) >= 4 and parts[1] == "docs":
        return repo_root / "src" / namespace / f"{parts[2]}.py"
    if parts[1] == "docs":
        return repo_root / "src" / namespace / f"{parts[2]}.py"

    owner = parts[1]
    if namespace == "hooks" and len(parts) == 4 and owner == "docs":
        return repo_root / "src" / namespace / f"{parts[2]}.py"
    if len(parts) == 4 and parts[2] == "docs":
        return repo_root / "src" / namespace / owner / "__init__.py"
    if len(parts) == 5 and parts[2] == "docs":
        return repo_root / "src" / namespace / owner / f"{parts[3]}.py"
    return None


def extract_metadata_field(raw_text: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}":\s*"([^"]+)"', raw_text)
    return match.group(1).strip() if match else ""
