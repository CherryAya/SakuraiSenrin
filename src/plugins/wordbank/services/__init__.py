"""Wordbank service exports."""

from pathlib import Path

from src.config import config
from src.lib.object_storage.factory import object_storage_registry
from src.plugins.wordbank.database import wordbank_repo
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import (
    DEFAULT_MEDIA_ROOT,
    LocalLruCacheWordbankMediaStorage,
    LocalWordbankMediaStorage,
    ObjectStorageWordbankMediaStorage,
    R2WordbankMediaStorage,
    WordbankMediaService,
)


def _build_wordbank_media_service() -> WordbankMediaService:
    local_storage = LocalWordbankMediaStorage()
    cache_storage = LocalLruCacheWordbankMediaStorage(
        Path(config.WORDBANK_MEDIA_CACHE_ROOT),
        enabled=config.WORDBANK_MEDIA_CACHE_ENABLED,
        max_bytes=config.WORDBANK_MEDIA_CACHE_MAX_BYTES,
        trim_to_bytes=config.WORDBANK_MEDIA_CACHE_TRIM_TO_BYTES,
        max_files=config.WORDBANK_MEDIA_CACHE_MAX_FILES,
    )
    provider = config.WORDBANK_MEDIA_PROVIDER.strip().lower()
    remote_storage: ObjectStorageWordbankMediaStorage | None = None
    if provider in {"github", "r2"}:
        client = object_storage_registry.get(provider)
        if client is not None and client.available:
            remote_storage = ObjectStorageWordbankMediaStorage(client)
    return WordbankMediaService(
        wordbank_repo,
        media_root=DEFAULT_MEDIA_ROOT,
        remote_storage=remote_storage,
        legacy_storage=local_storage,
        cache_storage=cache_storage,
        remote_required=config.WORDBANK_MEDIA_REMOTE_REQUIRED,
        remote_provider=provider,
        remote_sync_mode=getattr(
            config,
            "WORDBANK_MEDIA_REMOTE_SYNC_MODE",
            "deferred",
        ),
        prewarm_local_cache=config.WORDBANK_MEDIA_CACHE_ENABLED,
    )


wordbank_service = WordbankService(wordbank_repo)
wordbank_media_service = _build_wordbank_media_service()

__all__ = [
    "LocalLruCacheWordbankMediaStorage",
    "LocalWordbankMediaStorage",
    "ObjectStorageWordbankMediaStorage",
    "R2WordbankMediaStorage",
    "WordbankMediaService",
    "WordbankService",
    "wordbank_media_service",
    "wordbank_service",
]
