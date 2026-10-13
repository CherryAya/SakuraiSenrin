"""i18n 目录完整性校验（人工执行的运维工具）。

校验三语 catalog 与 `keys.py` 的一致性：未知键、必备键缺失、占位符不匹配。

# deadcode-keep: manual-entry | 人工执行的 i18n 完整性校验入口，无代码调用方

用法::

    uv run python -m src.lib.i18n.check

本模块零 import 者属预期形态：它是 CLI 入口而非库。删除它会丢掉「改文案后
校验三语是否同步」的能力（当前测试未覆盖这三类校验）。
"""

from __future__ import annotations

import re

from src.lib.i18n.keys import ALL_MESSAGE_KEYS
from src.locales.lzh import CATALOG as LZH_CATALOG
from src.locales.x_meme import CATALOG as X_MEME_CATALOG
from src.locales.zh_cn import CATALOG as ZH_CATALOG


def _extract_placeholders(value: str) -> set[str]:
    placeholder_pattern = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
    return set(placeholder_pattern.findall(value))


def _assert_no_unknown_keys() -> None:
    known = set(ALL_MESSAGE_KEYS)
    for locale_name, catalog in (
        ("zh-CN", ZH_CATALOG),
        ("lzh", LZH_CATALOG),
        ("x-meme", X_MEME_CATALOG),
    ):
        extra = set(catalog) - known
        if extra:
            raise ValueError(f"{locale_name} has unknown keys: {sorted(extra)}")


def _assert_required_completeness() -> None:
    zh_keys = set(ZH_CATALOG)
    lzh_keys = set(LZH_CATALOG)
    if zh_keys != lzh_keys:
        missing = sorted(zh_keys - lzh_keys)
        extra = sorted(lzh_keys - zh_keys)
        raise ValueError(
            f"lzh catalog must match zh-CN exactly; missing={missing}, extra={extra}"
        )


def _assert_placeholder_compatibility() -> None:
    for key in ALL_MESSAGE_KEYS:
        zh_placeholders = _extract_placeholders(ZH_CATALOG[key])
        for locale_name, catalog in (
            ("lzh", LZH_CATALOG),
            ("x-meme", X_MEME_CATALOG),
        ):
            if key not in catalog:
                continue
            placeholders = _extract_placeholders(catalog[key])
            if placeholders != zh_placeholders:
                raise ValueError(
                    f"{locale_name} placeholders mismatch for {key}: "
                    f"{sorted(placeholders)} != {sorted(zh_placeholders)}"
                )


def main() -> None:
    _assert_no_unknown_keys()
    _assert_required_completeness()
    _assert_placeholder_compatibility()


if __name__ == "__main__":
    main()
