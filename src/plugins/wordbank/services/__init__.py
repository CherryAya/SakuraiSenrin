"""Wordbank service exports."""

from src.config import config
from src.lib.object_storage.factory import object_storage_registry
from src.plugins.wordbank.database import wordbank_repo
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import (
    LocalWordbankMediaStorage,
    ObjectStorageWordbankMediaStorage,
    R2WordbankMediaStorage,
    WordbankMediaService,
)


def _build_wordbank_media_service() -> WordbankMediaService:
    local_storage = LocalWordbankMediaStorage()
    provider = config.WORDBANK_MEDIA_PROVIDER.strip().lower()
    if provider in {"github", "r2"}:
        client = object_storage_registry.get(provider)
        if client is not None and client.available:
            return WordbankMediaService(
                wordbank_repo,
                storage=ObjectStorageWordbankMediaStorage(
                    client,
                    fallback=local_storage,
                ),
            )
    return WordbankMediaService(wordbank_repo, storage=local_storage)


wordbank_service = WordbankService(wordbank_repo)
wordbank_media_service = _build_wordbank_media_service()

__all__ = [
    "LocalWordbankMediaStorage",
    "ObjectStorageWordbankMediaStorage",
    "R2WordbankMediaStorage",
    "WordbankMediaService",
    "WordbankService",
    "wordbank_media_service",
    "wordbank_service",
]
