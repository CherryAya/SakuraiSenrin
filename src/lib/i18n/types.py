from typing import Literal, get_args

LocaleCode = Literal["zh-CN", "lzh", "x-meme"]

DEFAULT_LOCALE_CODE: LocaleCode = "zh-CN"

# 语言码集合由 LocaleCode 字面量推导
SUPPORTED_LOCALE_CODES: frozenset[str] = frozenset(get_args(LocaleCode))


def normalize_locale_code(value: object) -> LocaleCode:
    """严格按已声明的 LocaleCode 校验，其余值回落到默认语言。"""
    if isinstance(value, str) and value in SUPPORTED_LOCALE_CODES:
        return value  # type: ignore[return-value]
    return DEFAULT_LOCALE_CODE
