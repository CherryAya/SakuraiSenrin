"""Static help asset manifest and local-file loading helpers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import json
from pathlib import Path
from typing import Literal, TypedDict

from src.database.core.consts import Permission

from .models import DocNode, FeatureDoc, HelpDashboardSection

StaticPermissionProfile = Literal[
    "normal",
    "group_admin",
    "group_owner",
    "superuser",
]
StaticTargetKind = Literal["dashboard", "feature", "guide", "static"]


class StaticAssetManifest(TypedDict):
    version: int
    locale: str
    targets: dict[str, dict[StaticPermissionProfile, str]]


@dataclass(slots=True, frozen=True)
class StaticTarget:
    key: str
    source_path: Path
    kind: StaticTargetKind


def permission_profile(actor_permission: Permission) -> StaticPermissionProfile:
    if actor_permission.has(Permission.SUPERUSER):
        return "superuser"
    if actor_permission.has(Permission.GROUP_OWNER):
        return "group_owner"
    if actor_permission.has(Permission.GROUP_ADMIN):
        return "group_admin"
    return "normal"


def dashboard_target_key() -> str:
    return "dashboard:index"


def feature_target_key(node: DocNode, feature: FeatureDoc) -> str:
    return f"feature:{node.slug}:{feature.slug}"


def guide_target_key(node: DocNode) -> str:
    return f"guide:{node.slug}"


def static_target_key(node: DocNode) -> str:
    return f"static:{node.slug}"


def dashboard_signature(sections: tuple[HelpDashboardSection, ...]) -> str:
    payload = "|".join(
        f"{section.kind}:{','.join(node.slug for node in section.nodes)}"
        for section in sections
    )
    return short_signature(payload)


def feature_signature(node: DocNode, feature: FeatureDoc) -> str:
    return short_signature(f"{node.slug}|{feature.slug}")


def guide_signature(
    node: DocNode,
    *,
    feature_slugs: tuple[str, ...],
    child_slugs: tuple[str, ...],
) -> str:
    payload = f"{node.slug}|features:{','.join(feature_slugs)}|children:{','.join(child_slugs)}"
    return short_signature(payload)


def static_signature(node: DocNode) -> str:
    return short_signature(node.slug)


def short_signature(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:10]


def manifest_path_for(source_path: Path) -> Path:
    return source_path.parent / "demos" / "manifest.json"


def load_manifest(source_path: Path) -> StaticAssetManifest | None:
    manifest_path = manifest_path_for(source_path)
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    locale = payload.get("locale")
    targets = payload.get("targets")
    if version != 1 or not isinstance(locale, str) or not isinstance(targets, dict):
        return None
    normalized: dict[str, dict[StaticPermissionProfile, str]] = {}
    valid_profiles = {"normal", "group_admin", "group_owner", "superuser"}
    for target_key, variants in targets.items():
        if not isinstance(target_key, str) or not isinstance(variants, dict):
            continue
        entries: dict[StaticPermissionProfile, str] = {}
        for profile, filename in variants.items():
            if profile not in valid_profiles or not isinstance(filename, str):
                continue
            trimmed = filename.strip()
            if trimmed:
                entries[profile] = trimmed  # type: ignore[assignment]
        if entries:
            normalized[target_key] = entries
    return {
        "version": 1,
        "locale": locale,
        "targets": normalized,
    }


def resolve_static_asset_path(
    source_path: Path,
    *,
    target_key: str,
    actor_permission: Permission,
) -> Path | None:
    manifest = load_manifest(source_path)
    if manifest is None:
        return None
    variants = manifest["targets"].get(target_key)
    if not variants:
        return None
    filename = variants.get(permission_profile(actor_permission))
    if not filename:
        return None
    path = source_path.parent / "demos" / filename
    return path if path.is_file() else None


def load_static_asset_bytes(
    source_path: Path,
    *,
    target_key: str,
    actor_permission: Permission,
) -> bytes | None:
    path = resolve_static_asset_path(
        source_path,
        target_key=target_key,
        actor_permission=actor_permission,
    )
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None
