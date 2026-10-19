"""平台请求标识的基础校验；标识内容是 opaque token，不作裁剪或解析。"""

from typing import TypeGuard


def is_valid_request_flag(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())
