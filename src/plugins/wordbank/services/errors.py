"""User-facing wordbank errors with i18n metadata."""

from __future__ import annotations

from src.lib.i18n.keys import MessageKey
from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode


class WordbankUserError(ValueError):
    """Error safe to show to users after localization."""

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


def format_wordbank_error(exc: Exception, locale: LocaleCode) -> str:
    if isinstance(exc, WordbankUserError):
        return exc.localize(locale)
    return str(exc)
