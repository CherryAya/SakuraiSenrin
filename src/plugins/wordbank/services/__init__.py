"""Wordbank service exports."""

from src.plugins.wordbank.database import wordbank_repo
from src.plugins.wordbank.services.core import WordbankService
from src.plugins.wordbank.services.media import WordbankMediaService

wordbank_service = WordbankService(wordbank_repo)
wordbank_media_service = WordbankMediaService(wordbank_repo)

__all__ = [
    "WordbankMediaService",
    "WordbankService",
    "wordbank_media_service",
    "wordbank_service",
]
