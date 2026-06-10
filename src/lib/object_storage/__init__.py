"""Reusable object storage clients."""

from .factory import build_object_storage_registry
from .r2 import R2ObjectStorageClient
from .registry import ObjectStorageRegistry
from .types import (
    ObjectStorageClient,
    ObjectStorageConfigError,
    ObjectStorageError,
    StorageObject,
)

__all__ = [
    "ObjectStorageClient",
    "ObjectStorageConfigError",
    "ObjectStorageError",
    "ObjectStorageRegistry",
    "R2ObjectStorageClient",
    "StorageObject",
    "build_object_storage_registry",
]
