from __future__ import annotations

from src.lib.db.schema import PatchRegistry


def build_core_patch_registry() -> PatchRegistry:
    return PatchRegistry()


def build_log_patch_registry() -> PatchRegistry:
    return PatchRegistry()


def build_snapshot_patch_registry() -> PatchRegistry:
    return PatchRegistry()
