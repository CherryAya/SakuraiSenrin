from __future__ import annotations

from pathlib import Path

from src.locales.zh_cn import CATALOG

HEADER = (
    '"""Generated message keys from src.locales.zh_cn."""\n\n'
    "from typing import Literal\n\n"
)


def build_keys_module() -> str:
    keys = tuple(sorted(CATALOG))
    literal_values = ",\n    ".join(repr(key) for key in keys)
    tuple_values = ",\n    ".join(repr(key) for key in keys)
    return (
        HEADER
        + "MessageKey = Literal[\n"
        + f"    {literal_values}\n"
        + "]\n\n"
        + "ALL_MESSAGE_KEYS: tuple[MessageKey, ...] = (\n"
        + f"    {tuple_values}\n"
        + ")\n"
    )


def main() -> None:
    target = Path(__file__).with_name("keys.py")
    target.write_text(build_keys_module(), encoding="utf-8")


if __name__ == "__main__":
    main()
