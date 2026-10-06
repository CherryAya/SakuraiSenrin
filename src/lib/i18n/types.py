from typing import Literal, get_args

LocaleCode = Literal["zh-CN", "lzh", "x-meme"]

DEFAULT_LOCALE_CODE: LocaleCode = "zh-CN"

# 从 LocaleCode 推导，保证 Literal 字面量是唯一事实来源。
SUPPORTED_LOCALE_CODES: frozenset[str] = frozenset(get_args(LocaleCode))


def normalize_locale_code(value: object) -> LocaleCode:
    """严格校验 locale：不受支持时回落到默认语言。

    与 ``src.lib.i18n.runtime.normalize_locale`` 的区别：后者做别名与模糊匹配，
    用于解析用户上报的语言标识；本函数只接受已声明的 LocaleCode，用于校验
    从会话 state / 数据库读回的值。
    """
    if isinstance(value, str) and value in SUPPORTED_LOCALE_CODES:
        return value  # type: ignore[return-value]
    return DEFAULT_LOCALE_CODE
