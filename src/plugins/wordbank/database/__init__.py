"""wordbank 数据库导出。"""

from .repo import WordbankRepository

wordbank_repo = WordbankRepository()

__all__ = ["WordbankRepository", "wordbank_repo"]
