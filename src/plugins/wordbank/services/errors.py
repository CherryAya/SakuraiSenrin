"""wordbank 面向用户的错误，带 i18n 元数据。"""

from __future__ import annotations

from src.lib.i18n.catalog import tr
from src.lib.i18n.keys import MessageKey
from src.lib.i18n.types import LocaleCode


class WordbankUserError(ValueError):
    """本地化后可安全展示给用户的错误。"""

    def __init__(
        self,
        fallback: str,
        *,
        key: MessageKey,
        **params: object,
    ) -> None:
        super().__init__(fallback)
        self.key: MessageKey = key
        self.params: dict[str, object] = params

    def localize(self, locale: LocaleCode) -> str:
        return tr(locale, self.key, **self.params)
