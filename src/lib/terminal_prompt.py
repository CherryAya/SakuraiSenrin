from __future__ import annotations

import sys

from src.logger import logger


def ask_user_yes_no_with_timeout(
    prompt: str,
    *,
    timeout: int,
    default: bool = False,
    default_label: str | None = None,
) -> bool:
    default_hint = "Y/n" if default else "y/N"
    default_text = default_label or ("确认" if default else "取消")
    sys.stdout.write(
        f"\n{prompt} [{default_hint}] (默认 {timeout} 秒后{default_text}): "
    )
    sys.stdout.flush()

    if sys.platform == "win32":
        try:
            value = input().strip().lower()
        except EOFError:
            logger.warning("\n⏳ 未读取到终端输入，按默认选项继续。")
            return default
        return _parse_yes_no(value, default=default)

    import select

    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
    if not rlist:
        logger.warning("\n⏳ 等待超时，按默认选项继续。")
        return default
    return _parse_yes_no(sys.stdin.readline().strip().lower(), default=default)


def _parse_yes_no(value: str, *, default: bool) -> bool:
    if value == "":
        return default
    if value in {"y", "yes"}:
        return True
    if value in {"n", "no"}:
        return False
    return default
