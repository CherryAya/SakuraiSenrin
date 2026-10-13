"""从 `src/locales/zh_cn.py` 重新生成 `keys.py`（人工执行的代码生成工具）。

新增/改动文案键后运行：uv run python -m src.lib.i18n.generate_keys

# deadcode-keep: manual-entry | 人工执行的 i18n key 生成入口
"""

from __future__ import annotations

from pathlib import Path

from src.locales.zh_cn import CATALOG

HEADER = (
    '"""Generated message keys from src.locales.zh_cn."""\n\n'
    "from typing import Literal\n\n"
)


def build_keys_module() -> str:
    keys = tuple(sorted(CATALOG))
    literal_values = ",\n    ".join(f'"{key}"' for key in keys)
    tuple_values = ",\n    ".join(f'"{key}"' for key in keys)
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
