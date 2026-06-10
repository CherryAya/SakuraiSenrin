"""Reusable object storage clients."""

from .factory import build_object_storage_registry
from .github import GitHubObjectStorageClient
from .r2 import R2ObjectStorageClient
from .registry import ObjectStorageRegistry
from .types import (
    ObjectStorageClient,
    ObjectStorageConfigError,
    ObjectStorageError,
    StorageObject,
)

__all__ = [
    "GitHubObjectStorageClient",
    "ObjectStorageClient",
    "ObjectStorageConfigError",
    "ObjectStorageError",
    "ObjectStorageRegistry",
    "R2ObjectStorageClient",
    "StorageObject",
    "build_object_storage_registry",
]
