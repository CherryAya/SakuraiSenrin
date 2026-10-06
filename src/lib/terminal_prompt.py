from __future__ import annotations

import sys

from src.lib.i18n.runtime import tr
from src.lib.i18n.types import LocaleCode
from src.logger import logger


def build_yes_no_prompt_text(
    prompt: str,
    *,
    timeout: int,
    default: bool = False,
    default_label: str | None = None,
    locale: LocaleCode = "zh-CN",
) -> str:
    default_hint = "Y/n" if default else "y/N"
    default_text = default_label or tr(
        locale,
        "terminal.prompt.confirm" if default else "terminal.prompt.cancel",
    )
    hint_text = tr(
        locale,
        "terminal.prompt.default_hint",
        timeout=timeout,
        default_text=default_text,
    )
    return f"\n{prompt} [{default_hint}]{hint_text}"


def ask_user_yes_no_with_timeout(
    prompt: str,
    *,
    timeout: int,
    default: bool = False,
    default_label: str | None = None,
    locale: LocaleCode = "zh-CN",
) -> bool:
    sys.stdout.write(
        build_yes_no_prompt_text(
            prompt,
            timeout=timeout,
            default=default,
            default_label=default_label,
            locale=locale,
        )
    )
    sys.stdout.flush()

    if sys.platform == "win32":
        try:
            value = input().strip().lower()
        except EOFError:
            logger.warning(tr(locale, "terminal.prompt.no_input_default_applied"))
            return default
        return _parse_yes_no(value, default=default)

    import select

    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
    if not rlist:
        logger.warning(tr(locale, "terminal.prompt.timeout_default_applied"))
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
