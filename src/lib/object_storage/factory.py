"""Factory helpers for configured object storage providers."""

from __future__ import annotations

from src.config import config

from .github import GitHubObjectStorageClient
from .r2 import R2ObjectStorageClient
from .registry import ObjectStorageRegistry


def build_object_storage_registry() -> ObjectStorageRegistry:
    registry = ObjectStorageRegistry()
    registry.register(
        R2ObjectStorageClient(
            access_key_id=config.R2_ACCESS_KEY_ID,
            secret_access_key=config.R2_SECRET_ACCESS_KEY,
            bucket=config.R2_BUCKET,
            account_id=config.R2_ACCOUNT_ID,
            endpoint=config.R2_ENDPOINT,
            public_base_url=config.R2_PUBLIC_BASE_URL,
        )
    )
    registry.register(
        GitHubObjectStorageClient(
            token=config.GITHUB_TOKEN,
            repo=config.GITHUB_REPO,
            branch=config.GITHUB_BRANCH,
        )
    )
    return registry


object_storage_registry = build_object_storage_registry()
