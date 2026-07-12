"""Wordbank database exports."""

from .repo import WordbankRepository

wordbank_repo = WordbankRepository()

__all__ = ["WordbankRepository", "wordbank_repo"]
